"""`ApiFootballClient._get` -- bug real visto en vivo (seccion "Selecciones",
2026-11): el usuario corrio `debug_api_football_national_teams.py` (~30
llamadas seguidas a `/fixtures`) y varias fallaron con
`errors.rateLimit`, pero el codigo de ANTES solo lo logueaba y devolvia
`[]` -- indistinguible de "de verdad no hay partidos programados". Ahora
se reintenta (igual que un error de red real) y, si se agotan los
reintentos, se propaga `ApiFootballRateLimitError` en vez de fingir una
respuesta vacia."""

from __future__ import annotations

import time

import pytest

from backend.app.ingestion.api_football.client import ApiFootballClient, ApiFootballRateLimitError


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _FakeHttpClient:
    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.calls = 0

    def get(self, url: str, params: dict, headers: dict) -> _FakeResponse:
        self.calls += 1
        payload = self._payloads[min(self.calls, len(self._payloads)) - 1]
        return _FakeResponse(payload)


@pytest.fixture(autouse=True)
def _enable_api_football(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_ENABLED", "true")
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    from backend.app.config.settings import get_settings

    get_settings.cache_clear()
    # El reintento real espera segundos de verdad (backoff exponencial) --
    # sin esto el test seria lento de verdad, no solo lento en apariencia.
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield
    get_settings.cache_clear()


def test_get_raises_after_exhausting_retries_on_persistent_rate_limit():
    rate_limited_payload = {"response": [], "errors": {"rateLimit": "Too many requests."}}
    http_client = _FakeHttpClient([rate_limited_payload])
    client = ApiFootballClient(http_client=http_client)

    with pytest.raises(ApiFootballRateLimitError):
        client._get("/fixtures", {"league": 32, "season": 2024})

    assert http_client.calls == 3  # stop_after_attempt(3): nunca se rinde al primer fallo


def test_get_recovers_if_a_retry_succeeds_before_exhausting_attempts():
    rate_limited_payload = {"response": [], "errors": {"rateLimit": "Too many requests."}}
    real_fixtures_payload = {"response": [{"fixture": {"id": 1}}], "errors": {}}
    http_client = _FakeHttpClient([rate_limited_payload, real_fixtures_payload])
    client = ApiFootballClient(http_client=http_client)

    result = client._get("/fixtures", {"league": 32, "season": 2024})

    assert result == [{"fixture": {"id": 1}}]
    assert http_client.calls == 2  # fallo en el primer intento, exito en el segundo


def test_get_never_raises_for_a_non_rate_limit_error_still_just_logs_and_returns_empty():
    """Conservador: solo `rateLimit` (confirmado contra una respuesta
    real) se trata como fallo de verdad -- otros tipos de error siguen
    con el comportamiento ya existente (loguear y devolver lo que haya en
    "response"), nunca se asume sin evidencia que tambien hace falta
    reintentarlos."""
    other_error_payload = {"response": [], "errors": {"plan": "Esta temporada requiere un plan de pago."}}
    http_client = _FakeHttpClient([other_error_payload])
    client = ApiFootballClient(http_client=http_client)

    result = client._get("/fixtures", {"league": 1, "season": 2010})

    assert result == []
    assert http_client.calls == 1  # nunca reintenta un error que no sea rateLimit
