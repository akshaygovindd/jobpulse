"""Cards for Resume match and Visa: text from postings and resumes is never markup."""

from __future__ import annotations

import re

import pytest
from streamlit.testing.v1 import AppTest

from jobpulse.app_components import badge_text, hue, initials, md_escape

HOSTILE = [
    "**bold** and _italic_ [link](http://evil.example) ![img](http://evil.example/x.png)",
    ":red[colored] :smile: :material/home: :blue-badge[x] $x^2$ <script>alert(1)</script>",
    "# heading\n> quote\n- list &amp; &#x41; `code`",
]
# Characters that would start markdown, a directive, an entity, LaTeX or HTML if left bare.
BARE_TRIGGER = re.compile(r"(?<!\\)[*_\[\]()!`#>$<~|:-]|&")


@pytest.mark.parametrize("text", HOSTILE)
def test_md_escape_leaves_no_bare_markup_trigger(text: str) -> None:
    out = md_escape(text)
    assert "\n" not in out
    # strip the escapes themselves and the two entities we emit on purpose, then nothing is left
    assert not BARE_TRIGGER.search(out.replace("&amp;", "").replace("&#58;", "")), out
    unescaped = re.sub(r"\\(.)", r"\1", out).replace("&amp;", "&").replace("&#58;", ":")
    assert unescaped == " ".join(text.split())


@pytest.mark.parametrize("text", HOSTILE)
def test_badge_text_has_no_brackets_colons_or_bare_markup(text: str) -> None:
    out = badge_text(text)
    assert not re.search(r"(?<!\\)[\[\]:*_`$<~|]", out), out
    assert ":" not in out
    assert "[" not in out.replace("\\[", "")
    assert "]" not in out.replace("\\]", "")


def test_initials_and_hue_are_stable_and_safe() -> None:
    assert initials("Scale AI") == "SA"
    assert initials("Stripe") == "ST"
    assert initials("<img onerror=x>") == "IO"  # only letters and digits survive
    assert initials("") == "?"
    assert hue("DoorDash") == hue("DoorDash")
    assert 0 <= hue("anything") < 360


def test_avatar_html_contains_only_two_characters_and_a_number() -> None:
    def script() -> None:
        from jobpulse.app_components import avatar

        avatar('"><img src=x onerror=alert(1)> Evil & Co')

    at = AppTest.from_function(script, default_timeout=20).run()
    assert not at.exception, at.exception
    html = at.get("html")[0].proto.body
    assert re.fullmatch(
        r'<div class="jp-avatar" style="--hue:\d{1,3}" aria-hidden="true">[A-Za-z0-9]{1,2}</div>',
        html,
    ), html


def test_match_card_shows_escaped_text_chips_bar_and_a_safe_apply_link() -> None:
    def script() -> None:
        from jobpulse.app_components import match_card

        common = dict(
            company="Acme **Co**", title="Data [Eng](http://evil) :red[x]", score=0.5,
            matched=["SQL", "dbt [x]"], missing=["Go", "Rust", "Ray"], location="NYC",
            remote="Hybrid", salary="$1K", visa_label="No matching filings found",
            visa_color="gray", visa_icon=":material/help:", filings=0,
        )  # fmt: skip
        match_card(key="a", apply_url="https://boards.example.com/1", missing_shown=2, **common)
        match_card(key="b", apply_url="javascript:alert(1)", **common)

    at = AppTest.from_function(script, default_timeout=20).run()
    assert not at.exception, at.exception
    bodies = [m.value for m in at.markdown]
    assert "**Data \\[Eng\\]\\(http&#58;\\/\\/evil\\) &#58;red\\[x\\]**" in bodies
    assert not any("[Eng](http" in b for b in bodies)
    assert ":green-badge[:material/check: dbt (x)]" in bodies  # brackets became parentheses
    # the first card shows 2 of 3 missing skills, the second (default 5) shows all 3
    assert bodies.count(":red-badge[:material/close: Ray]") == 1
    assert ":gray-badge[+1 more]" in bodies
    assert next(c.value for c in at.caption) == "Acme \\*\\*Co\\*\\*"
    links = [b.proto.url for b in at.get("link_button")]
    assert links == ["https://boards.example.com/1"]  # a javascript: URL gets no button
    assert [p.proto.value for p in at.get("progress")] == [50, 50]  # 50% match bars
