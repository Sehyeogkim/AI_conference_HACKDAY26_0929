"""Convert Marble scene coordinates into the project's metric frame (y up, x east, z north).

Marble assets are in the ``marble_raw_opencv`` convention: +x to the camera's right, +y down, +z forward
(the direction the input photo looks), in arbitrary raw units. World Labs documents the order: multiply by
``metric_scale_factor``, then subtract ``ground_plane_offset`` (already in meters) from y, so the ground
lies at y = 0; only then convert axes.

After scaling, with the photo's compass heading ``h`` (degrees clockwise from north, the direction the
camera looked), the axes map as:

| Metric axis | From OpenCV axes |
| --- | --- |
| x (east) | x·cos h + z·sin h |
| y (up) | −y |
| z (north) | −x·sin h + z·cos h |

Example: a camera looking due east (h = 90°) has "forward" (+z) become east (+x) and "right" (+x) become
south (−z).

Note: y up, x east, z north is a left-handed frame (east × up points south), so this mapping is a
reflection (determinant −1), not a rotation. A renderer with a right-handed frame such as Three.js must
flip one axis (for example use −north as its z) to avoid showing the scene mirrored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MarbleToMetricTransform:
    """metric = linear @ raw + translation, for points given in Marble's raw OpenCV frame."""

    metric_scale_factor: float
    ground_plane_offset_m: float
    camera_heading_deg: float

    @property
    def axis_matrix(self) -> np.ndarray:
        heading = math.radians(self.camera_heading_deg)
        cos_h, sin_h = math.cos(heading), math.sin(heading)
        return np.array([[cos_h, 0.0, sin_h], [0.0, -1.0, 0.0], [-sin_h, 0.0, cos_h]])

    @property
    def linear(self) -> np.ndarray:
        return self.axis_matrix * self.metric_scale_factor

    @property
    def translation(self) -> np.ndarray:
        # Subtracting the offset from OpenCV y (pointing down) raises points by the offset in "up".
        return self.axis_matrix @ np.array([0.0, -self.ground_plane_offset_m, 0.0])

    def as_matrix4(self) -> list[list[float]]:
        """Row-major 4x4 homogeneous matrix (apply to column vectors)."""
        matrix = np.eye(4)
        matrix[:3, :3] = self.linear
        matrix[:3, 3] = self.translation
        return [[float(value) for value in row] for row in matrix]

    def raw_to_metric_opencv(self, raw_points: np.ndarray) -> np.ndarray:
        """Step 1 only: meters, ground at y = 0, axes still OpenCV (y down)."""
        metric = np.asarray(raw_points, dtype=np.float64) * self.metric_scale_factor
        metric[..., 1] -= self.ground_plane_offset_m
        return metric

    def apply(self, raw_points: np.ndarray) -> np.ndarray:
        """Raw Marble points (N, 3) to the project's metric frame (N, 3)."""
        return self.raw_to_metric_opencv(raw_points) @ self.axis_matrix.T

    def metric_size(self, raw_size: np.ndarray) -> np.ndarray:
        """Splat sizes scale with the metric factor; the ground offset never applies to sizes."""
        return np.asarray(raw_size, dtype=np.float64) * self.metric_scale_factor


def heading_from_site_frame_vector(north: float, east: float) -> float:
    """Compass heading in degrees (0 = north, 90 = east) of a horizontal direction."""
    return math.degrees(math.atan2(east, north)) % 360.0


def elevation_from_site_frame_vector(north: float, east: float, down: float) -> float:
    """Elevation angle in degrees (positive = above the horizon) of a direction in a north-east-down frame."""
    return math.degrees(math.atan2(-down, math.hypot(north, east)))
