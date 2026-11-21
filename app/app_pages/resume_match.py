from datetime import UTC, datetime
from typing import Any

import streamlit as st

from jobpulse.app_components import match_card
from jobpulse.app_ui import (
    REMOTE_LABELS,
    app_config,
    careers_urls,
    clean,
    h1b_applies,
    human,
    intern_pay_unclear,
    load,
    pay_as_posted,
    role_label,
    salary_range,
    skill_label,
    visa_disclaimer,
)
from jobpulse.config import load_resume
from jobpulse.resume.match import (
    Match,
    PostingSkills,
    canonical_skills,
    collapse_locations,
    default_levels,
    effective_visa_class,
    level_group,
    rank_postings,
    skills_to_learn,
)
from jobpulse.resume.parse import ResumeError, check_text, extract_profile, read_resume

# Everything this page keeps about a resume lives under these session keys (all start with
# "resume_"): the profile (skills, a few title lines, years) and the filter widgets. The raw text
# and the uploaded file are dropped as soon as they are parsed. Nothing is cached or logged.
PREFIX = "resume_"
PROFILE, NONCE, ERROR, TEXT, DELETED = (
    "resume_profile",
    "resume_nonce",
    "resume_error",
    "resume_text",
    "resume_deleted",
)
CLASS_LABELS = {
    "CONFIRMED_OFFERED": "Posting says sponsorship is offered",
    "EXPLICITLY_UNAVAILABLE": "Posting says sponsorship is not available",
    "HISTORICAL_EVIDENCE": "Company has certified H-1B filings",
    "UNKNOWN": "No matching filings found",
    "NOT_US": "H-1B applies to US roles only",
}

cfg = load_resume()
# Only skills that open, analyzed postings actually ask for (at least `min_posting_frequency` of
# them) can be recognized: this keeps one-off phrases out of "Skills recognized".
vocabulary = set(
    load(
        "SELECT s.canonical_name FROM gold.dim_skill s "
        "JOIN gold.bridge_posting_skill b ON b.skill_id = s.skill_id "
        "JOIN gold.fct_postings p ON p.posting_key = b.posting_key "
        "WHERE p.is_active AND p.is_target_role AND p.has_extraction "
        "AND NOT p.is_probable_duplicate "
        "GROUP BY s.canonical_name HAVING count(DISTINCT p.posting_key) >= ?",
        [cfg.parse.min_posting_frequency],
    )["canonical_name"]
)


def _upload_key() -> str:
    return f"resume_file_{st.session_state.get(NONCE, 0)}"


def _analyze() -> None:
    st.session_state.pop(ERROR, None)
    st.session_state.pop(DELETED, None)
    upload = st.session_state.get(_upload_key())
    try:
        if upload is not None:
            text = read_resume(upload.name, upload.getvalue(), cfg)
        else:
            text = check_text(st.session_state.get(TEXT, ""), cfg, pasted=True)
        profile = extract_profile(text, vocabulary, cfg, datetime.now(UTC).year)
        st.session_state[PROFILE] = profile
        # Experience level is a pre-selected filter, never part of the score.
        st.session_state["resume_levels"] = default_levels(
            profile.years, profile.titles, cfg.levels
        )
    except ResumeError as e:
        st.session_state[ERROR] = str(e)
        if len(st.session_state.get(TEXT, "")) > cfg.upload.max_text_chars:
            st.session_state[TEXT] = ""  # do not keep an oversize paste in the session
        return
    st.session_state[TEXT] = ""  # drop the raw text
    st.session_state[NONCE] = st.session_state.get(NONCE, 0) + 1  # drops the uploaded file


def _delete() -> None:
    nonce = st.session_state.get(NONCE, 0)
    for key in [k for k in st.session_state if str(k).startswith(PREFIX)]:
        del st.session_state[key]
    st.session_state[NONCE] = nonce + 1
    st.session_state[DELETED] = True


st.info(
    "**Privacy.** Your resume is processed in memory in this browser session only. It is never "
    "saved to disk or a database, never logged, and no AI service is called: skills are found by "
    "matching words against the skill list built from the postings. Only the skills, a few short "
    "lines (as written, without any email, phone number or link), the degrees and the years of "
    "experience found are kept for this session, until you press **Delete** "
    "or close the tab. Nothing is sent to the project owner.",
    icon=":material/lock:",
)

profile = st.session_state.get(PROFILE)

