"""Worst-case credit estimates for World Labs calls, used by the budget guard before anything is sent.

Prices from World Labs' pricing page (docs.worldlabs.ai/api/pricing), read 2026-09-28 and recorded in the
project's World Labs research note. 1,250 credits = $1.00. The estimate is deliberately pessimistic: when
the input might or might not be treated as a panorama, the panorama-generation step is counted.
"""

from __future__ import annotations

from wefarm.worlds.worldlabs.api_models import ExportWorldRequest, GenerateWorldRequest

CREDITS_PER_US_DOLLAR = 1250

PANORAMA_STEP_CREDITS_FROM_PANORAMA_IMAGE = 0
PANORAMA_STEP_CREDITS_FROM_TEXT_OR_IMAGE = 80
PANORAMA_STEP_CREDITS_FROM_MULTI_IMAGE_OR_VIDEO = 100

WORLD_STEP_CREDITS_BY_MODEL = {
    "marble-1.0-draft": 150,
    "marble-1.0": 1500,
    "marble-1.1": 1500,
    # Plus worlds cost 1,500 plus a variable 0-1,500 for automatic expansion; the worst case is used.
    "marble-1.1-plus": 3000,
}

HIGH_QUALITY_MESH_EXPORT_CREDITS = 3500
PLY_SPLAT_EXPORT_CREDITS = 0


def panorama_step_worst_case_credits(request: GenerateWorldRequest) -> int:
    prompt = request.world_prompt
    if prompt.type in ("multi-image", "video"):
        return PANORAMA_STEP_CREDITS_FROM_MULTI_IMAGE_OR_VIDEO
    if prompt.type == "image" and prompt.is_pano in (True, "true"):
        return PANORAMA_STEP_CREDITS_FROM_PANORAMA_IMAGE
    return PANORAMA_STEP_CREDITS_FROM_TEXT_OR_IMAGE


def worst_case_generation_credits(request: GenerateWorldRequest) -> int:
    """The most this generation could cost. Example: a draft world from an ordinary photo is 80 + 150 = 230."""
    return panorama_step_worst_case_credits(request) + WORLD_STEP_CREDITS_BY_MODEL[request.model]


def worst_case_export_credits(request: ExportWorldRequest) -> int:
    if request.asset_type == "mesh":
        return HIGH_QUALITY_MESH_EXPORT_CREDITS
    return PLY_SPLAT_EXPORT_CREDITS


def credits_to_us_dollars(credits: float) -> float:
    return credits / CREDITS_PER_US_DOLLAR
