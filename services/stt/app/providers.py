"""Proveedores de Speech-to-Text intercambiables (patrón Strategy).

La arquitectura de referencia exige que el motor de reconocimiento sea un
detalle de implementación: el servicio expone siempre el mismo contrato
(`voice.text.transcribed`) y el proveedor se elige por configuración
(STT_PROVIDER). Esto es lo que se mide en la evaluación de *modificabilidad*:
cambiar de motor debe tocar solo este archivo (o añadir uno nuevo).

Proveedores:
  echo     — devuelve `text_override` (pruebas/benchmarks; sin ML)
  whisper  — faster-whisper local (instalar extra `stt[whisper]`)
  kichwa   — adaptador al pipeline heredado de kichwa-app-api (Whisper +
             corrección fonética + clasificación Kichwa). Requiere montar el
             paquete `kichwa_translator` (ver Dockerfile) y modelos.
"""
from __future__ import annotations

import asyncio

import base64
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger("stt.providers")

# inferencias simultáneas por réplica; más réplicas = más capacidad (cola compartida del bus)
INFERENCE_SLOTS = asyncio.Semaphore(int(os.getenv("STT_WORKERS", "2")))


@dataclass
class Transcript:
    text: str
    language: str = "unknown"
    confidence: float = 0.0
    provider: str = "unknown"


class STTProvider(Protocol):
    name: str

    async def transcribe(self, audio_b64: str, mime_type: str, language_hint: str,
                         text_override: str | None) -> Transcript: ...


class EchoProvider:
    name = "echo"

    async def transcribe(self, audio_b64, mime_type, language_hint, text_override) -> Transcript:
        text = text_override or ""
        lang = language_hint if language_hint in ("es", "qu") else "es"
        return Transcript(text=text, language=lang, confidence=1.0 if text else 0.0, provider=self.name)


def kichwa_vocabulary(path: str | None) -> str | None:
    """Palabras kichwa del diccionario (sin variantes ortográficas), para sesgar a
    Whisper: sin esto, «yaku» sale «y aco». None si no hay diccionario."""
    if not path or not Path(path).exists():
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    words = dict.fromkeys(qu for level, entries in raw.items() if level != "variations" and isinstance(entries, dict)
                          for qu in entries)
    return " ".join(words) or None


class SignVocabulary:
    """Palabras del vocabulario de señas, para sesgar a Whisper (ADR-013). Las lee del
    servicio de traducción a señas, que es su dueño, y las refresca cada `ttl` segundos:
    una seña añadida por API entra sin redesplegar (QA-04). Si el servicio no responde
    se conserva lo último leído; sin URL no hay sesgo."""

    def __init__(self, url: str | None, ttl: float = 60.0) -> None:
        self.url, self.ttl, self._words, self._at = url, ttl, "", float("-inf")

    def words(self) -> str:
        if self.url and time.monotonic() - self._at > self.ttl:
            self._at = time.monotonic()
            try:
                import httpx
                items = httpx.get(self.url, params={"limit": 2000}, timeout=2).json()["items"]
                # las letras sueltas del alfabeto no ayudan al reconocimiento
                self._words = " ".join(i["gloss"].lower() for i in items if len(i["gloss"]) > 1)
            except Exception as e:  # el reconocimiento no depende del vocabulario (QA-06)
                log.warning("vocabulario de señas no disponible: %s", e)
        return self._words


class WhisperProvider:
    """faster-whisper (CTranslate2). Modelo y dispositivo por variables de entorno."""

    name = "whisper"

    def __init__(self) -> None:
        from faster_whisper import WhisperModel  # import perezoso: dependencia opcional

        model = os.getenv("WHISPER_MODEL", "base")
        device = os.getenv("WHISPER_DEVICE", "cpu")
        compute = os.getenv("WHISPER_COMPUTE", "int8")
        log.info("cargando faster-whisper %s en %s (%s)", model, device, compute)
        self._model = WhisperModel(model, device=device, compute_type=compute)
        # Solo en uso real (make up-voz); la evaluación no lo activa y mide Whisper sin sesgo.
        self._hotwords = kichwa_vocabulary(os.getenv("KICHWA_HOTWORDS_PATH"))
        if self._hotwords:
            log.info("vocabulario kichwa para Whisper: %d palabras", len(self._hotwords.split()))
        self._signs = SignVocabulary(os.getenv("SIGNS_VOCAB_URL") or None)
        self._beam = int(os.getenv("WHISPER_BEAM", "1"))
        # tope de salida: sin él una decodificación que se repite tarda segundos (ADR-013).
        # 32 tokens ≈ 20 palabras; subirlo si el despliegue admite enunciados más largos.
        self._max_tokens = int(os.getenv("WHISPER_MAX_TOKENS", "32"))

    async def transcribe(self, audio_b64, mime_type, language_hint, text_override) -> Transcript:
        if text_override:
            return await EchoProvider().transcribe(audio_b64, mime_type, language_hint, text_override)
        data = base64.b64decode(audio_b64)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            lang = None if language_hint == "auto" else ("es" if language_hint in ("es", "qu") else None)

            def infer():  # CPU: en un hilo, para no bloquear el bucle de eventos (ADR-009)
                hotwords = " ".join(w for w in (self._hotwords, self._signs.words()) if w) or None
                segments, info = self._model.transcribe(
                    path, language=lang, beam_size=self._beam, hotwords=hotwords,
                    max_new_tokens=self._max_tokens or None, no_repeat_ngram_size=3 if self._max_tokens else 0,
                    condition_on_previous_text=not self._max_tokens)
                return " ".join(s.text.strip() for s in segments).strip(), info

            async with INFERENCE_SLOTS:
                text, info = await asyncio.to_thread(infer)
            return Transcript(
                text=text,
                language=info.language if info.language in ("es", "qu") else "unknown",
                confidence=float(getattr(info, "language_probability", 0.0)),
                provider=self.name,
            )
        finally:
            Path(path).unlink(missing_ok=True)


