"""Servicio Speech-to-Text.

Consume `voice.audio.chunk`, transcribe con el proveedor configurado y publica
`voice.text.transcribed`. Las réplicas comparten la cola "stt", de modo que
el trabajo pesado (modelos) se escala añadiendo instancias, sin tocar el resto
del sistema (ADR-003: escalabilidad por cola de trabajo).
"""
from __future__ import annotations

from rsp_common.events import AudioChunk, Subjects, TextTranscribed, now_ms
from rsp_common.service import Service

from .providers import build_provider

svc = Service("stt")
app = svc.app
provider = None


@svc.on_startup
async def start():
    global provider
    provider = build_provider()
    svc.log.info("proveedor STT: %s", provider.name)
    await svc.bus.subscribe(Subjects.AUDIO_CHUNK, handle_chunk, queue="stt")


async def handle_chunk(event: AudioChunk):  # type: ignore[override]
    if not event.is_final:
        return  # el prototipo transcribe por enunciado completo; streaming parcial = trabajo futuro
    with svc.timer("transcribe"):
        t = await provider.transcribe(event.audio_b64, event.mime_type, event.language_hint, event.text_override)
    out = TextTranscribed(
        session_id=event.session_id,
        text=t.text,
        language=t.language,  # type: ignore[arg-type]
        confidence=t.confidence,
        provider=t.provider,
        trace={**event.trace, "transcribed": now_ms()},
        options=event.options,
    )
    await svc.bus.publish(Subjects.TEXT_TRANSCRIBED, out)
    svc.track(Subjects.AUDIO_CHUNK)
    svc.log.info("[%s] %s → %r (%s)", event.session_id[:8], t.provider, t.text, t.language)


@app.get("/provider", tags=["stt"])
async def current_provider():
    return {"provider": provider.name if provider else None}
