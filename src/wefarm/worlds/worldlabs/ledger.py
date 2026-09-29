"""Persistent, append-only ledger of every paid World Labs call.

One JSON object per line at ``$RUNS_ROOT/worldlabs/ledger.jsonl`` (Git-ignored). A paid call writes a
``requested`` line *before* anything is sent, so a crash mid-call still counts its estimated cost. Later
lines for the same ``spend_id`` record the outcome. The project total counts, per spend:

| Latest state | Counted credits |
| --- | --- |
| finished (cost known) | actual credits from the operation's cost |
| start_refused (a definite 4xx refusal: nothing started) | 0 |
| anything else (requested, started, failed, ambiguous start error) | the worst-case estimate |
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

LedgerEvent = Literal["requested", "started", "start_refused", "start_uncertain", "finished", "failed"]
LEDGER_RELATIVE_PATH = Path("worldlabs") / "ledger.jsonl"
RUNS_ROOT_ENVIRONMENT_VARIABLE = "RUNS_ROOT"


def default_ledger_path(environment: dict[str, str] | None = None) -> Path:
    """$RUNS_ROOT/worldlabs/ledger.jsonl; RUNS_ROOT defaults to ./runs."""
    environment = dict(os.environ) if environment is None else environment
    return Path(environment.get(RUNS_ROOT_ENVIRONMENT_VARIABLE) or "runs") / LEDGER_RELATIVE_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LedgerFormatError(RuntimeError):
    """The ledger file has a line that is not a valid entry. The guard refuses to spend until it is fixed."""


@dataclass(frozen=True)
class SpendState:
    """Everything the ledger knows about one paid call."""

    spend_id: str
    operation_kind: str
    label: str | None
    model: str | None
    estimated_credits: float
    latest_event: str
    operation_id: str | None
    world_id: str | None
    actual_credits: float | None
    balance_before: float | None
    balance_after: float | None

    @property
    def counted_credits(self) -> float:
        if self.latest_event == "finished" and self.actual_credits is not None:
            return self.actual_credits
        if self.latest_event == "start_refused":
            return 0.0
        return self.estimated_credits


class SpendingLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: LedgerEvent, spend_id: str, **fields: Any) -> dict[str, Any]:
        entry = {"recorded_at": utc_now_iso(), "event": event, "spend_id": spend_id, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as ledger_file:
            ledger_file.write(json.dumps(entry, sort_keys=True) + "\n")
            ledger_file.flush()
            os.fsync(ledger_file.fileno())
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        parsed_entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                raise LedgerFormatError(f"{self.path} line {line_number} is not JSON") from None
            if not isinstance(entry, dict) or "event" not in entry or "spend_id" not in entry:
                raise LedgerFormatError(f"{self.path} line {line_number} lacks event or spend_id")
            parsed_entries.append(entry)
        return parsed_entries

    def spend_states(self) -> list[SpendState]:
        merged_by_spend_id: dict[str, dict[str, Any]] = {}
        for entry in self.entries():
            merged = merged_by_spend_id.setdefault(entry["spend_id"], {})
            for key, value in entry.items():
                if value is not None:
                    merged[key] = value
            merged["latest_event"] = entry["event"]
        states = []
        for spend_id, merged in merged_by_spend_id.items():
            if "estimated_credits" not in merged:
                raise LedgerFormatError(f"spend {spend_id} has no estimated_credits (missing 'requested' line)")
            states.append(
                SpendState(
                    spend_id=spend_id,
                    operation_kind=merged.get("operation_kind", "unknown"),
                    label=merged.get("label"),
                    model=merged.get("model"),
                    estimated_credits=float(merged["estimated_credits"]),
                    latest_event=merged["latest_event"],
                    operation_id=merged.get("operation_id"),
                    world_id=merged.get("world_id"),
                    actual_credits=float(merged["actual_credits"]) if "actual_credits" in merged else None,
                    balance_before=merged.get("balance_before"),
                    balance_after=merged.get("balance_after"),
                )
            )
        return states

    def total_counted_credits(self) -> float:
        """Project spending so far, counting unfinished or ambiguous calls at their worst case."""
        return sum(state.counted_credits for state in self.spend_states())

    def spends_with_label(self, label: str) -> list[SpendState]:
        return [state for state in self.spend_states() if state.label == label]