class KichwaLegacyProvider:
    """Adaptador (patrón Adapter) al pipeline heredado `kichwa_translator`.

    El pipeline original hace transcripción + corrección fonética + clasificación
    Kichwa/Español + traducción por diccionario en una sola llamada. En la nueva
    arquitectura solo nos interesa la transcripción corregida; la clasificación y
    traducción pertenecen al servicio NLP. Se conserva el comportamiento
    especializado en Kichwa sin reescribirlo.
    """

    name = "kichwa"

    def __init__(self) -> None:
        from kichwa_translator.pipeline import KichwaTranslationPipeline  # type: ignore

        cfg = os.getenv("KICHWA_CONFIG_PATH")
        self._pipeline = KichwaTranslationPipeline(cfg)

    async def transcribe(self, audio_b64, mime_type, language_hint, text_override) -> Transcript:
        if text_override:
            return await EchoProvider().transcribe(audio_b64, mime_type, language_hint, text_override)
        data = base64.b64decode(audio_b64)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            if language_hint == "es":
                # el mismo camino que /process/es de la API actual (process_spanish_audio): carga,
                # características y transcripción en español, sin los pasos de kichwa. Permite comparar
                # ambas arquitecturas con el mismo código de reconocimiento (QA-01).
                def spanish() -> str:
                    waveform, _ = self._pipeline.audio_processor.load_audio(path)
                    self._pipeline.audio_processor.extract_audio_features(waveform)
                    return self._pipeline._transcribe_audio(waveform, language_mode="spanish")

                async with INFERENCE_SLOTS:
                    text = (await asyncio.to_thread(spanish) or "").strip()
                return Transcript(text=text, language="es", confidence=1.0 if text else 0.0, provider=self.name)
            async with INFERENCE_SLOTS:
                result = await asyncio.to_thread(self._pipeline.process_audio_file, path)
            text = getattr(result, "transcription", "") or ""
            kichwa_words = getattr(result, "kichwa_words", []) or []
            lang = "qu" if kichwa_words else "es"
            conf = float(getattr(result, "confidence_score", 0.0) or 0.0)
            return Transcript(text=text, language=lang, confidence=conf, provider=self.name)
        finally:
            Path(path).unlink(missing_ok=True)


class OpenAIWhisperProvider(WhisperProvider):
    """openai-whisper (PyTorch, fp32): el mismo motor que usa la arquitectura actual.

    Existe para comparar ambas arquitecturas con el mismo motor (QA-01) y como
    experimento de sustitución del motor (QA-03).
    """

    name = "whisper-openai"

    def __init__(self) -> None:
        import whisper  # import perezoso: dependencia opcional

        model = os.getenv("WHISPER_MODEL", "base")
        log.info("cargando openai-whisper %s (fp32, CPU)", model)
        self._model = whisper.load_model(model, device="cpu")

    async def transcribe(self, audio_b64, mime_type, language_hint, text_override) -> Transcript:
        if text_override:
            return await EchoProvider().transcribe(audio_b64, mime_type, language_hint, text_override)
        data = base64.b64decode(audio_b64)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            lang = "es" if language_hint in ("es", "qu") else None
            async with INFERENCE_SLOTS:
                result = await asyncio.to_thread(self._model.transcribe, path, language=lang, fp16=False)
            text = (result.get("text") or "").strip()
            return Transcript(text=text, language=result.get("language") if result.get("language") in ("es", "qu") else "unknown",
                              confidence=1.0 if text else 0.0, provider=self.name)
        finally:
            Path(path).unlink(missing_ok=True)


def build_provider() -> STTProvider:
    name = os.getenv("STT_PROVIDER", "echo").lower()
    if name == "whisper":
        return WhisperProvider()
    if name == "whisper-openai":
        return OpenAIWhisperProvider()
    if name == "kichwa":
        return KichwaLegacyProvider()
    return EchoProvider()
