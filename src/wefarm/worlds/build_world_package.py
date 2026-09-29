"""Build a WeFarm world package from a downloaded Marble world: measure ground, scale, and path; write world.json.

Run inside the jobs service (no network, no credits):

    docker compose run --rm jobs python -m wefarm.worlds.build_world_package \
        --marble-world-id <id> --world-id crete-path --version 1 --input-dir /data/inputs/crete-path \
        --camera-height-m 1.6

Frames:
- Marble raw (OpenCV camera frame of the capture camera): +x right, +y down, +z forward, arbitrary units.
  The capture camera sits at the raw origin.
- WeFarm world: metres, z up, right-handed. Origin on the fitted ground directly below the capture camera;
  +x forward along the path (into the image); +y to the left.

world.json stores ``raw_to_world``, a row-major 4x4 matrix applied to column vectors [x, y, z, 1] of raw
Marble coordinates. The ground is fitted to the collider mesh (Marble's own ground_plane_offset was about
1.1 m wrong in an earlier project), and the scale comes from Marble's metric_scale_factor when present,
otherwise from an assumed camera height.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from wefarm.worlds.worldlabs.glb_reader import read_glb
from wefarm.worlds.worldlabs.spz_io import read_spz_data

GROUND_CELL_FRACTION_OF_CAMERA_HEIGHT = 0.1
GROUND_RADIUS_IN_CAMERA_HEIGHTS = 4.0
WALL_BAND_HEIGHTS_M = (0.5, 1.0)
PATH_SLICE_LENGTH_M = 0.25
HEIGHT_MAP_CELL_M = 0.05


def data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT") or "data")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sample_triangle_surface(vertices: np.ndarray, triangles: np.ndarray, samples_per_triangle: int = 4) -> np.ndarray:
    """Vertices plus random points on each triangle, so large flat triangles (like open ground) are covered."""
    generator = np.random.default_rng(7)
    corners = vertices[triangles]  # (T, 3, 3)
    weights = generator.dirichlet((1.0, 1.0, 1.0), size=(triangles.shape[0], samples_per_triangle))
    samples = np.einsum("tsk,tkd->tsd", weights, corners).reshape(-1, 3)
    return np.vstack([vertices, samples])


def fit_ground_plane_raw(points_raw: np.ndarray, *, first_radius_raw: float) -> dict[str, Any]:
    """Fit the ground under the camera (raw OpenCV frame, camera at the origin, y down).

    Takes the lowest surface point in each horizontal cell near the camera, fits a plane by SVD, then refits
    to the inliers. Repeats with a radius that follows the fitted camera height until it settles.
    """
    radius = first_radius_raw
    result: dict[str, Any] = {}
    for _ in range(6):
        cell = radius / GROUND_RADIUS_IN_CAMERA_HEIGHTS * GROUND_CELL_FRACTION_OF_CAMERA_HEIGHT
        horizontal = np.hypot(points_raw[:, 0], points_raw[:, 2])
        near_below = points_raw[(horizontal <= radius) & (points_raw[:, 1] > 0.0)]
        keys = np.floor(near_below[:, [0, 2]] / cell).astype(np.int64)
        order = np.lexsort((-near_below[:, 1], keys[:, 1], keys[:, 0]))
        sorted_keys = keys[order]
        first_in_cell = np.r_[True, np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1)]
        surface = near_below[order][first_in_cell]
        inliers = surface
        for _ in range(3):
            centroid = inliers.mean(axis=0)
            _, _, right_vectors = np.linalg.svd(inliers - centroid, full_matrices=False)
            normal = right_vectors[-1]
            if normal[1] > 0:
                normal = -normal  # point up, i.e. toward raw -y
            residuals = (surface - centroid) @ normal
            threshold = max(3.0 * np.median(np.abs(residuals)), 1e-4)
            inliers = surface[np.abs(residuals) <= threshold]
        camera_height_raw = float(normal @ (np.zeros(3) - centroid))
        result = {
            "up_normal_raw": normal.tolist(),
            "camera_height_raw": camera_height_raw,
            "tilt_from_raw_up_deg": float(np.degrees(np.arccos(np.clip(-normal[1], -1.0, 1.0)))),
            "cells": int(surface.shape[0]),
            "inlier_cells": int(inliers.shape[0]),
            "median_abs_residual_raw": float(np.median(np.abs((inliers - centroid) @ normal))),
            "radius_raw": float(radius),
        }
        new_radius = GROUND_RADIUS_IN_CAMERA_HEIGHTS * camera_height_raw
        if abs(new_radius - radius) < 0.01 * radius:
            break
        radius = new_radius
    return result


def rotation_raw_to_world(up_normal_raw: np.ndarray, yaw_correction_rad: float = 0.0) -> np.ndarray:
    """Rows are the world axes (forward, left, up) written in raw coordinates."""
    up = up_normal_raw / np.linalg.norm(up_normal_raw)
    camera_forward = np.array([0.0, 0.0, 1.0])
    forward = camera_forward - (camera_forward @ up) * up
    forward /= np.linalg.norm(forward)
    left = np.cross(up, forward)
    if yaw_correction_rad:
        cos_a, sin_a = math.cos(yaw_correction_rad), math.sin(yaw_correction_rad)
        forward, left = cos_a * forward + sin_a * left, -sin_a * forward + cos_a * left
    rotation = np.vstack([forward, left, up])
    assert np.linalg.det(rotation) > 0.999, "raw-to-world rotation must be proper (right-handed to right-handed)"
    return rotation


def raw_to_world_matrix(rotation: np.ndarray, scale: float, camera_height_raw: float, up_normal_raw: np.ndarray) -> np.ndarray:
    """world = scale * R (raw + camera_height_raw * up); the raw ground point under the camera maps to 0.

    Raw coordinates are OpenCV (+x right, +y down); R's rows (forward, left, up) are expressed in them, and R
    is a proper rotation because OpenCV (x right, y down, z forward) is itself right-handed.
    """
    matrix = np.eye(4)
    matrix[:3, :3] = scale * rotation
    matrix[:3, 3] = scale * rotation @ (camera_height_raw * np.asarray(up_normal_raw))
    return matrix


def apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def measure_path(world_points: np.ndarray) -> dict[str, Any]:
    """Walls = collider surface between 0.5 and 1.0 m high. For slices along +x, the nearest wall on the left
    (+y) and right (-y) of the centre line give the open width. The usable length ends where the opening
    narrows below 0.35 m or the wall data runs out."""
    low, high = WALL_BAND_HEIGHTS_M
    band = world_points[(world_points[:, 2] >= low) & (world_points[:, 2] <= high)]
    slices = []
    x = 0.0
    centre_y = 0.0
    while x < 40.0:
        in_slice = band[(band[:, 0] >= x) & (band[:, 0] < x + PATH_SLICE_LENGTH_M)]
        left = in_slice[in_slice[:, 1] > centre_y, 1]
        right = in_slice[in_slice[:, 1] <= centre_y, 1]
        if left.size < 3 or right.size < 3:
            slices.append({"x_m": round(x, 2), "left_wall_y_m": None, "right_wall_y_m": None, "width_m": None})
        else:
            left_wall = float(np.percentile(left, 5))
            right_wall = float(np.percentile(right, 95))
            slices.append({"x_m": round(x, 2), "left_wall_y_m": round(left_wall, 3),
                           "right_wall_y_m": round(right_wall, 3), "width_m": round(left_wall - right_wall, 3)})
            centre_y = 0.5 * (left_wall + right_wall)
        x += PATH_SLICE_LENGTH_M
    usable_until = 0.0
    misses = 0
    for entry in slices:
        width = entry["width_m"]
        if width is None or width < 0.35:
            misses += 1
            if misses >= 3:
                break
            continue
        misses = 0
        usable_until = entry["x_m"] + PATH_SLICE_LENGTH_M
    measured = [entry for entry in slices if entry["width_m"] is not None and entry["x_m"] < usable_until]
    widths = np.array([entry["width_m"] for entry in measured]) if measured else np.array([np.nan])
    centres = np.array([[entry["x_m"], 0.5 * (entry["left_wall_y_m"] + entry["right_wall_y_m"])] for entry in measured]) \
        if measured else np.zeros((0, 2))
    heading_deg = None
    if centres.shape[0] >= 4:
        slope = np.polyfit(centres[:, 0], centres[:, 1], 1)[0]
        heading_deg = float(np.degrees(np.arctan(slope)))
    return {
        "width_m_median": float(np.nanmedian(widths)),
        "width_m_p10": float(np.nanpercentile(widths, 10)),
        "width_m_p90": float(np.nanpercentile(widths, 90)),
        "usable_length_m": round(usable_until, 2),
        "centre_line_heading_deg_from_plus_x": heading_deg,
        "slices": slices[: int(min(len(slices), usable_until / PATH_SLICE_LENGTH_M + 8))],
    }


def height_map_image(world_points: np.ndarray, path: Path, *, x_range=(-4.0, 16.0), y_range=(-5.0, 5.0)) -> None:
    """Top-down view: +x up the image, +y to the image's left. Colour = highest collider surface per cell."""
    cell = HEIGHT_MAP_CELL_M
    width = int((y_range[1] - y_range[0]) / cell)
    height = int((x_range[1] - x_range[0]) / cell)
    heights = np.full((height, width), np.nan)
    inside = (world_points[:, 0] >= x_range[0]) & (world_points[:, 0] < x_range[1]) & \
             (world_points[:, 1] >= y_range[0]) & (world_points[:, 1] < y_range[1])
    points = world_points[inside]
    rows = (height - 1 - ((points[:, 0] - x_range[0]) / cell)).astype(int)
    columns = (width - 1 - ((points[:, 1] - y_range[0]) / cell)).astype(int)
    order = np.argsort(points[:, 2])
    heights[rows[order], columns[order]] = points[order, 2]
    normalised = np.clip(heights / 2.5, 0.0, 1.0)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    known = ~np.isnan(normalised)
    image[known, 0] = (255 * normalised[known]).astype(np.uint8)
    image[known, 1] = (255 * (1 - np.abs(normalised[known] - 0.5) * 2)).astype(np.uint8)
    image[known, 2] = (255 * (1 - normalised[known])).astype(np.uint8)
    picture = Image.fromarray(image).resize((width * 2, height * 2), Image.NEAREST)
    draw = ImageDraw.Draw(picture)
    for metre in range(int(x_range[0]), int(x_range[1]) + 1):
        row = int((height - 1 - (metre - x_range[0]) / cell) * 2)
        draw.line([(0, row), (12 if metre % 5 else 30, row)], fill=(255, 255, 255))
        if metre % 5 == 0:
            draw.text((34, row - 6), f"x={metre} m", fill=(255, 255, 255))
    origin_column = int((width - 1 - (0 - y_range[0]) / cell) * 2)
    origin_row = int((height - 1 - (0 - x_range[0]) / cell) * 2)
    draw.ellipse([origin_column - 5, origin_row - 5, origin_column + 5, origin_row + 5], outline=(255, 255, 255))
    draw.text((4, 4), "top-down collider height (blue 0 m -> red 2.5 m); +x up, +y left; circle = origin", fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    picture.save(path)


def camera_view_image(positions_raw: np.ndarray, colours: np.ndarray, opacities: np.ndarray, path: Path,
                      *, width: int = 320, height: int = 240, horizontal_fov_deg: float = 75.0) -> None:
    """Render splat centres through a pinhole camera at the raw origin (OpenCV: x right, y down). If the scene
    matches the input photo (not mirrored), the raw frame really has +x to the right."""
    visible = (positions_raw[:, 2] > 0.05) & (opacities > 0.3)
    points, point_colours = positions_raw[visible], colours[visible]
    focal = 0.5 * width / math.tan(math.radians(horizontal_fov_deg) / 2)
    u = (focal * points[:, 0] / points[:, 2] + width / 2).astype(int)
    v = (focal * points[:, 1] / points[:, 2] + height / 2).astype(int)
    on_image = (u >= 0) & (u < width) & (v >= 0) & (v < height)
    u, v, depth, point_colours = u[on_image], v[on_image], points[on_image, 2], point_colours[on_image]
    order = np.argsort(-depth)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    # Base colours come out dim; stretch contrast so the check image is readable (colours are not calibrated).
    stretched = np.clip(point_colours / max(float(np.percentile(point_colours, 99)), 1e-3), 0.0, 1.0)
    image[v[order], u[order]] = (255 * stretched[order]).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).resize((width * 2, height * 2), Image.NEAREST).save(path)


