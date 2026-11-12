"""Servicio de procesamiento de lenguaje natural.

Consume `voice.text.transcribed`, detecta/normaliza el idioma, traduce entre
kichwa y español con el proveedor configurado (TRANSLATION_PROVIDER = none |
dictionary | google), genera las glosas (siempre en español, que es el idioma
del vocabulario de señas) y publica `nlp.gloss.sequence` con el texto
traducido para que los clientes puedan mostrar ambos idiomas.

Aquí vive la lógica que en el sistema heredado estaba repartida entre la app
Flutter (Google Translate con API key embebida, `normalizarFraseQuechua`,
`_getBasicQuechuaTranslation`) y el pipeline Kichwa (diccionario por niveles).
La clave de Google ya no viaja en ningún cliente: es un secreto del servicio
(ADR-006). El diccionario es el `kichwa_dictionary.json` del proyecto heredado.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from pydantic import BaseModel

from rsp_common.db import Database
from rsp_common.events import GlossSequence, Subjects, TextTranscribed, now_ms
from rsp_common.service import Service

from .glossing import normalize, to_glosses

svc = Service("nlp")
app = svc.app

PROVIDER = os.getenv("TRANSLATION_PROVIDER", "dictionary").lower()
GOOGLE_KEY = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
DICT_PATH = Path(os.getenv("KICHWA_DICTIONARY_PATH", "/data/kichwa_dictionary.json"))
_qu_es: dict[str, str] = {}   # kichwa (normalizado) → español
_es_qu: dict[str, str] = {}   # español (normalizado) → kichwa (forma canónica)


DB_URL = os.getenv("NLP_DATABASE_URL")
db = Database(DB_URL) if DB_URL else None   # base propia del servicio (ADR-014); sin ella, el JSON


def _json_entries() -> list[tuple[str, str, str]]:
    """Aplana el diccionario heredado (niveles: basic_words, priority_words, variations, numbers,
    colors, …) en filas (nivel, kichwa, español), en el orden del archivo."""
    if not DICT_PATH.exists():
        svc.log.warning("diccionario no encontrado en %s", DICT_PATH)
        return []
    try:
        raw = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        svc.log.warning("no se pudo cargar diccionario %s: %s", DICT_PATH, exc)
        return []
    rows = []
    for level, entries in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(entries, dict):
            rows += [(level, qu, es) for qu, es in entries.items() if isinstance(es, str)]
        elif isinstance(entries, str):
            rows.append(("", level, entries))
    return rows


def _load_dictionary() -> None:
    """Carga el diccionario kichwa–español en memoria. Con base propia, la primera vez se siembra
    desde el JSON heredado y después manda la base. Para es→qu se prefiere la primera forma
    canónica que aparezca (basic/priority/numbers) sobre las variantes ortográficas."""
    global _qu_es, _es_qu
    if db is None:
        entries = _json_entries()
    else:
        with db.conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS dictionary (id {serial}, level TEXT NOT NULL, kichwa TEXT NOT NULL, spanish TEXT NOT NULL)")
            if c.execute("SELECT COUNT(*) AS n FROM dictionary").fetchone()["n"] == 0:
                for level, qu, es in _json_entries():
                    c.execute("INSERT INTO dictionary (level, kichwa, spanish) VALUES (?, ?, ?)", (level, qu, es))
            entries = [(r["level"], r["kichwa"], r["spanish"])
                       for r in c.execute("SELECT level, kichwa, spanish FROM dictionary ORDER BY id").fetchall()]
    qu_es, es_qu = {}, {}
    # primero los niveles canónicos, al final las variantes ortográficas
    for level, qu, es in sorted(entries, key=lambda e: e[0] == "variations"):
        qu_es.setdefault(normalize(qu), es)
        if level not in ("variations", ""):
            es_qu.setdefault(normalize(es), qu)
    _qu_es, _es_qu = qu_es, es_qu
    svc.log.info("diccionario kichwa: %d entradas qu→es, %d es→qu", len(_qu_es), len(_es_qu))


def detect_language(text: str, hint: str) -> str:
    """Si el STT no supo el idioma, decide por pertenencia al diccionario
    (misma heurística que `detectLanguage` de la app heredada)."""
    if hint in ("es", "qu"):
        words = normalize(text).split()
        if hint == "es" and words and all(w in _qu_es for w in words):
            return "qu"  # el usuario escribió kichwa con el selector en español
        return hint
    words = normalize(text).split()
    if words and sum(w in _qu_es for w in words) * 2 >= len(words):
        return "qu"
    return "es"


async def _google(text: str, source: str, target: str) -> str | None:
    if not GOOGLE_KEY:
        return None
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(
            "https://translation.googleapis.com/language/translate/v2",
            headers={"X-goog-api-key": GOOGLE_KEY},  # en cabecera: en la URL acabaría en los registros
            json={"q": text, "source": source, "target": target, "format": "text"},
        )
        r.raise_for_status()
        return r.json()["data"]["translations"][0]["translatedText"]


def _dictionary(text: str, source: str) -> tuple[str | None, bool]:
    table = _qu_es if source == "qu" else _es_qu
    words = normalize(text).split()
    if not words:
        return None, False
    out, hits = [], 0
    for w in words:
        t = table.get(w)
        if t:
            hits += 1
            out.append(t)
        else:
            out.append(w)
    return (" ".join(out), hits > 0) if hits else (None, False)


async def translate(text: str, source: str) -> tuple[str | None, bool]:
    """Devuelve (texto en el otro idioma, se_tradujo)."""
    target = "es" if source == "qu" else "qu"
    if PROVIDER == "google":
        try:
            t = await _google(text, source, target)
            if t:
                return t, True
        except Exception as exc:  # noqa: BLE001
            svc.log.warning("google translate falló: %s", type(exc).__name__)  # sin el texto: podría incluir la petición
    if PROVIDER in ("google", "dictionary"):
        return _dictionary(text, source)
    return None, False


@svc.on_startup
async def start():
    _load_dictionary()
    await svc.bus.subscribe(Subjects.TEXT_TRANSCRIBED, handle_text, queue="nlp")


async def process(text: str, language: str) -> dict:
    lang = detect_language(text, language)
    translated, applied = await translate(text, lang)
    # las glosas siempre en español (idioma del vocabulario de señas)
    spanish = translated if (lang == "qu" and applied and translated) else text
    glosses = to_glosses(spanish)
    return {
        "source_language": lang,
        "glosses": glosses,
        "translated_text": translated,
        "target_language": "es" if lang == "qu" else "qu",
        "translation_applied": applied,
    }


async def handle_text(event: TextTranscribed):  # type: ignore[override]
    with svc.timer("gloss"):
        res = await process(event.text, event.language)
    out = GlossSequence(
        session_id=event.session_id,
        source_text=event.text,
        trace={**event.trace, "glossed": now_ms()},
        options=event.options,
        **res,
    )
    await svc.bus.publish(Subjects.GLOSS_SEQUENCE, out)
    svc.track(Subjects.TEXT_TRANSCRIBED)
    svc.log.info("[%s] %d caracteres (%s) → %d glosas", event.session_id[:8], len(event.text), res["source_language"], len(res["glosses"]))
    svc.log.debug("[%s] %r → %s", event.session_id[:8], event.text, res["glosses"])  # el texto dicho solo en DEBUG


class GlossRequest(BaseModel):
    text: str
    language: str = "unknown"


@app.post("/gloss", tags=["nlp"])
async def gloss(req: GlossRequest):
    """Endpoint síncrono (vista previa en clientes, depuración, evaluación de reglas)."""
    return {"source_text": req.text, "provider": PROVIDER, **(await process(req.text, req.language))}


@app.get("/dictionary", tags=["nlp"])
async def dictionary(direction: str = "es-qu", limit: int = 2000):
    """Diccionario plano para clientes (pantalla de vocabulario)."""
    table = _es_qu if direction == "es-qu" else _qu_es
    items = sorted(table.items())[:limit]
    return {"direction": direction, "count": len(table), "items": [{"source": k, "target": v} for k, v in items]}
