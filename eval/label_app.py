"""Local labeling app for the benchmark. Run from the repo root:

    uv run streamlit run eval/label_app.py

Reads and writes ONLY eval/labels.csv and eval/sponsorship_labels.csv (copy them from the
templates first). It never reads predictions: the file layer refuses any path with "prediction"
in its name. Save & Next writes the row back (done=y) atomically and keeps a .bak of the previous
file. Close any spreadsheet that has the CSV open while you label here.
"""

from __future__ import annotations

import common
import streamlit as st
from label_io import (
    ENUM_OPTIONS,
    MAIN_ENUMS,
    SPONSOR_SHEET_VALUES,
    SheetSpec,
    build_specs,
    counts,
    highlight_html,
    is_labeled,
    next_unlabeled,
    read_sheet,
    save_row,
    validate_main,
    validate_sponsorship,
)

st.set_page_config(page_title="JobPulse labeling", layout="wide")

PREFIX = 100  # the "first N rows" milestone (rows are in a prefix-balanced order)
HELP = """
**Fill in only what the posting says.** An empty cell means "not stated"; mark the row done
anyway (Save & Next) if the posting states nothing.
- **salary**: advertised pay only (no equity/bonus). Numbers like `120000`, `$120,000` or `120k`.
  Currency (e.g. `USD`) and period are required when there is an amount.
- **sponsorship**: `offered` only if the posting explicitly says it sponsors; `not_offered` only
  if it explicitly says it does not (or requires existing work authorization). Otherwise leave
  it empty. Generic equal-opportunity text is not a statement.
- **skills**: tools/technologies named in the posting, separated by `;`, `,` or new lines.
"""


def _label(value: str) -> str:
    return value if value else "— not stated —"


def _goto(spec_key: str, index: int) -> None:
    st.session_state["_goto"] = (spec_key, index)