def run(arguments: argparse.Namespace) -> int:
    marble_directory = data_root() / "marble" / arguments.marble_world_id
    package_directory = data_root() / "worlds" / arguments.world_id / f"v{arguments.version}"
    marble_world = json.loads((marble_directory / "world.json").read_text(encoding="utf-8"))
    source = json.loads((Path(arguments.input_dir) / "source.json").read_text(encoding="utf-8"))
    semantics = ((marble_world.get("assets") or {}).get("splats") or {}).get("semantics_metadata") or {}

    geometry = read_glb(marble_directory / "collider_mesh.glb")
    if geometry.compressed_primitive_count:
        raise RuntimeError("collider mesh is compressed; cannot read its vertices")
    spz = read_spz_data(marble_directory / "spz_500k.spz")
    splat_positions_raw = spz.positions_raw()
    splat_opacities = spz.opacities()

    # Which frame does the collider file use? Compare its surface with the splats under both conventions.
    frame_scores = {}
    for name, signs in (("raw_opencv", (1, 1, 1)), ("raw_opengl_y_up_z_back", (1, -1, -1))):
        candidate = geometry.vertices * np.array(signs)
        sample = candidate[:: max(1, candidate.shape[0] // 20000)]
        opaque_splats = splat_positions_raw[splat_opacities > 0.5][::10]
        distances = []
        for point in sample[:: max(1, sample.shape[0] // 2000)]:
            distances.append(np.min(np.linalg.norm(opaque_splats - point, axis=1)))
        frame_scores[name] = float(np.median(distances))
    collider_frame = min(frame_scores, key=frame_scores.get)
    signs = np.array((1, 1, 1) if collider_frame == "raw_opencv" else (1, -1, -1))
    collider_raw = geometry.vertices * signs
    collider_samples_raw = sample_triangle_surface(collider_raw, geometry.triangles)
    print(f"collider frame: {collider_frame} (median splat distance by hypothesis {frame_scores})", flush=True)

    splat_ground = fit_ground_plane_raw(splat_positions_raw[splat_opacities > 0.5], first_radius_raw=4.0)
    ground = fit_ground_plane_raw(collider_samples_raw, first_radius_raw=4.0)
    print("ground from collider:", json.dumps(ground), flush=True)
    print("ground from splats:  ", json.dumps(splat_ground), flush=True)

    up_normal = np.array(ground["up_normal_raw"])
    camera_height_scale = arguments.camera_height_m / ground["camera_height_raw"]
    metric_scale_factor = semantics.get("metric_scale_factor")
    if metric_scale_factor:
        scale = float(metric_scale_factor)
        implied_camera_height = scale * ground["camera_height_raw"]
        disagreement = abs(scale - camera_height_scale) / camera_height_scale
        method = "marble_metric_scale_factor"
        uncertainty_note = (
            f"Marble metric_scale_factor {scale:.4f} m per raw unit. Cross-check: it implies a camera height of "
            f"{implied_camera_height:.2f} m above the collider-fitted ground; an assumed {arguments.camera_height_m} m "
            f"hand-held camera would give {camera_height_scale:.4f} ({100 * disagreement:.0f}% "
            f"{'disagreement, over the 15% threshold: check with a 1 m reference cube' if disagreement > 0.15 else 'difference, within 15%'})."
        )
        camera_height_used = implied_camera_height
    else:
        scale = camera_height_scale
        method = "camera_height"
        uncertainty_note = (
            f"No Marble scale metadata (draft world). Scale assumes the photo was taken hand-held at "
            f"{arguments.camera_height_m} m above the ground fitted to the collider mesh; a plausible 1.5-1.7 m range "
            f"gives about +/-6%. Not checked against a known-size object."
        )
        camera_height_used = arguments.camera_height_m

    rotation = rotation_raw_to_world(up_normal)
    matrix = raw_to_world_matrix(rotation, scale, ground["camera_height_raw"], up_normal)
    collider_world = apply(matrix, collider_samples_raw)
    path_measurement = measure_path(collider_world)
    yaw = path_measurement["centre_line_heading_deg_from_plus_x"]
    if yaw is not None and abs(yaw) > 2.0:
        # Align +x with the measured path centre line instead of the camera's own heading.
        rotation = rotation_raw_to_world(up_normal, math.radians(yaw))
        matrix = raw_to_world_matrix(rotation, scale, ground["camera_height_raw"], up_normal)
        collider_world = apply(matrix, collider_samples_raw)
        path_measurement = measure_path(collider_world)
        path_measurement["yaw_correction_applied_deg"] = yaw
    print("path:", json.dumps({key: value for key, value in path_measurement.items() if key != "slices"}), flush=True)

    # Sanity checks: camera -> (0, 0, h); a point ahead of the camera -> +x; a raw ground point -> z about 0.
    camera_world = apply(matrix, np.zeros((1, 3)))[0]
    ahead_world = apply(matrix, np.array([[0.0, 0.0, 1.0]]))[0]
    ground_band = collider_world[(np.abs(collider_world[:, 0] - 2.0) < 1.0) & (np.abs(collider_world[:, 1]) < 0.2)]
    sanity = {
        "camera_maps_to": [round(float(value), 4) for value in camera_world],
        "raw_forward_unit_maps_to": [round(float(value), 4) for value in ahead_world],
        "lowest_collider_z_on_path_x1_to_3_m": round(float(np.percentile(ground_band[:, 2], 5)), 3) if ground_band.size else None,
        "median_collider_z_on_path_x1_to_3_m": round(float(np.median(ground_band[:, 2])), 3) if ground_band.size else None,
        "splat_ground_fit_camera_height_m": round(scale * splat_ground["camera_height_raw"], 3),
    }
    print("sanity:", json.dumps(sanity), flush=True)

    # Package files.
    (package_directory / "splat").mkdir(parents=True, exist_ok=True)
    splat_files = {}
    for marble_name, package_name in (("spz_100k", "100k.spz"), ("spz_500k", "500k.spz"), ("spz_full_res", "full.spz")):
        source_file = marble_directory / f"{marble_name}.spz"
        if source_file.exists():
            shutil.copyfile(source_file, package_directory / "splat" / package_name)
            splat_files[package_name.removesuffix(".spz")] = f"splat/{package_name}"
    shutil.copyfile(marble_directory / "collider_mesh.glb", package_directory / "collider.glb")
    if (marble_directory / "panorama.png").exists():
        shutil.copyfile(marble_directory / "panorama.png", package_directory / "pano.png")
        # A small equirectangular JPEG for image-based lighting of meshes in the browser.
        (package_directory / "lighting").mkdir(parents=True, exist_ok=True)
        with Image.open(package_directory / "pano.png") as panorama:
            panorama.convert("RGB").resize((1024, 512), Image.LANCZOS).save(
                package_directory / "lighting" / "pano-1024.jpg", "JPEG", quality=90)

    height_map_image(collider_world, package_directory / "review" / "collider-height-top-down.png")
    camera_view_image(splat_positions_raw, spz.colours_rgb(), splat_opacities,
                      package_directory / "review" / "splats-from-capture-camera.png")

    world_json = {
        "world_id": arguments.world_id,
        "version": arguments.version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "marble_world_id": arguments.marble_world_id,
        "model": marble_world.get("model"),
        "input": {
            "description": source.get("description",
                                      "Single photo looking along a dirt path between cherry-tomato plants, Sitia, Crete"),
            "page_url": source["page_url"],
            "author": source["author"],
            "license": source["license"],
            "license_url": source["license_url"],
            "checked_on": source["checked_on"],
            "text_prompt_hint": arguments.text_prompt_note,
            "world_license_note": source.get("world_license_note",
                                             "Derived from a CC BY-SA 4.0 photo: share derived worlds under CC BY-SA 4.0 with credit."),
        },
        "frames": {
            "raw": "Marble export frame (OpenCV camera frame of the capture camera): +x right, +y down, +z forward; raw units; camera at the origin",
            "world": "metres, z up, right-handed; origin on the fitted ground directly below the capture camera; +x forward along the path; +y left",
        },
        "raw_to_world": [[round(float(value), 8) for value in row] for row in matrix],
        "raw_to_world_convention": "row-major 4x4, applied to column vectors [x_raw, y_raw, z_raw, 1]",
        "scale": {
            "meters_per_raw_unit": round(scale, 6),
            "method": method,
            "uncertainty_note": uncertainty_note,
            "marble_semantics_metadata": semantics or None,
        },
        "camera_height_m": round(camera_height_used, 3),
        "ground": {
            "fitted_to": "collider mesh (lowest surface per cell within about 4 camera heights, plane fit with inlier refit)",
            "tilt_from_raw_up_deg": round(ground["tilt_from_raw_up_deg"], 2),
            "median_abs_residual_m": round(scale * ground["median_abs_residual_raw"], 4),
        },
        "path": {
            "width_m": round(path_measurement["width_m_median"], 2),
            "width_m_p10_p90": [round(path_measurement["width_m_p10"], 2), round(path_measurement["width_m_p90"], 2)],
            "width_method": "measured: gap between the nearest collider surfaces left and right of the centre line, 0.5-1.0 m above ground, in 0.25 m slices along +x (median over the usable length)",
            "usable_length_m": path_measurement["usable_length_m"],
            "ground_z": 0.0,
            "yaw_correction_applied_deg": path_measurement.get("yaw_correction_applied_deg", 0.0),
        },
        "files": {"splats": splat_files, "collider": "collider.glb",
                  "pano": "pano.png" if (package_directory / "pano.png").exists() else None,
                  "review": ["review/collider-height-top-down.png", "review/splats-from-capture-camera.png"],
                  "lighting_pano": "lighting/pano-1024.jpg" if (package_directory / "lighting" / "pano-1024.jpg").exists() else None},
        "collider_file_frame": collider_frame,
        "sanity_checks": sanity,
    }
    if method == "marble_metric_scale_factor" and disagreement > 0.15:
        # Keep the camera-height alternative ready, so a person can switch after checking a reference object.
        # Written only when the two scales disagree by more than 15%, because the browser page prefers it.
        alternative_matrix = raw_to_world_matrix(rotation, camera_height_scale, ground["camera_height_raw"], up_normal)
        alternative_path = measure_path(apply(alternative_matrix, collider_samples_raw))
        world_json["scale_alternative_camera_height"] = {
            "meters_per_raw_unit": round(camera_height_scale, 6),
            "camera_height_m": arguments.camera_height_m,
            "raw_to_world": [[round(float(value), 8) for value in row] for row in alternative_matrix],
            "path_width_m": round(alternative_path["width_m_median"], 2),
            "path_usable_length_m": alternative_path["usable_length_m"],
        }
    write_json(package_directory / "world.json", world_json)
    write_json(package_directory / "review" / "measurements.json", {
        "collider_frame_scores": frame_scores, "ground_from_collider": ground, "ground_from_splats": splat_ground,
        "path": path_measurement, "sanity": sanity,
    })
    print(json.dumps({key: world_json[key] for key in ("raw_to_world", "scale", "camera_height_m", "path")}, indent=2))
    print(f"Package: {package_directory}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--marble-world-id", required=True)
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--camera-height-m", type=float, default=1.6)
    parser.add_argument("--text-prompt-note", default="prompt D (simple path scene description) sent as text_prompt with the image")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
