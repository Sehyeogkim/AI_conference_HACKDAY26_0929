"""Measure a Marble scene: how big it is in meters, where its ground is, and whether its collider mesh
lines up with its splats once both are converted to the project's metric frame.

All distances are meters in the metric frame (x east, y up, z north) unless a key says otherwise.
"Camera frame" numbers use the photo's own directions: forward (where the camera looked) and right.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from wefarm.worlds.worldlabs.glb_reader import GlbGeometry
from wefarm.worlds.worldlabs.scene_frame import MarbleToMetricTransform
from wefarm.worlds.worldlabs.splat_readers import SplatCloud

OPAQUE_SPLAT_THRESHOLD = 0.5
GROUND_BAND_M = (-2.0, 2.0)
ALIGNMENT_CELL_SIZE_M = 1.0
ALIGNMENT_RADIUS_M = 40.0
MINIMUM_SPLATS_PER_CELL = 5
LOCAL_GROUND_RADIUS_M = 10.0


def _round(value: float, digits: int = 3) -> float:
    return float(round(float(value), digits))


def _axis_summary(values: np.ndarray) -> dict[str, float]:
    if values.size == 0:
        return {}
    percentiles = np.percentile(values, [1, 5, 50, 95, 99])
    return {
        "min": _round(values.min()), "p01": _round(percentiles[0]), "p05": _round(percentiles[1]),
        "median": _round(percentiles[2]), "p95": _round(percentiles[3]), "p99": _round(percentiles[4]),
        "max": _round(values.max()),
    }


def _extent(values: np.ndarray, low: float, high: float) -> float:
    if values.size == 0:
        return 0.0
    lower, upper = np.percentile(values, [low, high])
    return _round(upper - lower)


def camera_frame_coordinates(transform: MarbleToMetricTransform, raw_points: np.ndarray) -> np.ndarray:
    """(right, up, forward) in meters: the photo's own directions, ground at up = 0."""
    metric_opencv = transform.raw_to_metric_opencv(raw_points)
    return np.stack([metric_opencv[:, 0], -metric_opencv[:, 1], metric_opencv[:, 2]], axis=1)


def measure_splats(cloud: SplatCloud, transform: MarbleToMetricTransform) -> dict[str, Any]:
    metric = transform.apply(cloud.positions)
    camera = camera_frame_coordinates(transform, cloud.positions)
    opaque = cloud.opacities >= OPAQUE_SPLAT_THRESHOLD
    heights = metric[:, 1]
    near_ground = opaque & (heights >= GROUND_BAND_M[0]) & (heights <= GROUND_BAND_M[1])
    horizontal_distance = np.hypot(metric[:, 0], metric[:, 2])
    metric_sizes = transform.metric_size(cloud.largest_scale)
    return {
        "splat_count": cloud.count,
        "opaque_splat_count": int(opaque.sum()),
        "source_format": cloud.source_format,
        "format_version": cloud.format_version,
        "raw_bounds": {
            "min": [_round(value, 4) for value in cloud.positions.min(axis=0)],
            "max": [_round(value, 4) for value in cloud.positions.max(axis=0)],
        },
        "metric_axes_all_splats": {
            "x_east": _axis_summary(metric[:, 0]),
            "y_up": _axis_summary(metric[:, 1]),
            "z_north": _axis_summary(metric[:, 2]),
        },
        "horizontal_distance_from_camera_m": _axis_summary(horizontal_distance[opaque]),
        "camera_frame_extent_opaque_m": {
            "forward_p05_to_p95": _extent(camera[opaque, 2], 5, 95),
            "forward_p01_to_p99": _extent(camera[opaque, 2], 1, 99),
            "right_p05_to_p95": _extent(camera[opaque, 0], 5, 95),
            "right_p01_to_p99": _extent(camera[opaque, 0], 1, 99),
            "up_p05_to_p95": _extent(camera[opaque, 1], 5, 95),
            "forward_axis": _axis_summary(camera[opaque, 2]),
            "right_axis": _axis_summary(camera[opaque, 0]),
        },
        "ground_band_footprint_m": {
            "description": f"opaque splats within {GROUND_BAND_M} m of the ground plane",
            "count": int(near_ground.sum()),
            "east_p05_to_p95": _extent(metric[near_ground, 0], 5, 95),
            "north_p05_to_p95": _extent(metric[near_ground, 2], 5, 95),
            "forward_p05_to_p95": _extent(camera[near_ground, 2], 5, 95),
            "right_p05_to_p95": _extent(camera[near_ground, 0], 5, 95),
            "forward_axis": _axis_summary(camera[near_ground, 2]),
            "right_axis": _axis_summary(camera[near_ground, 0]),
            "horizontal_distance": _axis_summary(horizontal_distance[near_ground]),
        },
        "splat_size_m": _axis_summary(metric_sizes[opaque]),
    }


