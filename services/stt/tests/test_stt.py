"""STT: proveedor intercambiable (QA-03) y propagación de la traza (QA-08)."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from rsp_common.events import AudioChunk, Subjects, TextTranscribed


@pytest.fixture
def stt(monkeypatch, fake_bus):
    monkeypatch.setenv("STT_PROVIDER", "echo")
    import app.main as m
    from app.providers import build_provider
    m.provider = build_provider()
    m.svc.bus = fake_bus
    return m, fake_bus


def test_echo_provider_returns_override_and_language():
    from app.providers import EchoProvider
    t = asyncio.run(EchoProvider().transcribe("", "text/plain", "qu", "yaku"))
    assert (t.text, t.language, t.confidence, t.provider) == ("yaku", "qu", 1.0, "echo")
    empty = asyncio.run(EchoProvider().transcribe("", "audio/wav", "auto", None))
    assert empty.text == "" and empty.language == "es" and empty.confidence == 0.0


@pytest.mark.parametrize("value", ["echo", "ECHO", "desconocido", ""])
def test_build_provider_defaults_to_echo(monkeypatch, value):
    monkeypatch.setenv("STT_PROVIDER", value)
    from app.providers import build_provider
    assert build_provider().name == "echo"


def test_build_provider_selects_whisper_and_kichwa_by_config(monkeypatch):
    """Cambiar de motor es solo configuración: build_provider elige la clase."""
    from app import providers
    monkeypatch.setattr(providers, "WhisperProvider", lambda: type("W", (), {"name": "whisper"})())
    monkeypatch.setattr(providers, "KichwaLegacyProvider", lambda: type("K", (), {"name": "kichwa"})())
    monkeypatch.setenv("STT_PROVIDER", "whisper")
    assert providers.build_provider().name == "whisper"
    monkeypatch.setenv("STT_PROVIDER", "kichwa")
    assert providers.build_provider().name == "kichwa"


def test_whisper_short_circuits_text_override():
    from app.providers import WhisperProvider
    w = WhisperProvider.__new__(WhisperProvider)  # sin cargar el modelo
    t = asyncio.run(w.transcribe("", "text/plain", "es", "hola"))
    assert t.text == "hola" and t.provider == "echo"


def test_handle_chunk_publishes_transcript_extending_trace(stt):
    m, bus = stt
    chunk = AudioChunk(session_id="s1", audio_b64="", text_override="hola mamá", language_hint="es",
                       trace={"captured": 1000}, options={"fingerspell": True})
    asyncio.run(m.handle_chunk(chunk))
    (out,) = bus.events(Subjects.TEXT_TRANSCRIBED)
    assert isinstance(out, TextTranscribed)
    assert out.text == "hola mamá" and out.language == "es" and out.provider == "echo"
    assert out.session_id == "s1" and out.options == {"fingerspell": True}
    assert out.trace["captured"] == 1000 and out.trace["transcribed"] >= 1000
    assert list(out.trace) == ["captured", "transcribed"]


def test_partial_chunks_are_ignored(stt):
    m, bus = stt
    asyncio.run(m.handle_chunk(AudioChunk(session_id="s", audio_b64="", is_final=False, text_override="x")))
    assert bus.published == []


def test_provider_endpoint(stt):
    m, _ = stt
    assert TestClient(m.app).get("/provider").json() == {"provider": "echo"}


def test_kichwa_vocabulary_excludes_variations(tmp_path):
    from app.providers import kichwa_vocabulary
    d = tmp_path / "dict.json"
    d.write_text('{"basic_words": {"yaku": "agua", "wasi": "casa"}, "priority_words": {"yaku": "agua"},'
                 ' "variations": {"yacu": "agua"}}', encoding="utf-8")
    assert kichwa_vocabulary(str(d)) == "yaku wasi"
    assert kichwa_vocabulary(None) is None and kichwa_vocabulary(str(tmp_path / "no.json")) is None


def test_sign_vocabulary_biases_refreshes_and_survives_outage(monkeypatch):
    """ADR-013: palabras del vocabulario como sesgo; se refresca por TTL y un fallo no lo borra."""
    import httpx
    from app.providers import SignVocabulary
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) == 3:
            raise httpx.ConnectError("caído")
        return type("R", (), {"json": lambda self: {"items": [{"gloss": "A"}, {"gloss": "MESERO"}, {"gloss": "UN RATITO"}]}})()

    monkeypatch.setattr(httpx, "get", fake_get)
    assert SignVocabulary(None).words() == "" and not calls  # sin URL no hay sesgo
    v = SignVocabulary("http://signs/signs", ttl=1000)
    assert v.words() == "mesero un ratito"  # sin letras sueltas
    assert v.words() == "mesero un ratito" and len(calls) == 1  # dentro del TTL no se vuelve a pedir
    v.ttl = -1
    assert v.words() == "mesero un ratito" and len(calls) == 2
    assert v.words() == "mesero un ratito" and len(calls) == 3  # servicio caído: conserva lo último


def test_kichwa_provider_uses_legacy_spanish_path_for_spanish():
    """Con language_hint=es el adaptador sigue el camino de /process/es de la API actual."""
    from app.providers import KichwaLegacyProvider
    calls = []
    audio = type("A", (), {"load_audio": lambda self, p: (calls.append("load") or ("onda", 16000)),
                           "extract_audio_features": lambda self, w: calls.append("features")})()
    pipe = type("P", (), {"audio_processor": audio,
                          "_transcribe_audio": lambda self, w, language_mode: calls.append(language_mode) or " Hola. ",
                          "process_audio_file": lambda self, p: calls.append("kichwa")})()
    k = KichwaLegacyProvider.__new__(KichwaLegacyProvider)  # sin cargar el pipeline
    k._pipeline = pipe
    t = asyncio.run(k.transcribe("", "audio/wav", "es", None))
    assert calls == ["load", "features", "spanish"]
    assert (t.text, t.language, t.confidence, t.provider) == ("Hola.", "es", 1.0, "kichwa")
