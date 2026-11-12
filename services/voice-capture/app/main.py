"""Servicio de captura de voz.

Responsabilidad: recibir audio de cualquier cliente (app móvil, web, micrófono
del robot), normalizarlo a un formato canónico (WAV mono 16 kHz cuando ffmpeg
está disponible) y publicarlo como `voice.audio.chunk`. A partir de aquí el
resto del pipeline es asíncrono y no sabe quién capturó el audio.

También acepta texto directo (`/sessions/{id}/text`), que desde la corrección de QA-06 se publica
directamente como `voice.text.transcribed` y no pasa por el STT: el modo texto sigue funcionando con
el reconocimiento caído. Permite usar el robot sin micrófono y ejecutar benchmarks sin variabilidad
del STT. El campo `text_override` de `AudioChunk` queda obsoleto (ver events.py).
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from rsp_common.events import AudioChunk, Subjects, TextTranscribed, new_id, now_ms
from rsp_common.service import Service

svc = Service("voice-capture")
app = svc.app

FFMPEG = shutil.which("ffmpeg")
TARGET_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
MAX_BYTES = int(os.getenv("AUDIO_MAX_BYTES", str(10 * 1024 * 1024)))


async def _normalize(data: bytes, suffix: str) -> tuple[bytes, str]:
    """Convierte a WAV 16 kHz mono si ffmpeg existe; si no, pasa el audio tal cual."""
    if not FFMPEG:
        return data, "audio/original"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{suffix}"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
            "-ac", "1", "-ar", str(TARGET_RATE), "-f", "wav", str(dst),
        )
        await proc.wait()
        if proc.returncode != 0 or not dst.exists():
            raise HTTPException(422, "no se pudo convertir el audio")
        return dst.read_bytes(), "audio/wav"


@app.post("/sessions/{session_id}/audio", tags=["capture"])
async def capture_audio(
    session_id: str,
    file: UploadFile = File(...),
    language_hint: Literal["es", "qu", "auto"] = Form("auto"),
    seq: int = Form(0),
    is_final: bool = Form(True),
    options: str = Form("{}", description="JSON con preferencias: {\"fingerspell\": true, \"dry_run\": false}"),
):
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "audio demasiado grande")
    with svc.timer("normalize"):
        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        data, mime = await _normalize(raw, suffix)
    event = AudioChunk(
        session_id=session_id,
        seq=seq,
        audio_b64=base64.b64encode(data).decode(),
        mime_type=mime,
        sample_rate=TARGET_RATE,
        language_hint=language_hint,
        is_final=is_final,
        trace={"captured": now_ms()},
        options=_parse_options(options),
    )
    await svc.bus.publish(Subjects.AUDIO_CHUNK, event)
    svc.track(Subjects.AUDIO_CHUNK)
    return {"session_id": session_id, "event_id": event.event_id, "bytes": len(data), "mime": mime}


def _parse_options(raw: str | dict | None) -> dict:
    import json
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) or {}
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"options no es JSON válido: {exc}") from exc


class TextInput(BaseModel):
    text: str
    # tipado: un valor fuera de es|qu|auto es 422 (antes reventaba como 500 al crear el evento)
    language_hint: Literal["es", "qu", "auto"] = "es"
    session_id: str | None = None
    options: dict = {}


@app.post("/sessions/{session_id}/text", tags=["capture"])
async def capture_text(session_id: str, body: TextInput):
    """Entrada de texto: salta el STT (útil para pruebas, benchmarks y accesibilidad).

    Publica directamente `voice.text.transcribed`, así el modo texto sigue
    funcionando aunque el servicio de reconocimiento esté caído (QA-06).
    """
    t = now_ms()
    event = TextTranscribed(
        session_id=session_id or new_id(),
        text=body.text,
        language=body.language_hint if body.language_hint in ("es", "qu") else "es",
        confidence=1.0 if body.text else 0.0,
        provider="text",
        # misma traza que con voz: captura y transcripción coinciden (no hubo reconocimiento)
        trace={"captured": t, "transcribed": t},
        options=body.options or {},
    )
    await svc.bus.publish(Subjects.TEXT_TRANSCRIBED, event)
    svc.track(Subjects.TEXT_TRANSCRIBED)
    return {"session_id": event.session_id, "event_id": event.event_id}


@app.get("/capabilities", tags=["capture"])
async def capabilities():
    return {"ffmpeg": bool(FFMPEG), "target_sample_rate": TARGET_RATE, "max_bytes": MAX_BYTES}
