"""Read triangle geometry from a binary glTF (GLB) file, such as Marble's collider mesh.

Supports uncompressed float32 POSITION data with optional indices, node transforms (matrix or
translation/rotation/scale), and scenes. For compressed primitives (Draco or meshopt), vertex data cannot
be read without a decoder, so only the accessor bounds and counts (which glTF requires) are reported.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

GLB_MAGIC = 0x46546C67  # "glTF"
JSON_CHUNK_TYPE = 0x4E4F534A
BINARY_CHUNK_TYPE = 0x004E4942
TRIANGLES_MODE = 4
COMPONENT_DTYPES = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
TYPE_COMPONENT_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
COMPRESSION_EXTENSIONS = ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_meshopt_compression")


class GlbFormatError(ValueError):
    pass


@dataclass
class GlbGeometry:
    """World-space geometry of every triangle primitive in the GLB's default scene."""

    vertices: np.ndarray  # (V, 3) float64 in the file's own frame, node transforms applied
    triangles: np.ndarray  # (T, 3) int64 indices into vertices
    triangle_count: int  # includes primitives whose data is compressed
    vertex_count: int
    bounds_min: np.ndarray
    bounds_max: np.ndarray
    compressed_primitive_count: int = 0
    extensions_used: list[str] = field(default_factory=list)
    generator: str | None = None


def _read_chunks(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20:
        raise GlbFormatError("file too short for a GLB header")
    magic, version, total_length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise GlbFormatError("not a GLB file (bad magic)")
    if version != 2:
        raise GlbFormatError(f"unsupported GLB version {version}")
    if total_length > len(data):
        raise GlbFormatError("GLB header length exceeds the file size")
    offset = 12
    document: dict[str, Any] | None = None
    binary_chunk = b""
    while offset + 8 <= total_length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8 : offset + 8 + chunk_length]
        if chunk_type == JSON_CHUNK_TYPE:
            document = json.loads(chunk.decode("utf-8"))
        elif chunk_type == BINARY_CHUNK_TYPE and not binary_chunk:
            binary_chunk = chunk
        offset += 8 + chunk_length
    if document is None:
        raise GlbFormatError("GLB has no JSON chunk")
    return document, binary_chunk


