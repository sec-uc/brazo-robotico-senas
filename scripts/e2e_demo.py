#!/usr/bin/env python3
"""Demostración end-to-end a través del gateway: obtiene token, crea sesión,
envía un texto (o un audio) y sigue los eventos SSE hasta que el brazo termina.

    python scripts/e2e_demo.py "hola mamá"                    # modo texto (sin STT)
    python scripts/e2e_demo.py --audio data/audio/mama.wav     # modo audio (STT real)
    GATEWAY=http://localhost:8080 python scripts/e2e_demo.py "bien"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

GATEWAY = os.getenv("GATEWAY", "http://127.0.0.1:8080")
USER = os.getenv("RSP_USER", "operator")
PASS = os.getenv("RSP_PASS", "operator123")


def get_token(client: httpx.Client) -> str:
    r = client.post(f"{GATEWAY}/auth/token", json={"username": USER, "password": PASS})
    r.raise_for_status()
    return r.json()["access_token"]


def run(text: str | None, audio: str | None, language: str = "es", quiet: bool = False) -> dict:
    with httpx.Client(timeout=90) as client:
        headers = {"Authorization": f"Bearer {get_token(client)}"}
        session_id = client.post(f"{GATEWAY}/v1/sessions", headers=headers).json()["session_id"]
        t0 = time.time()
        # abrir el stream ANTES de enviar para no perder eventos
        with client.stream("GET", f"{GATEWAY}/v1/sessions/{session_id}/events", headers=headers,
                           params={"timeout_s": 80}) as stream:
            if audio:
                with open(audio, "rb") as f:
                    r = client.post(f"{GATEWAY}/v1/sessions/{session_id}/audio", headers=headers,
                                    files={"file": (os.path.basename(audio), f)}, data={"language_hint": language})
            else:
                r = client.post(f"{GATEWAY}/v1/sessions/{session_id}/text", headers=headers,
                                json={"text": text, "language_hint": language})
            r.raise_for_status()
            last = {}
            event_name = None
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:") and event_name:
                    data = json.loads(line[5:])
                    if not quiet:
                        summary = {k: data[k] for k in ("text", "glosses", "status", "gesture_gloss", "unknown_glosses")
                                   if k in data}
                        if event_name == "signs.gesture.plan":
                            summary["gestures"] = [g["gloss"] for g in data["gestures"]]
                        print(f"  {time.time() - t0:6.2f}s  {event_name:24s} {summary}")
                    last = {"event": event_name, **data}
                    if event_name == "actuator.state" and data.get("status") in ("done", "failed", "timeout"):
                        break
                    event_name = None
        detail = client.get(f"{GATEWAY}/v1/sessions/{session_id}", headers=headers).json()
        return {"session_id": session_id, "wall_s": round(time.time() - t0, 3), "last": last, "telemetry": detail}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="hola mamá")
    ap.add_argument("--audio")
    ap.add_argument("--lang", default="es")
    args = ap.parse_args()
    result = run(None if args.audio else args.text, args.audio, args.lang)
    tel = result["telemetry"]
    print(f"\nsesión {result['session_id']} · estado={tel.get('status')} · e2e={tel.get('e2e_ms')} ms · etapas={tel.get('stages_ms')}")
    sys.exit(0 if tel.get("status") == "done" else 1)
