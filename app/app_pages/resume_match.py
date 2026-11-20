import streamlit as st

from jobpulse.app_ui import (
    REMOTE_LABELS,
    human,
    load,
    role_label,
    salary_range,
    visa_disclaimer,
)
from jobpulse.config import load_resume
from jobpulse.resume.match import PostingSkills, rank_postings
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
    "UNKNOWN": "No filings found",
}

cfg = load_resume()
vocabulary = set(load("SELECT canonical_name FROM gold.dim_skill")["canonical_name"])


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
            text = check_text(st.session_state.get(TEXT, ""), cfg)
        st.session_state[PROFILE] = extract_profile(text, vocabulary, cfg)
    except ResumeError as e:
        st.session_state[ERROR] = str(e)
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
    "matching words against the skill list built from the postings. Only the skills, a few title "
    "lines and years of experience found are kept for this session, until you press **Delete** "
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
        "by how many of the skills they ask for appear in your resume."
    )
    tab_paste, tab_file = st.tabs(["Paste text", "Upload a file"])
    with tab_paste:
        st.text_area("Resume text", key=TEXT, height=240)
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
left, right = st.columns(2)
with left:
    st.metric("Skills recognized", len(profile.skills), border=True)
    st.write(", ".join(sorted(profile.skills)) or "None. Try listing tools and methods by name.")
with right:
    st.metric(
        "Years of experience mentioned",
        "Not found" if profile.years is None else profile.years,
        border=True,
    )
    st.write("; ".join(profile.titles) or "No title lines found.")
st.caption(
    "Only skills that appear in the skill list built from the postings can be recognized, and "
    "spelling variants the list does not know are missed. Single letters and everyday words "
    "(for example R, C, Go, search, testing) count only in a form that is clearly a skill."
)

postings = load(
    "SELECT p.posting_key, c.name AS company, p.title, p.role_family, p.remote_policy, p.city, "
    "p.sponsorship_class, c.n_lca_certified, p.salary_min_annual, p.salary_max_annual, "
    "p.salary_currency, list(s.canonical_name) AS skills "
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
st.caption(
    f"Compared against the {len(postings):,} open data and AI postings whose skills the AI "
    "has read (a title filter chooses these roles; other open postings are not read). The match "
    "is the share of the skills a posting asks for that appear in your resume. Postings with "
    f"fewer than {cfg.match.min_posting_skills} extracted skills are not ranked."
)
visa_disclaimer()
with st.container(horizontal=True):
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
    ("role_family", roles),
    ("remote_policy", remote),
    ("city", cities),
    ("sponsorship_class", classes),
):
    if picked:
        view = view[view[column].isin(picked)]

pool = [
    PostingSkills(
        str(r.posting_key),
        frozenset(r.skills),
        {
            "company": r.company,
            "title": r.title,
            "role": r.role_family,
            "remote": r.remote_policy,
            "city": r.city,
            "visa": CLASS_LABELS.get(str(r.sponsorship_class), "Unknown")
            + f" ({int(r.n_lca_certified)} filings)",
            "salary": salary_range(r.salary_min_annual, r.salary_max_annual, r.salary_currency),
        },
    )
    for r in view.itertuples()
]
matches = rank_postings(profile.skills, pool, cfg.match)
if not matches:
    st.info("No postings match with the current filters.", icon=":material/search_off:")
    st.stop()

rows = [
    {
        "Match": round(100 * m.score),
        "Company": m.posting.info["company"],
        "Title": m.posting.info["title"],
        "City": m.posting.info["city"] or "No single city",
        "Remote": human(m.posting.info["remote"], REMOTE_LABELS),
        "Matched skills": ", ".join(m.matched),
        "Missing skills": ", ".join(m.missing),
        "Visa evidence": m.posting.info["visa"],
        "Advertised salary": m.posting.info["salary"],
    }
    for m in matches
]
st.dataframe(
    rows,
    hide_index=True,
    column_config={
        "Match": st.column_config.ProgressColumn(
            "Match", format="%d%%", min_value=0, max_value=100, width="small"
        ),
        "Company": st.column_config.TextColumn(width="small"),
        "Title": st.column_config.TextColumn(width="medium"),
        "City": st.column_config.TextColumn(width="small"),
        "Remote": st.column_config.TextColumn(width="small"),
        "Matched skills": st.column_config.TextColumn(width="large"),
        "Missing skills": st.column_config.TextColumn(width="large"),
        "Visa evidence": st.column_config.TextColumn(width="medium"),
        "Advertised salary": st.column_config.TextColumn(width="small"),
    },
)
st.caption(
    "Advertised salary is what the posting states, converted to a yearly figure where the period "
    "is known; it is not the wage on H-1B filings. "
    f"Showing the best {len(matches)} matches (at most {cfg.match.max_results})."
)
