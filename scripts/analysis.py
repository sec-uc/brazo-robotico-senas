#!/usr/bin/env python3
"""Análisis estadístico de la latencia percibida (QA-01). El plan (margen, sesión válida, pruebas)
se registró en el commit 6c48a91 el 28 de octubre, ocho minutos después del commit de la primera ronda
de resultados y conociendo esos datos.

    python scripts/analysis.py            # → results/analysis_latency.json

Plan (pre-registrado en el artículo, §Protocolo experimental):
  * Sesión válida = terminó (`done`/`completed`) Y produjo una transcripción no vacía.
    Se reporta también el análisis con todas las sesiones (sensibilidad).
  * Diseño pareado: el mismo archivo de audio pasa por ambos sistemas → Wilcoxon de
    rangos con signo sobre los pares válidos en ambos; efecto = mediana de las
    diferencias pareadas (Hodges-Lehmann de una muestra) con IC por bootstrap.
  * Equivalencia (TOST, α = 0,05): el IC del 90 % del efecto cae dentro de ±Δ,
    Δ = 200 ms (hueco típico entre turnos de conversación).
  * Mann-Whitney y r de rangos biserial como análisis no pareado complementario.
  * Añadidos tras la revisión, descriptivos: r biserial de rangos pareado con IC bootstrap y
    efecto mínimo detectable (80 % de potencia) con la dispersión robusta de las diferencias.
  * Comparaciones del plan: (a) misma arquitectura de motor (openai-whisper fp32 en ambas), que
    el plan designaba como medida del efecto de la arquitectura; (b) motor de cada sistema,
    descriptiva. Añadida después del plan (11-12 nov), cuando (a) dejó de ser equivalente con la
    segunda línea base: (c) mismo código de reconocimiento en ambas. El artículo la presenta como
    principal y la declara como análisis posterior al plan.
"""
from __future__ import annotations

import csv
import json
import statistics as st
from pathlib import Path

import numpy as np
from scipy.stats import mannwhitneyu, wilcoxon

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
DELTA_MS = 200.0
B = 10_000
rng = np.random.default_rng(20261028)


def load(name: str, ok_status: str) -> dict[str, dict]:
    rows = list(csv.DictReader(open(RES / name, encoding="utf-8")))
    out = {}
    for r in rows:  # una medición por archivo de audio (la primera)
        out.setdefault(r["file"], {"wall_ms": float(r["wall_ms"]), "ok": r["status"] == ok_status,
                                   "valid": r["status"] == ok_status and bool(r["transcript"].strip()),
                                   "exact": r.get("exact") == "True", "gloss": r.get("gloss_match") == "True"})
    return out


def hl_two_sample(x, y) -> float:
    return float(np.median(np.subtract.outer(np.asarray(x), np.asarray(y))))


def boot_ci(stat, *samples, level=0.95, paired=False) -> list[float]:
    vals = []
    n = len(samples[0])
    for _ in range(B):
        if paired:
            idx = rng.integers(0, n, n)
            vals.append(stat(*(np.asarray(s)[idx] for s in samples)))
        else:
            vals.append(stat(*(np.asarray(s)[rng.integers(0, len(s), len(s))] for s in samples)))
    a = (1 - level) / 2
    return [float(np.quantile(vals, a)), float(np.quantile(vals, 1 - a))]


def r_rb_paired(d) -> float:
    """r biserial de rangos para muestras pareadas: (T+ − T−) / (T+ + T−), sin ceros."""
    d = np.asarray(d); d = d[d != 0]
    ranks = np.argsort(np.argsort(np.abs(d))) + 1.0
    return float((ranks[d > 0].sum() - ranks[d < 0].sum()) / ranks.sum())


