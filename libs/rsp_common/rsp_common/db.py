"""Base de datos propia de cada servicio (ADR-014).

Cada servicio con datos abre SU base con `Database(url)`: `postgresql://…` en el despliegue (un contenedor
por servicio) o la ruta de un archivo SQLite en pruebas y desarrollo sin contenedores. Ningún servicio conoce
la base de otro. El SQL se escribe una sola vez: `?` como marcador de parámetro y `{serial}` como clave
autoincremental; lo demás es SQL común a ambos motores.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class _Conn:
    def __init__(self, raw, postgres: bool) -> None:
        self._raw, self._postgres = raw, postgres

    def execute(self, sql: str, params: tuple | list = ()):
        if self._postgres:
            from psycopg.rows import dict_row
            cur = self._raw.cursor(row_factory=dict_row)
            cur.execute(sql.replace("{serial}", "BIGSERIAL PRIMARY KEY").replace("?", "%s"), params)
            return cur
        return self._raw.execute(sql.replace("{serial}", "INTEGER PRIMARY KEY AUTOINCREMENT"), params)


class Database:
    def __init__(self, url: str | Path) -> None:
        self.url = str(url)
        self.postgres = self.url.startswith(("postgresql://", "postgres://"))
        self._pg = None  # una conexión por proceso: los servicios atienden en un solo bucle de eventos
        if not self.postgres:
            Path(self.url).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def conn(self):
        """Una transacción: confirma al salir y deshace si hay error."""
        if not self.postgres:
            raw = sqlite3.connect(self.url)
            raw.row_factory = sqlite3.Row
            try:
                yield _Conn(raw, False)
                raw.commit()
            finally:
                raw.close()
            return
        import psycopg  # import perezoso: las pruebas no lo necesitan
        if self._pg is None or self._pg.closed:
            self._pg = psycopg.connect(self.url, connect_timeout=5)
        try:
            yield _Conn(self._pg, True)
            self._pg.commit()
        except Exception:
            try:
                self._pg.rollback()
            except Exception:  # conexión rota (la base se reinició): la próxima llamada reconecta
                self._pg = None
            raise

    def columns(self, table: str) -> set[str]:
        with self.conn() as c:
            if self.postgres:
                rows = c.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?", (table,))
                return {r["name"] for r in rows.fetchall()}
            return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
