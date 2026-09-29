"""Sintesis de voz (texto -> audio) para Jarvis (Fase 7).

Por que esto y no `speechSynthesis` nativa del navegador: al igual que con
el reconocimiento de voz, esa API depende de voces instaladas por el
sistema/navegador -- en Linux, Brave/Chromium reportan CERO voces
(`speechSynthesis.getVoices().length === 0`, confirmado por un usuario),
porque las voces de calidad de Chrome son remotas (dependen de un servicio
de Google no disponible ahi). Piper (proyecto Rhasspy) sintetiza
localmente en CPU con voces neuronales de buena calidad -- sin API key de
pago ni depender de ningun servicio externo, misma logica que
faster-whisper para la transcripcion.
"""

from __future__ import annotations

import io
import wave
from functools import lru_cache

from backend.app.config.settings import REPO_ROOT, Settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


class SpeechSynthesisError(RuntimeError):
    """Fallo al cargar la voz o generar el audio."""


@lru_cache(maxsize=1)
def _load_voice(voice_name: str, cache_dir: str):
    # Import perezoso (igual que faster_whisper en agent/transcription.py):
    # solo se paga el coste de importar/cargar la voz cuando de verdad se
    # usa /agent/speak, nunca al arrancar la app.
    from piper import PiperVoice
    from piper.download_voices import download_voice

    download_dir = REPO_ROOT / cache_dir
    download_dir.mkdir(parents=True, exist_ok=True)
    model_path = download_dir / f"{voice_name}.onnx"
    config_path = download_dir / f"{voice_name}.onnx.json"

    try:
        if not model_path.exists() or not config_path.exists():
            logger.info("agent.tts.download_voice: voice=%s", voice_name)
            download_voice(voice_name, download_dir)
        logger.info("agent.tts.load_voice: voice=%s", voice_name)
        return PiperVoice.load(model_path, config_path)
    except Exception as exc:  # noqa: BLE001 -- traducido a SpeechSynthesisError, nunca un 500 pelado
        raise SpeechSynthesisError(f"No se pudo cargar la voz Piper '{voice_name}': {exc}") from exc


def synthesize_speech(text: str, settings: Settings) -> bytes:
    """Sintetiza `text` a un clip de audio WAV usando una voz Piper local.
    Devuelve los bytes del WAV completo (cabecera incluida), listos para
    servir con `Content-Type: audio/wav`."""
    if not text.strip():
        raise SpeechSynthesisError("No hay texto que sintetizar.")

    voice = _load_voice(settings.piper_voice, settings.piper_voice_cache_dir)

    buffer = io.BytesIO()
    try:
        with wave.open(buffer, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
    except Exception as exc:  # noqa: BLE001 -- fallo real de inferencia
        raise SpeechSynthesisError(f"No se pudo sintetizar el audio: {exc}") from exc

    return buffer.getvalue()