def compare(label: str, prop: dict, base: dict, which: str) -> dict:
    common = sorted(set(prop) & set(base))
    keep = (lambda f: prop[f]["valid"] and base[f]["valid"]) if which == "validas" else (lambda f: True)
    pairs = [(prop[f]["wall_ms"], base[f]["wall_ms"]) for f in common if keep(f)]
    p = np.array([a for a, _ in pairs]); b = np.array([c for _, c in pairs])
    d = p - b
    med_d = float(np.median(d))
    ci95 = boot_ci(lambda x: float(np.median(x)), d, paired=True)
    ci90 = boot_ci(lambda x: float(np.median(x)), d, level=0.90, paired=True)
    w = wilcoxon(p, b)
    mw = mannwhitneyu(p, b, alternative="two-sided")
    return {
        "comparacion": label, "sesiones": which, "pares": len(pairs),
        "propuesta": {"mediana": float(np.median(p)), "p95": float(np.quantile(p, .95))},
        "actual": {"mediana": float(np.median(b)), "p95": float(np.quantile(b, .95))},
        "diferencia_pareada_mediana_ms": med_d, "ic95_ms": ci95, "ic90_ms": ci90,
        "wilcoxon_p": float(w.pvalue),
        "r_rb_pareado": r_rb_paired(d), "r_rb_pareado_ic95": boot_ci(r_rb_paired, d, paired=True),
        # (z_.975 + z_.80) · σ_robusta / √n, con la eficiencia de Wilcoxon frente a t (√(π/3))
        "efecto_minimo_detectable_ms": float(2.80 * 1.4826 * np.median(np.abs(d - med_d)) / np.sqrt(len(d)) * np.sqrt(np.pi / 3)),
        "mann_whitney_p": float(mw.pvalue), "r_rb": 1 - 2 * float(mw.statistic) / (len(p) * len(b)),
        "hodges_lehmann_2m_ms": hl_two_sample(p, b),
        "tost_delta_ms": DELTA_MS, "equivalente": bool(-DELTA_MS < ci90[0] and ci90[1] < DELTA_MS),
        "propuesta_mas_rapida": bool(ci95[1] < 0),
    }


def main() -> None:
    legacy = load("baseline_legacy_whisper_base.csv", "completed")
    systems = {"mismo_codigo_de_reconocimiento (pipeline de la API actual como proveedor, nativo)": "qa01_asr_legacy_provider.csv",
               "misma_arquitectura_de_motor (openai-whisper fp32 en ambas)": "qa01_asr_whisper_openai.csv",
               "motor_de_cada_sistema (faster-whisper int8 vs openai-whisper fp32)": "qa01_asr_whisper_base.csv"}
    out = {"plan": {"delta_ms": DELTA_MS, "bootstrap": B, "seed": 20261028,
                    "sesion_valida": "estado final correcto y transcripción no vacía"},
           "validez": {"actual": {"total": len(legacy), "validas": sum(v["valid"] for v in legacy.values()),
                                  "vacias": sum(v["ok"] and not v["valid"] for v in legacy.values())}},
           "comparaciones": []}
    for label, name in systems.items():
        if not (RES / name).exists():
            print(f"(falta {name}; se omite {label})")
            continue
        prop = load(name, "done")
        out["validez"][label] = {"total": len(prop), "validas": sum(v["valid"] for v in prop.values()),
                                 "vacias": sum(v["ok"] and not v["valid"] for v in prop.values())}
        for which in ("validas", "todas"):
            c = compare(label, prop, legacy, which)
            out["comparaciones"].append(c)
            print(f"{label[:28]:28s} {which:7s} n={c['pares']:2d} Δmed={c['diferencia_pareada_mediana_ms']:7.1f} "
                  f"IC95={c['ic95_ms'][0]:7.1f}..{c['ic95_ms'][1]:7.1f} Wilcoxon p={c['wilcoxon_p']:.3f} "
                  f"equiv±200={c['equivalente']} más_rápida={c['propuesta_mas_rapida']}")
    (RES / "analysis_latency.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("→ results/analysis_latency.json")


if __name__ == "__main__":
    main()
