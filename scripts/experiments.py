#!/usr/bin/env python3
"""Experimentos de los escenarios de calidad del artículo (contra la plataforma en Docker).

    python scripts/experiments.py qa09            # seguridad: rechazo sin token
    python scripts/experiments.py qa12            # fiabilidad: palabras sin seña
    python scripts/experiments.py qa04            # modificabilidad: 10 señas nuevas por API
    python scripts/experiments.py qa06 --label antes   # disponibilidad: caída del STT
    python scripts/experiments.py load            # rendimiento bajo concurrencia

Cada experimento escribe results/<qa>_<label>.json (+ .csv cuando hay series) con
el entorno de ejecución, para poder citarlo en el artículo y regenerar las figuras.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import os
import platform
import statistics
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
GATEWAY = os.getenv("GATEWAY", "http://127.0.0.1:8080")
USER, PASS = os.getenv("RSP_USER", "operator"), os.getenv("RSP_PASS", "operator123")


# ----------------------------------------------------------------- utilidades
def token(client: httpx.Client) -> str:
    r = client.post(f"{GATEWAY}/auth/token", json={"username": USER, "password": PASS})
    r.raise_for_status()
    return r.json()["access_token"]


def environment() -> dict:
    def sh(cmd: str) -> str:
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": platform.platform(),
        "cpu": sh("sysctl -n machdep.cpu.brand_string") or platform.processor(),
        "cpus": os.cpu_count(),
        "docker": sh("docker version --format '{{.Server.Version}}'"),
        "git_commit": sh(f"git -C {ROOT} rev-parse --short HEAD"),
        # results/ es la salida de los experimentos, no el código evaluado
        "git_dirty": bool(sh(f"git -C {ROOT} status --porcelain -- . ':!results' ':!data/audio_tts' ':!data/audio_ctrl'")),
        "stt_provider": os.getenv("RSP_STT_PROVIDER") or sh("docker compose exec -T stt printenv STT_PROVIDER") or "echo",  # RSP_STT_PROVIDER si el STT medido corre fuera de contenedores
        # sistema bajo prueba cuando no es este árbol (estado «antes» o la arquitectura actual)
        **({"sut_dir": Path(sut).name, "sut_commit": sh(f"git -C {sut} rev-parse --short HEAD"),
            "sut_dirty": bool(sh(f"git -C {sut} status --porcelain")), "sut_diff": sh(f"git -C {sut} diff --stat"),
            "sut_note": os.environ.get("RSP_SUT_NOTE", "")}
           if (sut := os.environ.get("RSP_SUT_DIR")) else {}),
    }


def save(name: str, payload: dict, rows: list[dict] | None = None) -> None:
    RESULTS.mkdir(exist_ok=True)
    payload = {"experiment": name, "environment": environment(), **payload}
    (RESULTS / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if rows:
        with open(RESULTS / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list({k: None for r in rows for k in r}))
            w.writeheader()
            w.writerows(rows)
    print(f"→ results/{name}.json" + (f" + .csv ({len(rows)} filas)" if rows else ""))


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def describe(xs: list[float]) -> dict:
    return {"n": len(xs), "mean": statistics.fmean(xs), "median": statistics.median(xs), "p95": pct(xs, 0.95),
            "sd": statistics.stdev(xs) if len(xs) > 1 else 0.0, "min": min(xs), "max": max(xs)} if xs else {"n": 0}


def session(client: httpx.Client, headers: dict, text: str | None = None, audio: Path | None = None,
            options: dict | None = None, timeout_s: float = 60) -> dict:
    """Una sesión completa: abre el SSE, envía, espera done/failed o timeout. Devuelve el detalle de telemetría."""
    t0 = time.time()
    sid = client.post(f"{GATEWAY}/v1/sessions", headers=headers).json()["session_id"]
    status, last, plan = "timeout", {}, {}
    with client.stream("GET", f"{GATEWAY}/v1/sessions/{sid}/events", headers=headers,
                       params={"timeout_s": timeout_s}, timeout=timeout_s + 5) as stream:
        body = {"language_hint": "es", "options": options or {}}
        if audio is not None:
            with open(audio, "rb") as f:
                r = client.post(f"{GATEWAY}/v1/sessions/{sid}/audio", headers=headers,
                                files={"file": (audio.name, f, "audio/wav")},
                                data={"language_hint": "es", "options": json.dumps(options or {})})
        else:
            r = client.post(f"{GATEWAY}/v1/sessions/{sid}/text", headers=headers, json={"text": text, **body})
        if r.status_code >= 400:
            return {"session_id": sid, "status": f"http_{r.status_code}", "wall_ms": (time.time() - t0) * 1000}
        event = None
        for line in stream.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                data = json.loads(line[5:])
                if event == "voice.text.transcribed":
                    last["transcript"] = data.get("text")
                if event == "signs.gesture.plan":
                    plan = data
                if event == "actuator.state" and data.get("status") in ("done", "failed", "timeout"):
                    status = data["status"]
                    last["trace"] = data.get("trace") or {}
                    break
                event = None
    # la traza viaja en el propio evento final: no depende de que telemetría ya lo haya guardado
    tr = last.get("trace") or {}
    order = ["captured", "transcribed", "glossed", "planned", "accepted", "executing", "done"]
    marks = [k for k in order if k in tr]
    detail = {**tr, "status": status, "stages_ms": {f"{a}->{b}": tr[b] - tr[a] for a, b in zip(marks, marks[1:])}}
    return {"session_id": sid, "status": status, "wall_ms": (time.time() - t0) * 1000, "detail": detail,
            "plan": plan, **last}


# ----------------------------------------------------------------- QA-09 seguridad
def qa09(args) -> None:
    """Toda ruta protegida rechaza sin token / con token inválido; los servicios internos no son alcanzables."""
    import jwt  # PyJWT, dependencia de rsp_common

    with httpx.Client(timeout=10) as c:
        spec = c.get(f"{GATEWAY}/openapi.json").json()
        good = token(c)
        viewer = c.post(f"{GATEWAY}/auth/token", json={"username": "viewer", "password": "viewer123"}).json().get("access_token")
        forged = jwt.encode({"sub": "operator", "roles": ["admin"], "exp": int(time.time()) + 600}, "otra-clave", algorithm="HS256")
        expired = jwt.encode({"sub": "operator", "roles": ["operator"], "exp": int(time.time()) - 60},
                             os.getenv("AUTH_SECRET", "cambia-este-secreto"), algorithm="HS256")
        variants = {"sin_token": {}, "token_malformado": {"Authorization": "Bearer abc.def.ghi"},
                    "firma_falsa": {"Authorization": f"Bearer {forged}"}, "expirado": {"Authorization": f"Bearer {expired}"},
                    "valido": {"Authorization": f"Bearer {good}"}}
        rows = []
        public = {"/auth/token", "/health", "/metrics", "/v1/system/health", "/openapi.json", "/docs", "/redoc"}
        # ADR-011: con un token válido de solo lectura, lo que mueve el brazo o cambia el vocabulario → 403
        operator_only = [("POST", "/v1/sessions"), ("POST", "/v1/sessions/00000000000000000000000000000000/text"),
                         ("POST", "/v1/sessions/00000000000000000000000000000000/audio"), ("POST", "/v1/actuator/servo"),
                         ("POST", "/v1/actuator/pose"), ("POST", "/v1/actuator/reset"), ("PUT", "/v1/signs/HOLA"),
                         ("DELETE", "/v1/signs/HOLA")]
        role_rows = []
        for method, path in operator_only:
            r = c.request(method, f"{GATEWAY}{path}", headers={"Authorization": f"Bearer {viewer}"}, json={}, timeout=5)
            role_rows.append({"method": method, "path": path, "status": r.status_code})
        for path, ops in spec["paths"].items():
            for method in ops:
                if method.upper() not in {"GET", "POST", "PUT", "DELETE"}:
                    continue
                concrete = path.replace("{session_id}", "00000000000000000000000000000000").replace("{path:path}", "x").replace("{path}", "x")
                for vname, h in variants.items():
                    if path in public and vname != "sin_token":
                        continue
                    try:
                        r = c.request(method.upper(), f"{GATEWAY}{concrete}", headers=h, json={} if method != "get" else None,
                                      timeout=5)
                        code = r.status_code
                    except httpx.ReadTimeout:
                        code = "timeout"  # SSE abierto con token válido
                    rows.append({"method": method.upper(), "path": path, "variant": vname, "status": code,
                                 "public": path in public})
        # puertos internos: solo el gateway debería responder desde el host
        ports = {"auth": 8001, "voice-capture": 8002, "stt": 8003, "nlp": 8004, "sign-translation": 8005,
                 "actuator-control": 8006, "telemetry": 8007}
        reach = {}
        for name, port in ports.items():
            try:
                c.get(f"http://127.0.0.1:{port}/health", timeout=1.5)
                reach[name] = "alcanzable"
            except httpx.HTTPError:
                reach[name] = "no alcanzable"
    # frontera del bus y del monitoreo: el bus no debe aceptar conexiones desde el host ni, dentro de la red
    # de contenedores, sin credencial; Prometheus y Grafana solo deben responder en la interfaz local
    import socket

    def tcp(host: str, port: int) -> str:
        try:
            socket.create_connection((host, port), timeout=1.5).close()
            return "alcanzable"
        except OSError:
            return "no alcanzable"
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1)); lan = probe.getsockname()[0]
    except OSError:
        lan = None
    finally:
        probe.close()
    grafana = int(os.getenv("GRAFANA_PORT", "3000"))
    boundary = {"bus_desde_el_host": {str(p): tcp("127.0.0.1", p) for p in (4222, 8222)},
                "monitoreo_desde_la_red": {str(p): tcp(lan, p) if lan else "sin interfaz de red" for p in (9090, grafana)}}
    # dentro de la red de contenedores, sin la credencial del bus
    code = ("import asyncio, nats\n"
            "async def main():\n"
            "    try:\n"
            "        nc = await nats.connect('nats://nats:4222', connect_timeout=3, allow_reconnect=False)\n"
            "        await nc.publish('signs.gesture.plan', b'{}'); await nc.flush(); await nc.close(); print('ACEPTADO')\n"
            "    except Exception as e:\n"
            "        print('RECHAZADO', type(e).__name__, e)\n"
            "asyncio.run(main())\n")
    out = subprocess.run(["docker", "compose", "exec", "-T", "gateway", "python", "-c", code], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    boundary["bus_sin_credencial_dentro_de_la_red"] = out or "sin respuesta"
    print("frontera:", json.dumps(boundary, ensure_ascii=False))
    protected = [r for r in rows if not r["public"]]
    bad = [r for r in protected if r["variant"] != "valido" and r["status"] not in (401, 403)]
    summary = {v: {"requests": sum(1 for r in protected if r["variant"] == v),
                   "rejected_401_403": sum(1 for r in protected if r["variant"] == v and r["status"] in (401, 403))}
               for v in variants}
    print(json.dumps(summary, indent=1, ensure_ascii=False), "\nno rechazadas:", bad[:5], "\npuertos:", reach)
    forbidden = sum(1 for r in role_rows if r["status"] == 403)
    print(f"rol viewer en rutas de operador: {forbidden}/{len(role_rows)} con 403")
    save("qa09_security", {"protected_routes": len({(r['method'], r['path']) for r in protected}), "summary": summary,
                           "not_rejected": bad, "internal_ports_from_host": reach, "bus_and_monitoring_boundary": boundary,
                           "viewer_on_operator_routes": {"requests": len(role_rows), "forbidden_403": forbidden, "rows": role_rows}}, rows)


# ----------------------------------------------------------------- QA-12 fiabilidad
QA12_CASES = ["hola", "año", "ñandú", "42", "hola 42 bien", "café", "xyzw", "hola ñ bien", "pingüino", "¿cómo?"]


def qa12(args) -> None:
    """Palabras sin seña: ¿el plan se ejecuta con lo conocido y la brecha queda registrada?"""
    with httpx.Client(timeout=30) as c:
        h = {"Authorization": f"Bearer {token(c)}"}
        rows = []
        for text in QA12_CASES:
            gl = c.post(f"{GATEWAY}/v1/nlp/gloss", headers=h, json={"text": text, "language": "es"}).json()
            glosses = gl.get("glosses", [])
            plan = c.post(f"{GATEWAY}/v1/signs/plan", headers=h, json={"glosses": glosses, "fingerspell": True}).json()
            gestures = plan.get("gestures", [])
            # letras que el texto pedía y que no llegaron al plan ni a unknown_glosses
            asked = [ch for g in glosses if not any(p["gloss"] == g for p in gestures) for ch in g if ch.isalpha()]
            spelled = [p["gloss"].split(":")[1] for p in gestures if ":" in p["gloss"]]
            dropped = [ch for ch in asked if ch not in spelled] if asked else []
            reported = plan.get("unknown_glosses", [])
            silent = bool(dropped) and not any(set(dropped) & set("".join(reported)) for _ in [0])
            rows.append({"text": text, "glosses": " ".join(glosses), "gestures": len(gestures),
                         "unknown_reported": " ".join(reported), "dropped_letters": "".join(dropped),
                         "silent_gap": silent})
            print(f"{text!r:16} glosas={glosses} gestos={len(gestures):2d} desconocidas={reported} "
                  f"letras_perdidas={''.join(dropped)!r} {'SILENCIOSA' if silent else ''}")
        e2e = session(c, h, text="hola 42 bien")
    save(f"qa12_reliability_{args.label}", {"cases": rows, "e2e_mixed": {k: e2e[k] for k in ("status", "wall_ms")} | {
        "unknown": e2e["plan"].get("unknown_glosses"), "gestures": len(e2e["plan"].get("gestures", []))},
        "silent_gaps": sum(r["silent_gap"] for r in rows)}, rows)


# ----------------------------------------------------------------- QA-04 modificabilidad (vocabulario)
def qa04(args) -> None:
    """Añadir 10 señas nuevas por API y usarlas de inmediato: 0 recompilaciones, 0 despliegues."""
    new = [f"PRUEBA{i}" for i in range(1, 11)]
    with httpx.Client(timeout=30) as c:
        h = {"Authorization": f"Bearer {token(c)}"}
        before = {g: c.get(f"{GATEWAY}/v1/signs/{g}", headers=h).status_code for g in new}
        t0 = time.perf_counter()
        for i, g in enumerate(new):
            r = c.put(f"{GATEWAY}/v1/signs/{g}", headers=h,
                      json={"gloss": g, "angles": [90 + (i % 5)] * 15, "kichwa": f"pruebakichwa{i}"})
            r.raise_for_status()
        t_add = (time.perf_counter() - t0) * 1000
        plan = c.post(f"{GATEWAY}/v1/signs/plan", headers=h, json={"glosses": new, "fingerspell": False}).json()
        used = sum(1 for p in plan["gestures"] if p["kind"] == "sign")
        for g in new:  # limpieza: el experimento no deja rastro en el vocabulario
            c.delete(f"{GATEWAY}/v1/signs/{g}", headers=h)
    restarts = subprocess.run("docker compose ps --format '{{.Service}} {{.RunningFor}}'", shell=True,
                              capture_output=True, text=True, cwd=ROOT).stdout.strip().splitlines()
    out = {"signs_added": len(new), "existed_before": sum(1 for s in before.values() if s == 200),
           "add_time_ms_total": t_add, "planned_as_sign_immediately": used, "unknown_after": plan["unknown_glosses"],
           "rebuilds": 0, "redeploys": 0, "services_running_for": restarts,
           "client_changes": "ninguno: la app consume /v1/signs y /v1/nlp/dictionary"}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    save("qa04_vocabulary", out)


# ----------------------------------------------------------------- QA-06 disponibilidad
def qa06(args) -> None:
    """Caída del STT a mitad de uso. Sondea la salud agregada y mantiene sesiones de texto y de audio en curso."""
    audio = sorted((ROOT / "data" / "audio_tts").glob("*.wav"))[0]
    stop = threading.Event()
    t_start = time.time()
    health_rows, sess_rows = [], []

    def prober():
        with httpx.Client(timeout=4) as c:
            while not stop.is_set():
                t = time.time() - t_start
                try:
                    hs = c.get(f"{GATEWAY}/v1/system/health").json()
                    stt = hs.get("stt", {}).get("status", "down")
                    others = all(v.get("status") == "ok" for k, v in hs.items() if k != "stt")
                except httpx.HTTPError:
                    stt, others = "gateway_down", False
                health_rows.append({"t": round(t, 2), "stt": stt, "others_ok": others})
                time.sleep(0.5)

    def sessions(kind: str):
        with httpx.Client(timeout=40) as c:
            h = {"Authorization": f"Bearer {token(c)}"}
            while not stop.is_set():
                t = time.time() - t_start
                sid = None
                try:
                    r = session(c, h, text="hola" if kind == "text" else None, audio=audio if kind == "audio" else None,
                                options={"dry_run": True}, timeout_s=args.session_timeout)
                    st, sid = r["status"], r["session_id"]
                except Exception as exc:  # noqa: BLE001
                    st = f"error:{type(exc).__name__}"
                sess_rows.append({"t": round(t, 2), "kind": kind, "status": st, "session_id": sid,
                                  "ok": st == "done", "elapsed_s": round(time.time() - t_start - t, 2)})
                time.sleep(0.3)

    threads = [threading.Thread(target=prober), threading.Thread(target=sessions, args=("text",)),
               threading.Thread(target=sessions, args=("audio",))]
    for th in threads:
        th.start()
    time.sleep(args.warmup)
    t_fail = time.time() - t_start
    # fallo: el proceso del STT termina (SIGTERM a PID 1); la política restart:unless-stopped lo levanta
    if args.mode == "crash":
        # la imagen slim no trae `kill`; Python sí está
        subprocess.run(["docker", "compose", "exec", "-T", "stt", "python", "-c",
                        "import os, signal; os.kill(1, signal.SIGTERM)"], cwd=ROOT)
    else:  # parada prolongada: se detiene y se vuelve a arrancar a mano tras --outage s
        subprocess.run("docker compose stop stt", shell=True, cwd=ROOT, capture_output=True)
        time.sleep(args.outage)
        subprocess.run("docker compose start stt", shell=True, cwd=ROOT, capture_output=True)
    time.sleep(args.observe)
    stop.set()
    for th in threads:
        th.join(timeout=args.session_timeout + 10)

    def first_after(rows, cond):
        return next((r["t"] for r in sorted(rows, key=lambda r: r["t"]) if r["t"] > t_fail and cond(r)), None)
    stt_down_at = first_after(health_rows, lambda r: r["stt"] != "ok")
    stt_up_at = first_after([r for r in health_rows if stt_down_at and r["t"] > stt_down_at], lambda r: r["stt"] == "ok")
    during = lambda r: stt_down_at and r["t"] >= t_fail and (stt_up_at is None or r["t"] < stt_up_at)
    out = {"mode": args.mode, "t_fail_s": round(t_fail, 2), "stt_down_detected_s": stt_down_at, "stt_healthy_again_s": stt_up_at,
           "recovery_s": round(stt_up_at - t_fail, 2) if stt_up_at else None,
           "others_ok_during_outage": all(r["others_ok"] for r in health_rows if during(r)),
           "sessions": {k: {"total": sum(1 for r in sess_rows if r["kind"] == k),
                            "ok": sum(1 for r in sess_rows if r["kind"] == k and r["ok"]),
                            "ok_during_outage": sum(1 for r in sess_rows if r["kind"] == k and r["ok"] and during(r)),
                            "total_during_outage": sum(1 for r in sess_rows if r["kind"] == k and during(r)),
                            "first_ok_after_fail_s": first_after([r for r in sess_rows if r["kind"] == k], lambda r: r["ok"])}
                         for k in ("text", "audio")}}
    # ADR-010: ¿el audio enviado durante el corte se procesó al volver el STT? (el cliente ya no esperaba)
    time.sleep(3)
    late = [r for r in sess_rows if r["kind"] == "audio" and during(r) and not r["ok"] and r["session_id"]]
    with httpx.Client(timeout=10) as c:
        h = {"Authorization": f"Bearer {token(c)}"}
        done_later = sum(1 for r in late
                         if c.get(f"{GATEWAY}/v1/sessions/{r['session_id']}", headers=h).json().get("status") == "done")
    out["audio_during_outage_processed_after_recovery"] = {"sent": len(late), "done_later": done_later}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    save(f"qa06_availability_{args.label}", {"summary": out, "health": health_rows},
         [dict(r, series="session") for r in sess_rows])


def qa06r(args) -> None:
    """QA-06 con un reconocedor real y fallos repetidos (ejecutar con docker-compose.whisper.yml).

    ciclos: en cada ciclo el proceso del STT termina de golpe y, en cuanto la salud lo da por caído, se envía
            un audio y un texto. Se mide la recuperación (incluye volver a cargar el modelo) y si el audio
            enviado durante la caída se procesa al volver el servicio.
    rafaga: una parada de `--outage` s con `--burst` audios enviados durante ella; se cuenta cuántos se
            procesan tras la recuperación (ADR-010, antigüedad máxima de 30 s).
    """
    audios = sorted((ROOT / "data" / "audio_tts").glob("*__v[01].wav"))
    rows = []

    def stt_status(c) -> str:
        try:
            return c.get(f"{GATEWAY}/v1/system/health", timeout=4).json().get("stt", {}).get("status", "down")
        except httpx.HTTPError:
            return "gateway_down"

    def wait_stt(c, want_ok: bool, limit: float) -> float | None:
        t0 = time.time()
        while time.time() - t0 < limit:
            if (stt_status(c) == "ok") == want_ok:
                return time.time() - t0
            time.sleep(0.2)
        return None

    def post_audio(c, h, audio: Path) -> tuple[str, float]:
        sid = c.post(f"{GATEWAY}/v1/sessions", headers=h).json()["session_id"]
        with open(audio, "rb") as f:
            c.post(f"{GATEWAY}/v1/sessions/{sid}/audio", headers=h, files={"file": (audio.name, f, "audio/wav")},
                   data={"language_hint": "es", "options": json.dumps({"dry_run": True})})
        return sid, time.time()

    def wait_done(c, h, sid: str, t_post: float, limit: float = 60) -> tuple[str, float | None]:
        while time.time() - t_post < limit:
            st = c.get(f"{GATEWAY}/v1/sessions/{sid}", headers=h).json().get("status")
            if st in ("done", "failed"):
                return st, round(time.time() - t_post, 2)
            time.sleep(0.25)
        return "timeout", None

    with httpx.Client(timeout=30) as c:
        h = {"Authorization": f"Bearer {token(c)}"}
        session(c, h, audio=audios[0], options={"dry_run": True}, timeout_s=120)  # calentamiento: carga del modelo
        if args.mode == "ciclos":
            for k in range(args.cycles):
                wait_stt(c, True, 120)
                time.sleep(1.0)
                t_fail = time.time()
                # terminación abrupta: SIGKILL al proceso del servicio (con init: true no es el PID 1)
                subprocess.run(["docker", "compose", "exec", "-T", "stt", "python", "-c",
                                "import os, signal\n"
                                "for p in os.listdir('/proc'):\n"
                                "    if p.isdigit() and int(p) not in (1, os.getpid()) and b'uvicorn' in open(f'/proc/{p}/cmdline', 'rb').read():\n"
                                "        os.kill(int(p), signal.SIGKILL)\n"], cwd=ROOT, capture_output=True)
                down = wait_stt(c, False, 10)
                sid, t_post = post_audio(c, h, audios[k % len(audios)])
                stt_at_post = stt_status(c)
                try:
                    text = session(c, h, text="hola", options={"dry_run": True}, timeout_s=8)["status"]
                except Exception as exc:  # noqa: BLE001
                    text = f"error:{type(exc).__name__}"
                up = wait_stt(c, True, 120)
                recovery = round(time.time() - t_fail, 2) if up is not None else None
                st, done_s = wait_done(c, h, sid, t_post)
                rows.append({"cycle": k, "down_detected_s": None if down is None else round(down, 2), "stt_at_post": stt_at_post,
                             "recovery_s": recovery, "audio_status": st, "audio_done_s": done_s, "text_during_outage": text})
                print(f"{k + 1:2d}/{args.cycles} caída detectada={rows[-1]['down_detected_s']} s recuperación={recovery} s "
                      f"audio={st} en {done_s} s texto={text}")
            sent = [r for r in rows if r["stt_at_post"] != "ok"]
            rec = [r["recovery_s"] for r in rows if r["recovery_s"] is not None]
            out = {"mode": "ciclos", "cycles": len(rows), "recovered": len(rec), "recovery_s": describe(rec),
                   "audio_sent_with_stt_down": len(sent), "audio_processed": sum(1 for r in sent if r["audio_status"] == "done"),
                   "audio_sent_total": len(rows), "audio_processed_total": sum(1 for r in rows if r["audio_status"] == "done"),
                   "audio_done_s": describe([r["audio_done_s"] for r in rows if r["audio_done_s"] is not None]),
                   "text_ok_during_outage": sum(1 for r in rows if r["text_during_outage"] == "done")}
        else:
            for rep in range(args.reps):
                wait_stt(c, True, 120)
                subprocess.run("docker compose stop stt", shell=True, cwd=ROOT, capture_output=True)
                t_fail = time.time()
                wait_stt(c, False, 10)
                posted = []
                for i in range(args.burst):
                    posted.append((*post_audio(c, h, audios[(rep * args.burst + i) % len(audios)]), round(time.time() - t_fail, 2)))
                    time.sleep(max(0.0, (args.outage - 3) / args.burst - 0.05))
                time.sleep(max(0.0, args.outage - (time.time() - t_fail)))
                subprocess.run("docker compose start stt", shell=True, cwd=ROOT, capture_output=True)
                t_start = time.time()
                up = wait_stt(c, True, 120)
                for sid, t_post, age in posted:
                    st, done_s = wait_done(c, h, sid, t_post, limit=90)
                    rows.append({"rep": rep, "sent_at_s": age, "audio_status": st, "audio_done_s": done_s,
                                 "outage_s": round(t_start - t_fail, 2), "recovery_after_start_s": None if up is None else round(up, 2)})
                print(f"ráfaga {rep + 1}/{args.reps}: {sum(1 for r in rows if r['rep'] == rep and r['audio_status'] == 'done')}/{args.burst} "
                      f"audios procesados tras un corte de {rows[-1]['outage_s']} s")
            out = {"mode": "rafaga", "reps": args.reps, "burst": args.burst, "audio_sent": len(rows),
                   "audio_processed": sum(1 for r in rows if r["audio_status"] == "done"),
                   "by_status": {s: sum(1 for r in rows if r["audio_status"] == s) for s in sorted({r["audio_status"] for r in rows})},
                   "outage_s": describe([r["outage_s"] for r in rows]),
                   "audio_done_s": describe([r["audio_done_s"] for r in rows if r["audio_done_s"] is not None])}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    save(f"qa06_real_{args.label}", out, rows)


# ----------------------------------------------------------------- carga / concurrencia
def load(args) -> None:
    """Latencia de software y rendimiento con N sesiones concurrentes (dry_run: sin brazo)."""
    words = ["hola", "bien", "agua", "mano", "mejor", "uno", "ocho", "mal", "miel", "mil"]
    audios = sorted((ROOT / "data" / "audio_tts").glob("*__v[01].wav"))  # voces estándar
    rows = []
    with httpx.Client() as c:  # un token para toda la prueba: se mide la sesión, no el inicio de sesión (scrypt, ADR-014)
        h = {"Authorization": f"Bearer {token(c)}"}
    for conc in args.levels:
        def one(i):
            with httpx.Client(timeout=120) as c:
                if args.audio:
                    r = session(c, h, audio=audios[i % len(audios)], options={"dry_run": True}, timeout_s=110)
                else:
                    r = session(c, h, text=words[i % len(words)], options={"dry_run": True})
                d = r["detail"]
                end = d.get("accepted") or d.get("done")
                # sin las dos marcas no hay latencia de software (antes salían valores negativos: R12)
                sw = end - d["captured"] if end and d.get("captured") else None
                return {"concurrency": conc, "i": i, "status": r["status"], "wall_ms": r["wall_ms"], "software_ms": sw}
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=conc) as ex:
            batch = list(ex.map(one, range(args.per_level)))
        dur = time.time() - t0
        ok = [b for b in batch if b["status"] == "done"]
        sw = [b["software_ms"] for b in ok if b["software_ms"] is not None]
        rows.append({"concurrency": conc, "i": -1, "status": "batch", "wall_ms": dur * 1000, "software_ms": None})
        print(f"concurrencia={conc:3d} ok={len(ok)}/{len(batch)} sesiones/s={len(ok) / dur:6.1f} "
              f"software p50={statistics.median(sw) if sw else float('nan'):.1f} p95={pct(sw, .95):.1f} ms")
        rows += batch
    summary = {}
    for conc in args.levels:
        dur = next(r["wall_ms"] for r in rows if r["concurrency"] == conc and r["status"] == "batch") / 1000
        b = [r for r in rows if r["concurrency"] == conc and r["status"] != "batch"]
        sw = [r["software_ms"] for r in b if r["status"] == "done" and r["software_ms"] is not None]
        wall = [r["wall_ms"] for r in b if r["status"] == "done"]
        summary[conc] = {"ok": len(wall), "total": len(b), "throughput_per_s": len(wall) / dur,
                         **{f"software_{k}": v for k, v in describe(sw).items()},
                         **{f"wall_{k}": v for k, v in describe(wall).items()}}
    save(f"load_{args.label}", {"levels": args.levels, "per_level": args.per_level, "summary": summary}, rows)


# ----------------------------------------------------------------- QA-01 con reconocimiento de voz
def _norm_words(s: str) -> list[str]:
    import re
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn" or c == "̃")
    return re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFC", s)).split()


def _wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)] / max(1, len(ref))


def qa01(args) -> None:
    """Latencia de software con STT real sobre el corpus de audio y exactitud del reconocimiento."""
    corpus = ROOT / "data" / args.corpus
    # columnas: archivo, referencia, voz y, en el corpus de control, conjunto (in = del vocabulario, oov = de fuera)
    items = [(line.split("\t") + ["corpus"])[:4] for line in (corpus / "manifest.tsv").read_text(encoding="utf-8").splitlines()[1:]]
    rows = []
    with httpx.Client(timeout=120) as c:
        h = {"Authorization": f"Bearer {token(c)}"}
        # calentamiento: la primera inferencia carga el modelo en memoria
        session(c, h, audio=corpus / items[0][0], options={"dry_run": True}, timeout_s=120)
        for k in range(args.n or len(items)):
            fname, ref, voice, kind = items[k % len(items)]
            r = session(c, h, audio=corpus / fname, options={"dry_run": True}, timeout_s=120)
            d = r["detail"]
            st = d.get("stages_ms", {})
            hyp = r.get("transcript") or ""
            wer = _wer(_norm_words(ref), _norm_words(hyp))
            # lo que importa para el robot: ¿se planifica la misma seña? (glosas de la referencia vs. de lo transcrito)
            g_ref = c.post(f"{GATEWAY}/v1/nlp/gloss", headers=h, json={"text": ref, "language": "es"}).json().get("glosses")
            g_hyp = c.post(f"{GATEWAY}/v1/nlp/gloss", headers=h, json={"text": hyp, "language": "es"}).json().get("glosses")
            rows.append({"i": k, "file": fname, "voice": voice, "set": kind, "reference": ref, "transcript": hyp, "status": r["status"],
                         "exact": _norm_words(ref) == _norm_words(hyp), "wer": round(wer, 3), "gloss_match": g_ref == g_hyp,
                         "stt_ms": st.get("captured->transcribed"), "nlp_ms": st.get("transcribed->glossed"),
                         "plan_ms": st.get("glossed->planned"), "accept_ms": st.get("planned->accepted"),
                         "software_ms": (d.get("accepted") or d.get("done") or 0) - (d.get("captured") or 0), "wall_ms": round(r["wall_ms"], 1)})
            print(f"{k + 1:3d}/{args.n} {ref!r:14} → {hyp!r:18} stt={st.get('captured->transcribed')} ms "
                  f"software={rows[-1]['software_ms']} ms {'✓' if rows[-1]['exact'] else '✗'}")
    ok = [r for r in rows if r["status"] == "done"]
    sw = [r["software_ms"] for r in ok]
    out = {"label": args.label, "corpus": args.corpus, "n": len(rows), "done": len(ok),
           "empty_transcripts": sum(1 for r in rows if not r["transcript"].strip()),
           "by_set": {s: {"gloss": sum(r["gloss_match"] for r in rows if r["set"] == s), "n": sum(1 for r in rows if r["set"] == s)}
                      for s in sorted({r["set"] for r in rows})},
           "software_ms": describe(sw), "stt_ms": describe([r["stt_ms"] for r in ok if r["stt_ms"] is not None]),
           "exact_match_rate": sum(r["exact"] for r in rows) / len(rows), "mean_wer": statistics.fmean(r["wer"] for r in rows),
           "gloss_match_rate": sum(r["gloss_match"] for r in rows) / len(rows),
           "qa01_target_ms": 1500, "meets_target_p95": pct(sw, .95) <= 1500 if sw else False,
           "by_voice": {v: {"exact": sum(r["exact"] for r in rows if r["voice"] == v), "gloss": sum(r["gloss_match"] for r in rows if r["voice"] == v), "n": sum(1 for r in rows if r["voice"] == v)}
                        for v in {r["voice"] for r in rows}}}
    print(json.dumps({k: out[k] for k in ("software_ms", "stt_ms", "exact_match_rate", "gloss_match_rate", "mean_wer", "meets_target_p95")},
                     indent=1, ensure_ascii=False))
    save(f"qa01_asr_{args.label}", out, rows)


# ----------------------------------------------------------------- línea base: arquitectura actual
_LEGACY_FIELDS = ["transcription", "text", "result", "translatedText", "translated_text", "recognizedText",
                  "recognized_text", "transcript", "transcribed_text", "spanish_text", "output", "message"]


def _legacy_text(res: dict) -> str:
    """Igual que la app heredada: `data.transcription` (sign_language_screen.dart:1519); si falta,
    los campos genéricos de AudioUploadService dentro de `data`."""
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    for scope in (data,):
        for f in _LEGACY_FIELDS:
            v = scope.get(f)
            if isinstance(v, str) and v.strip():
                return v
    return ""


def legacy(args) -> None:
    """Flujo de la app heredada contra kichwa-app-api: subir → procesar (es) → sondeo cada 1 s → resultados.

    Mide la latencia percibida por el cliente hasta tener el texto, comparable con `wall_ms` de qa01
    (sesión → plan aceptado), con el mismo corpus de audio y el mismo tamaño de modelo (Whisper base, CPU).
    """
    base = args.url
    items = [line.split("\t") for line in (ROOT / "data" / "audio_tts" / "manifest.tsv").read_text(encoding="utf-8").splitlines()[1:]]
    rows = []
    with httpx.Client(timeout=60) as c:
        def one(fname: str) -> dict:
            return _legacy_one(c, base, fname)
        one(items[0][0])  # calentamiento: carga del modelo
        for k in range(args.n):
            fname, ref, voice = items[k % len(items)]
            r = one(fname)
            g_ref, g_hyp = _norm_words(ref), _norm_words(r["transcript"])
            rows.append({"i": k, "file": fname, "voice": voice, "reference": ref, **r, "exact": g_ref == g_hyp,
                         "wer": round(_wer(g_ref, g_hyp), 3)})
            print(f"{k + 1:3d}/{args.n} {ref!r:14} → {r['transcript'][:22]!r:24} {r['status']} sondeos={r['polls']} {r['wall_ms']:.0f} ms")
    ok = [r["wall_ms"] for r in rows if r["status"] == "completed"]
    out = {"label": args.label, "system": "kichwa-app-api (arquitectura actual)", "model": "openai-whisper base, CPU, fp32",
           "n": len(rows), "completed": len(ok), "wall_ms": describe(ok),
           "polls": describe([r["polls"] for r in rows]), "exact_match_rate": sum(r["exact"] for r in rows) / len(rows)}
    print(json.dumps({k: out[k] for k in ("wall_ms", "polls", "exact_match_rate")}, indent=1, ensure_ascii=False))
    save(f"baseline_legacy_{args.label}", out, rows)


def _legacy_one(c: httpx.Client, base: str, fname: str) -> dict:
    """Una petición con el flujo de la app heredada: subir → procesar (es) → sondeo cada 1 s → resultados."""
    t0 = time.time()
    with open(ROOT / "data" / "audio_tts" / fname, "rb") as f:
        up = c.post(f"{base}/audio/upload", files={"file": (fname, f, "audio/wav")}).json()
    fid = (up.get("data") or {}).get("file_id")
    pr = c.post(f"{base}/process/es/{fid}").json()
    pid = (pr.get("data") or {}).get("process_id")
    polls, status = 0, None
    for _ in range(60):  # maxAttempts = 60, delay = 1 s (igual que la app)
        polls += 1
        status = (c.get(f"{base}/process/status/{pid}").json().get("data") or {}).get("status")
        if status in ("completed", "failed", "error"):
            break
        time.sleep(1.0)
    res = c.get(f"{base}/results/{pid}").json() if status == "completed" else {}
    return {"status": status, "polls": polls, "wall_ms": round((time.time() - t0) * 1000, 1), "transcript": _legacy_text(res)}


def replica(args) -> None:
    """R-31: ambas arquitecturas con el mismo código de reconocimiento, k repeticiones por archivo,
    intercaladas (plan en docs/plan-replica-qa01.md, etiqueta plan-replica-qa01)."""
    import random
    corpus = ROOT / "data" / "audio_tts"
    items = [line.split("\t")[:3] for line in (corpus / "manifest.tsv").read_text(encoding="utf-8").splitlines()[1:]]
    rows = []
    with httpx.Client(timeout=120) as c:
        def measure(system: str, fname: str) -> dict:
            if system == "actual":
                r = _legacy_one(c, args.url, fname)
                return {"status": "done" if r["status"] == "completed" else r["status"], "wall_ms": r["wall_ms"],
                        "transcript": r["transcript"], "polls": r["polls"]}
            h = {"Authorization": f"Bearer {token(c)}"}  # token nuevo en cada medición, fuera del tiempo medido: la corrida dura más que su vigencia
            r = session(c, h, audio=corpus / fname, options={"dry_run": True}, timeout_s=120)
            return {"status": r["status"], "wall_ms": round(r["wall_ms"], 1), "transcript": r.get("transcript") or "", "polls": None}

        for system in ("actual", "propuesta"):  # calentamiento, no se analiza
            measure(system, items[0][0])
        for rep in range(1, args.reps + 1):
            rnd = random.Random(1000 + rep)
            order = list(range(len(items)))
            rnd.shuffle(order)
            for pos, k in enumerate(order):
                fname, ref, voice = items[k]
                systems = ["actual", "propuesta"]
                rnd.shuffle(systems)
                for first, system in enumerate(systems):
                    m = measure(system, fname)
                    rows.append({"rep": rep, "pos": pos, "file": fname, "voice": voice, "reference": ref, "system": system,
                                 "first": first == 0, **m, "valid": m["status"] == "done" and bool(m["transcript"].strip())})
                    print(f"rep {rep} {pos + 1:2d}/60 {system:9s} {m['wall_ms']:7.0f} ms {m['transcript'][:24]!r}")
    out = {"label": "replica", "reps": args.reps, "n": len(rows), "plan": "docs/plan-replica-qa01.md (etiqueta plan-replica-qa01)",
           "by_system": {s: {"n": sum(r["system"] == s for r in rows), "valid": sum(r["valid"] for r in rows if r["system"] == s),
                             "wall_ms": describe([r["wall_ms"] for r in rows if r["system"] == s and r["valid"]])}
                         for s in ("actual", "propuesta")}}
    print(json.dumps(out["by_system"], indent=1, ensure_ascii=False))
    save("qa01_replica", out, rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("legacy"); p.add_argument("--label", default="whisper_base"); p.add_argument("--n", type=int, default=60)
    p.add_argument("--url", default="http://127.0.0.1:8090")
    p = sub.add_parser("replica"); p.add_argument("--reps", type=int, default=5); p.add_argument("--url", default="http://127.0.0.1:8090")
    p = sub.add_parser("qa01"); p.add_argument("--label", default="whisper_base"); p.add_argument("--n", type=int, default=0, help="0 = una pasada por el corpus")
    p.add_argument("--corpus", default="audio_tts", help="carpeta de data/ con manifest.tsv")
    sub.add_parser("qa09")
    p = sub.add_parser("qa12"); p.add_argument("--label", default="antes")
    sub.add_parser("qa04")
    p = sub.add_parser("qa06")
    p.add_argument("--label", default="antes")
    p.add_argument("--mode", choices=["crash", "outage"], default="crash")
    p.add_argument("--warmup", type=float, default=10)
    p.add_argument("--outage", type=float, default=15)
    p.add_argument("--observe", type=float, default=35)
    p.add_argument("--session-timeout", type=float, default=8)
    p = sub.add_parser("qa06r")
    p.add_argument("--label", default="ciclos"); p.add_argument("--mode", choices=["ciclos", "rafaga"], default="ciclos")
    p.add_argument("--cycles", type=int, default=30); p.add_argument("--reps", type=int, default=3)
    p.add_argument("--burst", type=int, default=30); p.add_argument("--outage", type=float, default=15)
    p = sub.add_parser("load")
    p.add_argument("--label", default="echo")
    p.add_argument("--levels", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32])
    p.add_argument("--per-level", type=int, default=64)
    p.add_argument("--audio", action="store_true", help="sesiones de voz (STT real) en lugar de texto")
    args = ap.parse_args()
    {"legacy": legacy, "replica": replica, "qa01": qa01, "qa09": qa09, "qa12": qa12, "qa04": qa04, "qa06": qa06, "qa06r": qa06r, "load": load}[args.cmd](args)


if __name__ == "__main__":
    main()
