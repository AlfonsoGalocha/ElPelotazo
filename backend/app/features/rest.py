"""Dias de descanso desde el partido anterior (proxy de fatiga/calendario)."""

from __future__ import annotations

import pandas as pd


def compute_rest_days(team_long: pd.DataFrame) -> pd.DataFrame:
    out = team_long.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values(["team_id", "date"])
    prev_date = out.groupby("team_id")["date"].shift(1)
    out["rest_days"] = (out["date"] - prev_date).dt.days
    return out
