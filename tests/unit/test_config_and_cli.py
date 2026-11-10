from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobpulse import cli
from jobpulse.config import load_employers, load_pipeline, load_sources
from jobpulse.validate import dump_employers_yaml
from tests.conftest import TickingClock


def test_employers_yaml_counts() -> None:
    f = load_employers()
    assert len(f.employers) == 43
    assert sum(e.source == "greenhouse" for e in f.employers) == 35
    assert sum(e.source == "lever" for e in f.employers) == 3
    assert sum(e.source == "ashby" for e in f.employers) == 5
    assert len(f.skipped) == 7


def test_employers_yaml_roundtrip(tmp_path: Path) -> None:
    f = load_employers()
    out = tmp_path / "employers.yaml"
    out.write_text(dump_employers_yaml(f))
    assert load_employers(out) == f


def test_pipeline_and_sources_load() -> None:
    assert load_pipeline().freshness.max_age_hours == 30
    sources = load_sources().by_id()
    assert sources["greenhouse"].enabled
    assert sources["lever"].enabled
    assert sources["ashby"].enabled
    assert "adzuna" not in sources  # deferred: see docs/ROADMAP.md "Later"


@pytest.mark.parametrize("bad", ["2026-11-09", "2026-11-11", "not-a-date"])
def test_cli_ingest_rejects_non_today(
    bad: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = TickingClock(datetime(2026, 11, 10, 9, 0, tzinfo=UTC))
    code = cli.main(["ingest", "--snapshot-date", bad, "--local-dir", str(tmp_path)], clock=clock)
    assert code == cli.EXIT_USAGE
    assert "error: snapshot_date" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_cli_ingest_today_local_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Today's date is accepted; one bad board fails, the run still exits 0."""
    from tests.unit.test_ingest_idempotency import FakeConnector

    monkeypatch.setattr(
        cli,
        "build_connectors",
        lambda http, cfg: {
            "greenhouse": FakeConnector(),
            "lever": FakeConnector("lever"),
            "ashby": FakeConnector("ashby"),
        },
    )
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    clock = TickingClock(datetime(2026, 11, 10, 9, 0, tzinfo=UTC))
    data = tmp_path / "data"
    code = cli.main(
        ["ingest", "--snapshot-date", "2026-11-10", "--local-dir", str(data)], clock=clock
    )
    assert code == cli.EXIT_OK
    pointers = list((data / "snapshots").rglob("_AUTHORITATIVE.json"))
    assert len(pointers) == 43
    assert (data / "ops" / "source_registry" / "latest.json").is_file()
    assert "Complete snapshots: **43/43**" in summary.read_text()

    code = cli.main(["check-freshness", "--local-dir", str(data)], clock=clock)
    assert code == cli.EXIT_OK


def test_cli_ingest_low_success_ratio_exits_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.test_ingest_idempotency import FakeConnector

    monkeypatch.setattr(
        cli,
        "build_connectors",
        lambda http, cfg: {
            "greenhouse": FakeConnector(fail_after=0),
            "lever": FakeConnector("lever", fail_after=0),
            "ashby": FakeConnector("ashby", fail_after=0),
        },
    )
    clock = TickingClock(datetime(2026, 11, 10, 9, 0, tzinfo=UTC))
    code = cli.main(["ingest", "--local-dir", str(tmp_path)], clock=clock)
    assert code == cli.EXIT_ALERT
    # Ops and the report are still written so the failure is diagnosable.
    assert list((tmp_path / "ops" / "run_reports").rglob("*.json"))


def test_cli_freshness_stale_exits_alert(tmp_path: Path) -> None:
    clock = TickingClock(datetime(2026, 11, 10, 9, 0, tzinfo=UTC))
    assert cli.main(["check-freshness", "--local-dir", str(tmp_path)], clock=clock) == 1
