#!/usr/bin/env python3
"""Convierte results/* en los CSV compactos que leen las figuras pgfplots del artículo.

    python scripts/export_figure_data.py        # → ../Articulo_Robot_Senas_MATI/datos/*.csv + resumen.json

Los cuantiles se calculan aquí (tipo 7, interpolación lineal) para que LaTeX solo
dibuje (`boxplot prepared`). Las figuras de voz usan solo sesiones válidas (estado
final correcto y transcripción no vacía), según el plan de scripts/analysis.py.
"""
from __future__ import annotations

import csv
import json
import re
import statistics as st
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import mannwhitneyu

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = ROOT.parent / "Articulo_Robot_Senas_MATI" / "datos"


def rows(name: str) -> list[dict]:
    return list(csv.DictReader(open(RES / name, encoding="utf-8")))


def js(name: str) -> dict:
    return json.load(open(RES / name, encoding="utf-8"))


def q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def box(xs: list[float]) -> dict:
    """Caja de Tukey: bigotes hasta el último dato dentro de 1,5·RIC."""
    q1, q3 = q(xs, .25), q(xs, .75)
    iqr = q3 - q1
    lo = min(x for x in xs if x >= q1 - 1.5 * iqr)
    hi = max(x for x in xs if x <= q3 + 1.5 * iqr)
    return {"lw": lo, "q1": q1, "med": q(xs, .5), "q3": q3, "uw": hi, "mean": st.fmean(xs), "p95": q(xs, .95),
            "n": len(xs), "outliers": [x for x in xs if x < lo or x > hi]}


def write(name: str, header: list[str], data: list[list]) -> None:
    # pgfplotstable no admite comillas: ninguna celda puede llevar comas
    assert not any("," in str(v) for r in data for v in r if not isinstance(v, (int, float))), name
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows([[f"{v:.3f}" if isinstance(v, float) else v for v in r] for r in data])


