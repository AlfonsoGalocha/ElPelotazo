"""`ApiFootballClient._get` -- bug real investigado por el usuario tras
reportar cuotas desactualizadas: TODOS los endpoints de API-Football
devuelven un sobre `paging` (`{"current": N, "total": M}`) en su
respuesta, y su propia documentacion dice explicitamente que `/odds`
limita a 10 resultados por pagina -- antes de este cambio, `_get` solo
leia `response` de la PRIMERA pagina y nunca comprobaba `paging`,
perdiendo en silencio cualquier resultado a partir del 11 (o del
decimoprimer bookmaker/fixture, segun el endpoint)."""

from __future__ import annotations

import time

import pytest

from backend.app.ingestion.api_football.client import ApiFootballClient


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _FakePaginatedHttpClient:
    """Devuelve una respuesta distinta segun el numero de "page" pedido,
    para comprobar que `_get` pide TODAS las paginas en orden."""

    def __init__(self, payload_by_page: dict[int, dict]):
        self._payload_by_page = payload_by_page
        self.pages_requested: list[int] = []

    def get(self, url: str, params: dict, headers: dict) -> _FakeResponse:
        page = params.get("page", 1)
        self.pages_requested.append(page)
        return _FakeResponse(self._payload_by_page[page])


@pytest.fixture(autouse=True)
def _enable_api_football(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_ENABLED", "true")
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    from backend.app.config.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield
    get_settings.cache_clear()


def test_get_fetches_every_page_and_combines_their_results():
    http_client = _FakePaginatedHttpClient(
        {
            1: {
                "response": [{"fixture": {"id": 1}}],
                "errors": [],
                "paging": {"current": 1, "total": 2},
            },
            2: {
                "response": [{"fixture": {"id": 2}}],
                "errors": [],
                "paging": {"current": 2, "total": 2},
            },
        }
    )
    client = ApiFootballClient(http_client=http_client)

    result = client._get("/odds", {"fixture": 999})

    assert result == [{"fixture": {"id": 1}}, {"fixture": {"id": 2}}]
    assert http_client.pages_requested == [1, 2]


def test_get_stops_after_a_single_page_when_paging_says_so():
    http_client = _FakePaginatedHttpClient(
        {1: {"response": [{"fixture": {"id": 1}}], "errors": [], "paging": {"current": 1, "total": 1}}}
    )
    client = ApiFootballClient(http_client=http_client)

    result = client._get("/odds", {"fixture": 999})

    assert result == [{"fixture": {"id": 1}}]
    assert http_client.pages_requested == [1]


def test_get_stops_after_a_single_page_when_paging_is_missing_entirely():
    """Conservador: sin sobre `paging` en absoluto (respuesta vacia, o un
    formato que no lo incluya), nunca se asume que faltan paginas -- se
    trata como una unica pagina, igual que el comportamiento de siempre."""
    http_client = _FakePaginatedHttpClient({1: {"response": [], "errors": []}})
    client = ApiFootballClient(http_client=http_client)

    result = client._get("/odds", {"fixture": 999})

    assert result == []
    assert http_client.pages_requested == [1]


def test_get_requests_three_pages_in_order_for_a_larger_result_set():
    http_client = _FakePaginatedHttpClient(
        {
            1: {"response": [{"id": 1}], "errors": [], "paging": {"current": 1, "total": 3}},
            2: {"response": [{"id": 2}], "errors": [], "paging": {"current": 2, "total": 3}},
            3: {"response": [{"id": 3}], "errors": [], "paging": {"current": 3, "total": 3}},
        }
    )
    client = ApiFootballClient(http_client=http_client)

    result = client._get("/fixtures", {"league": 140, "season": 2026})

    assert result == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert http_client.pages_requested == [1, 2, 3]
