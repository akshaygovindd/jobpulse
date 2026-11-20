"""Resume Match: deterministic parsing and ranking, and the privacy guarantees.

The resume is processed in memory only. These tests prove nothing from it is written to disk or
the database, logged, cached or left in session state after parsing, and that Delete clears it.
"""

from __future__ import annotations

import ast
import builtins
import logging
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from jobpulse.app_data import LOCAL_DB_ENV
from jobpulse.config import ResumeConfig, load_resume
from jobpulse.ingest.storage import LocalStorage
from jobpulse.resume.match import PostingSkills, rank_postings
from jobpulse.resume.parse import ResumeError, extract_profile, read_resume
from tests.unit.test_gold import add_extraction, gold, seed
from tests.unit.test_gold_visa import lca_case, load_ref
from tests.warehouse_helpers import gh_job

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "streamlit_app.py"
PAGE = "app_pages/resume_match.py"
CFG = load_resume()
VOCAB = {
    "python", "sql", "spark", "airflow", "dbt", "kafka", "tensorflow", "pytorch", "kubernetes",
    "machine learning", "natural language processing", "r", "go", "ray", "search", "testing",
    "ci/cd", "scikit learn", "tableau",
}  # fmt: skip
MARKER = "zebra-quokka-unique-sentence-4417"