def valid(rs: list[dict], ok: str) -> list[dict]:
    return [r for r in rs if r["status"] == ok and r["transcript"].strip()]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.csv"):
        old.unlink()
    summary: dict = {}

    # 1) latencia percibida con voz: actual vs propuesta (mismo código, mismo motor y su motor)
    series = [("Actual (openai-whisper fp32)", valid(rows("baseline_legacy_whisper_base.csv"), "completed")),
              ("Propuesta (mismo código de reconocimiento)", valid(rows("qa01_asr_legacy_provider.csv"), "done")),
              ("Propuesta (openai-whisper fp32)", valid(rows("qa01_asr_whisper_openai.csv"), "done")),
              ("Propuesta (faster-whisper int8)", valid(rows("qa01_asr_whisper_base.csv"), "done"))]
    boxes = [(name, box([float(r["wall_ms"]) for r in rs])) for name, rs in series]
    write("latencia_cliente_caja.csv", ["sistema", "lw", "q1", "med", "q3", "uw", "media", "p95", "n"],
          [[n, b["lw"], b["q1"], b["med"], b["q3"], b["uw"], b["mean"], b["p95"], b["n"]] for n, b in boxes])
    write("latencia_cliente_atipicos.csv", ["x", "y"], [[i + 1, y] for i, (_, b) in enumerate(boxes) for y in b["outliers"]])
    summary["latencia_cliente"] = {n: {k: v for k, v in b.items() if k != "outliers"} for n, b in boxes}
    summary["analisis"] = js("analysis_latency.json")

    # 2) desglose por etapa (medias)
    def stage_means(name: str, voice: bool) -> list[float]:
        rs = [r for r in rows(name) if r.get("status") == "done"]
        if voice:
            rs = [r for r in rs if r["transcript"].strip()]

        def get(k: str) -> float:
            xs = [float(r[k]) for r in rs if r.get(k) not in (None, "")]
            return st.fmean(xs) if xs else 0.0
        if not voice:
            return [get("captured->transcribed"), get("transcribed->glossed"), get("glossed->planned"), get("planned->accepted")]
        return [get("stt_ms"), get("nlp_ms"), get("plan_ms"), get("accept_ms")]
    stages = {"Texto": stage_means("bench_text_sim.csv", False),
              "Voz (faster-whisper base)": stage_means("qa01_asr_whisper_base.csv", True),
              "Voz (faster-whisper small)": stage_means("qa01_asr_whisper_small.csv", True),
              "Voz (openai-whisper base)": stage_means("qa01_asr_whisper_openai.csv", True)}
    summary["etapas_media_ms"] = stages
    write("etapas.csv", ["modo", "stt", "nlp", "plan", "aceptado", "total"], [[m, *v, sum(v)] for m, v in stages.items()])

    # 3) reconocimiento: exactitud y latencia por modelo (IC bootstrap del p95 de la latencia de software)
    rng = np.random.default_rng(20261028)
    asr = []
    for label, name in (("faster-whisper base (int8)", "whisper_base"), ("faster-whisper small (int8)", "whisper_small"),
                        ("openai-whisper base (fp32)", "whisper_openai")):
        d = js(f"qa01_asr_{name}.json")
        sw = np.array([float(r["software_ms"]) for r in rows(f"qa01_asr_{name}.csv") if r["status"] == "done"])
        boots = [np.quantile(rng.choice(sw, len(sw)), .95) for _ in range(5000)]
        good = [v for k, v in d["by_voice"].items() if not k.startswith("Eddy")]
        asr.append([label, d["stt_ms"]["median"], float(np.quantile(sw, .95)), float(np.quantile(boots, .025)),
                    float(np.quantile(boots, .975)), 100 * d["gloss_match_rate"],
                    100 * sum(v["gloss"] for v in good) / sum(v["n"] for v in good)])
    summary["asr"] = asr
    write("asr_compromiso.csv", ["modelo", "stt_med", "sw_p95", "sw_p95_lo", "sw_p95_hi", "glosa_todas", "glosa_voces_estandar"], asr)

    # 4) disponibilidad: corte prolongado del STT antes (texto acoplado al STT) y después (ADR-009/010)
    for label, src in (("antes", "antes_outage"), ("despues", "final_outage")):
        s = js(f"qa06_availability_{src}.json")["summary"]
        write(f"disponibilidad_{label}.csv", ["t", "y", "clase"],
              [[float(r["t"]), (1 if r["kind"] == "text" else 0) + (0.12 if r["ok"] == "True" else -0.12),
                f"{r['kind']}{'ok' if r['ok'] == 'True' else 'fallo'}"] for r in rows(f"qa06_availability_{src}.csv")])
        write(f"disponibilidad_{label}_corte.csv", ["inicio", "fin"], [[s["stt_down_detected_s"], s["stt_healthy_again_s"]]])
        summary[f"qa06_{label}"] = s
    summary["qa06_fallo_proceso_antes"] = js("qa06_availability_antes.json")["summary"]
    summary["qa06_fallo_proceso_despues"] = js("qa06_availability_final_crash.json")["summary"]

    # 5) impacto de cambio: auditoría manual (results/coupling_audit.json). Los límites de servos de la
    #    propuesta se cuentan tras quitar la copia del cliente (Calibración lee GET /v1/actuator/servos).
    aud = js("coupling_audit.json")["escenarios"]
    imp = []
    for name in ("Motor de voz", "Vocabulario", "Límites de servos", "IP en el código"):
        a = aud[name]["actual"]
        p = aud[name]["propuesta"]
        fixed = name == "Límites de servos"
        imp.append([name, a["archivos_que_requieren_cambio"], 1 if fixed else p["archivos_que_requieren_cambio"],
                    len(a["unidades_desplegables"]), 1 if fixed else len(p["unidades_desplegables"])])
    write("impacto_cambio.csv", ["escenario", "actual", "propuesta", "unidades_actual", "unidades_propuesta"], imp)
    summary["impacto_cambio"] = imp
    summary["qa03_cambio_real"] = js("qa03_engine_swap.json")

    # 6) latencia mecánica por gesto: servo a servo vs pose por lotes (simulador)
    def per_gesture(name: str) -> list[float]:
        return [float(r["mechanical_ms"]) / int(r["gestures"]) for r in rows(name) if r["gestures"] not in ("", "0")]
    ser, bat = per_gesture("bench_text_sim.csv"), per_gesture("bench_text_batch.csv")
    write("mecanica.csv", ["driver", "med", "menos", "mas"],
          [["Servo a servo (actual)", st.median(ser), st.median(ser) - min(ser), max(ser) - st.median(ser)],
           ["Pose por lotes", st.median(bat), st.median(bat) - min(bat), max(bat) - st.median(bat)]])
    summary["mecanica"] = {"serie_med": st.median(ser), "lotes_med": st.median(bat),
                           "reduccion_pct": 100 * (1 - st.median(bat) / st.median(ser)),
                           "p": float(mannwhitneyu(ser, bat, alternative="greater").pvalue),
                           "analitico_ms": {"serie": 15 * 150 + 600, "lotes": 150 + 600}}

    # 7) concurrencia en texto: antes y después de ADR-009; escalado de la voz con réplicas del STT
    def wall_by_level(name: str) -> dict[int, list[float]]:
        by = defaultdict(list)
        for r in rows(name):
            if r["status"] == "done":
                by[int(r["concurrency"])].append(float(r["wall_ms"]))
        return by
    before, after = wall_by_level("load_echo.csv"), wall_by_level("load_text.csv")
    thr = {int(k): v["throughput_per_s"] for k, v in js("load_text.json")["summary"].items()}
    write("concurrencia.csv", ["concurrencia", "antes_med", "antes_p95", "despues_med", "despues_p95", "despues_thr"],
          [[c, q(before[c], .5), q(before[c], .95), q(after[c], .5), q(after[c], .95), thr[c]] for c in sorted(after)])
    voz = []
    for c in (1, 2, 4, 8):
        r0 = js("load_voice_antes.json")["summary"][str(c)]  # e467743: bus en serie, inferencia en el bucle
        r1, r2 = js("load_voice_r1.json")["summary"][str(c)], js("load_voice_r2.json")["summary"][str(c)]
        voz.append([c, r0["throughput_per_s"], r1["throughput_per_s"], r2["throughput_per_s"],
                    r0["wall_median"], r1["wall_median"], r2["wall_median"]])
    write("escalado_voz.csv", ["concurrencia", "thr_antes", "thr_r1", "thr_r2", "med_antes", "med_r1", "med_r2"], voz)
    summary["concurrencia_texto_antes_thr"] = {int(k): v["throughput_per_s"] for k, v in js("load_echo.json")["summary"].items()}
    summary["concurrencia_texto_despues_thr"] = thr
    summary["escalado_voz"] = voz

    # 8) pruebas unitarias y cobertura por servicio (de coverage.json + junit.xml)
    cov = js("coverage.json")
    stat = defaultdict(lambda: [0, 0])
    for f, v in cov["files"].items():
        if "/tests/" in f or f.endswith("conftest.py"):
            continue
        m = re.match(r"services/([^/]+)/", f)
        svc = m.group(1) if m else ("rsp-common" if "rsp_common" in f else None)
        if svc:
            stat[svc][0] += v["summary"]["num_statements"]
            stat[svc][1] += v["summary"]["covered_lines"]
    tests = Counter()
    for tc in ET.parse(RES / "junit.xml").getroot().iter("testcase"):
        m = re.match(r"services\.([^.]+)\.", tc.get("classname", ""))
        tests[m.group(1).replace("_", "-") if m else "rsp-common"] += 1
    cov_rows = sorted(([s, tests.get(s, 0), 100 * c / n] for s, (n, c) in stat.items()), key=lambda r: -r[2])
    n_all, c_all = sum(n for n, _ in stat.values()), sum(c for _, c in stat.values())
    write("cobertura.csv", ["servicio", "pruebas", "cobertura"], cov_rows)
    ft = js("flutter_tests.json")
    summary["pruebas"] = {"python_total": sum(tests.values()), "cobertura_pct": 100 * c_all / n_all, "por_servicio": cov_rows,
                          "flutter": {k: ft.get(k) for k in ("total", "passed", "failed", "line_coverage_pct")}}

    # 8) tabla de latencia por etapa (tab:latencia) e IC bootstrap de la mediana y el p95; QA-08 por benchmark
    rng = np.random.default_rng(20261028)

    def ci(xs, p):
        a = np.asarray(xs, float)
        v = [q(list(a[rng.integers(0, len(a), len(a))]), p) for _ in range(2000)]
        return float(np.quantile(v, .025)), float(np.quantile(v, .975))

    def stage(name, xs):
        xs = [float(x) for x in xs]
        (ml, mh), (pl, ph) = ci(xs, .5), ci(xs, .95)
        return [name, len(xs), st.fmean(xs), q(xs, .5), ml, mh, q(xs, .95), pl, ph, st.stdev(xs), q(xs, .75) - q(xs, .25)]

    txt = [r for r in rows("bench_text_sim.csv") if r["status"] == "done"]
    voz = valid(rows("qa01_asr_whisper_base.csv"), "done")
    lat = [stage("texto_transcrito_glosado", [r["transcribed->glossed"] for r in txt]),
           stage("texto_glosado_plan", [r["glossed->planned"] for r in txt]),
           stage("texto_plan_aceptado", [r["planned->accepted"] for r in txt]),
           stage("texto_software", [r["software_ms"] for r in txt]),
           stage("voz_reconocimiento", [r["stt_ms"] for r in voz]),
           # glosado + plan + aceptación = software − STT; no incluye la espera hasta done
           stage("voz_glosado_plan_aceptado", [float(r["nlp_ms"]) + float(r["plan_ms"]) + float(r["accept_ms"]) for r in voz]),
           stage("voz_software", [r["software_ms"] for r in voz]),
           stage("mecanica_por_gesto", [float(r["mechanical_ms"]) / int(r["gestures"]) for r in txt if r["gestures"] not in ("", "0")])]
    write("latencia.csv", ["etapa", "n", "media", "mediana", "med_lo", "med_hi", "p95", "p95_lo", "p95_hi", "de", "ric"], lat)
    marks = ["captured->transcribed", "transcribed->glossed", "glossed->planned", "planned->accepted",
             "accepted->executing", "executing->done"]
    qa08 = [["texto_simulador", len(rows("bench_text_sim.csv")), sum(all(r[m] != "" for m in marks) for r in txt), 7],
            ["texto_lotes", len(rows("bench_text_batch.csv")),
             sum(r["status"] == "done" and all(r[m] != "" for m in marks) for r in rows("bench_text_batch.csv")), 7]]
    for lab in ("whisper_base", "whisper_small", "whisper_openai"):
        rs = rows(f"qa01_asr_{lab}.csv")  # vista previa: sin executing ni done de ejecución → 5 marcas
        qa08.append([f"voz_{lab}", len(rs), sum(all(r[k] != "" for k in ("stt_ms", "nlp_ms", "plan_ms", "accept_ms")) for r in rs), 5])
    for lab in ("text", "voice_r1", "voice_r2"):  # solo la propuesta; load_echo y load_voice_antes son el estado «antes»
        rs = [r for r in rows(f"load_{lab}.csv") if r["status"] != "batch"]
        qa08.append([f"carga_{lab}", len(rs), sum(r["software_ms"] not in ("", "None") for r in rs), 5])
    write("qa08.csv", ["benchmark", "sesiones", "con_todas_las_marcas", "marcas"], qa08)
    summary["qa08_negativos_load_echo"] = sum(r["software_ms"] not in ("", "None") and float(r["software_ms"]) < 0
                                              for r in rows("load_echo.csv"))

    # 9) resto de escenarios (para el texto)
    summary["qa09"] = {k: v for k, v in js("qa09_security.json").items()
                       if k in ("summary", "internal_ports_from_host", "viewer_on_operator_routes")}
    summary["qa04"] = {k: v for k, v in js("qa04_vocabulary.json").items()
                       if k in ("signs_added", "planned_as_sign_immediately", "add_time_ms_total", "rebuilds", "redeploys")}
    summary["qa06_bus_antes"] = js("qa06_availability_bus_antes_outage.json")["summary"]
    summary["qa12"] = {lab: js(f"qa12_reliability_{lab}.json")["cases"] for lab in ("antes", "final")}
    summary["commit"] = js("qa01_asr_whisper_base.json")["environment"]["git_commit"]

    (OUT / "resumen.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT.parent)}: " + ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
