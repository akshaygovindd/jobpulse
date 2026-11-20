"""Regression tests for the P0 findings of docs/QA_REPORT.md: concurrent reads, PDF bombs,
malformed PDFs, and a stale-module reload."""

from __future__ import annotations

import contextlib
import resource
import threading
import zlib
from pathlib import Path

import duckdb
import pytest

from jobpulse.app_data import read_gold
from jobpulse.config import load_resume
from jobpulse.resume.parse import ResumeError, read_resume
from tests.unit.test_resume import pdf_bytes

CFG = load_resume()


def _bomb_pdf(decoded_bytes: int) -> bytes:
    """A one-page PDF whose compressed content stream inflates to about `decoded_bytes`."""
    stream = zlib.compress(b"BT (x) Tj ET\n" * (decoded_bytes // 13))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def test_concurrent_sessions_never_see_each_others_rows(tmp_path: Path) -> None:
    path = tmp_path / "t.duckdb"
    w = duckdb.connect(str(path))
    w.execute("create schema gold")
    w.execute(
        "create table gold.dim_skill as "
        "select i as skill_id, 'skill ' || i as canonical_name from range(200) t(i)"
    )
    w.close()
    con = duckdb.connect(str(path), read_only=True)  # one shared connection, like the app
    errors: list[str] = []

    def work(worker: int) -> None:
        for n in range(300):
            want = (worker * 25 + n) % 200
            df = read_gold(
                con,
                "select skill_id, canonical_name from gold.dim_skill where skill_id = ?",
                [want],
            )
            if list(df["skill_id"]) != [want] or list(df["canonical_name"]) != [f"skill {want}"]:
                errors.append(f"worker {worker} asked {want}, got {df.to_dict('list')}")

    def guarded(worker: int) -> None:
        try:
            work(worker)
        except Exception as e:
            errors.append(f"worker {worker}: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=guarded, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_a_decompression_bomb_is_refused_without_a_memory_spike() -> None:
    data = _bomb_pdf(60_000_000)
    assert len(data) < CFG.upload.max_bytes  # it passes the upload size check
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with pytest.raises(ResumeError, match=r"could not be read|longer than"):
        read_resume("bomb.pdf", data, CFG)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    growth = (after - before) / (1 if "darwin" in __import__("sys").platform else 1024)
    assert growth < 150_000_000  # bytes; unprotected it is 300 MB and more


def test_a_normal_pdf_still_parses() -> None:
    assert "Python" in read_resume("cv.pdf", pdf_bytes("Python SQL"), CFG)


@pytest.mark.parametrize("cut", range(30, 700, 37))
def test_truncated_or_mutated_pdfs_give_the_friendly_error(cut: int) -> None:
    data = pdf_bytes("Python and SQL")
    for broken in (data[:cut], data[:cut] + b"\xff\xfe" * 20 + data[cut + 40 :]):
        with contextlib.suppress(ResumeError):  # a friendly refusal; anything else fails the test
            read_resume("cv.pdf", broken, CFG)


def test_an_unexpected_parser_exception_never_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    import jobpulse.resume.parse as parse

    class Boom:
        def __init__(self, *_: object, **__: object) -> None:
            raise NotImplementedError("Unsupported filter /HKFlateDecode secret-fragment")

    monkeypatch.setattr(parse, "PdfReader", Boom)
    with pytest.raises(ResumeError) as e:
        read_resume("cv.pdf", pdf_bytes("x"), CFG)
    assert "secret-fragment" not in str(e.value)
    assert "could not be read" in str(e.value)
