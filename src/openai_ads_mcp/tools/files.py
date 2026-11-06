"""Uploads: creative images, the advertiser icon, and custom-audience files.

Local files can be uploaded only from inside OPENAI_ADS_UPLOAD_DIR, so an agent
can't send arbitrary files from the machine to OpenAI.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from ..client import OpenAIAdsClient
from ..config import Config
from ._common import check_choice

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
AUDIENCE_TYPES = {".csv": "text/csv", ".txt": "text/plain"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_AUDIENCE_BYTES = 200 * 1024 * 1024


def resolve_upload(config: Config, file_path: str, allowed: dict[str, str], max_bytes: int) -> Path:
    """Resolve a local path and check it is a permitted file inside OPENAI_ADS_UPLOAD_DIR."""
    if config.upload_dir is None:
        raise PermissionError(
            "Local uploads are off. Set OPENAI_ADS_UPLOAD_DIR to the folder files may be "
            "uploaded from, or upload an image by image_url instead."
        )
    root = config.upload_dir.resolve()
    raw = Path(file_path).expanduser()
    path = (raw if raw.is_absolute() else root / raw).resolve()
    if not path.is_relative_to(root):
        raise PermissionError(f"{file_path} is outside OPENAI_ADS_UPLOAD_DIR ({root}).")
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    if path.suffix.lower() not in allowed:
        raise ValueError(
            f"Unsupported file type {path.suffix!r}; use {', '.join(sorted(allowed))}."
        )
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{path.name} is {size} bytes; the limit here is {max_bytes} bytes.")
    if size == 0:
        raise ValueError(f"{path.name} is empty.")
    return path


def register(mcp, client: OpenAIAdsClient) -> None:
    @mcp.tool()
    def openai_ads_upload_image(
        image_url: str | None = None,
        file_path: str | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """Upload an image and get a reusable file_id for ad creatives or the advertiser icon.

        Pass image_url (a public https image; for purpose='account_favicon' a website URL
        also works and OpenAI picks its icon) or file_path (JPEG, PNG or WebP inside
        OPENAI_ADS_UPLOAD_DIR, up to 20 MB). Ad images should be at least 640x640 (square
        for multi-image ads); icons at least 128x128. purpose: omit for ad creatives, or
        'account_favicon' for openai_ads_update_account_brand."""
        if bool(image_url) == bool(file_path):
            raise ValueError("Pass exactly one of image_url or file_path.")
        kind = check_choice(purpose, ("account_favicon",), "purpose") if purpose else None
        if image_url:
            if not image_url.startswith(("https://", "http://")):
                raise ValueError("image_url must be an http(s) URL.")
            body: dict[str, Any] = {"image_url": image_url}
            if kind:
                body["purpose"] = kind
            return client.request("POST", "/upload", json_body=body, op="upload_image")
        path = resolve_upload(client.config, file_path or "", IMAGE_TYPES, MAX_IMAGE_BYTES)
        form = {"purpose": kind} if kind else {}
        files = {"file": (path.name, path.read_bytes(), IMAGE_TYPES[path.suffix.lower()])}
        return client.request(
            "POST",
            "/upload",
            files=files,
            form=form,
            op="upload_image",
            audit_body={"file": path.name, "bytes": path.stat().st_size, **form},
        )

    @mcp.tool()
    def openai_ads_upload_audience_file(file_path: str) -> dict[str, Any]:
        """Upload a customer list (UTF-8 CSV with a header row, or TXT with one identifier
        per line) from OPENAI_ADS_UPLOAD_DIR for a custom audience. Up to 200 MB.

        Returns file_id plus the filename, mimetype and file_size that
        openai_ads_create_audience needs. Only upload first-party data you have the
        rights, notices and consents to use for ads (never broker data). The file goes
        straight to OpenAI; its contents never pass through the chat or the audit log."""
        path = resolve_upload(client.config, file_path, AUDIENCE_TYPES, MAX_AUDIENCE_BYTES)
        mime = AUDIENCE_TYPES[path.suffix.lower()] or mimetypes.guess_type(path.name)[0]
        size = path.stat().st_size
        out = client.request(
            "POST",
            "/uploads",
            files={"file": (path.name, path.read_bytes(), mime)},
            form={"purpose": "custom_audience"},
            op="upload_audience_file",
            audit_body={"file": path.name, "bytes": size, "purpose": "custom_audience"},
        )
        if isinstance(out, dict):
            out = {**out, "filename": path.name, "mimetype": mime, "file_size": size}
        return out
