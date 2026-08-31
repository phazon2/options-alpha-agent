"""Append-only decision ledger.

Every decision the agent reaches is written here before and after it acts, so
the record shows what was proposed, what the risk gate said, the exact command
sent to Alpaca, and what came back. Entries are never rewritten: a mistake is
corrected by appending, not by editing history.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(os.environ.get("LEDGER_PATH", "docs/ledger/decisions.jsonl"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DecisionLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, **fields: Any) -> str:
        """Append one entry and return its id."""
        entry_id = uuid.uuid4().hex[:12]
        entry = {
            "id": entry_id,
            "at": _now(),
            "kind": kind,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
        return entry_id

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
