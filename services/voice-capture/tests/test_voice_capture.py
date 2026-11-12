"""Captura de voz: texto y audio → `voice.audio.chunk` con la primera marca de la traza."""
import base64

import pytest
from fastapi.testclient import TestClient

from rsp_common.events import AudioChunk, Subjects


@pytest.fixture
def svc(monkeypatch, fake_bus):
    import app.main as m
    monkeypatch.setattr(m, "FFMPEG", None)  # sin conversión: pasa el audio tal cual
    m.svc.bus = fake_bus
    return m, TestClient(m.app), fake_bus


def test_text_input_skips_stt_and_publishes_transcribed_text(svc):
    """QA-06: el texto no pasa por el STT, así el modo texto sobrevive a su caída."""
    m, c, bus = svc
    r = c.post("/sessions/s1/text", json={"text": "hola mamá", "language_hint": "es", "options": {"dry_run": True}})
    assert r.status_code == 200 and r.json()["session_id"] == "s1"
    assert bus.events(Subjects.AUDIO_CHUNK) == []
    (ev,) = bus.events(Subjects.TEXT_TRANSCRIBED)
    assert ev.text == "hola mamá" and ev.language == "es" and ev.provider == "text"
    assert set(ev.trace) == {"captured", "transcribed"} and ev.options == {"dry_run": True}


def test_audio_upload_encodes_base64_and_parses_options(svc):
    m, c, bus = svc
    data = b"RIFF....WAVEfmt fake"
    r = c.post("/sessions/s2/audio", files={"file": ("voz.wav", data, "audio/wav")},
               data={"language_hint": "qu", "options": '{"fingerspell": false}'})
    assert r.status_code == 200 and r.json()["bytes"] == len(data)
    (ev,) = bus.events(Subjects.AUDIO_CHUNK)
    assert base64.b64decode(ev.audio_b64) == data and ev.language_hint == "qu"
    assert ev.options == {"fingerspell": False} and "captured" in ev.trace and ev.text_override is None


def test_audio_too_large_is_413(svc, monkeypatch):
    m, c, bus = svc
    monkeypatch.setattr(m, "MAX_BYTES", 4)
    r = c.post("/sessions/s3/audio", files={"file": ("a.wav", b"12345", "audio/wav")})
    assert r.status_code == 413 and bus.published == []


def test_invalid_options_json_is_422(svc):
    m, c, bus = svc
    r = c.post("/sessions/s4/audio", files={"file": ("a.wav", b"123", "audio/wav")}, data={"options": "{roto"})
    assert r.status_code == 422 and bus.published == []


def test_parse_options_variants(svc):
    m, _, _ = svc
    assert m._parse_options("") == {} and m._parse_options(None) == {}
    assert m._parse_options({"a": 1}) == {"a": 1} and m._parse_options("null") == {}


def test_capabilities(svc):
    m, c, _ = svc
    caps = c.get("/capabilities").json()
    assert caps == {"ffmpeg": False, "target_sample_rate": m.TARGET_RATE, "max_bytes": m.MAX_BYTES}


@pytest.mark.parametrize("path,kwargs", [
    ("/sessions/s5/text", {"json": {"text": "hola", "language_hint": "en"}}),
    ("/sessions/s5/audio", {"files": {"file": ("a.wav", b"123", "audio/wav")}, "data": {"language_hint": "fr"}}),
])
def test_invalid_language_hint_is_422_not_500(svc, path, kwargs):
    """Regresión: antes un idioma fuera de es|qu|auto reventaba como 500 al crear el evento."""
    m, c, bus = svc
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.post(path, **kwargs).status_code == 422 and bus.published == []
