#!/usr/bin/env python3
"""Impacto de cambio estático (QA-03, QA-04, QA-05, QA-11): ¿cuántos archivos de código
—y de cuántas unidades desplegables— contienen el conocimiento que un cambio obliga a tocar?

    python scripts/coupling_analysis.py            # → results/coupling.json

Compara la arquitectura actual (kichwa-app-api + robot_conection_app, rama vigente en
disco) con la propuesta (robot-signs-platform + rsp-mobile-client). Solo cuenta código
fuente (.py, .dart); excluye pruebas, plataformas generadas y dependencias.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # carpeta MAESTRIA
SYSTEMS = {
    "actual": {"kichwa-app-api": ROOT / "kichwa-app-api", "robot_conection_app": ROOT / "robot_conection_app" / "lib"},
    "propuesta": {"robot-signs-platform": ROOT / "robot-signs-platform", "rsp-mobile-client": ROOT / "rsp-mobile-client" / "lib"},
}
SKIP = re.compile(r"/(\.venv[^/]*|venv|node_modules|build|\.dart_tool|tests?|test_|__pycache__|scripts|results|frontend|output|docs)/|/test_[^/]+$|_test\.dart$")

# cada escenario: (descripción, regex del conocimiento que hay que tocar)
SCENARIOS = {
    "QA-03 motor de reconocimiento": r"\bwhisper\b|faster_whisper|speech_to_text|SpeechToText|distil[-_]?whisper|WhisperModel",
    # vocabulario incrustado en el código o en su formato de archivo (añadir señas obliga a tocarlo)
    "QA-04 vocabulario incrustado": r"abecedario|\.xlsx|ExcelDataset|excel_dataset|openpyxl",
    "QA-05 límites de servos": r"(min|max)_?(angle|Angle|limit|Limit)|servo_?(limits|Limits|config|Config)|SERVO_LIMITS|servoLimits",
    "Direcciones de servidor en código": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}\b",
}


COMMENT = re.compile(r"^\s*(#|//|/\*|\*)")


def code_text(p: Path) -> str:
    """Código sin líneas de comentario (los comentarios mencionan tecnologías sin acoplarse a ellas)."""
    return "\n".join(l for l in p.read_text(encoding="utf-8", errors="ignore").splitlines() if not COMMENT.match(l))


def code_files(base: Path):
    for p in base.rglob("*"):
        if p.suffix in (".py", ".dart") and p.is_file() and not SKIP.search(str(p)):
            yield p


def main() -> None:
    out: dict = {}
    for system, units in SYSTEMS.items():
        files = {u: list(code_files(b)) for u, b in units.items()}
        sizes = {str(p.relative_to(ROOT)): sum(1 for _ in p.open(encoding="utf-8", errors="ignore")) for fs in files.values() for p in fs}
        res = {"code_files": sum(len(v) for v in files.values()), "largest_module": max(sizes.items(), key=lambda kv: kv[1]),
               "modules_over_400_lines": sum(1 for v in sizes.values() if v > 400), "scenarios": {}}
        for name, rx in SCENARIOS.items():
            pat = re.compile(rx)
            hits = {u: [str(p.relative_to(ROOT)) for p in fs if pat.search(code_text(p))]
                    for u, fs in files.items()}
            res["scenarios"][name] = {"files": sum(len(v) for v in hits.values()),
                                      "deployable_units": sum(1 for v in hits.values() if v), "where": hits}
        out[system] = res
    dest = Path(__file__).resolve().parents[1] / "results" / "coupling.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    for system, res in out.items():
        print(f"\n== {system}: {res['code_files']} archivos de código, mayor {res['largest_module']}, >400 líneas: {res['modules_over_400_lines']}")
        for name, s in res["scenarios"].items():
            print(f"  {name:36s} archivos={s['files']:2d}  unidades={s['deployable_units']}")
    print(f"\n→ {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