if profile is None:
    if st.session_state.pop(DELETED, False):
        st.success(
            "Deleted. The skills and details found in your resume were removed from this session.",
            icon=":material/delete:",
        )
    st.write(
        "Paste your resume text or upload a PDF or text file (up to "
        f"{cfg.upload.max_bytes / 1_000_000:.0f} MB). I will rank the open data and AI postings "
        "by how many of the skills they ask for appear in your resume. English resumes only."
    )
    tab_paste, tab_file = st.tabs(["Paste text", "Upload a file"])
    with tab_paste:
        st.text_area(
            "Resume text",
            key=TEXT,
            height=240,
            on_change=lambda: st.session_state.pop(ERROR, None),  # a new paste clears the old error
        )
    with tab_file:
        st.file_uploader(
            "Resume file",
            type=cfg.upload.allowed_extensions,
            key=_upload_key(),
            accept_multiple_files=False,
        )
    if st.session_state.get(ERROR):
        st.error(st.session_state[ERROR], icon=":material/error:")
    st.button("Find matching postings", type="primary", on_click=_analyze)
    st.stop()

# ---- a profile exists: show it, then the matches -------------------------------------------
with st.container(horizontal=True, vertical_alignment="center"):
    st.subheader("What I found in your resume")
    st.button("Delete my resume data", icon=":material/delete:", on_click=_delete)
left, middle, right = st.columns(3)
with left:
    st.metric("Skills recognized", len(profile.skills), border=True)
    st.text(
        ", ".join(sorted(skill_label(v) for v in profile.skills))
        or "None. Try listing tools and methods by name."
    )
with middle:
    st.metric(
        "Years of experience",
        "Not found" if profile.years is None else profile.years,
        border=True,
        help="From the date ranges of your roles (overlaps counted once), or an explicit "
        "'N years of experience'. Shown as 'Not found' when it cannot be told reliably.",
    )
    st.text("Education: " + (", ".join(profile.education) or "not found"))
with right:
    st.markdown("**Roles found**")
    # Text taken from the resume is shown as plain text, never as markdown (no links, images or
    # LaTeX from an uploaded file).
    st.text("\n".join(profile.titles) or "No title lines found.")
    if profile.role_families:
        st.caption("Aiming at: " + ", ".join(role_label(f) for f in profile.role_families))
st.caption(
    "Only skills that appear in several open postings can be recognized, and spelling variants "
    "the list does not know are missed. Single letters and everyday words (for example R, C, Go, "
    "search, testing) count only in a form that is clearly a skill."
)
if len(profile.skills) < cfg.match.min_resume_skills:
    if profile.skills:
        st.warning(
            f"Only {len(profile.skills)} skill(s) recognized. Add your tools and methods (at "
            f"least {cfg.match.min_resume_skills}) to get a ranking.",
            icon=":material/info:",
        )
    else:
        st.warning(
            "No skills recognized. List your tools and methods by name (for example Python, "
            "SQL, Spark). Resumes in English only.",
            icon=":material/info:",
        )
    st.stop()

postings = load(
    "SELECT p.posting_key, c.name AS company, p.title, p.role_family, p.remote_policy, p.city, "
    "p.city_country, p.seniority, p.apply_url, p.sponsorship_class, c.n_lca_certified, "
    "p.salary_min_annual, p.salary_max_annual, p.salary_min, p.salary_max, p.salary_currency, "
    "p.salary_period, list(s.canonical_name) AS skills "
    "FROM gold.fct_postings p JOIN gold.dim_company c ON c.company_id = p.company_id "
    "JOIN gold.bridge_posting_skill b ON b.posting_key = p.posting_key "
    "JOIN gold.dim_skill s ON s.skill_id = b.skill_id "
    "WHERE p.is_active AND p.is_target_role AND p.has_extraction AND NOT p.is_probable_duplicate "
    "GROUP BY ALL"
)
if postings.empty:
    st.warning("No analyzed data and AI postings yet.")
    st.stop()

