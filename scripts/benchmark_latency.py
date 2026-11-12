#!/usr/bin/env python3
"""Benchmark de latencia end-to-end y por etapa (Fase 5 — escenarios QA-01/QA-02).

Envía N enunciados por el gateway en modo texto (STT_PROVIDER=echo) o audio,
espera el `done` de cada uno y guarda una fila por sesión. Separa la
*latencia de software* (captura → plan aceptado) de la *latencia mecánica*
(ejecución de servos), que depende del brazo y no de la arquitectura.

    python scripts/benchmark_latency.py --n 30 --out results/bench_sim.csv
    SIM_INTER_COMMAND_MS=0 ...  (en el actuador) para medir solo la arquitectura

Reporta media, mediana, p95, desviación. Con n ≥ 30 los resultados son
utilizables para el análisis estadístico de la tesis.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_demo import GATEWAY, get_token, run  # noqa: E402

CORPUS = ["hola", "bien", "mamá", "agua", "arriba", "abajo", "mañana", "manzana", "mano", "mejor",
          "uno", "cuatro", "ocho", "un ratito", "más o menos", "mal", "miel", "moneda", "mesero", "mil"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", default="results/benchmark.csv")
    ap.add_argument("--audio-dir", help="si se indica, usa los .wav de esa carpeta en lugar de texto")
    ap.add_argument("--label", default=os.getenv("BENCH_LABEL", "prototype"))
    args = ap.parse_args()

    rows = []
    audios = sorted(Path(args.audio_dir).glob("*.wav")) if args.audio_dir else []
    for i in range(args.n):
        if audios:
            a = audios[i % len(audios)]
            res = run(None, str(a), quiet=True)
            text = a.stem
        else:
            text = CORPUS[i % len(CORPUS)]
            res = run(text, None, quiet=True)
        tel = res["telemetry"]
        st = tel.get("stages_ms", {})
        software_ms = (tel.get("accepted") or 0) - (tel.get("captured") or 0)
        row = {"i": i, "label": args.label, "text": text, "session_id": res["session_id"], "status": tel.get("status"),
               "e2e_ms": tel.get("e2e_ms"), "software_ms": software_ms, "mechanical_ms": tel.get("execution_ms"),
               "gestures": tel.get("gestures"), "unknown": tel.get("unknown"), **st}
        rows.append(row)
        print(f"{i + 1:3d}/{args.n} {text!r:16s} e2e={row['e2e_ms']} ms  software={software_ms} ms  gestos={row['gestures']}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("i", "label", "text"), k))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)

    def stats(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return "n/a"
        s = sorted(vals)
        # p95 por interpolación lineal (tipo 7, el de numpy/R): el mismo que usa scripts/experiments.py
        k = 0.95 * (len(s) - 1)
        p95 = s[int(k)] + (s[min(int(k) + 1, len(s) - 1)] - s[int(k)]) * (k - int(k))
        sd = statistics.stdev(s) if len(s) > 1 else 0
        return f"n={len(s)} media={statistics.mean(s):.1f} mediana={statistics.median(s):.1f} p95={p95:.1f} sd={sd:.1f} min={s[0]} max={s[-1]}"

    print("\n== Resumen (ms) ==")
    print("software (captura→aceptado):", stats([r["software_ms"] for r in rows]))
    print("mecánica  (ejecución brazo):", stats([r["mechanical_ms"] for r in rows]))
    print("end-to-end:                 ", stats([r["e2e_ms"] for r in rows]))
    for k in sorted(k for k in keys if "->" in k):
        print(f"  {k:24s}", stats([r.get(k) for r in rows]))
    # ponytail: sin /telemetry/summary; es una ventana móvil de todas las corridas, no de esta (R13)
    print(f"\nCSV: {args.out}")
    return 0 if all(r["status"] == "done" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
