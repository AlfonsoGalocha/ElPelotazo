"""Tests del endpoint /agent/transcribe con Whisper MOCKEADO -- nunca se
carga un modelo real en la suite de tests. Comprueba el CABLEADO: mismo
guardian de autenticacion que /agent/chat, el audio subido llega a
`transcribe_audio`, y un TranscriptionError se traduce a 502 legible."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.config.settings import Settings, get_settings
from backend.app.main import app


def _settings_with_agent_enabled() -> Settings:
    return Settings(agent_enabled=True, agent_shared_secret="test-secret")


def test_agent_transcribe_requires_shared_secret():
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post("/agent/transcribe", files={"audio": ("clip.webm", b"fake", "audio/webm")})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_agent_transcribe_returns_503_when_disabled():
    app.dependency_overrides[get_settings] = lambda: Settings(agent_enabled=False)
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/transcribe",
            files={"audio": ("clip.webm", b"fake", "audio/webm")},
            headers={"X-Agent-Key": "whatever"},
        )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_agent_transcribe_returns_text_from_mocked_model(monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.routes.agent.transcribe_audio", lambda audio_bytes, settings, suffix=".webm": "qué partidos hay hoy"
    )
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/transcribe",
            files={"audio": ("clip.webm", b"fake-audio", "audio/webm")},
            headers={"X-Agent-Key": "test-secret"},
        )
        assert response.status_code == 200
        assert response.json() == {"text": "qué partidos hay hoy"}
    finally:
        app.dependency_overrides.clear()


def test_agent_transcribe_translates_transcription_error_to_502(monkeypatch):
    from backend.app.agent.transcription import TranscriptionError

    def _raise(*args, **kwargs):
        raise TranscriptionError("No se pudo cargar el modelo Whisper: boom")

    monkeypatch.setattr("backend.app.api.routes.agent.transcribe_audio", _raise)
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/transcribe",
            files={"audio": ("clip.webm", b"fake-audio", "audio/webm")},
            headers={"X-Agent-Key": "test-secret"},
        )
        assert response.status_code == 502
        assert "Whisper" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
