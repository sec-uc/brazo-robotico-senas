"""Repositorio de señas: base de datos propia del servicio (SQLite en el
prototipo; el esquema es trivialmente portable a PostgreSQL).

Sustituye al `abecedario.xlsx` empaquetado en la app Flutter: las señas se
consultan y editan por API sin recompilar ningún cliente (resuelve P3 del
diagnóstico). Cada seña guarda los 15 ángulos objetivo tal como los definió el
equipo del robot; la validación de límites físicos NO se hace aquí sino en el
servicio de actuadores, que es el único dueño del modelo del hardware.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from rsp_common.db import Database

SERVO_COUNT = 15
Pose = Annotated[list[int], Field(min_length=SERVO_COUNT, max_length=SERVO_COUNT)]


class Sign(BaseModel):
    gloss: str = Field(description="etiqueta en mayúsculas")
    angles: list[int] = Field(min_length=SERVO_COUNT, max_length=SERVO_COUNT)
    image_path: str | None = None
    hold_ms: int = 600
    source: str = "manual"
    kichwa: str | None = Field(default=None, description="traducción al kichwa para mostrar en clientes")
    motion: list[Pose] = Field(default_factory=list, description="poses siguientes (seña con movimiento)")


class SignRepository:
    def __init__(self, url: str | Path) -> None:
        """`url`: `postgresql://…` (base propia del servicio, ADR-014) o la ruta de un archivo SQLite."""
        self.db = Database(url)
        self._conn = self.db.conn
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS signs (
                    gloss TEXT PRIMARY KEY,
                    angles TEXT NOT NULL,
                    image_path TEXT,
                    hold_ms INTEGER NOT NULL DEFAULT 600,
                    source TEXT NOT NULL DEFAULT 'manual',
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    kichwa TEXT,
                    motion TEXT
                )"""
            )
        cols = self.db.columns("signs")  # bases SQLite creadas antes de los esquemas v2 y v3
        with self._conn() as c:
            if "kichwa" not in cols:
                c.execute("ALTER TABLE signs ADD COLUMN kichwa TEXT")
            if "motion" not in cols:  # señas con movimiento
                c.execute("ALTER TABLE signs ADD COLUMN motion TEXT")

    @staticmethod
    def _row(r) -> Sign:
        return Sign(gloss=r["gloss"], angles=json.loads(r["angles"]), image_path=r["image_path"],
                    hold_ms=r["hold_ms"], source=r["source"], kichwa=r["kichwa"],
                    motion=json.loads(r["motion"]) if r["motion"] else [])

    def upsert(self, sign: Sign) -> Sign:
        sign.gloss = sign.gloss.strip().upper()
        with self._conn() as c:
            c.execute(
                """INSERT INTO signs (gloss, angles, image_path, hold_ms, source, kichwa, motion)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(gloss) DO UPDATE SET angles=excluded.angles,
                     image_path=excluded.image_path, hold_ms=excluded.hold_ms,
                     source=excluded.source, kichwa=excluded.kichwa, motion=excluded.motion,
                     updated_at=CURRENT_TIMESTAMP""",
                (sign.gloss, json.dumps(sign.angles), sign.image_path, sign.hold_ms, sign.source, sign.kichwa,
                 json.dumps(sign.motion) if sign.motion else None),
            )
        return sign

    def get(self, gloss: str) -> Sign | None:
        key = gloss.strip().upper()
        with self._conn() as c:
            r = c.execute("SELECT * FROM signs WHERE gloss = ?", (key,)).fetchone()
            # vocabularios migrados antes de conservar la Ñ guardan MANANA por MAÑANA;
            # solo para palabras: la letra Ñ tiene su propia seña y no cae en la N
            if r is None and "Ñ" in key and len(key) > 1:
                r = c.execute("SELECT * FROM signs WHERE gloss = ?", (key.replace("Ñ", "N"),)).fetchone()
        return self._row(r) if r else None

    def delete(self, gloss: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM signs WHERE gloss = ?", (gloss.strip().upper(),))
        return cur.rowcount > 0

    def list(self, prefix: str | None = None, limit: int = 500) -> list[Sign]:
        with self._conn() as c:
            if prefix:
                rows = c.execute("SELECT * FROM signs WHERE gloss LIKE ? ORDER BY gloss LIMIT ?",
                                 (prefix.upper() + "%", limit)).fetchall()
            else:
                rows = c.execute("SELECT * FROM signs ORDER BY gloss LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM signs").fetchone()["n"]

    def import_json(self, path: str | Path, only_new: bool = False) -> int:
        """Importa una semilla. Con `only_new` no pisa señas editadas por el
        equipo: solo añade las que faltan y completa `kichwa` donde esté vacío."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        n = 0
        for item in data:
            sign = Sign(**item)
            existing = self.get(sign.gloss) if only_new else None
            if existing is None:
                self.upsert(sign)
                n += 1
            elif existing.kichwa is None and sign.kichwa:
                existing.kichwa = sign.kichwa
                self.upsert(existing)
        return n