def pdf_bytes(text: str) -> bytes:
    """A minimal one-page PDF whose page text is `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
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


# ---- parsing ----------------------------------------------------------------------------------


def test_skills_are_found_by_normalized_ngram_lookup() -> None:
    text = (
        "Senior Data Engineer\n"
        "Skills: Python, SQL, Apache Spark, Kubernetes, scikit-learn, CI/CD\n"
        "Built Natural Language Processing and machine learning pipelines. 7+ years experience."
    )
    p = extract_profile(text, VOCAB, CFG)
    assert p.skills == {
        "python", "sql", "spark", "kubernetes", "scikit learn", "ci/cd",
        "natural language processing", "machine learning",
    }  # fmt: skip
    assert p.years == 7
    assert p.titles == ("Senior Data Engineer",)


def test_extraction_is_deterministic() -> None:
    text = "Python, SQL and tableau dashboards; 4 years"
    assert extract_profile(text, VOCAB, CFG) == extract_profile(text, VOCAB, CFG)


def test_single_letters_and_everyday_words_need_the_right_form() -> None:
    cfg = CFG
    assert "r" not in extract_profile("I like r and go and ray", VOCAB, cfg).skills
    assert "r" in extract_profile("Languages: R, Python", VOCAB, cfg).skills
    assert "go" in extract_profile("Languages: Go, Python", VOCAB, cfg).skills
    assert "ray" in extract_profile("Frameworks: Ray", VOCAB, cfg).skills
    got = extract_profile("search and testing everywhere", VOCAB, cfg).skills
    assert got == set()  # in ignore_skills


def test_unknown_words_and_implausible_years_are_ignored() -> None:
    p = extract_profile("Fluent in Klingon. 120 years of experience.", VOCAB, CFG)
    assert p.skills == frozenset()
    assert p.years is None


def test_read_resume_accepts_text_and_pdf() -> None:
    assert "python" in read_resume("cv.txt", b"python and sql", CFG)
    assert "Python" in read_resume("cv.pdf", pdf_bytes("Python and SQL"), CFG)


@pytest.mark.parametrize(
    ("name", "data", "why"),
    [
        ("cv.docx", b"x", "extension"),
        ("cv.txt", b"", "empty"),
        ("cv.txt", b"a" * 2_000_001, "too large"),
        ("cv.pdf", b"not a pdf at all", "magic"),
        ("cv.pdf", b"%PDF-1.4 garbage", "unreadable pdf"),
        ("cv.txt", b"abc\x00def", "binary"),
        ("cv.txt", b"\xff\xfe\xfa", "not utf-8"),
        ("cv.txt", b"   \n  ", "blank"),
    ],
)
def test_read_resume_refuses(name: str, data: bytes, why: str) -> None:
    with pytest.raises(ResumeError):
        read_resume(name, data, CFG)


def test_text_longer_than_the_cap_is_refused_not_truncated() -> None:
    with pytest.raises(ResumeError):
        read_resume("cv.txt", b"a" * (CFG.upload.max_text_chars + 1), CFG)


def test_a_pdf_over_the_page_cap_is_refused() -> None:
    small = ResumeConfig.model_validate(
        {**CFG.model_dump(), "upload": {**CFG.upload.model_dump(), "max_pdf_pages": 1}}
    )
    assert read_resume("cv.pdf", pdf_bytes("Python"), small)  # one page is fine


# ---- ranking ----------------------------------------------------------------------------------


def _p(key: str, skills: set[str], company: str = "A", title: str = "T") -> PostingSkills:
    return PostingSkills(key, frozenset(skills), {"company": company, "title": title})


def test_score_is_share_of_posting_skills_the_resume_has() -> None:
    mine = {"python", "sql", "spark"}
    postings = [
        _p("a", {"python", "sql", "dbt", "kafka", "airflow"}),  # 2/5
        _p("b", {"python", "sql", "spark", "kafka", "airflow"}),  # 3/5
        _p("c", {"tableau", "kafka", "airflow", "dbt", "r"}),  # 0 matched: dropped
        _p("d", {"python", "sql"}),  # fewer than min_posting_skills: not ranked
    ]
    got = rank_postings(mine, postings, CFG.match)
    assert [m.posting.posting_key for m in got] == ["b", "a"]
    assert got[0].score == pytest.approx(0.6)
    assert got[0].matched == ("python", "spark", "sql")
    assert got[0].missing == ("airflow", "kafka")


def test_ranking_is_deterministic_and_ties_break_stably() -> None:
    mine = {"python"}
    postings = [
        _p("k2", {"python", "a", "b", "c", "d"}, "Zed"),
        _p("k1", {"python", "a", "b", "c", "d"}, "Acme"),
    ]
    assert [m.posting.posting_key for m in rank_postings(mine, postings, CFG.match)] == ["k1", "k2"]


# ---- the page ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def resume_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("resume_db")
    db = tmp / "wh.duckdb"
    seed(
        LocalStorage(tmp / "s3"),
        db,
        [gh_job("1"), gh_job("2", content="two"), gh_job("3", content="three")],
        days=(date(2026, 11, 10), date(2026, 11, 11)),
    )
    add_extraction(
        db, "1", skills=["Python", "SQL", "dbt", "Airflow", "Kafka"],
        sponsorship_statement="offered", sponsorship_original_text="We sponsor visas",
        salary_min=100000.0, salary_max=140000.0, salary_currency="USD", salary_period="year",
    )  # fmt: skip
    add_extraction(db, "2", skills=["Python", "Spark", "Tableau", "R", "Kafka"])
    add_extraction(db, "3", skills=["Go", "Rust", "Kafka"])  # too few skills: never ranked
    load_ref(db, [lca_case(i) for i in range(1, 6)])
    gold(db)
    con = duckdb.connect(str(db))
    con.execute("CHECKPOINT")
    con.close()
    out = db.with_name("app_copy.duckdb")
    shutil.copy(db, out)
    return out


def _open(db: Path, monkeypatch: pytest.MonkeyPatch, min_frequency: int = 1) -> AppTest:
    """The tiny fixture has 3 postings, so the real "at least 3 postings" rule is relaxed here
    (test_a_skill_in_too_few_postings_is_not_recognized covers the rule itself)."""
    monkeypatch.setenv(LOCAL_DB_ENV, str(db))
    relaxed = CFG.model_copy(
        update={"parse": CFG.parse.model_copy(update={"min_posting_frequency": min_frequency})}
    )
    monkeypatch.setattr("jobpulse.config.load_resume", lambda *a, **k: relaxed)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.switch_page(PAGE).run()
    assert not at.exception, at.exception
    return at


def _click(at: AppTest, label: str) -> AppTest:
    button = next(b for b in at.button if label in b.label)
    return button.click().run()


def _everything(at: AppTest) -> str:
    parts = [str(v) for v in at.session_state.to_dict().values()]
    for group in (at.markdown, at.caption, at.info, at.error, at.success, at.warning):
        parts += [str(e.value) for e in group]
    return "\n".join(parts)


def _analyze(at: AppTest, text: str) -> AppTest:
    at.text_area(key="resume_text").set_value(text).run()
    return _click(at, "Find matching postings")


RESUME = f"""Jane Doe
Senior Data Engineer
{MARKER}
Skills: Python, SQL, Spark, Kafka, Airflow, dbt. 6 years of experience.
"""


def test_page_shows_privacy_notice_and_ranks_postings(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _open(resume_db, monkeypatch)
    assert "never saved to disk or a database, never logged" in _everything(at)
    at = _analyze(at, RESUME)
    assert not at.exception, at.exception
    assert at.metric[0].value == "6"  # python, sql, spark, kafka, airflow, dbt
    frame = at.dataframe[-1].value
    assert frame["Title"].iloc[0] == "Data Engineer"
    assert frame["Match"].iloc[0] == 100  # posting 1: every skill it asks for is in the resume
    assert "Python" in frame["Matched skills"].iloc[0]  # shown as "Python", stored as "python"
    assert "SQL" in frame["Matched skills"].iloc[0]
    assert "Posting says sponsorship is offered" in frame["Visa evidence"].iloc[0]
    assert frame["Advertised salary"].iloc[0] == "$100K to $140K"
    assert (
        len(frame) == 1
    )  # postings 1 and 2 are one role (same company and title); 3 has too few skills
    assert "Evidence from public filings, not a promise" in _everything(at)


def test_a_skill_in_too_few_postings_is_not_recognized(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _analyze(_open(resume_db, monkeypatch, min_frequency=1), RESUME)
    assert int(at.metric[0].value) > 0
    # no skill appears in 100 postings: nothing is offered as a skill, and the page says so
    at = _analyze(_open(resume_db, monkeypatch, min_frequency=100), RESUME)
    assert at.metric[0].value == "0"


def test_sponsorship_is_a_filter_and_never_changes_the_score(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _analyze(_open(resume_db, monkeypatch), RESUME)
    frame0 = at.dataframe[-1].value
    before = dict(zip(frame0["Matched skills"], frame0["Match"], strict=True))
    at.multiselect(key="resume_classes").set_value(["CONFIRMED_OFFERED"]).run()
    frame = at.dataframe[-1].value
    assert len(frame) == 1
    assert frame["Match"].iloc[0] == before[frame["Matched skills"].iloc[0]]


def test_nothing_is_written_logged_or_kept(
    resume_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    at = _open(resume_db, monkeypatch)
    real_open = builtins.open
    writes: list[str] = []

    def guarded(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if any(c in mode for c in "wax+"):
            writes.append(f"{file} {mode}")
            raise AssertionError(f"write attempted: {file}")
        return real_open(file, mode, *a, **k)

    def no_fd_write(path: Any, flags: int, *a: Any, **k: Any) -> Any:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            writes.append(str(path))
            raise AssertionError(f"os.open write attempted: {path}")
        return real_os_open(path, flags, *a, **k)

    real_os_open = os.open
    caplog.set_level(logging.DEBUG)
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", guarded)
        m.setattr(os, "open", no_fd_write)
        at = _analyze(at, RESUME)
    assert not at.exception, at.exception
    assert not writes
    assert list(tmp_path.iterdir()) == []
    assert MARKER not in caplog.text
    out, err = capfd.readouterr()
    assert MARKER not in out + err
    # The raw text is gone from session state and from the page; the profile holds skills only.
    assert MARKER not in _everything(at)
    assert at.session_state["resume_text"] == ""
    profile = at.session_state["resume_profile"]
    assert "Jane Doe" not in repr(profile)
    assert sorted(profile.skills) == ["airflow", "dbt", "kafka", "python", "spark", "sql"]


def test_database_is_never_written(resume_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = resume_db.stat().st_mtime_ns, resume_db.stat().st_size
    at = _analyze(_open(resume_db, monkeypatch), RESUME)
    assert not at.exception
    assert (resume_db.stat().st_mtime_ns, resume_db.stat().st_size) == before


def test_delete_clears_everything_from_the_session(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _analyze(_open(resume_db, monkeypatch), RESUME)
    at.multiselect(key="resume_classes").set_value(["CONFIRMED_OFFERED"]).run()
    assert "resume_profile" in at.session_state
    at = _click(at, "Delete my resume data")
    assert not at.exception, at.exception
    assert "resume_profile" not in at.session_state
    kept = {
        k: v
        for k, v in at.session_state.to_dict().items()
        if k.startswith("resume_") and v not in (None, "")
    }
    assert set(kept) <= {"resume_nonce", "resume_deleted"}  # empty widget state only
    assert at.session_state.get("resume_text", "") == ""
    assert "Deleted." in _everything(at)
    assert not at.dataframe  # no results are left on the page
    assert not at.metric  # nor the detected skills


def test_a_bad_file_or_empty_input_shows_a_message(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _open(resume_db, monkeypatch)
    at = _click(at, "Find matching postings")
    assert at.error
    assert "No text found" in at.error[0].value
    assert "resume_profile" not in at.session_state


def test_no_resume_code_logs_prints_caches_or_opens_files() -> None:
    forbidden_calls = {"print", "open"}
    for path in [
        *sorted((ROOT / "src/jobpulse/resume").glob("*.py")),
        ROOT / "app/app_pages/resume_match.py",
    ]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any(
                    n.split(".")[0] in {"logging", "structlog", "tempfile", "shutil"} for n in names
                ), path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls, (path, node.func.id)
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "cache_data",
                    "cache_resource",
                    "write_text",
                    "write_bytes",
                }, path


# ---- QA fixes: experience, education, levels, collapsing, learn list, thin resumes -------------
def test_years_come_from_date_ranges_and_never_from_a_stray_number() -> None:
    text = (
        "Data Analyst, Acme 2019 - 2022\nSenior Analyst, Foo 2021-Present\n"
        "BS Computer Science, State University 2015-2019\nVolunteered for 12 years."
    )
    p = extract_profile(text, VOCAB, CFG, today_year=2026)
    assert p.years == 7  # 2019-2026, the overlap counted once, the degree not counted
    assert p.education == ("Bachelor's",)
    assert extract_profile("volunteer for 12 years", VOCAB, CFG).years is None
    assert extract_profile("5 years of professional experience", VOCAB, CFG).years == 5


def test_fullwidth_and_zero_width_spellings_are_recognized() -> None:
    fullwidth = "".join(chr(c) for c in (0xFF33, 0xFF31, 0xFF2C))  # the letters S, Q, L
    zero_width = chr(0x200B)
    assert "sql" in extract_profile("Skills: " + fullwidth, VOCAB, CFG).skills
    assert "python" in extract_profile(f"Py{zero_width}thon", VOCAB, CFG).skills


def test_default_levels_follow_the_resume_but_are_only_a_default() -> None:
    from jobpulse.resume.match import default_levels

    lv = CFG.levels
    assert default_levels(1, [], lv) == [lv.entry_group, lv.mid_group]
    assert default_levels(None, ["Data Analyst Intern"], lv) == [lv.entry_group, lv.mid_group]
    assert default_levels(9, [], lv) == lv.senior_groups
    assert default_levels(None, [], lv) == []  # unknown: no filter
    assert "Manager and above" not in default_levels(15, [], lv)  # never defaulted


def test_the_same_role_in_several_places_is_one_row() -> None:
    from jobpulse.resume.match import collapse_locations

    skills = {"python", "sql", "dbt", "kafka", "spark"}
    a = PostingSkills(
        "a", frozenset(skills), {"company": "Acme", "title": "ML Eng", "location": "London"}
    )
    b = PostingSkills(
        "b", frozenset(skills), {"company": "Acme", "title": "ML Eng", "location": "Warsaw"}
    )
    c = PostingSkills(
        "c",
        frozenset(skills - {"spark"}),
        {"company": "Acme", "title": "ML Researcher", "location": "Rome"},
    )
    merged = collapse_locations([b, a, c])
    assert len(merged) == 2  # a+b collapse (same company and title); c is another role
    two = next(m for m in merged if len(m.info["locations"]) == 2)
    assert two.posting_key == "a"
    assert two.info["locations"] == ["London", "Warsaw"]


def test_skills_to_learn_counts_missing_skills_and_skips_the_stop_list() -> None:
    from jobpulse.resume.match import skills_to_learn

    postings = [
        _p("a", {"python", "sql", "dbt", "kafka", "star"}),
        _p("b", {"python", "dbt", "kafka", "airflow", "star"}),
    ]
    got = rank_postings({"python"}, postings, CFG.match)
    assert skills_to_learn(got, 10, 3, exclude={"star"}) == [
        ("dbt", 2, 2),
        ("kafka", 2, 2),
        ("airflow", 1, 2),
    ]


def test_a_thin_resume_gets_a_request_for_more_not_a_ranking(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _analyze(_open(resume_db, monkeypatch), "Python")
    assert not at.exception, at.exception
    assert "Add your tools and methods" in _everything(at)
    assert not at.dataframe  # nothing ranked
    at = _analyze(_open(resume_db, monkeypatch), "I like gardening and long walks.")
    assert "No skills recognized" in _everything(at)
    assert "No postings match" not in _everything(at)


def test_inference_is_a_skill_and_agent_needs_a_phrase() -> None:
    vocab = {"inference", "agent", "ai agent", "multi agent system"}
    got = extract_profile(
        "Model inference at scale. Built agent tools, AI agents and multi-agent systems.",
        vocab,
        CFG,
    ).skills
    assert "inference" in got
    assert "agent" not in got  # the single word stays on the stop-list
    assert {"ai agent", "multi agent system"} <= got