def _triangle_normals_and_areas(vertices: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    first, second, third = (vertices[triangles[:, corner]] for corner in range(3))
    cross = np.cross(second - first, third - first)
    doubled_area = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(doubled_area, 1e-12)[:, np.newaxis]
    return normals, doubled_area / 2.0


def fit_plane(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares plane through points: (centroid, unit normal pointing to +y)."""
    centroid = points.mean(axis=0)
    _, _, right_singular_vectors = np.linalg.svd(points - centroid, full_matrices=False)
    normal = right_singular_vectors[-1]
    if normal[1] < 0:
        normal = -normal
    return centroid, normal


def measure_collider(geometry: GlbGeometry, metric_vertices: np.ndarray) -> dict[str, Any]:
    """Size, up axis, and ground plane of a collider mesh whose vertices are already in the metric frame."""
    result: dict[str, Any] = {
        "triangle_count": geometry.triangle_count,
        "vertex_count": geometry.vertex_count,
        "compressed_primitive_count": geometry.compressed_primitive_count,
        "extensions_used": geometry.extensions_used,
        "generator": geometry.generator,
        "file_frame_bounds": {
            "min": [_round(value, 4) for value in geometry.bounds_min],
            "max": [_round(value, 4) for value in geometry.bounds_max],
        },
    }
    if metric_vertices.shape[0] == 0:
        result["note"] = "vertex data compressed; only bounds are available"
        return result
    result["metric_bounds"] = {
        "min": [_round(value) for value in metric_vertices.min(axis=0)],
        "max": [_round(value) for value in metric_vertices.max(axis=0)],
    }
    result["metric_extent_m"] = [_round(value) for value in metric_vertices.max(axis=0) - metric_vertices.min(axis=0)]
    result["height_m"] = _axis_summary(metric_vertices[:, 1])
    normals, areas = _triangle_normals_and_areas(metric_vertices, geometry.triangles)
    total_area = float(areas.sum()) or 1.0
    alignment = np.abs(normals)
    result["area_fraction_facing"] = {
        "up_or_down_within_30deg": _round(areas[alignment[:, 1] >= np.cos(np.radians(30))].sum() / total_area),
        "east_or_west_within_30deg": _round(areas[alignment[:, 0] >= np.cos(np.radians(30))].sum() / total_area),
        "north_or_south_within_30deg": _round(areas[alignment[:, 2] >= np.cos(np.radians(30))].sum() / total_area),
    }
    result["area_weighted_mean_normal"] = [_round(value) for value in (normals * areas[:, np.newaxis]).sum(axis=0) / total_area]
    result["surface_area_m2"] = _round(total_area, 1)
    horizontal_distance = np.hypot(metric_vertices[:, 0], metric_vertices[:, 2])
    local = metric_vertices[horizontal_distance <= LOCAL_GROUND_RADIUS_M]
    if local.shape[0] >= 3:
        centroid, normal = fit_plane(local)
        tilt_deg = float(np.degrees(np.arccos(np.clip(normal[1], -1.0, 1.0))))
        height_at_origin = centroid[1] - (normal[0] * (0 - centroid[0]) + normal[2] * (0 - centroid[2])) / normal[1]
        result["local_ground_plane"] = {
            "radius_m": LOCAL_GROUND_RADIUS_M,
            "vertex_count": int(local.shape[0]),
            "normal": [_round(value) for value in normal],
            "tilt_from_vertical_deg": _round(tilt_deg, 2),
            "height_at_camera_position_m": _round(height_at_origin),
        }
    return result


def surface_agreement(
    collider_metric_vertices: np.ndarray,
    splat_metric_positions: np.ndarray,
    splat_opacities: np.ndarray,
    *,
    cell_size_m: float = ALIGNMENT_CELL_SIZE_M,
    radius_m: float = ALIGNMENT_RADIUS_M,
) -> dict[str, Any]:
    """Compare ground height per grid cell: the collider's lowest vertex vs the opaque splats' 10th percentile.

    Good alignment means the collider sits on the visible surface: small median difference and most
    cells within about 0.3 m.
    """
    opaque = splat_opacities >= OPAQUE_SPLAT_THRESHOLD
    splats = splat_metric_positions[opaque]
    in_radius_splats = splats[np.hypot(splats[:, 0], splats[:, 2]) <= radius_m]
    in_radius_collider = collider_metric_vertices[
        np.hypot(collider_metric_vertices[:, 0], collider_metric_vertices[:, 2]) <= radius_m
    ]
    if in_radius_splats.shape[0] == 0 or in_radius_collider.shape[0] == 0:
        return {"cells_compared": 0}

    def cell_keys(points: np.ndarray) -> np.ndarray:
        return np.floor(points[:, [0, 2]] / cell_size_m).astype(np.int64)

    collider_low: dict[tuple[int, int], float] = {}
    for key, height in zip(map(tuple, cell_keys(in_radius_collider)), in_radius_collider[:, 1]):
        if key not in collider_low or height < collider_low[key]:
            collider_low[key] = float(height)
    splat_heights: dict[tuple[int, int], list[float]] = {}
    for key, height in zip(map(tuple, cell_keys(in_radius_splats)), in_radius_splats[:, 1]):
        splat_heights.setdefault(key, []).append(float(height))
    differences = []
    for key, heights in splat_heights.items():
        if key in collider_low and len(heights) >= MINIMUM_SPLATS_PER_CELL:
            differences.append(collider_low[key] - float(np.percentile(heights, 10)))
    if not differences:
        return {"cells_compared": 0}
    differences_array = np.array(differences)
    absolute = np.abs(differences_array)
    return {
        "cells_compared": int(differences_array.size),
        "cell_size_m": cell_size_m,
        "radius_m": radius_m,
        "median_collider_minus_splat_m": _round(np.median(differences_array)),
        "median_absolute_difference_m": _round(np.median(absolute)),
        "p90_absolute_difference_m": _round(np.percentile(absolute, 90)),
        "share_within_0_3_m": _round((absolute <= 0.3).mean()),
    }


@dataclass(frozen=True)
class ColliderFrameHypothesis:
    """How the collider GLB's file coordinates might relate to the SPZ's raw OpenCV coordinates."""

    name: str
    description: str
    # Multiply file coordinates by this (per axis) to get SPZ-raw OpenCV coordinates, before scale.
    axis_signs: tuple[float, float, float]
    already_metric: bool


COLLIDER_FRAME_HYPOTHESES = (
    ColliderFrameHypothesis("raw_opencv", "same raw units and OpenCV axes as the SPZ", (1, 1, 1), False),
    ColliderFrameHypothesis("raw_opengl", "raw units, OpenGL axes (y up, z backward)", (1, -1, -1), False),
    ColliderFrameHypothesis("metric_opencv", "already meters, OpenCV axes", (1, 1, 1), True),
    ColliderFrameHypothesis("metric_opengl", "already meters, OpenGL axes (y up, z backward)", (1, -1, -1), True),
)


def collider_to_metric(
    vertices: np.ndarray, hypothesis: ColliderFrameHypothesis, transform: MarbleToMetricTransform
) -> np.ndarray:
    opencv = vertices * np.array(hypothesis.axis_signs, dtype=np.float64)
    if hypothesis.already_metric:
        # Already meters: convert to raw units so the documented scale-then-offset order still applies.
        opencv = opencv / transform.metric_scale_factor
    return transform.apply(opencv)


def choose_collider_frame(
    geometry: GlbGeometry, cloud: SplatCloud, transform: MarbleToMetricTransform
) -> tuple[ColliderFrameHypothesis, dict[str, Any]]:
    """Try each hypothesis and keep the one whose collider surface best matches the splat ground."""
    splat_metric = transform.apply(cloud.positions)
    scores: dict[str, Any] = {}
    best: tuple[float, ColliderFrameHypothesis] | None = None
    for hypothesis in COLLIDER_FRAME_HYPOTHESES:
        metric_vertices = collider_to_metric(geometry.vertices, hypothesis, transform)
        agreement = surface_agreement(metric_vertices, splat_metric, cloud.opacities)
        scores[hypothesis.name] = agreement
        if agreement.get("cells_compared", 0) >= 20:
            score = agreement["median_absolute_difference_m"]
            if best is None or score < best[0]:
                best = (score, hypothesis)
    if best is None:
        raise ValueError("no collider frame hypothesis overlapped the splats enough to compare")
    return best[1], scores


GROUND_ESTIMATE_RADIUS_RAW = 8.0
GROUND_ESTIMATE_CELL_RAW = 0.25
MAXIMUM_LEVEL_TILT_DEG = 5.0


@dataclass(frozen=True)
class GroundEstimate:
    """The ground under the camera, fitted in Marble's raw OpenCV frame (y down, raw units)."""

    up_normal_raw: tuple[float, float, float]
    camera_height_raw: float
    tilt_from_vertical_deg: float
    cells_used: int
    median_residual_raw: float
    radius_raw: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "up_normal_raw": [_round(value, 4) for value in self.up_normal_raw],
            "camera_height_raw": _round(self.camera_height_raw, 4),
            "tilt_from_vertical_deg": _round(self.tilt_from_vertical_deg, 2),
            "cells_used": self.cells_used,
            "median_residual_raw": _round(self.median_residual_raw, 4),
            "radius_raw": self.radius_raw,
        }


def lowest_surface_per_cell(raw_points: np.ndarray, cell_size: float) -> np.ndarray:
    """For each horizontal cell, the point with the largest raw y (the lowest point, since y points down)."""
    keys = np.floor(raw_points[:, [0, 2]] / cell_size).astype(np.int64)
    order = np.lexsort((-raw_points[:, 1], keys[:, 1], keys[:, 0]))
    sorted_keys = keys[order]
    first_in_cell = np.r_[True, np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1)]
    return raw_points[order][first_in_cell]


