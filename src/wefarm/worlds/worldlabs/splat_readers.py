"""Read Gaussian splat centers (and opacity and size) from SPZ and PLY files.

SPZ is Niantic's open compressed splat format that Marble uses. Layout (versions 2 and 3; the whole file
is gzip-compressed):

| Part | Bytes |
| --- | --- |
| Header: magic "NGSP" (0x5053474E), version, point count (uint32 each); SH degree, fractional bits, flags, reserved (uint8 each) | 16 |
| Positions: three 24-bit signed fixed-point values per point, divided by 2^fractional_bits | 9 N |
| Opacities: one byte per point, opacity = byte / 255 | N |
| Colors | 3 N |
| Scales: three bytes per point, log scale = byte / 16 - 10 | 3 N |
| Rotations: 3 bytes (version 2) or 4 bytes (version 3) per point | 3 N or 4 N |

PLY exports are the uncompressed 3D Gaussian Splatting layout: x, y, z, opacity (logit), scale_0..2
(log), rotation, color coefficients, as little-endian float32 vertex properties.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SPZ_MAGIC = 0x5053474E
SUPPORTED_SPZ_VERSIONS = (2, 3)
SPZ_HEADER_BYTES = 16
PLY_TYPE_TO_DTYPE = {
    "float": "<f4", "float32": "<f4", "double": "<f8", "float64": "<f8",
    "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
    "ushort": "<u2", "uint16": "<u2", "short": "<i2", "int16": "<i2",
    "uint": "<u4", "uint32": "<u4", "int": "<i4", "int32": "<i4",
}


class SplatFormatError(ValueError):
    pass


@dataclass
class SplatCloud:
    """Splat centers in the file's own frame and units, with per-splat opacity and largest size."""

    positions: np.ndarray  # (N, 3) float64
    opacities: np.ndarray  # (N,) in [0, 1]
    largest_scale: np.ndarray  # (N,) linear size along the splat's longest axis, file units
    source_format: str
    format_version: int | None = None
    spherical_harmonics_degree: int | None = None

    @property
    def count(self) -> int:
        return int(self.positions.shape[0])


def read_spz_bytes(data: bytes) -> SplatCloud:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if len(data) < SPZ_HEADER_BYTES:
        raise SplatFormatError("SPZ data too short for a header")
    magic, version, point_count = struct.unpack_from("<III", data, 0)
    sh_degree, fractional_bits, _flags, _reserved = struct.unpack_from("<BBBB", data, 12)
    if magic != SPZ_MAGIC:
        raise SplatFormatError("not SPZ data (bad magic)")
    if version not in SUPPORTED_SPZ_VERSIONS:
        raise SplatFormatError(f"SPZ version {version} is not supported (supported: {SUPPORTED_SPZ_VERSIONS})")
    offset = SPZ_HEADER_BYTES
    position_bytes = point_count * 9
    alpha_bytes = point_count
    color_bytes = point_count * 3
    scale_bytes = point_count * 3
    if len(data) < offset + position_bytes + alpha_bytes + color_bytes + scale_bytes:
        raise SplatFormatError("SPZ data is truncated")

    packed = np.frombuffer(data, dtype=np.uint8, count=position_bytes, offset=offset).reshape(-1, 3).astype(np.int32)
    fixed_point = packed[:, 0] | (packed[:, 1] << 8) | (packed[:, 2] << 16)
    fixed_point = np.where(fixed_point & 0x800000, fixed_point - (1 << 24), fixed_point)
    positions = (fixed_point.astype(np.float64) / float(1 << fractional_bits)).reshape(-1, 3)
    offset += position_bytes

    opacities = np.frombuffer(data, dtype=np.uint8, count=alpha_bytes, offset=offset).astype(np.float64) / 255.0
    offset += alpha_bytes + color_bytes
    log_scales = np.frombuffer(data, dtype=np.uint8, count=scale_bytes, offset=offset).reshape(-1, 3)
    largest_scale = np.exp(log_scales.astype(np.float64) / 16.0 - 10.0).max(axis=1)
    return SplatCloud(
        positions=positions,
        opacities=opacities,
        largest_scale=largest_scale,
        source_format="spz",
        format_version=version,
        spherical_harmonics_degree=sh_degree,
    )


def read_spz(path: Path) -> SplatCloud:
    return read_spz_bytes(path.read_bytes())


def read_spz_header(path: Path) -> dict[str, int]:
    """Only the header (cheap): version, point count, SH degree, fractional bits."""
    with gzip.open(path, "rb") as compressed:
        header = compressed.read(SPZ_HEADER_BYTES)
    magic, version, point_count = struct.unpack_from("<III", header, 0)
    sh_degree, fractional_bits, flags, _reserved = struct.unpack_from("<BBBB", header, 12)
    if magic != SPZ_MAGIC:
        raise SplatFormatError("not SPZ data (bad magic)")
    return {"version": version, "point_count": point_count, "spherical_harmonics_degree": sh_degree,
            "fractional_bits": fractional_bits, "flags": flags}


def read_ply_bytes(data: bytes) -> SplatCloud:
    header_end = data.find(b"end_header")
    if not data.startswith(b"ply") or header_end < 0:
        raise SplatFormatError("not a PLY file")
    body_start = data.index(b"\n", header_end) + 1
    header_lines = data[:header_end].decode("ascii", errors="replace").splitlines()
    if "format binary_little_endian 1.0" not in [line.strip() for line in header_lines]:
        raise SplatFormatError("only binary little-endian PLY is supported")
    vertex_count = None
    properties: list[tuple[str, str]] = []
    in_vertex_element = False
    for line in header_lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "element":
            in_vertex_element = parts[1] == "vertex"
            if in_vertex_element:
                vertex_count = int(parts[2])
            elif vertex_count is not None:
                break
        elif parts[0] == "property" and in_vertex_element:
            if parts[1] == "list":
                raise SplatFormatError("list properties in the vertex element are not supported")
            properties.append((parts[2], PLY_TYPE_TO_DTYPE[parts[1]]))
    if vertex_count is None:
        raise SplatFormatError("PLY has no vertex element")
    dtype = np.dtype(properties)
    vertices = np.frombuffer(data, dtype=dtype, count=vertex_count, offset=body_start)
    positions = np.stack([vertices["x"], vertices["y"], vertices["z"]], axis=1).astype(np.float64)
    names = dtype.names or ()
    if "opacity" in names:
        opacities = 1.0 / (1.0 + np.exp(-vertices["opacity"].astype(np.float64)))
    else:
        opacities = np.ones(vertex_count)
    scale_names = [name for name in ("scale_0", "scale_1", "scale_2") if name in names]
    if scale_names:
        largest_scale = np.exp(np.stack([vertices[name] for name in scale_names], axis=1).astype(np.float64)).max(axis=1)
    else:
        largest_scale = np.zeros(vertex_count)
    return SplatCloud(positions=positions, opacities=opacities, largest_scale=largest_scale, source_format="ply")


def read_ply(path: Path) -> SplatCloud:
    return read_ply_bytes(path.read_bytes())
