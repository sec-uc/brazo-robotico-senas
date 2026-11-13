#!/usr/bin/env python3
"""Tipos de error de la coincidencia de glosas (QA-01 y corpus de control), sobre los CSV existentes.

Para cada audio se glosan la referencia y la transcripción con el servicio lingüístico de la plataforma,
y cada glosa se clasifica como seña del vocabulario o deletreo. Tipos (por audio, el más grave):
  coincide        — misma secuencia de glosas
  sena_erronea    — aparece una seña del vocabulario que la referencia no tiene (el robot haría otra seña)
  deletreo        — una seña de la referencia se deletrea o se pierde, sin señas nuevas
  vacio           — la transcripción está vacía
  python scripts/analysis_tipos_error.py      # requiere la plataforma levantada (GATEWAY)
"""
from __future__ import annotations
import csv, json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import httpx
import experiments as e

RES = Path(__file__).resolve().parents[1] / "results"
FUENTES = {"corpus": ["qa01_asr_whisper_base.csv"], "control": ["qa01_asr_ctrl.csv"]}

def main() -> None:
    filas, cache = [], {}
    with httpx.Client(timeout=30) as c:
        h = {"Authorization": f"Bearer {e.token(c)}"}
        vocab = {it["gloss"] for it in c.get(f"{e.GATEWAY}/v1/signs?limit=1000", headers=h).json()["items"] if len(it["gloss"]) > 1}
        def glosas(t: str) -> list[str]:
            if t not in cache:
                cache[t] = c.post(f"{e.GATEWAY}/v1/nlp/gloss", headers=h, json={"text": t, "language": "es"}).json().get("glosses") or [] if t.strip() else []
            return cache[t]
        for grupo, archivos in FUENTES.items():
            for nombre in archivos:
                for r in csv.DictReader(open(RES / nombre, encoding="utf-8")):
                    gr, gh = glosas(r["reference"]), glosas(r["transcript"])
                    if not r["transcript"].strip(): tipo = "vacio"
                    elif gr == gh: tipo = "coincide"
                    elif {g for g in gh if g in vocab} - set(gr): tipo = "sena_erronea"
                    else: tipo = "deletreo"
                    filas.append({"grupo": grupo, "conjunto": r.get("set", ""), "archivo": r["file"], "referencia": r["reference"],
                                  "transcripcion": r["transcript"], "glosas_ref": " ".join(gr), "glosas_hyp": " ".join(gh), "tipo": tipo})
    with open(RES / "qa01_tipos_error.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0])); w.writeheader(); w.writerows(filas)
    resumen = {g: dict(Counter(x["tipo"] for x in filas if x["grupo"] == g)) for g in FUENTES}
    resumen["control_por_conjunto"] = {s: dict(Counter(x["tipo"] for x in filas if x["grupo"] == "control" and x["conjunto"] == s)) for s in ("in", "oov")}
    resumen["vocabulario"] = len(vocab)
    (RES / "qa01_tipos_error.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(resumen, indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