st.subheader("Matching open postings")
open_roles = int(
    load(
        "SELECT count(*) AS n FROM gold.fct_postings WHERE is_active AND is_target_role "
        "AND has_extraction AND NOT is_probable_duplicate"
    )["n"].iloc[0]
)
no_skills = open_roles - len(postings)
st.caption(
    f"Compared against {len(postings):,} of the {open_roles:,} open data and AI postings the AI "
    "has read"
    + (
        f" (the other {no_skills:,} list no skills, so there is nothing to match). "
        if no_skills > 0
        else ". "
    )
    + "The match is the share of the skills a posting asks for that appear in your resume, so a "
    "posting that lists few skills can score high. Postings with fewer than "
    f"{cfg.match.min_posting_skills} extracted skills are not ranked. The same role in several "
    "places is one row."
)
visa_disclaimer()
postings["level"] = postings["seniority"].map(lambda v: level_group(v, cfg.levels))
# H-1B filings are about US jobs: a posting abroad gets "H-1B applies to US roles only" instead of
# the company's filing history (a posting's own sponsorship statement is still shown).
postings["us_ok"] = postings["city_country"].map(lambda c: h1b_applies([c]))
postings["visa_class"] = [
    "NOT_US" if (not us and cls in ("HISTORICAL_EVIDENCE", "UNKNOWN")) else cls
    for us, cls in zip(postings["us_ok"], postings["sponsorship_class"], strict=True)
]
with st.container(horizontal=True):
    levels = st.multiselect(
        "Experience level",
        list(cfg.levels.groups),
        key="resume_levels",
        help="Pre-selected from the years and titles in your resume; change it freely. A filter "
        "only: it never changes a match score.",
    )
    roles = st.multiselect(
        "Role family",
        sorted(set(postings["role_family"].dropna())),
        format_func=role_label,
        key="resume_roles",
    )
    remote = st.multiselect(
        "Remote policy",
        sorted(set(postings["remote_policy"].dropna())),
        format_func=lambda v: human(v, REMOTE_LABELS),
        key="resume_remote",
    )
    cities = st.multiselect(
        "City (preference)",
        sorted(set(postings["city"].dropna())),
        key="resume_cities",
        help="Postings in no single city (remote or multi-location) are hidden when you pick one.",
    )
    classes = st.multiselect(
        "Visa evidence (filter only)",
        list(CLASS_LABELS),
        format_func=lambda v: CLASS_LABELS[v],
        key="resume_classes",
        help="A filter, never part of the match. Evidence from public filings is not a promise "
        "of sponsorship.",
    )

view = postings
for column, picked in (
    ("level", levels),
    ("role_family", roles),
    ("remote_policy", remote),
    ("city", cities),
    ("visa_class", classes),
):
    if picked:
        view = view[view[column].isin(picked)]


def _location(row: Any) -> str:
    return clean(row.city) or clean(row.city_country)


def _salary(row: Any) -> str:
    if row.seniority == "intern":
        # Internship pay is shown as posted (hourly or monthly), never as a yearly salary.
        if intern_pay_unclear(
            row.salary_min,
            row.salary_max,
            row.salary_period,
            app_config().pay.intern_max_range_ratio,
        ):
            return "Not clearly stated (the range spans several levels)"
        return pay_as_posted(row.salary_min, row.salary_max, row.salary_currency, row.salary_period)
    text = salary_range(row.salary_min_annual, row.salary_max_annual, row.salary_currency)
    period = clean(row.salary_period)
    if text != "Not advertised" and period and period != "year":
        text += f" (yearly equivalent of {period}ly pay)"
    return text


groups = cfg.parse.synonym_groups
have = canonical_skills(profile.skills, groups)  # "dashboard" and "dashboarding" are one skill
careers = careers_urls()
ignored = frozenset(cfg.parse.ignore_skills)  # stop-list words are not skills on either side
pool = collapse_locations(
    PostingSkills(
        str(r.posting_key),
        canonical_skills(r.skills, groups) - ignored,
        {
            "company": r.company,
            "title": r.title,
            "role": r.role_family,
            "remote": r.remote_policy,
            "location": _location(r),
            "apply_url": clean(r.apply_url),
            "careers_url": careers.get(str(r.company), ""),
            "us_ok": bool(r.us_ok),
            "country": clean(r.city_country),
            "visa": CLASS_LABELS.get(str(r.visa_class), "Unknown")
            + (f" ({int(r.n_lca_certified)} filings)" if r.visa_class != "NOT_US" else ""),
            "visa_class": str(r.visa_class),
            "base_class": str(r.sponsorship_class),
            "filings_all": int(r.n_lca_certified),
            "filings": 0,
            "salary": _salary(r),
        },
    )
    for r in view.itertuples()
)
for _p in pool:  # the badge follows the places the card lists (see collapse_locations)
    _i = _p.info
    _i["visa_class"] = effective_visa_class(_i["base_class"], _i["us_ok"])
    _i["visa"] = CLASS_LABELS.get(_i["visa_class"], "Unknown") + (
        f" ({_i['filings_all']} filings)" if _i["visa_class"] != "NOT_US" else ""
    )
    _i["filings"] = 0 if _i["visa_class"] == "NOT_US" else _i["filings_all"]
matches = rank_postings(have, pool, cfg.match, profile.role_families)
if not matches:
    st.info("No postings match with the current filters.", icon=":material/search_off:")
    st.stop()

# ---- top skills to learn -------------------------------------------------------------------
learn = skills_to_learn(
    matches,
    cfg.match.top_learn_n,
    cfg.match.top_learn_max,
    exclude=set(cfg.parse.ignore_skills)
    | {
        skill
        for family in profile.role_families
        for skill in cfg.parse.role_implied_skills.get(family, [])
    },  # "Data analysis" is not news to a Data Analyst
)
if learn:
    st.subheader("Top skills to learn")
    st.caption(
        f"Skills that the best {learn[0][2]} matches ask for and your resume does not list, "
        "most common first. A hint from the postings, not a judgement of your profile."
    )
    st.dataframe(
        [
            {"Skill": skill_label(skill), "In postings": f"{n} of {total}"}
            for skill, n, total in learn
        ],
        hide_index=True,
        width="content",
    )


