"""Generate ONE private Marble world from one local photo (plus an optional text hint) and download it.

Run inside the jobs service with a per-run credit cap, for example:

    WORLDLABS_MAX_CREDITS=300 docker compose run --rm jobs python -m wefarm.worlds.worldlabs.generate_world \
        --input-dir /data/inputs/crete-path --label crete-path-draft-1 --model marble-1.0-draft

Safety:
- The generation is labeled in the spending ledger ($RUNS_ROOT/worldlabs/ledger.jsonl). Running again with the
  same label never starts a second paid generation: an unfinished one is resumed by polling, a finished one is
  re-downloaded, and a failed or uncertain start stops with a message (no automatic retry).
- The account balance is recorded before and after the generation.

Outputs:
- ``$DATA_ROOT/marble/<marble_world_id>/``: splats (SPZ), collider mesh, panorama, thumbnail, world.json.
- ``$RUNS_ROOT/worldlabs/<label>/summary.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from wefarm.live_test_caps import cap_from_environment
from wefarm.worlds.worldlabs.api_key import load_world_labs_api_key
from wefarm.worlds.worldlabs.api_models import (
    ContentReference,
    GenerateWorldRequest,
    Operation,
    World,
    WorldPermission,
    WorldPrompt,
)
from wefarm.worlds.worldlabs.budget_guard import PER_RUN_CAP_ENVIRONMENT_VARIABLE, BudgetGuard
from wefarm.worlds.worldlabs.client import WorldLabsClient, hosts_of
from wefarm.worlds.worldlabs.ledger import SpendingLedger, default_ledger_path

logger = logging.getLogger("generate_world")

MARBLE_INPUT_LONG_SIDE_PX = 1024


def data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT") or "data")


def runs_root() -> Path:
    return Path(os.environ.get("RUNS_ROOT") or "runs")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def prepare_marble_input_image(source_image: Path, destination: Path) -> dict[str, Any]:
    """Downscale to about 1024 px on the long side (World Labs' recommended size) as a quality-92 JPEG."""
    with Image.open(source_image) as image:
        image = image.convert("RGB")
        scale = MARBLE_INPUT_LONG_SIDE_PX / max(image.size)
        if scale < 1.0:
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "JPEG", quality=92)
        return {"file": destination.name, "width_px": image.width, "height_px": image.height}


def build_generation_request(
    media_asset_id: str, *, model: str, seed: int, text_prompt: str | None, display_name: str, tags: list[str]
) -> GenerateWorldRequest:
    return GenerateWorldRequest(
        world_prompt=WorldPrompt(
            type="image",
            image_prompt=ContentReference(source="media_asset", media_asset_id=media_asset_id),
            is_pano=False,
            text_prompt=text_prompt,
        ),
        model=model,
        display_name=display_name,
        seed=seed,
        tags=tags,
        permission=WorldPermission(public=False, allow_id_access=False, allowed_readers=[], allowed_writers=[]),
    )


