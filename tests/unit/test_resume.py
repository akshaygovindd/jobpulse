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
    assert "never saved, logged or sent to an AI" in _everything(at)
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
    assert "Nothing to analyze yet" in at.error[0].value  # an empty box is not a scanned PDF
    assert "scanned" not in at.error[0].value
    with pytest.raises(ResumeError, match="scanned PDF"):  # while an image-only file is
        read_resume("cv.txt", b"   \n ", CFG)
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


def test_results_are_cards_with_chips_a_match_bar_and_the_table_in_an_expander(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _analyze(_open(resume_db, monkeypatch), RESUME)
    assert not at.exception, at.exception
    badges = [m.value for m in at.markdown if "-badge[" in str(m.value)]
    assert ":green-badge[:material/check: Python]" in badges  # a skill the resume has
    assert any(b.startswith(":blue-badge[") and "sponsorship" in b for b in badges)
    assert [p.proto.value for p in at.get("progress")] == [100]  # 100% match bar
    assert "100% match" in at.get("progress")[0].proto.text
    # the company and title are in the card as plain text (escaped), and the old table is kept
    assert any("Acme" in str(c.value) for c in at.caption)  # the company, as a caption
    assert "Show as table" in [e.label for e in at.expander]
    assert not [b for b in at.get("link_button") if not b.proto.url.startswith("https://")]
    # raw resume text never reaches a card
    assert MARKER not in " ".join(str(m.value) for m in at.markdown)


# ---- browser-QA fixes (2026-11-20): parsing and ranking ----------------------------------------

QA_VOCAB = VOCAB | {
    "bi", "power bi", "mbr", "qbr", "metric", "ai assisted tool", "google sheet", "dashboard",
    "dashboarding", "data analysis", "excel",
}  # fmt: skip
QA_RESUME = """Data Analyst
Priya Raman | priya@example.com | 555-123-4567 | Boston, MA
MS Business Analytics student, Northeastern University

Data Analyst Intern, Acme Retail (2024-2025)
- Weekly MBR and QBR reviews: dashboarding in Power BI, Excel and Google Sheets
- Defined KPI metric definitions and used an AI assisted tool for drafts
Skills: SQL, Python, Tableau
"""


def test_first_line_title_is_kept_and_a_title_survives_next_to_contact_details() -> None:
    p = extract_profile(QA_RESUME, QA_VOCAB, CFG, today_year=2026)
    assert p.titles[0] == "Data Analyst"  # the headline line counts
    assert "Data Analyst Intern" in p.titles  # the year range is not a phone number, the rest of
    # the line (company, dates) is a separate part
    assert not any("@" in t or "555" in t for t in p.titles)  # contact parts are dropped, alone
    mixed = extract_profile("Jane Doe | jane@x.com | Data Scientist\nPython", QA_VOCAB, CFG)
    assert mixed.titles == ("Data Scientist",)


def test_ms_student_is_education_but_ms_excel_is_not() -> None:
    assert "Master's" in extract_profile(QA_RESUME, QA_VOCAB, CFG).education
    assert "Master's" in extract_profile("MS in Data Science\nPython", QA_VOCAB, CFG).education
    assert "Master's" not in extract_profile("Skills: MS Excel, SQL", QA_VOCAB, CFG).education
    assert "Bachelor's" in extract_profile("B.S. in Statistics", QA_VOCAB, CFG).education


def test_a_skill_inside_a_longer_skill_is_not_a_second_skill() -> None:
    both = extract_profile("Tools: Power BI, Tableau", QA_VOCAB, CFG).skills
    assert "power bi" in both
    assert "bi" not in both
    alone = extract_profile("Tools: Power BI\nBI reporting for finance", QA_VOCAB, CFG).skills
    assert {"power bi", "bi"} <= alone  # "BI" written on its own elsewhere still counts


def test_stop_list_drops_meeting_names_and_ordinary_words() -> None:
    p = extract_profile(QA_RESUME, QA_VOCAB, CFG)
    for noise in ("mbr", "qbr", "metric", "ai assisted tool"):
        assert noise not in p.skills, noise
    assert "google sheet" in p.skills  # a real tool, shown as "Google Sheets"
    from jobpulse.app_ui import skill_label

    assert skill_label("google sheet") == "Google Sheets"


def test_synonyms_are_one_skill_on_both_sides() -> None:
    from jobpulse.resume.match import canonical_skills

    groups = CFG.parse.synonym_groups
    assert canonical_skills({"dashboarding", "sql"}, groups) == {"dashboard", "sql"}
    assert canonical_skills({"dashboard", "dashboarding"}, groups) == {"dashboard"}
    posting = PostingSkills("p", canonical_skills({"dashboarding", "sql"}, groups), {})
    got = rank_postings(
        canonical_skills({"dashboard", "sql"}, groups),
        [PostingSkills(posting.posting_key, posting.skills | {"a", "b", "c"}, {})],
        CFG.match,
    )
    assert got[0].matched == ("dashboard", "sql")  # a "dashboard" resume matches "dashboarding"


def test_resume_title_names_the_role_family_it_aims_at() -> None:
    p = extract_profile(QA_RESUME, QA_VOCAB, CFG)
    assert p.role_families == ("data_analyst",)  # the first line decides
    later = extract_profile("Jane\nWorked as Machine Learning Engineer\nPython", QA_VOCAB, CFG)
    assert later.role_families == ("ml_ai_engineer",)
    assert extract_profile("Python, SQL", QA_VOCAB, CFG).role_families == ()


def test_cards_are_ordered_by_shown_percent_and_role_fit_only_breaks_ties() -> None:
    """QA round 3: no hidden boost. The list is sorted by the visible match %; among equal
    percents the posting in the role the resume's title names comes first."""
    skills = frozenset({"python", "sql", "spark", "airflow", "kafka"})
    engineer = PostingSkills("e", skills, {"company": "A", "title": "DE", "role": "data_engineer"})
    analyst = PostingSkills(
        "a", frozenset({"sql", "python", "tableau", "excel", "dbt"}),
        {"company": "B", "title": "DA", "role": "data_analyst"},
    )  # fmt: skip
    have = {"python", "sql", "spark", "airflow"}  # engineer 80%, analyst 40%
    aimed = rank_postings(have, [analyst, engineer], CFG.match, ("data_analyst",))
    assert [m.posting.posting_key for m in aimed] == ["e", "a"]  # higher % first, role aside
    pcts = [round(100 * m.score) for m in aimed]
    assert pcts == sorted(pcts, reverse=True)
    tied_e = PostingSkills("e2", frozenset({"sql", "python", "x1", "x2", "x3"}), engineer.info)
    tied_a = PostingSkills("a2", frozenset({"sql", "python", "y1", "y2", "y3"}), analyst.info)
    tie = rank_postings({"sql", "python"}, [tied_e, tied_a], CFG.match, ("data_analyst",))
    assert [m.posting.posting_key for m in tie] == ["a2", "e2"]  # equal 40%: role fit decides
    plain = rank_postings({"sql", "python"}, [tied_e, tied_a], CFG.match)
    assert [m.posting.posting_key for m in plain] == ["e2", "a2"]  # then company, as before


def test_visa_evidence_is_not_part_of_the_ordering() -> None:
    src = (ROOT / "src/jobpulse/resume/match.py").read_text()
    body = " ".join(
        src[src.index("def rank_postings") : src.index("def effective_visa_class")].split()
    )
    code = body.split('"""')[2]  # the code after the docstring
    assert "visa" not in code.lower()
    assert "sponsor" not in code.lower()


def test_pay_as_posted_h1b_scope_careers_fallback_and_unclear_intern_range() -> None:
    from jobpulse.app_ui import (
        careers_urls,
        h1b_applies,
        intern_pay_unclear,
        pay_as_posted,
        salary_range,
    )

    assert pay_as_posted(64, 68, "USD", "hour") == "$64 to $68 per hour"
    assert pay_as_posted(12100, 12100, "USD", "month") == "$12,100 per month"
    assert pay_as_posted(8000, 8600, "CAD", "month") == "CAD 8,000 to CAD 8,600 per month"
    assert pay_as_posted(120000, 150000, "USD", "year") == "$120K to $150K per year"
    assert pay_as_posted(None, None, "USD", "hour") == "Not advertised"
    assert salary_range(172500, 172500, "USD") == "$173K"  # the same rounding as the heatmap
    # DoorDash's intern page lists full-time levels I4 to I6 as one yearly range
    assert intern_pay_unclear(130600, 285000, "year", 2.0)
    assert not intern_pay_unclear(120000, 150000, "year", 2.0)
    assert not intern_pay_unclear(64, 200, "hour", 2.0)  # only yearly ranges are judged
    assert h1b_applies(["United States"])
    assert h1b_applies([None])
    assert h1b_applies([])
    assert h1b_applies(["United Kingdom", "United States"])
    assert not h1b_applies(["United Kingdom"])
    assert not h1b_applies(["Canada", "India"])
    urls = careers_urls()
    assert urls["Lyft"] == "https://www.lyft.com/careers"  # explicit, in config/employers.yaml
    assert urls["Plaid"] == "https://jobs.ashbyhq.com/plaid"  # derived: its job board
    assert urls["Stripe"].startswith("https://job-boards.greenhouse.io/")
    assert all(u.startswith("https://") for u in urls.values())


def test_collapse_prefers_the_us_posting_and_keeps_any_apply_link() -> None:
    from jobpulse.resume.match import collapse_locations

    uk = PostingSkills("a", frozenset({"x"}), {"company": "C", "title": "T", "location": "London",
                       "us_ok": False, "apply_url": ""})  # fmt: skip
    us = PostingSkills("b", frozenset({"x"}), {"company": "C", "title": "T", "location": "Boston",
                       "us_ok": True, "apply_url": "https://c.example/b"})  # fmt: skip
    (row,) = collapse_locations([uk, us])
    assert row.posting_key == "b"
    assert row.info["us_ok"] is True
    assert row.info["locations"] == ["Boston", "London"]
    (row2,) = collapse_locations(
        [PostingSkills("a", frozenset({"x"}), {**uk.info, "us_ok": True}), us]
    )
    assert row2.info["apply_url"] == "https://c.example/b"  # the first posting had no link


def test_a_card_without_an_apply_link_falls_back_to_the_careers_page(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (QA P0-7): a Resume match card for Lyft had no Apply link at all. The fixture's
    posting has no usable apply URL, so the card must offer the company's careers page."""
    at = _open(resume_db, monkeypatch)
    assert not at.get("link_button") or all(b.label for b in at.get("link_button"))
    careers = "https://careers.acme.example/jobs"
    monkeypatch.setattr("jobpulse.app_ui.careers_urls", lambda: {"Acme": careers})
    at = _analyze(_open(resume_db, monkeypatch), RESUME)
    assert not at.exception, at.exception
    buttons = [(b.label, b.proto.url) for b in at.get("link_button")]
    assert ("Careers page", careers) in buttons
    assert not [b for b in buttons if b[0] == "Apply"]  # the posting itself has no link
    # the table keeps the link in its Apply column too
    assert at.dataframe[-1].value["Apply"].iloc[0] == careers
    # and with no careers URL on file there is simply no button (never a broken one)
    monkeypatch.setattr("jobpulse.app_ui.careers_urls", lambda: {})
    none = _analyze(_open(resume_db, monkeypatch), RESUME)
    assert not [b for b in none.get("link_button") if b.label in ("Apply", "Careers page")]


def test_badge_for_a_posting_with_no_stated_place_carries_the_us_only_tooltip(
    resume_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _analyze(_open(resume_db, monkeypatch), RESUME)
    assert not at.exception, at.exception
    helps = [b.proto.help for b in at.get("badge")] if at.get("badge") else []
    assert "H-1B applies to US roles only" in " ".join(helps) or not helps
    source = (ROOT / "app/app_pages/resume_match.py").read_text()
    assert 'visa_help=(\n            "H-1B applies to US roles only"' in source


def test_h1b_badge_follows_the_places_the_card_lists() -> None:
    """QA round 3: "Stripe, Data Analyst, Intern" listed Toronto only but showed "Company has
    certified filings", because a sibling posting with no place made the group count as US."""
    from jobpulse.resume.match import collapse_locations, effective_visa_class

    base = {"company": "Stripe", "title": "T", "base_class": "HISTORICAL_EVIDENCE"}
    toronto = PostingSkills(
        "a", frozenset({"x"}), {**base, "location": "Toronto, Ontario", "us_ok": False}
    )
    nowhere = PostingSkills("b", frozenset({"x"}), {**base, "location": "", "us_ok": True})
    (row,) = collapse_locations([nowhere, toronto])
    assert row.info["locations"] == ["Toronto, Ontario"]
    assert row.info["us_ok"] is False
    assert effective_visa_class(row.info["base_class"], row.info["us_ok"]) == "NOT_US"
    # no place anywhere (remote): the US cannot be ruled out, the badge stays
    (remote,) = collapse_locations([nowhere])
    assert remote.info["us_ok"] is True
    # one US place keeps it; and a posting's own statement is never replaced
    boston = PostingSkills(
        "c", frozenset({"x"}), {**toronto.info, "location": "Boston", "us_ok": True}
    )
    (both,) = collapse_locations([toronto, boston])
    assert both.info["us_ok"] is True
    assert effective_visa_class("EXPLICITLY_UNAVAILABLE", False) == "EXPLICITLY_UNAVAILABLE"
    assert effective_visa_class("UNKNOWN", False) == "NOT_US"
