"""Tests de la transcripcion de voz (Fase 7) con faster-whisper MOCKEADO --
nunca se descarga/carga un modelo real en la suite de tests (seria lento y
dependiente de red). Lo que se comprueba es el CABLEADO: que el audio
llega al modelo, que el texto de los segmentos se concatena, y que un
fallo de carga/inferencia se traduce a TranscriptionError (nunca un 500
sin cuerpo)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.agent import transcription
from backend.app.config.settings import Settings


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeWhisperModel:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    def transcribe(self, path, language=None, vad_filter=None, initial_prompt=None):  # noqa: ANN001
        return [_FakeSegment(" Qué partidos hay hoy")], SimpleNamespace(language="es")


@pytest.fixture(autouse=True)
def _clear_model_cache():
    transcription._load_model.cache_clear()
    yield
    transcription._load_model.cache_clear()


def test_transcribe_audio_concatenates_segment_text(monkeypatch):
    monkeypatch.setattr(
        transcription, "_load_model", lambda *a, **k: _FakeWhisperModel()
    )
    settings = Settings(whisper_model_size="base", whisper_compute_type="int8")
    text = transcription.transcribe_audio(b"fake-audio-bytes", settings)
    assert text == "Qué partidos hay hoy"


def test_transcribe_audio_rejects_empty_bytes():
    settings = Settings()
    with pytest.raises(transcription.TranscriptionError):
        transcription.transcribe_audio(b"", settings)


def test_transcribe_audio_wraps_inference_failure(monkeypatch):
    class _BrokenModel:
        def transcribe(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("boom")

    monkeypatch.setattr(transcription, "_load_model", lambda *a, **k: _BrokenModel())
    settings = Settings()
    with pytest.raises(transcription.TranscriptionError):
        transcription.transcribe_audio(b"fake-audio-bytes", settings)