def main() -> None:
    specs = [s for s in build_specs(common.LABELS_PATH, common.SPONSOR_LABELS_PATH)]
    present = [s for s in specs if s.path.exists()]
    if not present:
        st.error("No label sheets found. From the repo root, run:")
        st.code(
            "cp eval/labels_template.csv eval/labels.csv\n"
            "cp eval/sponsorship_labels_template.csv eval/sponsorship_labels.csv"
        )
        return

    with st.sidebar:
        st.header("Labeling")
        spec: SheetSpec = st.radio(
            "Sheet", present, format_func=lambda s: s.title, key="sheet_choice"
        )
        missing = [s.path.name for s in specs if not s.path.exists()]
        if missing:
            st.caption(f"Not found (skipped): {', '.join(missing)}")

    sheet = read_sheet(spec.path)  # always re-read: the file on disk is the source of truth
    rows = sheet.rows
    n = len(rows)
    if n == 0:
        st.warning(f"{spec.path.name} has no rows.")
        return

    idx_key = f"idx::{spec.key}"
    jump_key = f"jump::{spec.key}"
    if idx_key not in st.session_state:
        first = next_unlabeled(spec, rows, -1)
        st.session_state[idx_key] = first if first is not None else 0
    goto = st.session_state.pop("_goto", None)
    if goto and goto[0] == spec.key:
        st.session_state[idx_key] = max(0, min(n - 1, goto[1]))
    idx = min(st.session_state[idx_key], n - 1)
    st.session_state[jump_key] = idx  # keep the jump box in step with the current row

    done_n, done_prefix = counts(spec, rows, PREFIX)
    with st.sidebar:
        st.progress(done_n / n, text=f"Labeled {done_n} / {n}  ({100 * done_n // n}%)")
        if spec.key == "main":
            p = min(PREFIX, n)
            st.caption(f"First {p} rows labeled: **{done_prefix} / {p}**")
        st.divider()
        c1, c2 = st.columns(2)
        c1.button(
            "◀ Prev",
            disabled=idx == 0,
            use_container_width=True,
            on_click=_goto,
            args=(spec.key, idx - 1),
        )
        c2.button(
            "Next ▶",
            disabled=idx >= n - 1,
            use_container_width=True,
            on_click=_goto,
            args=(spec.key, idx + 1),
        )
        nxt = next_unlabeled(spec, rows, idx)
        st.button(
            "Go to next unlabeled",
            disabled=nxt is None,
            use_container_width=True,
            on_click=_goto,
            args=(spec.key, nxt if nxt is not None else idx),
        )
        st.selectbox(
            "Jump to / edit a row (✓ = labeled)",
            range(n),
            key=jump_key,
            format_func=lambda i: (
                f"{i + 1:>3} {'✓' if is_labeled(spec, rows[i]) else '·'}  "
                f"{rows[i]['title'][:42]} — {rows[i]['company'][:18]}"
            ),
            on_change=lambda: _goto(spec.key, st.session_state[jump_key]),
        )
        with st.expander("Labeling rules"):
            st.markdown(HELP)

    flash = st.session_state.pop("_flash", None)
    if flash:
        st.toast(flash)

    row = rows[idx]
    pk = row["posting_key"]
    labeled = is_labeled(spec, row)
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader(row["title"])
        st.markdown(
            f"**{row['company']}** · row {idx + 1} of {n} · "
            + ("✅ labeled (editing)" if labeled else "⬜ not labeled yet")
        )
        st.markdown(highlight_html(row["description_text"]), unsafe_allow_html=True)
        st.caption(
            "Highlights: yellow = salary/$, blue = remote/hybrid/on-site, "
            "pink = sponsor/visa/work authorization/H-1B"
        )

    def k(name: str) -> str:
        return f"{spec.key}::{pk}::{name}"

    with right, st.form(f"form::{spec.key}::{pk}"):
        values: dict[str, str] = {}
        if spec.key == "main":
            for name in MAIN_ENUMS:
                opts = ["", *ENUM_OPTIONS[name]]
                cur = row[name] if row[name] in opts else ""
                values[name] = st.selectbox(
                    name, opts, index=opts.index(cur), format_func=_label, key=k(name)
                )
            a, b = st.columns(2)
            values["salary_min"] = a.text_input("salary_min", row["salary_min"], key=k("min"))
            values["salary_max"] = b.text_input("salary_max", row["salary_max"], key=k("max"))
            a, b = st.columns(2)
            values["salary_currency"] = a.text_input(
                "salary_currency", row["salary_currency"], key=k("cur"), placeholder="USD"
            )
            popts = ["", *ENUM_OPTIONS["salary_period"]]
            pcur = row["salary_period"] if row["salary_period"] in popts else ""
            values["salary_period"] = b.selectbox(
                "salary_period", popts, index=popts.index(pcur), format_func=_label, key=k("per")
            )
            sopts = ["", *ENUM_OPTIONS["sponsorship_statement"]]
            scur = row["sponsorship_statement"] if row["sponsorship_statement"] in sopts else ""
            values["sponsorship_statement"] = st.selectbox(
                "sponsorship_statement",
                sopts,
                index=sopts.index(scur),
                format_func=lambda v: v if v else "— not stated (unspecified) —",
                key=k("sp"),
            )
            values["skills"] = st.text_area(
                "skills (; , or new line)",
                row["skills"].replace("; ", "\n"),
                height=110,
                key=k("skills"),
            )
            values["notes"] = st.text_input("notes (optional)", row["notes"], key=k("notes"))
        else:
            sopts = ["", *SPONSOR_SHEET_VALUES]
            scur = row["sponsorship_statement"] if row["sponsorship_statement"] in sopts else ""
            values["sponsorship_statement"] = st.selectbox(
                "sponsorship_statement",
                sopts,
                index=sopts.index(scur),
                format_func=lambda v: v if v else "— choose —",
                key=k("sp"),
            )
            st.caption(
                "Blank means not labeled yet: choose `unspecified` if the posting "
                "does not state a sponsorship position."
            )
        submitted = st.form_submit_button("Save & Next", type="primary", use_container_width=True)

    if submitted:
        validate = validate_main if spec.key == "main" else validate_sponsorship
        clean, errors = validate(values)
        if errors:
            with right:
                for e in errors:
                    st.error(e)
            return
        save_row(spec, pk, clean)
        after = [r for r in read_sheet(spec.path).rows]
        nxt = next_unlabeled(spec, after, idx)
        target = nxt if nxt is not None else min(idx + 1, n - 1)
        st.session_state["_flash"] = f"Saved row {idx + 1}" + (
            "" if nxt is not None else " — every row is labeled 🎉"
        )
        st.session_state["_goto"] = (spec.key, target)
        st.rerun()


main()
