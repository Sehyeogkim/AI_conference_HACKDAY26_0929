"""Typed shapes of World Labs World API requests and responses (Marble API v1).

Shapes follow World Labs' API reference, read 2026-09-29 (docs.worldlabs.ai/api/reference). These are
vendor wire formats, not project contracts: responses ignore unknown fields (the vendor may add
fields), while requests reject unknown fields so a typo never reaches the API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MarbleModelName = Literal["marble-1.0-draft", "marble-1.0", "marble-1.1", "marble-1.1-plus"]
ContentSourceKind = Literal["uri", "data_base64", "media_asset"]
IsPanoSetting = Literal["auto", "true", "false"] | bool
WorldStatus = Literal["SUCCEEDED", "PENDING", "FAILED", "RUNNING"]


class _VendorResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _VendorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------- requests


class ContentReference(_VendorRequest):
    """One image or video input: a public URL, inline base64 data, or an uploaded media asset."""

    source: ContentSourceKind
    uri: str | None = None
    data_base64: str | None = None
    extension: str | None = None
    media_asset_id: str | None = None

    @model_validator(mode="after")
    def _matching_field_for_source(self) -> "ContentReference":
        required_field_by_source = {"uri": self.uri, "data_base64": self.data_base64, "media_asset": self.media_asset_id}
        if not required_field_by_source[self.source]:
            raise ValueError(f"content source {self.source!r} needs its matching field")
        return self

    def __repr__(self) -> str:
        # Inline image data can be megabytes long; never print it.
        return f"ContentReference(source={self.source!r}, media_asset_id={self.media_asset_id!r}, uri={self.uri!r})"


class MultiImageEntry(_VendorRequest):
    content: ContentReference
    azimuth: float | None = None


class WorldPrompt(_VendorRequest):
    type: Literal["text", "image", "multi-image", "video"]
    text_prompt: str | None = Field(default=None, max_length=2000)
    disable_recaption: bool | None = None
    image_prompt: ContentReference | None = None
    is_pano: IsPanoSetting | None = None
    multi_image_prompt: list[MultiImageEntry] | None = Field(default=None, max_length=8)
    reconstruct_images: bool | None = None
    video_prompt: ContentReference | None = None

    @model_validator(mode="after")
    def _input_matches_type(self) -> "WorldPrompt":
        if self.type == "text" and not self.text_prompt:
            raise ValueError("a text world prompt needs text_prompt")
        if self.type == "image" and self.image_prompt is None:
            raise ValueError("an image world prompt needs image_prompt")
        if self.type == "multi-image" and not self.multi_image_prompt:
            raise ValueError("a multi-image world prompt needs multi_image_prompt")
        if self.type == "video" and self.video_prompt is None:
            raise ValueError("a video world prompt needs video_prompt")
        return self


class WorldPermission(_VendorRequest):
    public: bool = False
    allow_id_access: bool = False
    allowed_readers: list[str] = Field(default_factory=list)
    allowed_writers: list[str] = Field(default_factory=list)


PRIVATE_PERMISSION = WorldPermission(public=False, allow_id_access=False, allowed_readers=[], allowed_writers=[])


class GenerateWorldRequest(_VendorRequest):
    """Body of POST /marble/v1/worlds:generate. `model` and `permission` are always sent explicitly."""

    world_prompt: WorldPrompt
    model: MarbleModelName
    display_name: str | None = Field(default=None, max_length=64)
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    tags: list[str] | None = Field(default=None, max_length=10)
    permission: WorldPermission = PRIVATE_PERMISSION

    @model_validator(mode="after")
    def _tag_lengths_and_privacy(self) -> "GenerateWorldRequest":
        for tag in self.tags or []:
            if not tag or len(tag) > 32:
                raise ValueError("each tag must be 1 to 32 characters")
        return self

    def to_json_dict(self) -> dict[str, Any]:
        body = super().to_json_dict()
        # Always send the full permission object, even when every value is the default.
        body["permission"] = self.permission.model_dump(mode="json")
        return body


class PrepareUploadRequest(_VendorRequest):
    file_name: str = Field(min_length=1, max_length=64)
    kind: Literal["image", "video"]
    extension: str | None = None
    metadata: dict[str, Any] | None = None


class ListWorldsRequest(_VendorRequest):
    page_size: int = Field(default=20, ge=1, le=100)
    page_token: str | None = None
    status: WorldStatus | None = None
    model: MarbleModelName | None = None
    tags: list[str] | None = None
    is_public: bool | None = None
    created_after: str | None = None
    created_before: str | None = None
    sort_by: Literal["created_at", "updated_at"] | None = None


class ExportWorldRequest(_VendorRequest):
    asset_type: Literal["splats", "mesh"]
    format: Literal["ply", "glb"]
    resolution: Literal["full_res", "500k", "150k", "100k"] | None = None
    mesh_variant: Literal["textured", "vertex_colored"] | None = None

    @model_validator(mode="after")
    def _format_matches_asset(self) -> "ExportWorldRequest":
        if self.asset_type == "splats" and self.format != "ply":
            raise ValueError("splat exports are PLY")
        if self.asset_type == "mesh" and self.format != "glb":
            raise ValueError("mesh exports are GLB")
        return self


# ---------------------------------------------------------------- responses


class CreditsResponse(_VendorResponse):
    remaining_credits: float = Field(ge=0)


class CostLineItem(_VendorResponse):
    name: str
    credits: float


class OperationCost(_VendorResponse):
    total_credits: float
    line_items: list[CostLineItem] = Field(default_factory=list)


class OperationError(_VendorResponse):
    code: int | None = None
    message: str | None = None


class SemanticsMetadata(_VendorResponse):
    metric_scale_factor: float | None = None
    ground_plane_offset: float | None = None


class SplatAssets(_VendorResponse):
    spz_urls: dict[str, str] | None = None
    semantics_metadata: SemanticsMetadata | None = None


class MeshAssets(_VendorResponse):
    collider_mesh_url: str | None = None
    full_res_mesh_url: str | None = None
    hq_mesh_url: str | None = None


class ImageryAssets(_VendorResponse):
    pano_url: str | None = None


class WorldAssets(_VendorResponse):
    thumbnail_url: str | None = None
    caption: str | None = None
    imagery: ImageryAssets | None = None
    mesh: MeshAssets | None = None
    splats: SplatAssets | None = None


class WorldPermissionResponse(_VendorResponse):
    public: bool = False
    allow_id_access: bool = False
    allowed_readers: list[str] = Field(default_factory=list)
    allowed_writers: list[str] = Field(default_factory=list)


class World(_VendorResponse):
    world_id: str = Field(min_length=1)
    display_name: str | None = None
    world_marble_url: str | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    model: str | None = None
    permission: WorldPermissionResponse | None = None
    world_prompt: dict[str, Any] | None = None
    assets: WorldAssets | None = None

    def asset_urls(self) -> dict[str, str]:
        """Every downloadable asset URL of this world, keyed by a descriptive name."""
        urls: dict[str, str] = {}
        assets = self.assets
        if assets is None:
            return urls
        if assets.thumbnail_url:
            urls["thumbnail"] = assets.thumbnail_url
        if assets.imagery and assets.imagery.pano_url:
            urls["panorama"] = assets.imagery.pano_url
        if assets.mesh:
            for field_name in ("collider_mesh_url", "full_res_mesh_url", "hq_mesh_url"):
                value = getattr(assets.mesh, field_name)
                if value:
                    urls[field_name.removesuffix("_url")] = value
        if assets.splats and assets.splats.spz_urls:
            for resolution_name, url in assets.splats.spz_urls.items():
                urls[f"spz_{resolution_name}"] = url
        return urls


class Operation(_VendorResponse):
    """A long-running job (generation or export). `response` is the result once `done` is true."""

    operation_id: str = Field(min_length=1)
    done: bool
    created_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    error: OperationError | None = None
    cost: OperationCost | None = None

    @property
    def progress_percentage(self) -> float | None:
        value = (self.metadata or {}).get("progress_percentage")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def progress_status(self) -> str | None:
        """Live responses (2026-09-29) report progress as metadata.progress = {status, description}."""
        progress = (self.metadata or {}).get("progress")
        if isinstance(progress, dict):
            status = progress.get("status")
            return str(status) if status is not None else None
        return None

    @property
    def failed(self) -> bool:
        return self.done and self.error is not None and (self.error.code is not None or bool(self.error.message))


class MediaAsset(_VendorResponse):
    media_asset_id: str = Field(min_length=1)
    file_name: str | None = None
    kind: str | None = None
    extension: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UploadInfo(_VendorResponse):
    upload_url: str = Field(min_length=1)
    upload_method: str = "PUT"
    required_headers: dict[str, str] | None = None
    curl_example: str | None = None


class PrepareUploadResponse(_VendorResponse):
    media_asset: MediaAsset
    upload_info: UploadInfo


class WorldListPage(_VendorResponse):
    worlds: list[World] = Field(default_factory=list)
    next_page_token: str | None = None


class ExportResult(_VendorResponse):
    asset_type: str | None = None
    format: str | None = None
    url: str | None = None
    resolution: str | None = None
    mesh_variant: str | None = None
