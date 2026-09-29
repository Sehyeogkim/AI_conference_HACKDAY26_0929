"""World Labs World API client (Marble API v1) with a hard budget guard and a persistent spending ledger.

Rules this client enforces:

- The key header is sent only to the World Labs API host, only over HTTPS, and never across a redirect.
- Every paid call is checked by the budget guard, written to the ledger before it is sent, and never
  retried automatically. Free calls (credits, polling, get, list, uploads, PLY export) are not guarded.
- Every error message is scrubbed of the key before it is raised or logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ValidationError

from wefarm.worlds.worldlabs.api_key import WorldLabsApiKey
from wefarm.worlds.worldlabs.api_models import (
    CreditsResponse,
    ExportWorldRequest,
    GenerateWorldRequest,
    ListWorldsRequest,
    Operation,
    PrepareUploadRequest,
    PrepareUploadResponse,
    World,
    WorldListPage,
)
from wefarm.worlds.worldlabs.budget_guard import BudgetGuard
from wefarm.worlds.worldlabs.errors import BudgetRefusedError, WorldLabsError, error_kind_for_status
from wefarm.worlds.worldlabs.http_transport import (
    HttpRequest,
    HttpResponse,
    HttpTransport,
    HttpTransportError,
    UrllibHttpTransport,
)
from wefarm.worlds.worldlabs.ledger import SpendingLedger
from wefarm.worlds.worldlabs.pricing import worst_case_export_credits, worst_case_generation_credits

logger = logging.getLogger(__name__)

WORLD_LABS_API_BASE_URL = "https://api.worldlabs.ai"
API_KEY_HEADER_NAME = "WLT-Api-Key"
USER_AGENT = "martian-jevbots-worldlabs-client/1"
MAX_ERROR_BODY_CHARACTERS = 500
MAX_DOWNLOAD_REDIRECTS = 5
MAX_CONSECUTIVE_POLL_FAILURES = 5
# HTTP statuses that prove a paid start was refused, so nothing was charged.
DEFINITE_REFUSAL_STATUSES = {400, 401, 402, 403, 404, 422, 429}

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


@dataclass(frozen=True)
class StartedPaidOperation:
    """A paid call that the API accepted. `spend_id` links it to its ledger lines."""

    spend_id: str
    operation: Operation
    estimated_credits: float
    balance_before: float


@dataclass(frozen=True)
class DownloadedAsset:
    name: str
    url: str
    path: Path
    size_bytes: int
    sha256: str
    content_type: str | None


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def hosts_of(urls: dict[str, str] | list[str]) -> set[str]:
    values = urls.values() if isinstance(urls, dict) else urls
    return {host_of(url) for url in values if host_of(url)}


def url_without_query(url: str) -> str:
    """Signed asset URLs carry credentials in the query string; strip it before logging or recording."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class WorldLabsClient:
    def __init__(
        self,
        api_key: WorldLabsApiKey,
        *,
        ledger: SpendingLedger,
        budget_guard: BudgetGuard,
        transport: HttpTransport | None = None,
        base_url: str = WORLD_LABS_API_BASE_URL,
        request_timeout_s: float = 60.0,
        download_timeout_s: float = 600.0,
    ) -> None:
        if urlsplit(base_url).scheme != "https":
            raise ValueError("the World Labs base URL must use HTTPS")
        self._api_key = api_key
        self.ledger = ledger
        self.budget_guard = budget_guard
        self._transport = transport or UrllibHttpTransport()
        self.base_url = base_url.rstrip("/")
        self.api_host = host_of(self.base_url)
        self.request_timeout_s = request_timeout_s
        self.download_timeout_s = download_timeout_s

    def __repr__(self) -> str:
        return f"WorldLabsClient(base_url={self.base_url!r}, api_key={self._api_key!r})"

    # ------------------------------------------------------------ low level

    def _scrub(self, text: str) -> str:
        return self._api_key.scrub(text)

    def _error(self, kind, message: str, **extra) -> WorldLabsError:
        return WorldLabsError(kind, self._scrub(message), **extra)

    def _api_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _call_api(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
        url = self._api_url(path)
        headers = {API_KEY_HEADER_NAME: self._api_key.reveal(), "Accept": "application/json", "User-Agent": USER_AGENT}
        body = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode("utf-8")
        started = time.monotonic()
        try:
            response = self._transport.send(
                HttpRequest(method=method, url=url, headers=headers, body=body, timeout_s=self.request_timeout_s)
            )
        except HttpTransportError as transport_error:
            logger.warning("World Labs %s %s: transport failure", method, path)
            raise self._error("transport", str(transport_error)) from None
        elapsed_ms = (time.monotonic() - started) * 1000.0
        logger.info("World Labs %s %s -> HTTP %s in %.0f ms", method, path, response.status, elapsed_ms)
        self._raise_for_status(method, path, response)
        try:
            return json.loads(response.body.decode("utf-8")) if response.body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise self._error("invalid_response", f"{method} {path} returned a body that is not JSON") from None

    def _raise_for_status(self, method: str, path: str, response: HttpResponse) -> None:
        if 200 <= response.status <= 299:
            return
        if 300 <= response.status <= 399:
            target_host = host_of(response.header("Location") or "") or "an unknown host"
            raise self._error(
                "redirect_refused",
                f"{method} {path} answered HTTP {response.status} redirecting to {target_host}; "
                "redirects are never followed for calls that carry the API key",
                status=response.status,
            )
        retry_after_s = None
        retry_after_text = response.header("Retry-After")
        if retry_after_text:
            try:
                retry_after_s = float(retry_after_text)
            except ValueError:
                retry_after_s = None
        raise self._error(
            error_kind_for_status(response.status),
            f"{method} {path} failed with HTTP {response.status}: {self._error_detail(response.body)}",
            status=response.status,
            retry_after_s=retry_after_s,
        )

    def _error_detail(self, body: bytes) -> str:
        text = body.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("detail", "message", "error"):
                if key in parsed:
                    text = json.dumps(parsed[key]) if not isinstance(parsed[key], str) else parsed[key]
                    break
        text = self._scrub(text).replace("\n", " ")
        return text[:MAX_ERROR_BODY_CHARACTERS] or "(empty body)"

    def _parse(self, model: type[ResponseModel], payload: Any, what: str) -> ResponseModel:
        try:
            return model.model_validate(payload)
        except ValidationError as validation_error:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
                for problem in validation_error.errors()[:5]
            )
            raise self._error("invalid_response", f"{what} did not match the documented shape: {problems}") from None

    # ------------------------------------------------------------ free calls

    def credits(self) -> float:
        """Remaining API credits (free)."""
        return self._parse(CreditsResponse, self._call_api("GET", "/marble/v1/credits"), "credits").remaining_credits

    def get_operation(self, operation_id: str) -> Operation:
        payload = self._call_api("GET", f"/marble/v1/operations/{operation_id}")
        return self._parse(Operation, payload, "operation")

    def poll(self, operation_id: str) -> Operation:
        """One poll of an operation (free)."""
        return self.get_operation(operation_id)

    def wait_for_operation(
        self,
        operation_id: str,
        *,
        poll_interval_s: float = 15.0,
        timeout_s: float = 1800.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        on_poll: Callable[[Operation], None] | None = None,
    ) -> Operation:
        """Poll until the operation is done. Transient polling failures are retried (polling is free).

        Raises WorldLabsError("generation_failed") when the operation finishes with an error, and
        WorldLabsError("timeout") when it is not done in time. Never resubmits the job.
        """
        deadline = monotonic() + timeout_s
        consecutive_failures = 0
        while True:
            try:
                operation = self.get_operation(operation_id)
                consecutive_failures = 0
            except WorldLabsError as poll_error:
                if poll_error.kind not in ("rate_limited", "server", "transport"):
                    raise
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                    raise
                wait_s = max(poll_error.retry_after_s or 0.0, poll_interval_s * (2 ** (consecutive_failures - 1)))
                logger.warning("polling %s failed (%s); waiting %.0f s", operation_id, poll_error.kind, wait_s)
                if monotonic() + wait_s > deadline:
                    raise self._error("timeout", f"operation {operation_id} polling kept failing until the deadline")
                sleep(wait_s)
                continue
            if on_poll is not None:
                on_poll(operation)
            if operation.done:
                if operation.failed:
                    code = operation.error.code if operation.error else None
                    message = operation.error.message if operation.error else None
                    raise self._error("generation_failed", f"operation {operation_id} failed: code {code}: {message}")
                return operation
            if monotonic() + poll_interval_s > deadline:
                raise self._error("timeout", f"operation {operation_id} was not done within {timeout_s:g} s")
            sleep(poll_interval_s)

    def get_world(self, world_id: str) -> World:
        payload = self._call_api("GET", f"/marble/v1/worlds/{world_id}")
        # Some responses wrap the world in {"world": {...}}; accept both.
        if isinstance(payload, dict) and "world" in payload and "world_id" not in payload:
            payload = payload["world"]
        return self._parse(World, payload, "world")

    def list_worlds(self, request: ListWorldsRequest | None = None) -> WorldListPage:
        payload = self._call_api("POST", "/marble/v1/worlds:list", (request or ListWorldsRequest()).to_json_dict())
        return self._parse(WorldListPage, payload, "world list")

    def iterate_worlds(self, request: ListWorldsRequest | None = None, *, max_pages: int = 20) -> Iterator[World]:
        current_request = request or ListWorldsRequest()
        for _ in range(max_pages):
            page = self.list_worlds(current_request)
            yield from page.worlds
            if not page.next_page_token:
                return
            current_request = current_request.model_copy(update={"page_token": page.next_page_token})

    def prepare_upload(self, request: PrepareUploadRequest) -> PrepareUploadResponse:
        payload = self._call_api("POST", "/marble/v1/media-assets:prepare_upload", request.to_json_dict())
        return self._parse(PrepareUploadResponse, payload, "prepare-upload response")

    def upload_media_file(self, file_path: Path, *, kind: str, content_type: str, file_name: str | None = None) -> str:
        """Upload an image or video as a media asset (free) and return its media_asset_id.

        The file goes to the signed URL the API returns, without the API key header.
        """
        extension = file_path.suffix.lstrip(".").lower() or None
        prepared = self.prepare_upload(
            PrepareUploadRequest(file_name=(file_name or file_path.name)[:64], kind=kind, extension=extension)
        )
        upload_url = prepared.upload_info.upload_url
        if urlsplit(upload_url).scheme != "https":
            raise self._error("host_not_allowed", "the signed upload URL is not HTTPS")
        headers = {"Content-Type": content_type, **(prepared.upload_info.required_headers or {})}
        if any(header_name.lower() == API_KEY_HEADER_NAME.lower() for header_name in headers):
            raise self._error("invalid_response", "the upload instructions asked for the API key header; refused")
        method = (prepared.upload_info.upload_method or "PUT").upper()
        try:
            response = self._transport.send(
                HttpRequest(method=method, url=upload_url, headers=headers, body=file_path.read_bytes(),
                            timeout_s=self.download_timeout_s)
            )
        except HttpTransportError as transport_error:
            raise self._error("transport", f"upload failed: {transport_error}") from None
        if not 200 <= response.status <= 299:
            raise self._error(
                error_kind_for_status(response.status) if response.status >= 400 else "redirect_refused",
                f"upload to {host_of(upload_url)} failed with HTTP {response.status}",
                status=response.status,
            )
        logger.info("uploaded %s as media asset %s", file_path.name, prepared.media_asset.media_asset_id)
        return prepared.media_asset.media_asset_id

    # ------------------------------------------------------------ paid calls

    def _start_paid_call(
        self,
        *,
        path: str,
        body: dict[str, Any],
        operation_kind: str,
        estimated_credits: float,
        max_credits: float | None,
        label: str | None,
        model: str | None,
        world_id: str | None = None,
    ) -> StartedPaidOperation:
        if max_credits is not None and estimated_credits > max_credits:
            raise BudgetRefusedError(
                f"worst case {estimated_credits:g} credits exceeds this call's max_credits {max_credits:g}",
                estimated_credits=int(estimated_credits),
            )
        balance_before = self.credits()
        self.budget_guard.check(
            estimated_credits, project_counted_credits=self.ledger.total_counted_credits(), balance=balance_before
        )
        spend_id = uuid.uuid4().hex
        self.ledger.append(
            "requested",
            spend_id,
            operation_kind=operation_kind,
            label=label,
            model=model,
            world_id=world_id,
            estimated_credits=estimated_credits,
            balance_before=balance_before,
        )
        self.budget_guard.commit(estimated_credits)
        try:
            payload = self._call_api("POST", path, body)
        except WorldLabsError as start_error:
            if start_error.status in DEFINITE_REFUSAL_STATUSES:
                self.ledger.append("start_refused", spend_id, note=start_error.detail[:300])
                self.budget_guard.release(estimated_credits)
            else:
                # A server error or lost connection may or may not have started the job: count the worst case.
                self.ledger.append("start_uncertain", spend_id, note=start_error.detail[:300])
            raise
        try:
            operation = self._parse(Operation, payload, f"{operation_kind} operation")
        except WorldLabsError as parse_error:
            self.ledger.append("start_uncertain", spend_id, note=parse_error.detail[:300])
            raise
        self.ledger.append("started", spend_id, operation_id=operation.operation_id)
        return StartedPaidOperation(
            spend_id=spend_id, operation=operation, estimated_credits=estimated_credits, balance_before=balance_before
        )

    def generate(
        self, request: GenerateWorldRequest, max_credits: float | None = None, *, label: str | None = None
    ) -> StartedPaidOperation:
        """Start a world generation once (never retried). Refused before sending if over any budget."""
        if request.permission.public or request.permission.allow_id_access:
            raise ValueError("this project keeps every world private; public or ID access is refused")
        return self._start_paid_call(
            path="/marble/v1/worlds:generate",
            body=request.to_json_dict(),
            operation_kind="generate_world",
            estimated_credits=worst_case_generation_credits(request),
            max_credits=max_credits,
            label=label,
            model=request.model,
        )

    def export_world(
        self,
        world_id: str,
        request: ExportWorldRequest,
        max_credits: float | None = None,
        *,
        label: str | None = None,
    ) -> Operation | StartedPaidOperation:
        """Export splats as PLY (free, returns the Operation) or a high-quality mesh (paid, guarded)."""
        estimated_credits = worst_case_export_credits(request)
        path = f"/marble/v1/worlds/{world_id}:export"
        if estimated_credits == 0:
            return self._parse(Operation, self._call_api("POST", path, request.to_json_dict()), "export operation")
        return self._start_paid_call(
            path=path,
            body=request.to_json_dict(),
            operation_kind="export_mesh",
            estimated_credits=estimated_credits,
            max_credits=max_credits,
            label=label,
            model=None,
            world_id=world_id,
        )

    def export_ply(self, world_id: str, resolution: str = "100k") -> Operation:
        """Free PLY splat export at one resolution."""
        result = self.export_world(world_id, ExportWorldRequest(asset_type="splats", format="ply", resolution=resolution))
        assert isinstance(result, Operation)
        return result

    def record_outcome(self, started: StartedPaidOperation, finished_operation: Operation | None,
                       *, balance_after: float | None, failure_note: str | None = None) -> None:
        """Write the outcome of a paid call to the ledger: actual credits from the operation's cost."""
        world_id = None
        if finished_operation is not None and isinstance(finished_operation.response, dict):
            world_id = finished_operation.response.get("world_id")
        actual_credits = None
        line_items = None
        if finished_operation is not None and finished_operation.cost is not None:
            actual_credits = finished_operation.cost.total_credits
            line_items = [item.model_dump() for item in finished_operation.cost.line_items]
        if finished_operation is not None and finished_operation.done and not finished_operation.failed \
                and actual_credits is not None:
            self.ledger.append(
                "finished", started.spend_id, operation_id=finished_operation.operation_id, world_id=world_id,
                actual_credits=actual_credits, cost_line_items=line_items, balance_after=balance_after,
            )
        else:
            self.ledger.append(
                "failed", started.spend_id, world_id=world_id, actual_credits=actual_credits,
                cost_line_items=line_items, balance_after=balance_after,
                note=self._scrub(failure_note or "no cost reported; counted at the worst-case estimate")[:300],
            )

    # ------------------------------------------------------------ downloads

    def download(self, name: str, url: str, destination: Path, *, allowed_hosts: set[str]) -> DownloadedAsset:
        """Download one asset over HTTPS from an allowed host.

        The key header is attached only when the URL is on the API host; such a request never follows a
        redirect. Keyless requests follow at most a few redirects, and only to allowed hosts.
        """
        current_url = url
        for _ in range(MAX_DOWNLOAD_REDIRECTS + 1):
            parts = urlsplit(current_url)
            if parts.scheme != "https":
                raise self._error("host_not_allowed", f"{name}: refusing non-HTTPS URL on {parts.hostname}")
            current_host = host_of(current_url)
            if current_host not in allowed_hosts:
                raise self._error("host_not_allowed", f"{name}: host {current_host} is not in the allowed hosts")
            carries_key = current_host == self.api_host
            headers = {"User-Agent": USER_AGENT}
            if carries_key:
                headers[API_KEY_HEADER_NAME] = self._api_key.reveal()
            try:
                streamed = self._transport.stream_to_file(
                    HttpRequest(method="GET", url=current_url, headers=headers, timeout_s=self.download_timeout_s),
                    destination,
                )
            except HttpTransportError as transport_error:
                raise self._error("transport", f"{name}: {transport_error}") from None
            if 300 <= streamed.status <= 399:
                location = streamed.header("Location")
                if carries_key:
                    raise self._error(
                        "redirect_refused",
                        f"{name}: the API host redirected to {host_of(location or '') or 'an unknown host'}; "
                        "a request carrying the key never follows redirects",
                        status=streamed.status,
                    )
                if not location:
                    raise self._error("download_failed", f"{name}: HTTP {streamed.status} without Location")
                current_url = urljoin(current_url, location)
                continue
            if not 200 <= streamed.status <= 299:
                raise self._error(
                    error_kind_for_status(streamed.status),
                    f"{name}: download from {current_host} failed with HTTP {streamed.status}",
                    status=streamed.status,
                )
            if streamed.bytes_written <= 0:
                raise self._error("download_failed", f"{name}: downloaded file is empty")
            digest = hashlib.sha256()
            with destination.open("rb") as downloaded_file:
                for chunk in iter(lambda: downloaded_file.read(1024 * 1024), b""):
                    digest.update(chunk)
            logger.info("downloaded %s (%d bytes) from %s", name, streamed.bytes_written, current_host)
            return DownloadedAsset(
                name=name,
                url=url_without_query(url),
                path=destination,
                size_bytes=streamed.bytes_written,
                sha256=digest.hexdigest(),
                content_type=streamed.header("Content-Type"),
            )
        raise self._error("redirect_refused", f"{name}: more than {MAX_DOWNLOAD_REDIRECTS} redirects")
