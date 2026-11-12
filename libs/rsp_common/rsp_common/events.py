"""Contratos de eventos del pipeline voz → seña.

Cada etapa del pipeline publica UN tipo de evento en UN asunto (subject) NATS.
El contrato es la única dependencia entre servicios: un servicio puede
reescribirse en otro lenguaje o cambiar de proveedor (Whisper → Vosk,
diccionario → modelo neuronal, USB → ROS 2) sin que los demás lo noten.

Convención de asuntos:  <dominio>.<entidad>.<hecho>
    voice.audio.chunk         captura de voz  → STT
    voice.text.transcribed    STT             → NLP
    nlp.gloss.sequence        NLP             → traducción a señas
    signs.gesture.plan        traducción      → control de actuadores
    actuator.state            actuadores      → telemetría / gateway
    telemetry.event           cualquiera      → telemetría (eventos libres)

Todo evento lleva `session_id` (correlación end-to-end) y `ts` (epoch ms del
productor), que es lo que permite medir latencia por etapa en Telemetría.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


class Subjects:
    AUDIO_CHUNK = "voice.audio.chunk"
    TEXT_TRANSCRIBED = "voice.text.transcribed"
    GLOSS_SEQUENCE = "nlp.gloss.sequence"
    GESTURE_PLAN = "signs.gesture.plan"
    ACTUATOR_STATE = "actuator.state"
    TELEMETRY = "telemetry.event"
    ALL_DOMAIN = ["voice.>", "nlp.>", "signs.>", "actuator.>", "telemetry.>"]


class BaseEvent(BaseModel):
    """Campos comunes a todos los eventos."""

    event_id: str = Field(default_factory=new_id)
    session_id: str
    ts: int = Field(default_factory=now_ms, description="epoch ms en el productor")
    producer: str = Field(default="unknown", description="nombre del servicio emisor")
    trace: dict[str, int] = Field(
        default_factory=dict,
        description="marcas de tiempo acumuladas por etapa, p.ej. {'captured': ..., 'transcribed': ...}",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="preferencias del cliente que viajan por todo el pipeline: "
                    "fingerspell (bool, deletrear glosas sin seña), dry_run (bool, no ejecutar en el brazo)",
    )


# ---------------------------------------------------------------- voz → STT
class AudioChunk(BaseEvent):
    seq: int = 0
    audio_b64: str = Field(description="audio codificado en base64")
    mime_type: str = "audio/wav"
    sample_rate: int = 16000
    language_hint: Literal["es", "qu", "auto"] = "auto"
    is_final: bool = True
    text_override: str | None = Field(
        default=None,
        description="Obsoleto: el modo texto ya no pasa por STT (se publica como voice.text.transcribed). Se conserva por compatibilidad; si viene, el proveedor echo lo devuelve tal cual",
    )


# ---------------------------------------------------------------- STT → NLP
class TextTranscribed(BaseEvent):
    text: str
    language: Literal["es", "qu", "unknown"] = "unknown"
    confidence: float = 0.0
    provider: str = "unknown"


# ---------------------------------------------------------------- NLP → señas
class GlossSequence(BaseEvent):
    source_text: str
    source_language: str = "unknown"
    glosses: list[str] = Field(description="palabras/glosas en mayúsculas, ya normalizadas")
    translation_applied: bool = False
    translated_text: str | None = Field(default=None, description="texto en el otro idioma (es↔qu) si el proveedor pudo traducir")
    target_language: Literal["es", "qu"] | None = None


# ---------------------------------------------------------------- señas → actuadores
class ServoTarget(BaseModel):
    servo_id: int = Field(ge=1, le=15)
    angle: int = Field(ge=0, le=180)


class Gesture(BaseModel):
    gloss: str
    kind: Literal["sign", "fingerspell", "unknown"] = "sign"
    targets: list[ServoTarget]
    hold_ms: int = 600
    image_path: str | None = None
    kichwa: str | None = None
    # poses que siguen a `targets` dentro de la misma seña (trazo de la J, abrir y cerrar de HOLA…);
    # vacío = seña estática
    keyframes: list[list[ServoTarget]] = Field(default_factory=list)


class GesturePlan(BaseEvent):
    gestures: list[Gesture]
    unknown_glosses: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- actuadores → telemetría
class ActuatorStatus(str, Enum):
    ACCEPTED = "accepted"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


class ActuatorState(BaseEvent):
    status: ActuatorStatus
    gesture_index: int | None = None
    gesture_gloss: str | None = None
    servo_positions: dict[int, int] = Field(default_factory=dict)
    driver: str = "simulator"
    error: str | None = None
    execution_ms: int | None = None


class TelemetryEvent(BaseEvent):
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


SUBJECT_TO_MODEL: dict[str, type[BaseEvent]] = {
    Subjects.AUDIO_CHUNK: AudioChunk,
    Subjects.TEXT_TRANSCRIBED: TextTranscribed,
    Subjects.GLOSS_SEQUENCE: GlossSequence,
    Subjects.GESTURE_PLAN: GesturePlan,
    Subjects.ACTUATOR_STATE: ActuatorState,
    Subjects.TELEMETRY: TelemetryEvent,
}