def _quaternion_to_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def node_local_matrix(node: dict[str, Any]) -> np.ndarray:
    if "matrix" in node:
        # glTF stores matrices column-major.
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    matrix = np.eye(4)
    scale = np.array(node.get("scale", [1.0, 1.0, 1.0]), dtype=np.float64)
    rotation = _quaternion_to_matrix(*node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    matrix[:3, :3] = rotation * scale[np.newaxis, :]
    matrix[:3, 3] = node.get("translation", [0.0, 0.0, 0.0])
    return matrix


def _read_accessor(document: dict[str, Any], binary_chunk: bytes, accessor_index: int) -> np.ndarray:
    accessor = document["accessors"][accessor_index]
    component_count = TYPE_COMPONENT_COUNTS[accessor["type"]]
    dtype = np.dtype(COMPONENT_DTYPES[accessor["componentType"]]).newbyteorder("<")
    count = accessor["count"]
    if "bufferView" not in accessor:
        return np.zeros((count, component_count), dtype=dtype)
    buffer_view = document["bufferViews"][accessor["bufferView"]]
    if buffer_view.get("buffer", 0) != 0:
        raise GlbFormatError("only the GLB's embedded binary buffer is supported")
    start = buffer_view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    element_bytes = dtype.itemsize * component_count
    stride = buffer_view.get("byteStride") or element_bytes
    if stride == element_bytes:
        flat = np.frombuffer(binary_chunk, dtype=dtype, count=count * component_count, offset=start)
        values = flat.reshape(count, component_count)
    else:
        raw = np.frombuffer(binary_chunk, dtype=np.uint8, count=(count - 1) * stride + element_bytes, offset=start)
        rows = np.lib.stride_tricks.as_strided(raw, shape=(count, element_bytes), strides=(stride, 1))
        values = np.ascontiguousarray(rows).view(dtype).reshape(count, component_count)
    if accessor.get("normalized"):
        values = values.astype(np.float64) / float(np.iinfo(dtype).max)
    return values


def _primitive_is_compressed(document: dict[str, Any], primitive: dict[str, Any], position_accessor: dict[str, Any]) -> bool:
    extension_names = set(primitive.get("extensions", {}))
    if "bufferView" in position_accessor:
        extension_names |= set(document["bufferViews"][position_accessor["bufferView"]].get("extensions", {}))
    return any(name in extension_names for name in COMPRESSION_EXTENSIONS)


def read_glb(path: Path) -> GlbGeometry:
    document, binary_chunk = _read_chunks(path.read_bytes())
    nodes = document.get("nodes", [])
    scenes = document.get("scenes", [])
    if scenes:
        root_node_indices = scenes[document.get("scene", 0)].get("nodes", [])
    else:
        child_indices = {child for node in nodes for child in node.get("children", [])}
        root_node_indices = [index for index in range(len(nodes)) if index not in child_indices]

    vertex_blocks: list[np.ndarray] = []
    triangle_blocks: list[np.ndarray] = []
    compressed_bounds: list[np.ndarray] = []
    vertex_offset = 0
    triangle_count = 0
    compressed_vertex_count = 0
    compressed_primitive_count = 0

    stack = [(index, np.eye(4)) for index in root_node_indices]
    while stack:
        node_index, parent_matrix = stack.pop()
        node = nodes[node_index]
        world_matrix = parent_matrix @ node_local_matrix(node)
        for child_index in node.get("children", []):
            stack.append((child_index, world_matrix))
        if "mesh" not in node:
            continue
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            if primitive.get("mode", TRIANGLES_MODE) != TRIANGLES_MODE:
                continue
            position_accessor_index = primitive["attributes"]["POSITION"]
            position_accessor = document["accessors"][position_accessor_index]
            index_accessor = document["accessors"][primitive["indices"]] if "indices" in primitive else None
            primitive_triangles = (index_accessor["count"] if index_accessor else position_accessor["count"]) // 3
            triangle_count += primitive_triangles
            if _primitive_is_compressed(document, primitive, position_accessor):
                compressed_primitive_count += 1
                compressed_vertex_count += position_accessor["count"]
                corners = np.array(
                    [[x, y, z, 1.0] for x in (position_accessor["min"][0], position_accessor["max"][0])
                     for y in (position_accessor["min"][1], position_accessor["max"][1])
                     for z in (position_accessor["min"][2], position_accessor["max"][2])]
                )
                compressed_bounds.append((world_matrix @ corners.T).T[:, :3])
                continue
            positions = _read_accessor(document, binary_chunk, position_accessor_index).astype(np.float64)
            homogeneous = np.hstack([positions, np.ones((positions.shape[0], 1))])
            vertex_blocks.append((world_matrix @ homogeneous.T).T[:, :3])
            if index_accessor is not None:
                indices = _read_accessor(document, binary_chunk, primitive["indices"]).reshape(-1).astype(np.int64)
            else:
                indices = np.arange(positions.shape[0], dtype=np.int64)
            triangle_blocks.append(indices[: primitive_triangles * 3].reshape(-1, 3) + vertex_offset)
            vertex_offset += positions.shape[0]

    vertices = np.vstack(vertex_blocks) if vertex_blocks else np.zeros((0, 3))
    triangles = np.vstack(triangle_blocks) if triangle_blocks else np.zeros((0, 3), dtype=np.int64)
    all_points = [block for block in [vertices, *compressed_bounds] if len(block)]
    if not all_points:
        raise GlbFormatError("GLB contains no triangle geometry")
    stacked = np.vstack(all_points)
    return GlbGeometry(
        vertices=vertices,
        triangles=triangles,
        triangle_count=triangle_count,
        vertex_count=vertices.shape[0] + compressed_vertex_count,
        bounds_min=stacked.min(axis=0),
        bounds_max=stacked.max(axis=0),
        compressed_primitive_count=compressed_primitive_count,
        extensions_used=list(document.get("extensionsUsed", [])),
        generator=document.get("asset", {}).get("generator"),
    )
