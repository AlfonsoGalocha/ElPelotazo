"""Look up one finished API-Football fixture and optionally archive its corner count.

Run from repository root: python -m scripts.reconcile_corners MATCH_ID [--persist]
"""

from __future__ import annotations

import argparse

from backend.app.db.database import session_scope
from backend.app.ingestion.api_football.client import ApiFootballClient
from backend.app.services.corner_reconciliation import reconcile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("match_id", type=int)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    with session_scope() as db:
        total = reconcile(db, ApiFootballClient(), args.match_id, args.persist)
    print(f"Córners verificados: {total}" if total is not None else "Córners sin verificar")


if __name__ == "__main__":
    main()
