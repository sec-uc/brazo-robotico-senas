#!/usr/bin/env python3
"""Análisis de la replicación de QA-01 (R-31), tal como lo fija docs/plan-replica-qa01.md.

    python scripts/analysis_replica.py      # results/qa01_replica.csv → results/analysis_replica.json
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import wilcoxon

RES = Path(__file__).resolve().parents[1] / "results"
DELTA, B, MIN_VALID = 200.0, 10_000, 3


def boot(stat, x, level, rng):
    x = np.asarray(x)
    v = [stat(x[rng.integers(0, len(x), len(x))]) for _ in range(B)]
    a = (1 - level) / 2
    return [float(np.quantile(v, a)), float(np.quantile(v, 1 - a))]


def effect(rows, only_valid: bool, label: str) -> dict:
    """Mediana por archivo y sistema; efecto = mediana de las diferencias por archivo (propuesta − actual)."""
    per = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["valid"] or not only_valid:
            per[r["file"]][r["system"]].append(r["wall_ms"])
    need = MIN_VALID if only_valid else 1
    files = sorted(f for f, s in per.items() if len(s["actual"]) >= need and len(s["propuesta"]) >= need)
    d = np.array([np.median(per[f]["propuesta"]) - np.median(per[f]["actual"]) for f in files])
    rng = np.random.default_rng(0)
    ci95, ci90 = boot(np.median, d, .95, rng), boot(np.median, d, .90, rng)
    return {"analisis": label, "archivos": len(files), "efecto_mediana_ms": float(np.median(d)), "ic95_ms": ci95, "ic90_ms": ci90,
            "wilcoxon_p": float(wilcoxon(d).pvalue) if len(d) and np.any(d) else None,
            "equivalente_tost_200": bool(-DELTA < ci90[0] and ci90[1] < DELTA)}


def main() -> None:
    rows = [{**r, "wall_ms": float(r["wall_ms"]), "valid": r["valid"] == "True", "rep": int(r["rep"])}
            for r in csv.DictReader(open(RES / "qa01_replica.csv", encoding="utf-8"))]
    rng = np.random.default_rng(0)
    out = {"plan": "docs/plan-replica-qa01.md", "principal": effect(rows, True, "válidas, ≥3 por archivo y sistema"),
           "secundarios": {"todas": effect(rows, False, "todas las mediciones"),
                           "por_repeticion": [effect([r for r in rows if r["rep"] == k], False, f"repetición {k}")
                                              for k in sorted({r["rep"] for r in rows})]},
           "por_sistema": {}}
    for s in ("actual", "propuesta"):
        w = [r["wall_ms"] for r in rows if r["system"] == s and r["valid"]]
        out["por_sistema"][s] = {"mediciones": sum(r["system"] == s for r in rows), "validas": len(w),
                                 "vacias": sum(r["system"] == s and r["status"] == "done" and not r["valid"] for r in rows),
                                 "mediana_ms": float(np.median(w)), "p95_ms": float(np.quantile(w, .95)),
                                 "p95_ic95_ms": boot(lambda x: float(np.quantile(x, .95)), w, .95, rng)}
    (RES / "analysis_replica.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    p = out["principal"]
    print(f"principal: {p['archivos']} archivos, efecto {p['efecto_mediana_ms']:+.1f} ms, IC95 {p['ic95_ms'][0]:.1f}..{p['ic95_ms'][1]:.1f}, "
          f"IC90 {p['ic90_ms'][0]:.1f}..{p['ic90_ms'][1]:.1f}, equivalente={p['equivalente_tost_200']}")
    for s, v in out["por_sistema"].items():
        print(f"{s:9s} válidas {v['validas']}/{v['mediciones']} mediana {v['mediana_ms']:.0f} p95 {v['p95_ms']:.0f} ms")
    print("→ results/analysis_replica.json")


if __name__ == "__main__":
    main()
