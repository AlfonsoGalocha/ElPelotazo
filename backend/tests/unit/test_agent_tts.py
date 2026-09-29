"""Tests de la sintesis de voz (Fase 7) con Piper MOCKEADO -- nunca se
descarga/carga una voz real en la suite de tests. Comprueba el CABLEADO:
que el texto llega a la voz, que se devuelve un WAV valido, y que un fallo
de carga/inferencia se traduce a SpeechSynthesisError (nunca un 500 sin
cuerpo)."""

from __future__ import annotations

import wave

import pytest

from backend.app.agent import tts
from backend.app.config.settings import Settings


class _FakeVoice:
    def synthesize_wav(self, text, wav_file):  # noqa: ANN001
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * 100)


@pytest.fixture(autouse=True)
def _clear_voice_cache():
    tts._load_voice.cache_clear()
    yield
    tts._load_voice.cache_clear()


def test_synthesize_speech_returns_valid_wav_bytes(monkeypatch):
    monkeypatch.setattr(tts, "_load_voice", lambda *a, **k: _FakeVoice())
    settings = Settings(piper_voice="es_ES-davefx-medium")
    wav_bytes = tts.synthesize_speech("Hola, ¿qué tal?", settings)
    assert wav_bytes[:4] == b"RIFF"

    import io

    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 22050


def test_synthesize_speech_rejects_empty_text():
    settings = Settings()
    with pytest.raises(tts.SpeechSynthesisError):
        tts.synthesize_speech("   ", settings)


def test_synthesize_speech_wraps_inference_failure(monkeypatch):
    class _BrokenVoice:
        def synthesize_wav(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("boom")

    monkeypatch.setattr(tts, "_load_voice", lambda *a, **k: _BrokenVoice())
    settings = Settings()
    with pytest.raises(tts.SpeechSynthesisError):
        tts.synthesize_speech("hola", settings)
