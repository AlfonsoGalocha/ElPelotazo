"""Transcripcion de voz para Jarvis (Fase 7).

Por que esto y no el reconocimiento de voz nativo del navegador
(`webkitSpeechRecognition`): esa API NO es local pese a las apariencias --
manda el audio a un servidor de reconocimiento de Google usando una clave
API que Chrome trae integrada de fabrica. Brave (y otros Chromium
centrados en privacidad) la elimina a proposito, asi que falla siempre con
un error de "red" aunque el microfono en si funcione perfectamente (bug
real reportado por un usuario, confirmado en Brave con Shields
desactivado). La solucion, la MISMA que usa claude.ai: el navegador solo
graba el audio (`MediaRecorder`, funciona en cualquier navegador) y nuestro
propio backend lo transcribe -- sin API key de pago ni depender de un
servicio externo de Google.

faster-whisper (CTranslate2) se eligio sobre `openai-whisper` por ser mas
rapido en CPU con precision equivalente -- clave porque este es un
asistente de un solo usuario sin GPU dedicada.
"""

from __future__ import annotations

import tempfile
from functools import lru_cache

from backend.app.config.settings import REPO_ROOT, Settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


class TranscriptionError(RuntimeError):
    """Fallo al cargar el modelo o transcribir el audio recibido."""


# `initial_prompt` de faster-whisper: un trozo de texto que "precalienta" el
# modelo con el vocabulario esperado, sesgando la transcripcion hacia esos
# nombres en vez de la palabra generica mas probable (bug real reportado:
# "Bayern de Múnich" se transcribia como "Bayern de Monoch" con el modelo
# "base" sin pista de contexto). Whisper trunca el prompt a los ultimos
# ~224 tokens, asi que NO se vuelca aqui la lista entera de equipos
# (KNOWN_ALIASES, normalization/teams.py, ~140 nombres) -- se cortaria a
# mitad de lista de forma impredecible. Se cura a mano un subconjunto de
# nombres extranjeros con transliteracion realmente ambigua al hablarlos en
# espanhol (los nombres 100% espanholes como "Real Madrid" ya los reconoce
# bien sin pista); si un usuario reporta otro nombre mal entendido, se
# anhade aqui.
_TEAM_NAME_PROMPT = (
    "Partidos de fútbol de las 5 grandes ligas: Bayern de Múnich, Borussia Dortmund, "
    "Bayer Leverkusen, Eintracht Fráncfort, Werder Bremen, Wolfsburgo, Hoffenheim, "
    "Manchester United, Manchester City, Newcastle, Tottenham, Nottingham Forest, "
    "Wolverhampton, Brighton, Leicester, Sheffield United, West Ham, "
    "Paris Saint-Germain, Olympique de Lyon, Olympique de Marsella, Mónaco, Lens, Rennes, "
    "Inter de Milán, AC Milan, Nápoles, Juventus, Atalanta, Fiorentina, "
    "Atlético de Madrid, Athletic de Bilbao, Real Sociedad, Villarreal, Betis, Osasuna."
)


@lru_cache(maxsize=1)
def _load_model(model_size: str, compute_type: str, cache_dir: str):
    # Import perezoso (igual que anthropic/claude_agent_sdk en agent/llm.py):
    # solo se paga el coste de importar/cargar el modelo cuando de verdad se
    # usa /agent/transcribe, nunca al arrancar la app.
    from faster_whisper import WhisperModel

    download_root = REPO_ROOT / cache_dir
    download_root.mkdir(parents=True, exist_ok=True)
    logger.info(
        "agent.transcription.load_model: model_size=%s compute_type=%s", model_size, compute_type
    )
    try:
        return WhisperModel(
            model_size, device="cpu", compute_type=compute_type, download_root=str(download_root)
        )
    except Exception as exc:  # noqa: BLE001 -- traducido a TranscriptionError, nunca un 500 pelado
        raise TranscriptionError(f"No se pudo cargar el modelo Whisper: {exc}") from exc


def transcribe_audio(audio_bytes: bytes, settings: Settings, suffix: str = ".webm") -> str:
    """Transcribe un clip de audio corto (grabado con MediaRecorder en el
    navegador) a texto en espanhol. `suffix` refleja el `Content-Type` real
    del blob (MediaRecorder en Chrome/Brave/Edge produce webm/opus por
    defecto) -- faster-whisper/PyAV decodifican por extension."""
    if not audio_bytes:
        raise TranscriptionError("No se recibio ningun audio.")

    model = _load_model(
        settings.whisper_model_size, settings.whisper_compute_type, settings.whisper_model_cache_dir
    )

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        try:
            segments, _info = model.transcribe(
                tmp.name, language="es", vad_filter=True, initial_prompt=_TEAM_NAME_PROMPT
            )
            text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001 -- fallo real de decodificacion/inferencia
            raise TranscriptionError(f"No se pudo transcribir el audio: {exc}") from exc

    return text
