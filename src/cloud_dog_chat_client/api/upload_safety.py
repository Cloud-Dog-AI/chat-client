# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""W28F-948 multimodal upload edge-safety policy.

The chat-client is a PS-94 *proxy-relay*: durable storage, the authoritative
cumulative per-tenant quota, and virus scanning are owned by file-mcp (PS-94
FT-07). This module enforces the gate the chat-client edge is responsible for:

* a per-media-type maximum upload size (image / video / audio / pdf), so a
  multimodal upload is bounded appropriately at the edge (CSR-025 / CSR-026),
* operator-tunable per-tenant overrides (config / Vault-bound), and
* a structured rejection decision the caller turns into an HTTP 413.

The functions here are pure so they can be unit-tested without FastAPI; the
route layer supplies the resolved config block and base size limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_AUDIO = "audio"
MEDIA_PDF = "pdf"
MEDIA_OTHER = "other"

#: Conservative platform defaults (operator-tunable via config / Vault).
DEFAULT_MAX_BYTES: dict[str, int] = {
    MEDIA_IMAGE: 50 * 1024 * 1024,    # 50 MB
    MEDIA_VIDEO: 500 * 1024 * 1024,   # 500 MB
    MEDIA_AUDIO: 100 * 1024 * 1024,   # 100 MB
    MEDIA_PDF: 50 * 1024 * 1024,      # 50 MB
}

_EXT_MEDIA: dict[str, str] = {
    "png": MEDIA_IMAGE, "jpg": MEDIA_IMAGE, "jpeg": MEDIA_IMAGE, "gif": MEDIA_IMAGE,
    "webp": MEDIA_IMAGE, "bmp": MEDIA_IMAGE, "svg": MEDIA_IMAGE, "tiff": MEDIA_IMAGE,
    "mp4": MEDIA_VIDEO, "webm": MEDIA_VIDEO, "mov": MEDIA_VIDEO, "mkv": MEDIA_VIDEO, "avi": MEDIA_VIDEO,
    "mp3": MEDIA_AUDIO, "wav": MEDIA_AUDIO, "ogg": MEDIA_AUDIO, "m4a": MEDIA_AUDIO, "flac": MEDIA_AUDIO,
    "pdf": MEDIA_PDF,
}

#: Stable error code surfaced in the structured rejection (CSR-033).
ERROR_OVERSIZE = "UPLOAD_MEDIA_OVERSIZE"


@dataclass(frozen=True)
class UploadDecision:
    """Outcome of evaluating one upload against the edge policy."""

    allowed: bool
    media_type: str
    size_bytes: int
    max_bytes: int
    error_code: str = ""
    message: str = ""


def classify_media_type(filename: str | None, content_type: str | None) -> str:
    """Classify an upload into a media kind from its MIME type, then extension."""
    ct = (content_type or "").strip().lower()
    if ct.startswith("image/"):
        return MEDIA_IMAGE
    if ct.startswith("video/"):
        return MEDIA_VIDEO
    if ct.startswith("audio/"):
        return MEDIA_AUDIO
    if ct == "application/pdf":
        return MEDIA_PDF
    name = (filename or "").strip().lower()
    if "." in name:
        ext = name.rsplit(".", 1)[-1]
        if ext in _EXT_MEDIA:
            return _EXT_MEDIA[ext]
    return MEDIA_OTHER


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        ivalue = int(value)
        return ivalue if ivalue > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_max_bytes(
    config_block: Mapping[str, Any] | None,
    tenant_id: str | None,
    media_type: str,
    base_max_bytes: int,
) -> int:
    """Resolve the max upload size for a (tenant, media_type).

    Precedence: per-tenant override -> global multimodal override -> platform
    default for the media type -> the service base ``max_upload_bytes`` (used
    for ``other`` and as the final floor). The base limit is honoured as a
    minimum ceiling so a multimodal cap never silently undercuts the base.
    """
    block = config_block if isinstance(config_block, Mapping) else {}

    tenant_override: int | None = None
    tenants = block.get("tenants")
    if isinstance(tenants, Mapping) and tenant_id:
        tblock = tenants.get(tenant_id)
        if isinstance(tblock, Mapping):
            tmax = tblock.get("max_bytes")
            if isinstance(tmax, Mapping):
                tenant_override = _as_int(tmax.get(media_type))

    global_override: int | None = None
    gmax = block.get("max_bytes")
    if isinstance(gmax, Mapping):
        global_override = _as_int(gmax.get(media_type))

    default_for_kind = DEFAULT_MAX_BYTES.get(media_type)

    resolved = tenant_override or global_override or default_for_kind
    if resolved is None:
        # Unknown / "other" media -> fall back to the service base limit.
        return max(1, int(base_max_bytes))
    return max(int(resolved), int(base_max_bytes))


def evaluate_upload(
    *,
    filename: str | None,
    content_type: str | None,
    size_bytes: int,
    tenant_id: str | None,
    config_block: Mapping[str, Any] | None,
    base_max_bytes: int,
) -> UploadDecision:
    """Evaluate an upload against the per-media-type edge size policy."""
    media_type = classify_media_type(filename, content_type)
    max_bytes = resolve_max_bytes(config_block, tenant_id, media_type, base_max_bytes)
    if size_bytes > max_bytes:
        return UploadDecision(
            allowed=False,
            media_type=media_type,
            size_bytes=size_bytes,
            max_bytes=max_bytes,
            error_code=ERROR_OVERSIZE,
            message=(
                f"{media_type} upload of {size_bytes} bytes exceeds the "
                f"{max_bytes}-byte limit for this media type."
            ),
        )
    return UploadDecision(
        allowed=True,
        media_type=media_type,
        size_bytes=size_bytes,
        max_bytes=max_bytes,
    )
