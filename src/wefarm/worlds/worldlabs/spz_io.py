"""Lossless read, subset, and write of SPZ splat files (Niantic's compressed Gaussian-splat format).

Layout (versions 2 and 3; the whole file is gzip-compressed), all columns stored one after another:

| Column | Bytes per splat |
| --- | --- |
| header: magic "NGSP", version, count (uint32); SH degree, fractional bits, flags, reserved (uint8) | 16 total |
| positions: three 24-bit signed fixed-point numbers | 9 |
| opacity (alpha / 255) | 1 |
| colour (spherical-harmonics DC terms) | 3 |
| scales (log scale = byte / 16 - 10) | 3 |
| rotation | 3 (version 2) or 4 (version 3) |
| higher spherical-harmonics coefficients | 3 x ((degree + 1)^2 - 1) |

Keeping a subset of splats copies each column's bytes for the kept splats, so nothing is re-quantised.
Only the opacity column may be changed (to fade splats out toward a trim edge).
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from wefarm.worlds.worldlabs.splat_readers import SPZ_HEADER_BYTES, SPZ_MAGIC, SUPPORTED_SPZ_VERSIONS, SplatFormatError

SPHERICAL_HARMONICS_DC_SCALE = 0.15
SPHERICAL_HARMONICS_C0 = 0.28209479177387814


def _rotation_bytes(version: int) -> int:
    return 3 if version == 2 else 4


def _higher_harmonics_bytes(degree: int) -> int:
    return 3 * ((degree + 1) ** 2 - 1)


@dataclass(frozen=True)
class SpzData:
    """Every column of an SPZ file as raw byte arrays (N rows each), plus the header fields."""

    version: int
    spherical_harmonics_degree: int
    fractional_bits: int
    flags: int
    reserved: int
    positions: np.ndarray  # (N, 9) uint8
    alphas: np.ndarray  # (N,) uint8
    colours: np.ndarray  # (N, 3) uint8
    scales: np.ndarray  # (N, 3) uint8
    rotations: np.ndarray  # (N, 3 or 4) uint8
    harmonics: np.ndarray  # (N, K) uint8

    @property
    def count(self) -> int:
        return int(self.alphas.shape[0])

    def positions_raw(self) -> np.ndarray:
        """Splat centres in the file's own units (N, 3) float64."""
        packed = self.positions.reshape(-1, 3, 3).astype(np.int32)
        fixed_point = packed[..., 0] | (packed[..., 1] << 8) | (packed[..., 2] << 16)
        fixed_point = np.where(fixed_point & 0x800000, fixed_point - (1 << 24), fixed_point)
        return fixed_point.astype(np.float64) / float(1 << self.fractional_bits)

    def opacities(self) -> np.ndarray:
        return self.alphas.astype(np.float64) / 255.0

    def colours_rgb(self) -> np.ndarray:
        """Base (view-independent) colour in [0, 1] from the stored DC terms."""
        dc = (self.colours.astype(np.float64) / 255.0 - 0.5) / SPHERICAL_HARMONICS_DC_SCALE
        return np.clip(0.5 + SPHERICAL_HARMONICS_C0 * dc, 0.0, 1.0)

    def largest_scale(self) -> np.ndarray:
        return np.exp(self.scales.astype(np.float64) / 16.0 - 10.0).max(axis=1)

    def subset(self, keep: np.ndarray) -> "SpzData":
        return replace(
            self,
            positions=self.positions[keep], alphas=self.alphas[keep], colours=self.colours[keep],
            scales=self.scales[keep], rotations=self.rotations[keep], harmonics=self.harmonics[keep],
        )

    def with_opacity_factors(self, factors: np.ndarray) -> "SpzData":
        """Multiply each splat's opacity by a factor in [0, 1] (used to fade splats near a trim edge)."""
        faded = np.clip(np.round(self.alphas.astype(np.float64) * np.clip(factors, 0.0, 1.0)), 0, 255).astype(np.uint8)
        return replace(self, alphas=faded)


def decode_spz(data: bytes) -> SpzData:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if len(data) < SPZ_HEADER_BYTES:
        raise SplatFormatError("SPZ data too short for a header")
    magic, version, count = struct.unpack_from("<III", data, 0)
    degree, fractional_bits, flags, reserved = struct.unpack_from("<BBBB", data, 12)
    if magic != SPZ_MAGIC:
        raise SplatFormatError("not SPZ data (bad magic)")
    if version not in SUPPORTED_SPZ_VERSIONS:
        raise SplatFormatError(f"SPZ version {version} is not supported")
    widths = [9, 1, 3, 3, _rotation_bytes(version), _higher_harmonics_bytes(degree)]
    expected = SPZ_HEADER_BYTES + count * sum(widths)
    if len(data) < expected:
        raise SplatFormatError(f"SPZ data is truncated ({len(data)} of {expected} bytes)")
    columns = []
    offset = SPZ_HEADER_BYTES
    for width in widths:
        block = np.frombuffer(data, dtype=np.uint8, count=count * width, offset=offset)
        columns.append(block.reshape(count, width) if width > 1 else block.copy())
        offset += count * width
    positions, alphas, colours, scales, rotations, harmonics = columns
    return SpzData(version, degree, fractional_bits, flags, reserved,
                   positions.copy(), alphas.reshape(-1), colours.copy(), scales.copy(), rotations.copy(),
                   harmonics.copy() if harmonics.ndim == 2 else harmonics.reshape(count, 0))


def encode_spz(spz: SpzData, *, compression_level: int = 9) -> bytes:
    header = struct.pack("<IIIBBBB", SPZ_MAGIC, spz.version, spz.count, spz.spherical_harmonics_degree,
                         spz.fractional_bits, spz.flags, spz.reserved)
    body = b"".join(column.tobytes() for column in (spz.positions, spz.alphas, spz.colours, spz.scales, spz.rotations, spz.harmonics))
    return gzip.compress(header + body, compresslevel=compression_level, mtime=0)


def read_spz_data(path: Path) -> SpzData:
    return decode_spz(Path(path).read_bytes())


def write_spz_data(spz: SpzData, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = encode_spz(spz)
    path.write_bytes(data)
    return len(data)
