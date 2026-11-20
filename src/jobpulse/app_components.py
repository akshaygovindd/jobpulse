"""Cards for the Streamlit pages: Resume match results and Visa company cards.

Safety rule for this module: text that comes from a posting or a resume (company, title, city,
skills) is never rendered as HTML or as markdown. It is passed through `md_escape` or `badge_text`
first, which turn every markdown, directive, emoji-shortcode and LaTeX trigger into plain
characters. The only HTML here is the avatar, and it is built from two letters or digits and an
integer, nothing else.
"""

from __future__ import annotations

import string
import zlib
from collections.abc import Sequence
from typing import Any

import streamlit as st

_PUNCTUATION = frozenset(string.punctuation)


def md_escape(text: Any) -> str:
    """`text` as a single line of markdown that shows exactly those characters. Every ASCII
    punctuation mark gets a backslash (CommonMark allows that), `&` and `:` become entities so that
    no `&entity;`, `:emoji:` or `:color[...]` directive can form."""
    out: list[str] = []
    for ch in " ".join(str(text).split()):
        if ch == "&":
            out.append("&amp;")
        elif ch == ":":
            out.append("&#58;")
        elif ch in _PUNCTUATION:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def badge_text(text: Any) -> str:
    """Label for `st.badge`, which escapes square brackets itself and treats the label as inline
    markdown: brackets and colons are swapped for look-alikes, other markdown characters escaped."""
    swapped = str(text).replace("[", "(").replace("]", ")").replace(":", "\u2236")
    out: list[str] = []
    for ch in " ".join(swapped.split()):
        out.append("\\" + ch if ch in _PUNCTUATION and ch not in "()" else ch)
    return "".join(out)


def initials(name: str) -> str:
    """Up to two letters or digits from a name ('Scale AI' -> 'SA'), upper case."""
    words = ["".join(c for c in w if c.isalnum()) for w in name.split()]
    words = [w for w in words if w]
    if not words:
        return "?"
    letters = words[0][0] + words[1][0] if len(words) > 1 else words[0][:2]
    return letters.upper()


def hue(name: str) -> int:
    """A stable hue (0-359) per name, so a company keeps its avatar colour on every page."""
    return zlib.crc32(name.encode("utf-8")) % 360


def avatar(name: str) -> None:
    """A round avatar with the company's initials on a colour taken from its name (no logos)."""
    st.html(
        f'<div class="jp-avatar" style="--hue:{hue(name)}" aria-hidden="true">'
        f"{initials(name)}</div>",
        width="content",
    )


def chips(
    skills: Sequence[str], *, color: str, icon: str, shown: int, more_color: str = "gray"
) -> None:
    """Skill chips in a wrapping row: the first `shown` skills, then a '+N more' chip."""
    if not skills:
        return
    with st.container(horizontal=True, gap="xxsmall"):
        for skill in skills[:shown]:
            st.badge(badge_text(skill), color=color, icon=icon)  # type: ignore[arg-type]
        if len(skills) > shown:
            st.badge(f"+{len(skills) - shown} more", color=more_color)  # type: ignore[arg-type]


def meta_line(parts: Sequence[tuple[str, str]]) -> None:
    """Icon + text facts in a wrapping row, for example location, remote policy and salary. Text
    is escaped; the icon is a Material Symbol name chosen by the caller."""
    with st.container(horizontal=True, gap="medium"):
        for icon, text in parts:
            if text:
                st.caption(f":material/{icon}: {md_escape(text)}", width="content")


def match_card(
    *,
    key: str,
    company: str,
    title: str,
    score: float,
    matched: Sequence[str],
    missing: Sequence[str],
    location: str,
    remote: str,
    salary: str,
    visa_label: str,
    visa_color: str,
    visa_icon: str,
    filings: int,
    apply_url: str,
    matched_shown: int = 8,
    missing_shown: int = 5,
) -> None:
    """One Resume match result. `matched` and `missing` are display names; everything else is
    already a display string. The apply link is shown only for an https URL."""
    with st.container(border=True, key=f"card_match_{key}"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            avatar(company)
            with st.container(gap="xxsmall"):
                st.caption(md_escape(company))
                st.markdown(f"**{md_escape(title)}**")
        total = len(matched) + len(missing)
        st.progress(
            min(max(score, 0.0), 1.0),
            text=f"**{round(100 * score)}% match** ({len(matched)} of {total} skills asked for)",
        )
        chips(matched, color="green", icon=":material/check:", shown=matched_shown)
        chips(missing, color="red", icon=":material/close:", shown=missing_shown)
        meta_line([("location_on", location), ("home_work", remote), ("payments", salary)])
        with st.container(horizontal=True, vertical_alignment="center"):
            st.badge(
                badge_text(visa_label),
                color=visa_color,  # type: ignore[arg-type]
                icon=visa_icon,
            )
            if filings:
                st.badge(f"{filings:,} certified H-1B filings", color="gray")
            if apply_url.startswith("https://"):
                st.link_button("Apply", apply_url, icon=":material/open_in_new:", type="primary")


def company_card(
    *,
    key: str,
    name: str,
    evidence: str,
    has_evidence: bool,
    certified: int,
    not_certified: int,
    open_postings: int,
    by_year: Any,
) -> None:
    """One Visa company card: avatar, evidence badge, counts and filings per fiscal year (a small
    chart from the shared template; `by_year` has columns fiscal_year and certified)."""
    from jobpulse.app_ui import mini_bars

    with st.container(border=True, key=f"card_company_{key}"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            avatar(name)
            with st.container(gap="xxsmall"):
                st.markdown(f"**{md_escape(name)}**")
                st.badge(
                    evidence,
                    color="blue" if has_evidence else "gray",
                    icon=":material/verified:" if has_evidence else ":material/help:",
                )
        st.metric("Certified filings", f"{certified:,}", border=False)
        st.caption(f"{not_certified:,} withdrawn or denied · {open_postings:,} open postings")
        if by_year is not None and len(by_year):
            st.caption("Certified filings per fiscal year")
            mini_bars(by_year, x="fiscal_year", y="certified")
        else:
            st.caption("No matching H-1B filings found.")