def estimate_ground_under_camera(
    cloud: SplatCloud,
    *,
    radius_raw: float = GROUND_ESTIMATE_RADIUS_RAW,
    cell_size_raw: float = GROUND_ESTIMATE_CELL_RAW,
) -> GroundEstimate:
    """Fit a plane to the lowest visible surface around the camera (Marble puts the camera at the origin).

    Used when World Labs returns no scale metadata: the camera's height above this plane, in raw units,
    compared with the real camera height in meters, gives the metric scale.
    """
    opaque = cloud.positions[cloud.opacities >= OPAQUE_SPLAT_THRESHOLD]
    horizontal = np.hypot(opaque[:, 0], opaque[:, 2])
    below_camera = opaque[(horizontal <= radius_raw) & (opaque[:, 1] > 0.0)]
    if below_camera.shape[0] < 50:
        raise ValueError("too few splats below the camera to find the ground")
    surface = lowest_surface_per_cell(below_camera, cell_size_raw)
    centroid = surface.mean(axis=0)
    _, _, right_singular_vectors = np.linalg.svd(surface - centroid, full_matrices=False)
    normal = right_singular_vectors[-1]
    if normal[1] > 0:  # make it point up, i.e. toward -y in the OpenCV frame
        normal = -normal
    camera_height_raw = float(abs(normal @ centroid))
    tilt_deg = float(np.degrees(np.arccos(np.clip(-normal[1], -1.0, 1.0))))
    residuals = np.abs((surface - centroid) @ normal)
    return GroundEstimate(
        up_normal_raw=(float(normal[0]), float(normal[1]), float(normal[2])),
        camera_height_raw=camera_height_raw,
        tilt_from_vertical_deg=tilt_deg,
        cells_used=int(surface.shape[0]),
        median_residual_raw=float(np.median(residuals)),
        radius_raw=radius_raw,
    )


