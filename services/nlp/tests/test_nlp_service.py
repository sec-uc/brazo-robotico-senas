"""NLP: manejador de eventos (traza QA-08), proveedores de traducción y endpoints."""
import asyncio
import importlib
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from rsp_common.events import GlossSequence, Subjects, TextTranscribed

ROOT = pathlib.Path(__file__).resolve().parents[1]
DICT = ROOT.parents[1] / "data" / "kichwa_dictionary.json"


def load(monkeypatch, fake_bus, provider="dictionary", dict_path=DICT, key=""):
    monkeypatch.setenv("TRANSLATION_PROVIDER", provider)
    monkeypatch.setenv("KICHWA_DICTIONARY_PATH", str(dict_path))
    monkeypatch.setenv("GOOGLE_TRANSLATE_API_KEY", key)
    import app.main as m
    importlib.reload(m)
    m._load_dictionary()
    m.svc.bus = fake_bus
    return m


def test_handle_text_publishes_glosses_and_extends_trace(monkeypatch, fake_bus):
    m = load(monkeypatch, fake_bus)
    ev = TextTranscribed(session_id="s1", text="la mamá toma agua", language="es",
                         trace={"captured": 10, "transcribed": 20}, options={"dry_run": True})
    asyncio.run(m.handle_text(ev))
    (out,) = fake_bus.events(Subjects.GLOSS_SEQUENCE)
    assert isinstance(out, GlossSequence)
    assert out.glosses == ["MAMA", "TOMA", "AGUA"] and out.source_text == "la mamá toma agua"
    assert out.trace["captured"] == 10 and out.trace["transcribed"] == 20 and out.trace["glossed"] >= 20
    assert out.options == {"dry_run": True} and out.target_language == "qu"


def test_partial_dictionary_translation_keeps_unknown_words(monkeypatch, fake_bus):
    m = load(monkeypatch, fake_bus)
    text, applied = m._dictionary("agua xyzzy", "es")
    assert applied and text.split()[1] == "xyzzy" and text.split()[0] == "yaku"
    assert m._dictionary("xyzzy", "es") == (None, False)
    assert m._dictionary("   ", "qu") == (None, False)


def test_google_without_key_falls_back_to_dictionary(monkeypatch, fake_bus):
    m = load(monkeypatch, fake_bus, provider="google", key="")
    assert asyncio.run(m.translate("agua", "es")) == ("yaku", True)


def test_google_error_falls_back_to_dictionary(monkeypatch, fake_bus):
    m = load(monkeypatch, fake_bus, provider="google", key="k")

    async def boom(*a):
        raise RuntimeError("cuota agotada")

    monkeypatch.setattr(m, "_google", boom)
    assert asyncio.run(m.translate("yaku", "qu")) == ("agua", True)


def test_google_success_is_used(monkeypatch, fake_bus):
    m = load(monkeypatch, fake_bus, provider="google", key="k")

    async def ok(text, src, dst):
        return f"[{dst}] {text}"

    monkeypatch.setattr(m, "_google", ok)
    assert asyncio.run(m.translate("hola", "es")) == ("[qu] hola", True)


def test_missing_or_corrupt_dictionary_degrades_gracefully(monkeypatch, fake_bus, tmp_path):
    m = load(monkeypatch, fake_bus, dict_path=tmp_path / "no.json")
    assert m._qu_es == {} and asyncio.run(m.process("agua", "es"))["glosses"] == ["AGUA"]
    bad = tmp_path / "bad.json"
    bad.write_text("{roto", encoding="utf-8")
    m = load(monkeypatch, fake_bus, dict_path=bad)
    assert m._es_qu == {}


def test_variations_do_not_override_canonical_spanish_to_kichwa(monkeypatch, fake_bus, tmp_path):
    d = tmp_path / "d.json"
    d.write_text(json.dumps({"variations": {"yaco": "agua"}, "basic_words": {"yaku": "agua"}}), encoding="utf-8")
    m = load(monkeypatch, fake_bus, dict_path=d)
    assert m._es_qu["agua"] == "yaku" and m._qu_es["yaco"] == "agua"


def test_gloss_and_dictionary_endpoints(monkeypatch, fake_bus):
    m = load(monkeypatch, fake_bus)
    c = TestClient(m.app)
    r = c.post("/gloss", json={"text": "yaku", "language": "unknown"}).json()
    assert r["provider"] == "dictionary" and r["source_language"] == "qu" and r["glosses"] == ["AGUA"]
    d = c.get("/dictionary?direction=qu-es&limit=3").json()
    assert d["direction"] == "qu-es" and len(d["items"]) == 3 and d["count"] >= 3  # repositorio público: diccionario de ejemplo (data/README.md)


def test_startup_subscribes_with_queue_group(monkeypatch, fake_bus):
    m = load(monkeypatch, fake_bus)
    asyncio.run(m.start())
    assert fake_bus.subscriptions == [Subjects.TEXT_TRANSCRIBED]


def test_dictionary_lives_in_the_service_database(monkeypatch, fake_bus, tmp_path):
    """ADR-014: el diccionario se siembra una vez desde el JSON heredado y después manda la base."""
    monkeypatch.setenv("NLP_DATABASE_URL", str(tmp_path / "nlp.db"))
    try:
        m = load(monkeypatch, fake_bus)
        from_json = (dict(m._qu_es), dict(m._es_qu))
        assert m._qu_es["yaku"] == "agua"
        with m.db.conn() as c:
            c.execute("INSERT INTO dictionary (level, kichwa, spanish) VALUES (?, ?, ?)", ("basic_words", "zzkichwa", "zzcastellano"))
        monkeypatch.setenv("KICHWA_DICTIONARY_PATH", str(tmp_path / "no-existe.json"))
        m._load_dictionary()  # ya no depende del archivo
        assert m._qu_es["zzkichwa"] == "zzcastellano" and m._es_qu["zzcastellano"] == "zzkichwa"
        assert {k: v for k, v in m._qu_es.items() if k != "zzkichwa"} == from_json[0]
    finally:
        monkeypatch.delenv("NLP_DATABASE_URL")
        load(monkeypatch, fake_bus)
