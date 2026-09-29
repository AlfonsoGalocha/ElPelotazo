"""Tests del endpoint /agent/speak con Piper MOCKEADO -- nunca se carga
una voz real en la suite de tests. Comprueba el CABLEADO: mismo guardian
de autenticacion que /agent/chat y /agent/transcribe, el texto llega a
`synthesize_speech`, y un SpeechSynthesisError se traduce a 502 legible."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.config.settings import Settings, get_settings
from backend.app.main import app


def _settings_with_agent_enabled() -> Settings:
    return Settings(agent_enabled=True, agent_shared_secret="test-secret")


def test_agent_speak_requires_shared_secret():
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post("/agent/speak", json={"text": "hola"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_agent_speak_returns_503_when_disabled():
    app.dependency_overrides[get_settings] = lambda: Settings(agent_enabled=False)
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/speak", json={"text": "hola"}, headers={"X-Agent-Key": "whatever"}
        )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_agent_speak_returns_wav_audio_from_mocked_voice(monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.routes.agent.synthesize_speech", lambda text, settings: b"RIFF-fake-wav"
    )
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/speak", json={"text": "hola"}, headers={"X-Agent-Key": "test-secret"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.content == b"RIFF-fake-wav"
    finally:
        app.dependency_overrides.clear()


def test_agent_speak_translates_synthesis_error_to_502(monkeypatch):
    from backend.app.agent.tts import SpeechSynthesisError

    def _raise(*args, **kwargs):
        raise SpeechSynthesisError("No se pudo cargar la voz Piper: boom")

    monkeypatch.setattr("backend.app.api.routes.agent.synthesize_speech", _raise)
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/speak", json={"text": "hola"}, headers={"X-Agent-Key": "test-secret"}
        )
        assert response.status_code == 502
        assert "Piper" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
