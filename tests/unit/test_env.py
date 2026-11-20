"""Dev/prod isolation: the dev prefix guard, prefixed storage, and the CLI's dev refusals."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from jobpulse import cli
from jobpulse.config import load_pipeline
from jobpulse.env import (
    EnvError,
    PrefixedStorage,
    resolve_env,
    validate_dev_db,
    validate_dev_prefix,
)
from jobpulse.ingest.storage import LocalStorage

PROD = ["raw/", "snapshots/", "ops/"]


BAD_PREFIXES = [
    "", "  ", "/", "dev", "/dev/", "../x/", "dev/../raw/",
    "raw/", "raw/dev/", "ops/", "ops/x/", "snapshots/", "snapshots/dev/",
]  # fmt: skip


@pytest.mark.parametrize("prefix", BAD_PREFIXES)
def test_bad_dev_prefixes_are_refused(prefix: str) -> None:
    with pytest.raises(EnvError):
        validate_dev_prefix(prefix, PROD)


@pytest.mark.parametrize("prefix", ["dev/", "demo/airflow/", "dev-1/", "r/", "ops-dev/"])
def test_good_dev_prefixes(prefix: str) -> None:
    assert validate_dev_prefix(prefix, PROD) == prefix


def test_configured_dev_prefix_is_valid() -> None:
    cfg = load_pipeline().storage
    assert validate_dev_prefix(cfg.dev_prefix, cfg.prod_prefixes) == cfg.dev_prefix
    assert set(cfg.prod_prefixes) == set(PROD)


def test_prefixed_storage_never_writes_outside_its_prefix(tmp_path: Path) -> None:
    inner = LocalStorage(tmp_path)
    s = PrefixedStorage(inner, "dev/", PROD)
    s.put("raw/a/b.json", b"{}", content_type="application/json")
    s.put("snapshots/x/_AUTHORITATIVE.json", b"{}", content_type="application/json")
    s.put("ops/t/dt=1/a.json", b"{}", content_type="application/json")
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == [
        "dev/ops/t/dt=1/a.json",
        "dev/raw/a/b.json",
        "dev/snapshots/x/_AUTHORITATIVE.json",
    ]
    assert s.get("raw/a/b.json") == b"{}"
    assert inner.get("raw/a/b.json") is None  # the prod-shaped key is untouched
    assert s.list_keys("snapshots/") == ["snapshots/x/_AUTHORITATIVE.json"]


def test_prefixed_storage_refuses_a_prod_prefix(tmp_path: Path) -> None:
    with pytest.raises(EnvError):
        PrefixedStorage(LocalStorage(tmp_path), "raw/", PROD)


def test_resolve_env() -> None:
    assert resolve_env(None, {}) == "prod"
    assert resolve_env(None, {"JOBPULSE_ENV": "dev"}) == "dev"
    assert resolve_env("dev", {"JOBPULSE_ENV": "prod"}) == "dev"
    with pytest.raises(EnvError):
        resolve_env("staging", {})


@pytest.mark.parametrize("db", [None, "", "md:", "md:jobpulse", "motherduck:x", "s3://b/x.duckdb"])
def test_dev_refuses_remote_or_missing_db(db: str | None) -> None:
    with pytest.raises(EnvError):
        validate_dev_db(db)


def test_dev_accepts_a_local_file() -> None:
    assert validate_dev_db("/data/demo.duckdb") == Path("/data/demo.duckdb")


class FakeS3:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket


def test_cli_storage_dev_uses_prefix_prod_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBPULSE_BUCKET", "some-bucket")
    monkeypatch.setattr(cli, "S3Storage", FakeS3)
    dev = cli._storage(argparse.Namespace(local_dir=None, env="dev"))
    prod = cli._storage(argparse.Namespace(local_dir=None, env="prod"))
    assert isinstance(dev, PrefixedStorage)
    assert dev.prefix == "dev/"
    assert isinstance(prod, FakeS3)


def test_cli_dev_refuses_motherduck_and_never_reads_the_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("dev must not touch the token or a remote database")

    monkeypatch.setattr(cli, "load_env_keys", boom)
    monkeypatch.setattr(cli, "connect_warehouse", boom)
    monkeypatch.setenv("MOTHERDUCK_TOKEN", "should-not-be-used")
    for argv in (
        ["--env", "dev", "build-silver"],
        ["--env", "dev", "build-gold", "--db", "md:jobpulse"],
        ["--env", "dev", "extract", "--dry-run"],
        ["--env", "dev", "load-bronze", "--local-dir", "/nonexistent"],
    ):
        assert cli.main(argv) == cli.EXIT_USAGE, argv
    err = capsys.readouterr().err
    assert "never connects to MotherDuck" in err
    assert "refuses a remote database" in err


def test_cli_env_var_selects_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBPULSE_ENV", "dev")
    monkeypatch.setattr(
        cli, "load_env_keys", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError)
    )
    assert cli.main(["build-silver"]) == cli.EXIT_USAGE  # no --db in dev


def test_cli_unknown_env_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBPULSE_ENV", "staging")
    assert cli.main(["build-silver"]) == cli.EXIT_USAGE


def test_cli_reads_the_bucket_from_dotenv_when_not_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBPULSE_BUCKET", raising=False)
    asked: list[list[str]] = []

    def fake_load(names: Any, *_a: Any, **_k: Any) -> list[str]:
        asked.append(list(names))
        monkeypatch.setenv("JOBPULSE_BUCKET", "bucket-from-dotenv")
        return list(names)

    monkeypatch.setattr(cli, "load_env_keys", fake_load)
    monkeypatch.setattr(cli, "S3Storage", FakeS3)
    storage = cli._storage(argparse.Namespace(local_dir=None, env="prod"))
    assert asked == [["JOBPULSE_BUCKET"]]
    assert isinstance(storage, FakeS3)
    assert storage.bucket == "bucket-from-dotenv"


def test_cli_without_bucket_anywhere_still_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBPULSE_BUCKET", raising=False)
    monkeypatch.setattr(cli, "load_env_keys", lambda *_a, **_k: [])
    with pytest.raises(SystemExit, match="JOBPULSE_BUCKET"):
        cli._storage(argparse.Namespace(local_dir=None, env="prod"))
