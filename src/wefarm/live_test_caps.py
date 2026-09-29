"""Spending caps for live tests: tests that call paid APIs refuse to run without an explicit cap."""

from __future__ import annotations

from typing import Mapping

LIVE_MARKER_CAP_VARIABLES = {
    "live_jev": "JEV_MAX_REQUESTS",
    "live_worldlabs": "WORLDLABS_MAX_CREDITS",
}


def live_cap_problem(marker_names: set[str], environment: Mapping[str, str]) -> str | None:
    """Why a live test may not run, or None when every required cap is set to a positive integer."""
    for marker_name, cap_variable in LIVE_MARKER_CAP_VARIABLES.items():
        if marker_name not in marker_names:
            continue
        cap_text = environment.get(cap_variable, "").strip()
        if not cap_text.isdigit() or int(cap_text) <= 0:
            return f"{marker_name} tests require {cap_variable} set to a positive integer"
    return None


def cap_from_environment(cap_variable: str, environment: Mapping[str, str]) -> int:
    """The positive integer cap in `cap_variable`; raises ValueError when it is missing or not positive."""
    cap_text = environment.get(cap_variable, "").strip()
    if not cap_text.isdigit() or int(cap_text) <= 0:
        raise ValueError(f"{cap_variable} must be set to a positive integer")
    return int(cap_text)
