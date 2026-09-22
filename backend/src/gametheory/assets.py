import hashlib
import json
import logging
from functools import lru_cache
from urllib.parse import urlparse

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from fastapi import HTTPException

from gametheory.config import get_settings

logger = logging.getLogger(__name__)
ALLOWED_MEDIA = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
}


@lru_cache
def blob_service() -> BlobServiceClient:
    settings = get_settings()
    if settings.blob_connection_string:
        client = BlobServiceClient.from_connection_string(settings.blob_connection_string)
        endpoint = urlparse(client.url)
        if (
            settings.cloud != "commercial"
            or endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "localhost"}
        ):
            raise ValueError("Connection strings are supported only for explicit local Azurite")
        return client
    if not settings.blob_url:
        raise HTTPException(503, "Blob storage is not configured")
    if urlparse(settings.blob_url).scheme != "https" or not settings.profile.storage_scope:
        raise HTTPException(503, "Blob storage requires HTTPS and an explicitly supported audience")
    return BlobServiceClient(
        settings.blob_url,
        credential=DefaultAzureCredential(authority=settings.profile.authority),
        audience=settings.profile.storage_scope.removesuffix("/.default"),
    )


def validate_upload(data: bytes, media: str) -> str:
    if media not in ALLOWED_MEDIA:
        raise HTTPException(422, "Unsupported asset type")
    signatures = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        "application/pdf": data.startswith(b"%PDF-"),
    }
    if media in signatures and not signatures[media]:
        raise HTTPException(422, "File bytes do not match the declared type")
    if media.startswith("text/") or media == "application/json":
        try:
            text = data.decode("utf-8")
            if "\x00" in text:
                raise ValueError("Binary text")
            if media == "application/json":
                json.loads(text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(422, "Text/JSON assets must contain valid UTF-8 content") from exc
    if not data:
        raise HTTPException(422, "Empty assets are not supported")
    return hashlib.sha256(data).hexdigest()


def put_blob(key: str, data: bytes, media: str) -> None:
    client = blob_service().get_blob_client(get_settings().blob_container, key)
    try:
        client.upload_blob(
            data,
            overwrite=False,
            content_settings=ContentSettings(content_type=media),
            connection_timeout=10,
            read_timeout=60,
        )
    except AzureError as exc:
        logger.error(
            "Asset upload failed", extra={"blob_key": key, "error_type": type(exc).__name__}
        )
        raise HTTPException(503, "Asset upload failed; no usable asset was published") from exc


def read_blob(key: str, max_bytes: int | None = None) -> bytes:
    try:
        return (
            blob_service()
            .get_blob_client(get_settings().blob_container, key)
            .download_blob(
                offset=0,
                length=max_bytes + 1 if max_bytes is not None else None,
                connection_timeout=10,
                read_timeout=60,
            )
            .readall()
        )
    except AzureError as exc:
        logger.error("Asset read failed", extra={"blob_key": key, "error_type": type(exc).__name__})
        raise HTTPException(503, "Asset storage is unavailable") from exc