def _more(skills: tuple[str, ...], shown: int = 5) -> str:
    names = [skill_label(v) for v in skills]
    return ", ".join(names[:shown]) + (
        f" (+{len(names) - shown} more)" if len(names) > shown else ""
    )


def _where(m: Match) -> str:
    places = m.posting.info["locations"]
    if not places:
        return "No single city"
    return places[0] if len(places) == 1 else f"{len(places)} locations: " + "; ".join(places)


def _table(rows: list[dict[str, Any]]) -> None:
    st.dataframe(
        rows,
        hide_index=True,
        column_config={
            "Match": st.column_config.ProgressColumn(
                "Match", format="%d%%", min_value=0, max_value=100, width="small"
            ),
            "Company": st.column_config.TextColumn(width="small"),
            "Title": st.column_config.TextColumn(width="large"),
            "Location": st.column_config.TextColumn(width="medium"),
            "Remote": st.column_config.TextColumn(width="small"),
            "Matched skills": st.column_config.TextColumn(width="large"),
            "Top missing skills": st.column_config.TextColumn(width="large"),
            "Visa evidence": st.column_config.TextColumn(width="medium"),
            "Advertised salary": st.column_config.TextColumn(width="medium"),
            "Apply": st.column_config.LinkColumn("Apply", display_text="Open", width="small"),
        },
    )


# Short wording, colour and icon for the visa evidence badge. Blue is evidence, gray is "nothing
# found", orange is a posting that says no: none of it is part of the match score.
VISA_BADGE = {
    "CONFIRMED_OFFERED": ("Posting offers sponsorship", "blue", ":material/verified:"),
    "EXPLICITLY_UNAVAILABLE": ("Posting: no sponsorship", "orange", ":material/block:"),
    "HISTORICAL_EVIDENCE": ("Company has certified filings", "blue", ":material/verified:"),
    "UNKNOWN": ("No matching filings found", "gray", ":material/help:"),
    "NOT_US": ("H-1B applies to US roles only", "gray", ":material/public:"),
}
PAGE_SIZE = 10
SHOWN = "resume_shown"


def _show_more() -> None:
    st.session_state[SHOWN] = st.session_state.get(SHOWN, PAGE_SIZE) + PAGE_SIZE


shown = st.session_state.get(SHOWN, PAGE_SIZE)
for rank, m in enumerate(matches[:shown]):
    info = m.posting.info
    label, color, icon = VISA_BADGE.get(info["visa_class"], VISA_BADGE["UNKNOWN"])
    match_card(
        key=str(rank),
        company=info["company"],
        title=info["title"],
        score=m.score,
        matched=[skill_label(v) for v in m.matched],
        missing=[skill_label(v) for v in m.missing],
        location=_where(m),
        remote=human(info["remote"], REMOTE_LABELS),
        salary=info["salary"],
        visa_label=label,
        visa_color=color,
        visa_icon=icon,
        filings=info["filings"],
        apply_url=info["apply_url"] or info["careers_url"],
        apply_label="Apply" if info["apply_url"] else "Careers page",
        # no stated place (remote or multi-location): the badge stays, with the scope in a tooltip
        visa_help=(
            "H-1B applies to US roles only"
            if not info["locations"] and info["visa_class"] in ("HISTORICAL_EVIDENCE", "UNKNOWN")
            else None
        ),
    )
if len(matches) > shown:
    st.button(
        f"Show {min(PAGE_SIZE, len(matches) - shown)} more matches",
        icon=":material/expand_more:",
        on_click=_show_more,
        key="resume_more",
    )

rows = [
    {
        "Match": round(100 * m.score),
        "Company": m.posting.info["company"],
        "Title": m.posting.info["title"],
        "Location": _where(m),
        "Remote": human(m.posting.info["remote"], REMOTE_LABELS),
        "Matched skills": _more(m.matched, 8),
        "Top missing skills": _more(m.missing),
        "Visa evidence": m.posting.info["visa"],
        "Advertised salary": m.posting.info["salary"],
        "Apply": m.posting.info["apply_url"] or m.posting.info["careers_url"] or None,
    }
    for m in matches
]
with st.expander("Show as table", icon=":material/table:"):
    _table(rows)
st.caption(
    "Advertised salary is what the posting states, converted to a yearly figure where the period "
    "is known; hourly and monthly pay is a yearly equivalent, not a salary. It is not the wage "
    "on H-1B filings. "
    + (
        f"Showing the top {len(matches)} matches."
        if len(matches) >= cfg.match.max_results
        else f"Showing all {len(matches)} matches."
    )
    + " "
    "Apply links go to the employer's job board."
)
