#!/usr/bin/env python3
"""Migra el diccionario de señas heredado (abecedario.xlsx de robot_conection_app)
al formato semilla del servicio sign-translation (data/signs_seed.json).

Reproduce la lógica de ExcelDatasetService._parseAngle: celdas con
"Min 30"/"Max 120" se toman como el número; vacías o NaN → 0; el servo 15
siempre a 90. NO recorta a los límites físicos: eso lo hace el servicio de
actuadores (única fuente de verdad del hardware).

Uso:
    python scripts/migrate_excel_signs.py ../robot_conection_app/assets/abecedario.xlsx data/signs_seed.json
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import openpyxl

ANGLE_RE = re.compile(r"^(min|max)\s*(\d+)$", re.I)


def parse_angle(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(value))
    s = str(value).strip()
    if s.lower() == "nan":
        return 0
    m = ANGLE_RE.match(s)
    if m:
        return int(m.group(2))
    try:
        return int(float(s))
    except ValueError:
        return 0


def norm_gloss(text: str) -> str:
    # quita tildes pero conserva la ñ (misma regla que nlp/glossing.strip_accents)
    text = unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFD", str(text))
                                                if unicodedata.category(c) != "Mn" or c == "\u0303"))
    return re.sub(r"\s+", " ", text).strip().upper()


def migrate(xlsx: Path, out: Path) -> list[dict]:
    ws = openpyxl.load_workbook(xlsx, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    # fila 0: título, fila 1: cabecera "Palabra, 1..15, ImagePath, Secuencia"
    header_idx = next(i for i, r in enumerate(rows) if r and str(r[0]).strip().lower() == "palabra")
    signs: dict[str, dict] = {}
    for r in rows[header_idx + 1:]:
        if not r or r[0] is None or not str(r[0]).strip():
            continue
        gloss = norm_gloss(r[0])
        angles = [parse_angle(r[i]) for i in range(1, 15)] + [90]
        image = r[16] if len(r) > 16 and r[16] else None
        entry = {"gloss": gloss, "angles": angles, "image_path": image, "hold_ms": 600, "source": "abecedario.xlsx"}
        if gloss in signs:
            print(f"  duplicado: {gloss} (se conserva la primera fila)")
            continue
        signs[gloss] = entry
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(list(signs.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    return list(signs.values())


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    result = migrate(Path(sys.argv[1]), Path(sys.argv[2]))
    letters = sum(1 for s in result if len(s["gloss"]) == 1)
    print(f"migradas {len(result)} señas ({letters} letras, {len(result) - letters} palabras) → {sys.argv[2]}")
