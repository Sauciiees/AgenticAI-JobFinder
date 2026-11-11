"""Where uploaded CVs live.

LocalStorage keeps files in a folder (local runs, tests). S3Storage uses any S3-compatible private
bucket: Cloudflare R2, Supabase Storage (S3 endpoint) or AWS S3. Keys look like "user_7/cv.pdf".
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

import config


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...
    def signed_url(self, key: str, filename: str) -> str | None: ...
    def local_copy(self, key: str) -> str | None: ...


def _check_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in parts) or "\\" in key:
        raise ValueError(f"Invalid storage key: {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _check_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError(f"Storage key escapes the storage folder: {key!r}")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        folder = self._path(prefix.rstrip("/"))
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)

    def signed_url(self, key: str, filename: str) -> str | None:
        return None  # the API streams local files itself

    def local_copy(self, key: str) -> str | None:
        path = self._path(key)
        return str(path) if path.is_file() else None


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        if not config.S3_BUCKET:
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET (and S3_ENDPOINT_URL, keys).")
        self.bucket = config.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            region_name=config.S3_REGION,
            aws_access_key_id=config.S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=config.S3_SECRET_ACCESS_KEY or None,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=_check_key(key))
        except self.client.exceptions.NoSuchKey:
            return None
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def delete_prefix(self, prefix: str) -> None:
        prefix = _check_key(prefix.rstrip("/")) + "/"
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})

    def signed_url(self, key: str, filename: str) -> str | None:
        safe = filename.replace('"', "")
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": _check_key(key),
                    "ResponseContentDisposition": f'inline; filename="{safe}"'},
            ExpiresIn=config.SIGNED_URL_SECONDS,
        )

    def local_copy(self, key: str) -> str | None:
        data = self.get(key)
        if data is None:
            return None
        suffix = os.path.splitext(key)[1]
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage() if config.STORAGE_BACKEND == "s3" else LocalStorage(config.UPLOAD_DIR)
    return _storage


def user_prefix(user_id: int) -> str:
    return f"user_{user_id}"
