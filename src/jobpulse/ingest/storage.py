"""Object storage abstraction: S3 in production, a local directory for dev runs."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class StorageError(Exception):
    pass


class Storage(Protocol):
    def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        content_encoding: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def list_keys(self, prefix: str) -> list[str]: ...


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gzip_deterministic(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def put_json(storage: Storage, key: str, obj: Any) -> None:
    storage.put(
        key,
        json.dumps(obj, indent=2, sort_keys=True, default=str).encode(),
        content_type="application/json",
    )


def get_json(storage: Storage, key: str) -> Any | None:
    raw = storage.get(key)
    return None if raw is None else json.loads(raw)


class S3Storage:
    def __init__(self, bucket: str, client: Any | None = None) -> None:
        self.bucket = bucket
        self._s3 = client or boto3.client("s3")

    def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        content_encoding: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
            "Metadata": dict(metadata or {}),
        }
        if content_encoding:
            kwargs["ContentEncoding"] = content_encoding
        try:
            self._s3.put_object(**kwargs)
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"put {key}: {e}") from e

    def get(self, key: str) -> bytes | None:
        try:
            resp = self._s3.get_object(Bucket=self.bucket, Key=key)
            return bytes(resp["Body"].read())
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise StorageError(f"get {key}: {e}") from e
        except BotoCoreError as e:
            raise StorageError(f"get {key}: {e}") from e

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"list {prefix}: {e}") from e
        return sorted(keys)


class LocalStorage:
    """Writes the same key layout under a local directory (for dev runs without AWS)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        content_encoding: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        path = self.root / key
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        except OSError as e:
            raise StorageError(f"put {key}: {e}") from e

    def get(self, key: str) -> bytes | None:
        path = self.root / key
        return path.read_bytes() if path.is_file() else None

    def list_keys(self, prefix: str) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and p.relative_to(self.root).as_posix().startswith(prefix)
        )