def file_extension_from_url(url: str, default: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix and len(suffix) <= 6 else default


def download_world_assets(client: WorldLabsClient, world: World, world_directory: Path) -> dict[str, Any]:
    urls = world.asset_urls()
    allowed_hosts = hosts_of(urls) | {client.api_host}
    default_extensions = {"thumbnail": ".webp", "panorama": ".png", "collider_mesh": ".glb"}
    downloaded: dict[str, Any] = {}
    for name, url in urls.items():
        if name in ("full_res_mesh", "hq_mesh"):
            continue  # High-quality meshes exist only after a paid export.
        extension = ".spz" if name.startswith("spz_") else file_extension_from_url(url, default_extensions.get(name, ""))
        asset = client.download(name, url, world_directory / f"{name}{extension}", allowed_hosts=allowed_hosts)
        downloaded[name] = {
            "file": asset.path.name, "size_bytes": asset.size_bytes, "sha256": asset.sha256,
            "content_type": asset.content_type, "host": urlsplit(url).hostname,
        }
        print(f"  downloaded {name}: {asset.size_bytes:,} bytes", flush=True)
    return downloaded


def run(arguments: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    started_at = datetime.now(timezone.utc)
    run_directory = runs_root() / "worldlabs" / arguments.label
    input_directory = Path(arguments.input_dir)
    source_record = json.loads((input_directory / "source.json").read_text(encoding="utf-8"))
    text_prompt = (input_directory / arguments.text_prompt_file).read_text(encoding="utf-8").strip() \
        if arguments.text_prompt_file else None

    per_run_cap = cap_from_environment(PER_RUN_CAP_ENVIRONMENT_VARIABLE, os.environ)
    ledger = SpendingLedger(default_ledger_path())
    client = WorldLabsClient(load_world_labs_api_key(), ledger=ledger, budget_guard=BudgetGuard(per_run_cap))
    summary: dict[str, Any] = {"started_at": started_at.isoformat(), "per_run_cap_credits": per_run_cap,
                               "label": arguments.label, "model": arguments.model, "seed": arguments.seed,
                               "text_prompt": text_prompt, "input": source_record, "ledger": str(ledger.path)}

    input_jpeg = input_directory / f"marble_input_{MARBLE_INPUT_LONG_SIDE_PX}.jpg"
    summary["marble_input"] = prepare_marble_input_image(input_directory / source_record["file"], input_jpeg)

    existing_spends = ledger.spends_with_label(arguments.label)
    balance_before = client.credits()
    summary["balance_before_credits"] = balance_before
    summary["project_counted_credits_before"] = ledger.total_counted_credits()
    print(f"1. Balance before: {balance_before:,.0f} credits; project ledger total "
          f"{summary['project_counted_credits_before']:,.0f}", flush=True)

    started = None
    if not existing_spends:
        print(f"2. Upload input (free) and start ONE {arguments.model} generation", flush=True)
        media_asset_id = client.upload_media_file(input_jpeg, kind="image", content_type="image/jpeg")
        request = build_generation_request(
            media_asset_id, model=arguments.model, seed=arguments.seed, text_prompt=text_prompt,
            display_name=f"wefarm {arguments.label}"[:64], tags=["wefarm", arguments.label[:32]],
        )
        write_json(run_directory / "generate_request.json", request.to_json_dict())
        started = client.generate(request, max_credits=per_run_cap, label=arguments.label)
        summary.update(spend_id=started.spend_id, operation_id=started.operation.operation_id,
                       estimated_credits=started.estimated_credits, media_asset_id=media_asset_id)
        operation_id = started.operation.operation_id
    else:
        spend = existing_spends[-1]
        print(f"2. Ledger already holds generation {spend.spend_id} ({spend.latest_event}); no new generation")
        if spend.operation_id is None:
            print("   That start failed or is uncertain and has no operation to poll. Not retrying (paid).")
            write_json(run_directory / "summary.json", {**summary, "stopped": "earlier start has no operation"})
            return 2
        operation_id = spend.operation_id
        summary.update(spend_id=spend.spend_id, operation_id=operation_id, resumed=True)

    print(f"3. Polling operation {operation_id}", flush=True)
    poll_started = datetime.now(timezone.utc)

    def report_progress(operation: Operation) -> None:
        progress = operation.progress_percentage if operation.progress_percentage is not None else operation.progress_status
        print(f"   done={operation.done} progress={progress}", flush=True)

    finished_operation: Operation | None = None
    try:
        finished_operation = client.wait_for_operation(operation_id, poll_interval_s=10.0, timeout_s=2400.0,
                                                       on_poll=report_progress)
    finally:
        balance_after = client.credits()
        summary["balance_after_credits"] = balance_after
        if started is not None:
            if finished_operation is None:
                client.record_outcome(started, None, balance_after=balance_after, failure_note="polling did not finish")
            else:
                client.record_outcome(started, finished_operation, balance_after=balance_after)
        elif existing_spends and existing_spends[-1].latest_event != "finished" and finished_operation is not None:
            spend = existing_spends[-1]
            ledger.append("finished", spend.spend_id, operation_id=operation_id,
                          world_id=(finished_operation.response or {}).get("world_id"),
                          actual_credits=finished_operation.cost.total_credits if finished_operation.cost else None,
                          balance_after=balance_after, note="recorded on resume")
    summary["generation_wait_s"] = round((datetime.now(timezone.utc) - poll_started).total_seconds(), 1)
    summary["cost"] = finished_operation.cost.model_dump() if finished_operation.cost else None
    summary["balance_difference_credits"] = balance_before - balance_after
    write_json(run_directory / "operation_finished.json", finished_operation.model_dump(mode="json"))
    print(f"4. Balance after: {balance_after:,.0f}; operation cost {summary['cost']}", flush=True)

    world_id = (finished_operation.response or {}).get("world_id")
    if not world_id:
        write_json(run_directory / "summary.json", summary)
        raise RuntimeError("finished operation has no world_id")
    world = client.get_world(world_id)
    if world.permission is not None and (world.permission.public or world.permission.allow_id_access):
        print("WARNING: the world is not private", file=sys.stderr)
    world_directory = data_root() / "marble" / world_id
    write_json(world_directory / "world.json", world.model_dump(mode="json"))
    summary.update(world_id=world_id, world_model=world.model,
                   permission=world.permission.model_dump() if world.permission else None,
                   caption=world.assets.caption if world.assets else None,
                   semantics_metadata=(world.assets.splats.semantics_metadata.model_dump()
                                       if world.assets and world.assets.splats
                                       and world.assets.splats.semantics_metadata else None))

    print("5. Downloading assets", flush=True)
    summary["downloads"] = download_world_assets(client, world, world_directory)
    summary["project_counted_credits_after"] = ledger.total_counted_credits()
    write_json(run_directory / "summary.json", summary)
    print(json.dumps({key: summary.get(key) for key in (
        "world_id", "world_model", "cost", "balance_before_credits", "balance_after_credits",
        "project_counted_credits_after", "semantics_metadata", "caption")}, indent=2, default=str))
    print(f"Done. World directory: {world_directory}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", required=True, help="directory holding source.json and the photo it names")
    parser.add_argument("--label", required=True, help="ledger label; one paid generation per label, ever")
    parser.add_argument("--model", required=True, choices=["marble-1.0-draft", "marble-1.1"])
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--text-prompt-file", help="optional text hint file, relative to --input-dir")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
