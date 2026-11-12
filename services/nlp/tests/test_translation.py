import sys, pathlib, os, importlib, asyncio
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(monkeypatch, provider="dictionary"):
    monkeypatch.setenv("TRANSLATION_PROVIDER", provider)
    monkeypatch.setenv("KICHWA_DICTIONARY_PATH", str(ROOT.parents[1] / "data" / "kichwa_dictionary.json"))
    import app.main as m
    importlib.reload(m)
    m._load_dictionary()
    return m


def test_qu_to_es_glosses(monkeypatch):
    m = load(monkeypatch)
    res = asyncio.run(m.process("yaku", "qu"))
    assert res["translated_text"] == "agua" and res["glosses"] == ["AGUA"] and res["target_language"] == "es"


def test_es_to_qu_display_translation(monkeypatch):
    m = load(monkeypatch)
    res = asyncio.run(m.process("agua", "es"))
    assert res["glosses"] == ["AGUA"] and res["translated_text"] == "yaku"


def test_detects_kichwa_when_hint_is_spanish(monkeypatch):
    m = load(monkeypatch)
    assert m.detect_language("wasi yaku", "es") == "qu"
    assert m.detect_language("hola casa", "es") == "es"
    assert m.detect_language("shuk", "unknown") == "qu"


def test_provider_none(monkeypatch):
    m = load(monkeypatch, "none")
    res = asyncio.run(m.process("agua", "es"))
    assert res["translated_text"] is None and res["glosses"] == ["AGUA"]
