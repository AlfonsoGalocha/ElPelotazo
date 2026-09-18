from __future__ import annotations

import pandas as pd

from backend.app.ingestion.football_data.history_dataset import ClubFootballMatchDataProvider

_MINIMAL_CSV = (
    "MatchDate,Division,HomeTeam,AwayTeam,FTHome,FTAway,HTHome,HTAway,"
    "HomeShots,AwayShots,HomeTarget,AwayTarget,HomeFouls,AwayFouls,"
    "HomeCorners,AwayCorners,HomeYellow,AwayYellow,HomeRed,AwayRed,"
    "HomeElo,AwayElo,Form3Home,Form3Away,Form5Home,Form5Away,"
    "OddHome,OddDraw,OddAway,MaxHome,MaxDraw,MaxAway,Over25,Under25,"
    "MaxOver25,MaxUnder25\n"
    "2024-01-01,E0,TeamA,TeamB,1,0,0,0,10,8,5,4,10,9,5,4,2,1,0,0,,,,,,,"
    "1.9,3.5,4.0,1.9,3.5,4.0,1.9,1.9,1.9,1.9\n"
)


class _FakeHttpClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0

    def get(self, url: str):
        self.calls += 1
        return self

    def raise_for_status(self) -> None:
        return None


def test_refresh_cache_forces_a_real_redownload(tmp_path, monkeypatch):
    """Bug real corregido: `force_refresh` existia como parametro pero
    nunca se invocaba en `True` desde ningun sitio, asi que una vez
    descargado el CSV la primera vez, `update` jamas volvia a comprobar
    si habia partidos nuevos jugados. `refresh_cache()` debe forzar una
    redescarga real en la SIGUIENTE llamada a `fetch_matches`."""
    cache_path = tmp_path / "matches.csv"
    fake_client = _FakeHttpClient(_MINIMAL_CSV.encode())
    provider = ClubFootballMatchDataProvider(http_client=fake_client, cache_path=cache_path)

    provider.fetch_matches("laliga", "2023/24")
    assert fake_client.calls == 1
    assert cache_path.exists()

    # Segunda llamada SIN refrescar: no debe volver a descargar (usa cache).
    provider.fetch_matches("laliga", "2023/24")
    assert fake_client.calls == 1

    # Tras refresh_cache(), la siguiente llamada SI debe volver a descargar.
    provider.refresh_cache()
    assert not cache_path.exists()
    provider.fetch_matches("laliga", "2023/24")
    assert fake_client.calls == 2


def test_refresh_cache_is_safe_to_call_before_any_download(tmp_path):
    """No debe fallar si se llama antes de que exista ningun cache."""
    cache_path = tmp_path / "matches.csv"
    provider = ClubFootballMatchDataProvider(http_client=_FakeHttpClient(_MINIMAL_CSV.encode()), cache_path=cache_path)
    provider.refresh_cache()  # no debe lanzar excepcion
    assert not cache_path.exists()