def scale_from_camera_height(ground: GroundEstimate, camera_height_m: float) -> tuple[float, float]:
    """(metric_scale_factor, ground_plane_offset_m) that put the fitted ground at y = 0.

    Refuses when the fitted ground is tilted by more than a few degrees, because then the raw frame is not
    level and a y-only offset would be wrong.
    """
    if camera_height_m <= 0:
        raise ValueError("camera height must be positive")
    if ground.tilt_from_vertical_deg > MAXIMUM_LEVEL_TILT_DEG:
        raise ValueError(
            f"fitted ground is tilted {ground.tilt_from_vertical_deg:.1f} deg; the raw frame is not level"
        )
    metric_scale_factor = camera_height_m / ground.camera_height_raw
    # After scaling, the ground lies camera_height_m below the camera (y down), so the offset equals it.
    return metric_scale_factor, camera_height_m


LOCAL_GROUND_RADIUS_IN_CAMERA_HEIGHTS = 5.0
LOCAL_GROUND_ITERATIONS = 6


def estimate_local_ground_under_camera(cloud: SplatCloud, *, radius_in_camera_heights: float = LOCAL_GROUND_RADIUS_IN_CAMERA_HEIGHTS,
                                       cell_size_in_camera_heights: float = 0.15) -> GroundEstimate:
    """Like ``estimate_ground_under_camera``, but the fitting radius follows the scene's own scale: about five
    camera heights (about 10 m for a 2 m mast), found by repeating the fit until the radius settles.

    A fixed raw radius can reach far beyond the local ground in worlds whose raw unit is several meters,
    where rising terrain tilts and lifts the fitted plane (seen on a panorama world: 8 raw units = 43 m).
    """
    estimate = estimate_ground_under_camera(cloud)
    for _ in range(LOCAL_GROUND_ITERATIONS):
        radius = radius_in_camera_heights * estimate.camera_height_raw
        refined = estimate_ground_under_camera(cloud, radius_raw=radius, cell_size_raw=cell_size_in_camera_heights * estimate.camera_height_raw)
        if abs(refined.camera_height_raw - estimate.camera_height_raw) < 1e-3 * estimate.camera_height_raw:
            return refined
        estimate = refined
    return estimate
