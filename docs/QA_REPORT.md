# JobPulse: QA audit before final fixes

**Date:** 2026-11-20 · **Status:** all P0, P1 and P2 findings fixed or explicitly deferred (see "Fix status" below); the audit text after that section is unchanged, as written before the fixes

> Related: [SPEC.md](SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [ROADMAP.md](ROADMAP.md) · [NOTES.md](NOTES.md)

## Browser QA round 2 (2026-11-20, real Chrome, America/New_York, desktop and 390 px)

An independent browser pass found the items below. All P0 and P1 and the cheap P2 are fixed in small commits on `main` (each after a green `make check`, 581 tests now); gold was rebuilt on MotherDuck, exported and uploaded to `gold-latest` (6,303,744 bytes). Each fix was looked at in headless Chrome with the time zone set to America/New_York. **Reboot the Streamlit app after the push.**

| ID | Status | What was done |
|---|---|---|
| B-P0-1 Market filters divide by unfiltered totals | **Fixed** | A filtered view uses only the filtered set: its own city share, no "The other N" sentence, the fourth KPI hidden. Test `test_market_overview_filtered_view_uses_only_the_filtered_set` fails on the old code. Chrome, Seniority = Intern: "has the most open postings: 9, 23% of all 39". |
| B-P0-2 Daily charts one day early | **Fixed** | Date axes are text labels made in Python (`day_label`), never JS dates; no `temporal` axis is left in the app (a test greps for it). Chrome: busiest day Nov 11 = 217 and the first bar is Nov 11. Also fixed in gold: `agg_llm_cost_daily` and `closed_date` use the UTC date (a build on a US laptop had put evening calls on Nov 09, before data existed). Test fails on the old SQL. |
| B-P0-3 Salary insight vs heatmap | **Fixed** | The sentence is computed from exactly the rows drawn, and says "small sample" under 10 salaries. Now: Principal ML/AI $329K (from 5). |
| B-P0-4 "fewer than five" toggle did nothing | **Removed** on Advertised salary (the table always lists thin groups with dashes; an empty chart says how many salaries and groups exist). The LCA toggle works and stays. |
| B-P0-5 Heatmap with 1-2 cells | **Fixed** | Under 3 cells a table; text colour follows the cell's colour step; legend "$138K ... $290K"; currency sign follows the currency (CAD 175K); one half-up rounding function for chart, table, sentences. |
| B-P0-6 205 companies on one day | **Fixed** | New `n_authoritative_companies` (distinct boards). Chrome: 43 every day. Test with three attempts per board. |
| B-P0-7 Card without Apply link | **Fixed** | "Careers page" button: `careers_url` in `config/employers.yaml` (Lyft, Squarespace), else the company's board on its ATS. Regression test on the page. |
| B-P1-8 Home headline | **Fixed** | "515 open data & AI roles" leads, "of 8,445 open postings across 43 companies" below; details moved into help tooltips. |
| B-P1-9 Resume parsing | **Fixed** | First-line title kept; a title next to an email survives; "2024-2025" is no longer a phone number; "MS ... student" is Master's (but not "MS Excel"); "BI" inside "Power BI" skipped; dashboard = dashboarding and data analysis = data analytic; stop-list gained mbr, qbr, metric, ai assisted tool, business analytics; "Google Sheets" displays properly. Stop-list words are also dropped from posting skills. |
| B-P1-10 Ranking | **Fixed** | Postings in the role family the resume's title names are ordered higher by `match.role_family_boost` (0.15); the shown match % is unchanged; visa evidence is not in it (tested). |
| B-P1-11 H-1B badge | **Fixed** | Postings whose places are all outside the US show "H-1B applies to US roles only" (also a filter option). Postings with no place (remote, multi-location) keep the badge because the US cannot be ruled out. |
| B-P1-12 Intern pay | **Fixed** | Shown as posted everywhere ($40 per hour, $12,100 per month). **Cause of the $285K rows:** the seniority is right (the title says Intern); the DoorDash intern pages list a pay table for full-time levels I4 to I6 ("$130,600-$192,000 ... $193,800-$285,000") and the extraction took the lowest and highest. A yearly intern range wider than 2x (`pay.intern_max_range_ratio`) is shown as "not clearly stated" and left out of the intern table. |
| B-P1-13 Map | **Fixed** | Wheel scrolls the page (checked with real wheel events in Chrome); the map is on Market overview only (where the filters apply), attribution printed once. |
| B-P1-14 Company lookup | **Fixed** | Search box; no match says "We track 43 companies" and lists them. |
| B-P1-15 LCA tab | **Fixed** | Defaults to data roles ("Other roles" opt-in) and has a chart of median offered wage by role and worksite. |
| B-P1-16 Plaid extraction | **Fixed (owner approved)** | Gold now prefers the ATS's structured workplace type and pay (Ashby, Lever) over the AI's reading; no re-extraction, $0 spent. Coverage of analyzed postings: advertised salary 376 to 408 of 515 (73% to 79%); remote "Not stated" 189 to 138 (37% to 27%). Ashby salary 18 to 50 of 55; Ashby remote unstated 33 to 4; Lever remote unstated 22 to 0 (Lever rarely states pay, still 7). |
| B-P2 | **Fixed** | Rounding ($173K everywhere), 5 axis ticks and whole counts, empty paste no longer says "scanned PDF", 507 vs 515 explained on the page (8 postings list no skills), "Inc.." and "1 more is", raw timestamp gone (and failed-run times now converted to UTC; they showed local time labelled UTC), Home link "Pipeline health", chip "Intern and junior" shows in full, Visa role chips collapsed, KPI titles wrap at 800 px. Pipeline health explains the failed run in plain words (which step, when, a later run succeeded). |

**Why Plaid shows remote "Not stated" and salary "Not advertised".** Plaid is on Ashby. The Ashby API gives `workplaceType: "Hybrid"` and `compensation` ($249,120 - $367,920 for one Staff ML role) as structured fields, not in the description text. The pipeline fetches them (`includeCompensation=true`) and keeps the raw payload, but `bronze/normalize.py` maps only title, location, description and apply URL, so the AI never sees them. Same for Lever (`workplaceType`, `salaryRange`). Measured on analyzed postings: Greenhouse 80% have a salary and 31% have remote "Not stated"; Ashby 33% and 60%; Lever 32% and 100%. It affects 77 of 515 postings (15%). Fixed afterwards in gold (`int_structured_fields`, read from the raw bronze payload), without touching ingestion or content hashes.

**Not verified:** a real touch swipe on a phone, the map tiles (headless Chrome draws no WebGL tiles), the live Cloud build, an uploaded PDF in a browser.

## Fix status (2026-11-20, after the owner approved all P0, P1 and P2)

Everything below was fixed in small commits on `main`, each after a green `make check`. Gold was rebuilt on MotherDuck (`build-gold`, 107 dbt checks passed), exported and uploaded to the `gold-latest` release (6,041,600 bytes, 16 tables, 9,681 postings), so the live app has the new schema. Every page and a sample resume ran without exceptions on that exact file (`AppTest`). Tests: 456 before, 536 now.

**Not verified:** no browser look (mobile layout, chart appearance, Vega labels); the stale-module fix has not been triggered on Streamlit Cloud; the `publish` job has not run with the new code (the file was exported and uploaded by hand from this machine, the next daily run will do it in CI).

| ID | Status | What was done / why deferred |
|---|---|---|
| P0-1 | **Fixed** | `read_gold` runs on its own cursor per query. `tests/unit/test_qa_hardening.py`: 8 threads x 300 distinct queries; it fails on the old code (verified) and passes now. |
| P0-2 | **Fixed** | pypdf `apply_configuration` limits (2 MB per decoded stream, `config/resume.yaml` `upload.max_pdf_decoded_bytes`). A 60 MB-decoded bomb is refused without a memory spike (tested). |
| P0-3 | **Fixed, not seen on Cloud** | `app/code_reload.py`: a stamp of `src/jobpulse/**/*.py` in a process-lifetime cache; on change every `jobpulse*` module is dropped before the pages import. Also `server.folderWatchList = ["src"]`, a friendly "reload this page" on a late `ImportError`, and `sys.path` is extended once (P3-4). Tested in a subprocess. |
| P1-1, P1-2 | **Fixed** | Shared `app_ui.clean()` / `is_missing()`: NaN, NA, None and blanks never print as "None" or "nan" (Resume match City, Visa quote cards). |
| P1-3 | **Fixed** | "Experience level" filter on Resume match (groups in `config/resume.yaml` `levels`), pre-selected from years and titles (up to 2 years or an intern title: entry and mid; 6+ years: senior and staff). A filter and default only, never in the score. |
| P1-4 | **Fixed** | `norm_skill` keeps series, bias, Kubernetes, Databricks, pandas, analytics, statistics and the *Ops names, turns -ies into -y, merges api/apis and kpi/kpis; gold rebuilt (2,612 skills). Resume skills need 3 open analyzed postings (`parse.min_posting_frequency`) and a reviewed stop-list (`parse.ignore_skills`, 22 words added: cte, demo, english, join, language, star, agent, dag, delta, ci, risk, research, training, inference, vision, modeling...). Please review that list: some entries (inference, agent) are real skills in other resumes. |
| P1-5 | **Fixed** | Home says "over N of the M non-intern postings with a USD salary (of K analyzed)". Median is now $218K (interns left out). |
| P1-6 | **Fixed** | `fct_postings.apply_url`: https only, no credentials or spaces, and the host is either one of `gold.apply_url_hosts` (Greenhouse x2, Lever, Ashby) or the company's own career domain or a subdomain, from `domains` in `config/employers.yaml` (23 companies; vendors such as careerpuck are not listed). Owner approved the own-domain rule on 2026-11-20. `LinkColumn` "Apply" in Resume match. **Coverage: 483 of the 515 analyzed open roles (94%) and 8,277 of 8,445 open postings (98%) have a link** (was 232, 45%, with ATS hosts only). The 32 without are Lyft (31, hosted on a shared vendor) and Squarespace (1). |
| P1-7 | **Fixed** | The same company and title in several places is one row with a "Location" column ("3 locations: ..."). |
| P1-8 | **Fixed** | Resume text is shown with `st.text`; title lines with an email, phone, URL or profile link are dropped; the privacy sentence now says what is kept. **A person's name inside a title line ("Alex Kim, Data Scientist") is still kept for the session**; it is not detected. |
| P1-9 | **Fixed** | Fewer than 3 recognized skills: "Add your tools and methods", nothing ranked; zero skills has its own message; "English resumes only" note. |
| P1-10 | **Fixed** | Three cards: skills, years and education, roles. Years come from date ranges (overlaps counted once, education lines excluded) or "N years of ... experience", else "Not found". |
| P2-1 | **Fixed** | "Top skills to learn" from the best 20 matches, with the stop-list applied. |
| P2-2 | **Fixed** | `_pdf_text` converts any parser exception into "That PDF could not be read" without echoing it. |
| P2-3 | **Fixed** | `check_schema` compares the key tables' columns at connect (one friendly message); `AppDataError.public` is all visitors see; setting names and SQL detail go to the server log (P3-3). |
| P2-4 | **Fixed** | A banner on every page (except Pipeline health, which has its own) when the newest snapshot is older than the freshness limit. |
| P2-5 | **Partly fixed** | 2 attempts, 20 s timeout, no new attempt for 60 s after a failure (was 5 attempts, 17.5 s per rerun). Not done: a spinner and a "Retry" button. |
| P2-6 | **Fixed** | City coordinates parsed once per process. |
| P2-7 | **Fixed (relabelled)** | The Home card says "The change compares settled days: 19 Nov vs 12 Nov". The headline stays today's count, consistent with the other pages. |
| P2-8 | **Fixed** | Hiring trends opens on the largest role, hides roles under 10 analyzed postings (`app.yaml` `samples.min_group_n`), shows n; the salary insight ignores groups under 10. |
| P2-9 | **Fixed** | README numbers re-run from the cited scripts (9,681 / 8,445; 515 (100%); 72.8%; 62.3%; 2,612; 91,119 / 98,700; 2.3%; $0.25) with the regenerate commands. |
| P2-10 | **Fixed** | Pipeline health shows pipeline spend ($0.25, 641 sync calls and 15 escalations) and evaluation ($0.27, 100 calls) separately. |
| P2-11 | **Fixed** | The ingest chart counts each company once per day (the kept snapshot). |
| P2-12 | **Fixed** | The warning names the step, data date and start time and says whether a later run of the step succeeded; failed runs sort first. |
| P2-13 | **Fixed** | Hourly inference only for 10 to 150 per hour (`config/extraction.yaml`); earlier inferences outside that range were reverted in `extract.extractions` (never a model-stated period); the $354K to $532K posting no longer appears. Interns are left out of the Home median and of the Salary heatmap and table; a separate "Internship pay (as posted)" table shows hourly or monthly pay in its original unit with n. Resume match labels converted pay as a yearly equivalent. |
| P2-14 | **Fixed** | `ignored_locations`, aliases, `FI`, more `known_places` and a `not_state` hint: the junk labels are gone, Austin and Denver have their state, "Munich, DE" is Germany. City labels 385 to 365. The three Washington-DC spellings in the report were not reproduced and were not changed. |
| P2-15 | **Fixed** | Top 5 missing skills with "+N more", wider Title. No per-row detail panel. |
| P2-16 | **Fixed** | Percentile cells show "—" and groups under five salaries are behind a toggle (Salary, LCA tab). |
| P2-17 | **Fixed in code, not seen** | Theme-aware value-label colour, a series palette with a 6:1 lightness gap, dashed lines per series, a quantized heatmap whose text is at 4.5:1 or better (tested). Charts still have no text alternative beyond the key-insight sentence; `user-scalable=no` is Streamlit's. |
| P2-18 | **Fixed in code, checked at 390 px** | Checked in a headless Chrome at a real 390 px viewport (device emulation) on Market overview, Salary, Hiring trends, Visa and a Resume match card. Fixed from layout code: the shared chart template now fits axes and padding inside the container (long category labels and the value labels past the longest bar were clipped), the salary heatmap has a minimum width and scrolls sideways, the Resume results are cards instead of a 10-column table (the table is in a "Show as table" expander), raw tables are in "Show data" expanders, hero and page padding shrink on phones. The map no longer traps touch scroll: below 640 px it ignores pointer input (CSS, checked in Chrome: `pointer-events` is none at 390 and 639 px, auto at 640 and 1280 px), so it is a picture on a phone. Not done: the Resume page was checked with a demo card, not an uploaded resume; one long label ("Reinforcement learning") was still clipped once on Hiring trends and may depend on the web font loading before the chart measures it. |
| P2-19 | **Fixed** | Shares use all companies' filings for the selected roles; the caption says the named roles are about 17% of filings; "No matching filings found"; fiscal years come from data. |
| P2-20 | **Fixed** | Market overview filter bar (role family, seniority, remote policy) applies to KPIs, charts and the map; a filtered view covers analyzed postings only and says so. |
| P3-1 | **Half fixed** | URL guard is exact. Deferred: a `.sha256` next to the asset. |
| P3-2 | **Fixed** | Comma joins are refused. |
| P3-3, P3-4, P3-10 | **Fixed** | See P2-3, P0-3, P1-4. |
| P3-5 | **Half fixed** | The query cache key includes the file's modified time; a corrupt local copy is deleted so the next run downloads again. Old connections are still not closed. |
| P3-7, P3-8, P3-12, P3-15 | **Fixed** | Fiscal years and company count from data; 99.7% shows as 99.7%; the 6% explanation; a weekend note. |
| P3-9 | **Fixed** | NFKC and zero-width normalization; an oversize paste is cleared. |
| P3-11, P3-16 | **Mostly fixed** | Developer wording removed from Home, Market overview and Pipeline health; Home says "refreshed daily". |
| P3-13 | **Fixed** | Regression tests for concurrency, decompression, malformed PDFs, NaN, reload, schema, outage cooldown, contrast. The DuckDB `Pandas4Warning` remains (library). |
| P3-6, P3-14, P3-17, P3-18 | **Deferred** | Date formats (cosmetic); screenshots (owner); Streamlit's response headers (not ours); `pip-audit` (a new tool, needs your OK). |

**Reboot the Streamlit app** after pushing: the code on Cloud changed in `src/`, and the old process still holds the old modules.

## 1. Scope, method and limits

**What was tested.** The seven app pages and the Resume match flow, against the **real published gold export** (`jobpulse_gold.duckdb`, 6,041,600 bytes, release asset last modified 2026-11-20 17:53 UTC, built 12:53 ET). It was downloaded once from the public GitHub release and opened **read-only** from a scratch copy. Nothing touched prod S3 or MotherDuck, and nothing was written anywhere but the scratchpad and this file.

**How.**
- *Data correctness:* every number, chart input and insight sentence was recomputed with SQL written independently of the app's SQL (different formulations, straight from `fct_postings` and the aggregates), then compared with what each page rendered via Streamlit `AppTest`.
- *Resume match:* 20 synthetic inputs (text, PDF, bad files) through the project's own `read_resume` / `extract_profile` / `rank_postings`, plus the page itself through `AppTest` (analyze, Delete, filters).
- *Robustness:* the published-file connector exercised with mocked HTTP transports; 200 page runs sweeping every widget option; a 4,000-query concurrency test; a 1,500-case PDF fuzz; decompression-bomb PDFs.
- *Baseline:* `make check` on current `main`: **green** (ruff, ruff format, mypy strict, 456 passed / 3 deselected, terraform fmt + validate, gitleaks "no leaks").
- *Live app:* only a few read-only GETs to the public root URL (redirect chain and headers).

**Not verified (be aware).**
- **No browser rendering.** Mobile (390 px), chart appearance and Vega label collisions are reasoned from code and computed contrast, not seen.
- **No Streamlit Cloud runtime measurements.** Memory is measured locally (about 166 to 197 MB for all pages in one process) and compared with the 1 GB limit by arithmetic.
- The `publish` job log, the Cloud build and a dependency CVE audit (`pip-audit` on pypdf and the rest) were not run.
- The stale-module `ImportError` was not re-triggered on Cloud. The root cause is read from Streamlit's source (see P0-3).

**Reproduction.** The scripts live in the session scratchpad (`.../scratchpad/work/*.py`, resumes in `.../scratchpad/resumes/`) and are **not committed**. Per the "metrics come from a script" rule, I can promote them to `scripts/qa/` once approved. The key SQL is inlined in section 3.

**Severity and effort.**
P0 = wrong data or an outage without owner action, or publicly triggerable harm · P1 = visibly wrong or broken for visitors · P2 = misleading, slow, fragile or inconsistent · P3 = polish and hardening.
Effort: **S** ≤ 1 h · **M** 1 to 3 h · **L** 3 to 6 h, each including tests and `make check`.

## 2. Summary

| Severity | Count | Headline |
|---|---|---|
| **P0** | 3 | One DuckDB connection is shared across visitor sessions (crashes now, wrong results possible, cached an hour). A ~30 KB PDF can spike memory by 350 to 470 MB. Code-only pushes leave stale `jobpulse` modules, so the app throws `ImportError` until rebooted. |
| **P1** | 10 | "None" and "nan" in cities, student results led by Senior and Manager roles, skill noise (11 of 28 skills junk for the student resume), Home pay caption contradicts Salary, no apply links, duplicate-looking rows, resume lines rendered as markdown, thin resumes get confident results. |
| **P2** | 20 | Schema drift leaks raw errors, staleness invisible, 1.4 s CPU per Market-overview rerun, README and app numbers disagree, mislabelled cost, inflated chart, truncated tables, contrast, mobile. |
| **P3** | 18 | URL guard bypass, validator gap, hard-coded text, formats, small copy issues. |

**What held up (credit where due).**
- **Numbers tie out.** Every headline number, chart input and insight sentence I recomputed matched, apart from the specific mismatches listed in section 3. LCA totals agree across five tables (9,874 certified), open postings agree on Home, Market, Visa and Pipeline Health (8,445), and salary medians and percentiles match to the cent.
- **Upload validation is solid.** Oversize, wrong type, fake PDF, binary, non-UTF-8, corrupt, encrypted-style, scanned, over-page-limit and over-length inputs were all refused with clear messages.
- **No crashes in 200 page runs** across all 43 companies, every currency × city pair, every role family and the resume filters (including zero-result combinations).
- **The failure modes of the data download degrade cleanly:** 404, 403, 5xx, garbage, empty, truncated and wrong-table files all produce one friendly message, and a stale copy keeps serving when a refresh fails.
- **Delete works.** After **Delete my resume data** no profile or filter key remains. All queries are parameterized and no resume text reaches SQL, a cache or the database.

## 3. Data correctness: reconciliation

All "app" values are what the page rendered under `AppTest`; "SQL" is my independent recomputation. ✅ matches · ⚠️ matches but misleading · ❌ mismatch.

| Page | Item | App | Independent SQL | Result |
|---|---|---|---|---|
| Home | Open postings | 8,445 | `count(*) where is_active and not is_probable_duplicate` = 8,445 | ✅ |
| Home | "+16 vs last week" | +16 | open on 11-19 = 8,475, on 11-12 = 8,459 → +16; the headline 8,445 is the 11-20 state | ⚠️ P2-7 |
| Home | Analyzed | 515 of 515 (100%) | 515 target roles, 515 extracted | ✅ |
| Home | Most requested skill | Python, 60% of 515; next ML, SQL | 308, 239, 219 postings (59.8%) | ✅ |
| Home | Median advertised pay | $213K | median of USD annual midpoints = 212,750 over 316 postings | ✅ number |
| Home | Pay caption | "316 of 515 analyzed postings that state a salary" | 376 state a salary (Salary page: 73%); 316 is USD with a yearly figure | ❌ P1-5 |
| Home | Companies with filings | 42 of 43 | 42 with `n_lca_certified > 0` | ✅ |
| Home | Most filings | DoorDash 1,083 | 530 + 553 | ✅ |
| Market | Companies / Cities | 43 / 385 | 43 / 385 distinct city labels | ✅ ⚠️ 385 includes junk and duplicate labels (P2-14) |
| Market | Databricks 912, "11% of 8,445" | as shown | 912 / 8,445 = 10.8% | ✅ |
| Market | ML/AI largest: 211 of 515 (41%) | as shown | 211 / 515 = 41.0% | ✅ |
| Market | Remote 33%, "37% do not say" | as shown | 169 / 515 = 32.8%; 189 / 515 = 36.7% | ✅ |
| Market | NYC 695 = 13% of 5,290 | as shown | 695; 5,290 with a city (62.6%, shown as 63%) | ✅ |
| Market | 3 cities hold 33% of 4,868 mapped | as shown | (695 + 584 + 327) / 4,868 = 33.0%; 4,868 drawn, 422 left off | ✅ |
| Trends | New 1,409 over 10 days, busiest 217 | as shown | 217+131+186+103+2+97+148+176+184+165 = 1,409 | ✅ |
| Trends | Closed 1,236 over 9 days, busiest 195 | as shown | 161+139+143+110+6+140+186+156+195 = 1,236 | ✅ |
| Trends | "SQL in 94% (Analytics Engineer)" | as shown | 15 / 16 postings | ⚠️ n = 16, not shown (P2-8) |
| Salary | 73% state a salary; "yearly figure known 100%" | 73%, 100% | 376 / 515 = 73.0%; 375 / 376 = 99.7% | ✅ / ⚠️ rounds up (P3) |
| Salary | Medians, P25, P75 (18 USD groups) | table | quantile_cont recomputation | ✅ exact |
| Salary | Insight "Principal ML/AI $328K (from 5)" | as shown | median 328.5K, n = 5 | ⚠️ smallest allowed sample wins (P2-8) |
| Salary / LCA | 9,874 certified, 22 excluded, 100% usable | as shown | 9,852 usable + 22 = 9,874 (99.8%) | ✅ / ⚠️ rounds up |
| Visa | DoorDash 1,083; FY25 530; FY26 553; Level IV 445 | as shown | same | ✅ |
| Visa | "Pinterest 294 = 22% of the 1,325 shown" | as shown | 1,325 = top-15 sum; all data-role filings = 1,665 (Pinterest 17.7%) | ⚠️ P2-19 |
| Pipeline | Data quality table | 7 rows | matches `agg_data_quality` | ✅ |
| Pipeline | "Total spend $0.52, 756 calls" | as shown | 0.249 sync + **0.269 eval** (100 calls on 11-11) | ⚠️ P2-10 |
| Pipeline | Ingest outcomes chart | 205 on 11-10 | 43 employers; bars sum every re-run attempt | ❌ P2-11 |
| README | Key numbers table | 9,516 / 8,464; 441 (86%); 511; 2,484 skills; 73.2%; 62.4%; $0.22 | now 9,681 / 8,445; 515 (100%); 515; 2,615 (2,517 used); 73.0%; 62.6%; $0.25 sync | ❌ P2-9 |

**Cross-page agreement.** Open postings (Home, Market, sum of Visa "Open postings", Pipeline Health denominator) are all 8,445. Analyzed postings are 515 on Home, Market, Salary and Pipeline Health. H-1B: Home's 42 of 43 equals the count of non-zero rows in Visa's "All companies" table, and the Salary LCA tab's 9,874 equals the sum of Visa's certified column. Visa's "Top sponsors" (data roles, Pinterest first) and Home's "most filings" (all roles, DoorDash) are different scopes and are not explained side by side.

## 4. Findings

### P0: broken, wrong or unsafe

#### P0-1 · All visitors share one DuckDB connection (wrong or crashing results, cached for an hour)
- **Where:** `app_ui._connection` (`st.cache_resource`) → `app_data.read_gold` (`con.execute(...).df()`).
- **Steps:** open the published file once, share that connection object across N threads (this is what Streamlit does, one thread per session), and run distinct parameterized queries in a loop. Script: `work/conc.py`, `work/conc2.py`.
- **Expected:** every thread gets its own query's result.
- **Actual:**

| Threads | Queries | Exceptions (`TypeError: 'NoneType' object is not subscriptable`) | Wrong result |
|---|---|---|---|
| 2 | 2,000 | 1 | 0 |
| 3 | 3,000 | 42 | 0 |
| 4 | 4,000 | 9 | 0 |
| 8 | 2,400 | 15 | **1** (a thread received another thread's rows) |
| 8, per-thread `con.cursor()` | 2,400 | **0** | **0** |

  DuckDB's `execute()` stores the result on the connection object, so interleaved sessions read each other's result. A wrong DataFrame is then stored by `st.cache_data` and shown to everyone for up to an hour.
- **Likelihood:** low at portfolio traffic (queries run only on cache misses), but pages issue 5 to 12 queries per script run and the whole cache expires on the hour, so two visitors in the same second can collide.
- **Fix (S):** in `read_gold`, run on `con.cursor()` (cheap, per call), or guard with a lock. Add a threaded regression test.

#### P0-2 · A small PDF can spike server memory by 350 to 470 MB (public, unauthenticated)
- **Where:** Resume match → upload; `resume/parse.py::_pdf_text`. The 2 MB limit applies to the *compressed* file.
- **Steps:** build a one-page PDF whose content stream is `zlib`-compressed repeated `BT (x) Tj ET` (`work/mkbomb.py`), upload it, and measure the parse.
- **Expected:** refused quickly with little memory.
- **Actual** (local, baseline process 49 MB RSS):

| PDF size | Decompressed | Time | Peak RSS | Outcome |
|---|---|---|---|---|
| 10 KB | 5 MB | 4.3 s | 322 MB | "text longer than 60,000" |
| 16 KB | 8 MB | 1.5 s | 404 MB | "could not be read" |
| 39 KB | 20 MB | 1.5 s | 422 MB | "could not be read" |
| 136 KB | 70 MB | 1.5 s | 515 MB | "could not be read" |

  The harm happens before the refusal. With about 200 to 300 MB already used by the app, two near-simultaneous uploads approach Community Cloud's 1 GB limit (not tested on Cloud). It also blocks the thread and the GIL for seconds.
- **Fix (S, verified):** wrap parsing in `pypdf._configuration.apply_configuration(zlib_maximum_output_length=…, lzw_…, run_length_…, jbig2_…, array_based_stream_maximum_output_length=…)` with a ~2 MB cap. I tested it: the same four bombs are refused in **0.0 s at 52 MB RSS**, and `11_plain.pdf` and `12_tables.pdf` still parse. pypdf 7 removed the old `pypdf.filters.*_MAX_*` globals, so use the Configuration API. For defence in depth, also limit page content size and consider a worker with a memory cap.

#### P0-3 · After a code-only push, the app keeps stale `jobpulse` modules and fails with `ImportError`
- **Where:** `app/streamlit_app.py` adds `src/` to `sys.path`; pages do `from jobpulse.app_ui import …` (25+ names).
- **Cause (from Streamlit's source):** the file watcher only watches files under the main script's folder (`LocalSourcesWatcher._file_should_be_watched` checks `_script_folder`, i.e. `app/`) plus `server.folderWatchList` (empty by default). `src/jobpulse/*` is outside `app/`, so after `git push` the **pages reload but `sys.modules['jobpulse.*']` stays the old version**. A page importing a name that exists only in the new `app_ui.py` raises `ImportError: cannot import name …`. This matches the incident after the recent push. As far as I know, Community Cloud restarts the process only when `requirements.txt` changes (not verified here).
- **Steps (to reproduce on Cloud):** push a commit that adds a function to `app_ui.py` and uses it from a page, without touching `requirements.txt`.
- **Expected:** new code takes effect. **Actual:** `ImportError` until someone presses "Reboot app".
- **Fix (M):** at the top of `streamlit_app.py`, compute a stamp (max mtime or hash of `src/jobpulse/**/*.py`), compare with a value kept in a long-lived module attribute, and on a change delete every `jobpulse` / `jobpulse.*` entry from `sys.modules` before pages import them. Also try `server.folderWatchList = ["src"]` in `.streamlit/config.toml` as a second layer (unverified on Cloud), and wrap page imports so an `ImportError` shows a friendly "app updating, reload" message instead of a traceback. Also stop re-inserting into `sys.path` on every rerun (P3-4).

### P1: visibly wrong or broken

#### P1-1 · "City" shows `None` for most Resume match rows
- **Steps:** paste any resume (`03_ml_phd.txt`) → Find matching postings → look at the City column.
- **Expected:** "No single city". **Actual:** `None`/blank in **36 of 50 rows** (72%).
- **Cause:** `m.posting.info["city"] or "No single city"` — pandas gives `NaN`, a float, which is truthy, so the fallback never fires (`resume_match.py`).
- **Fix (S):** treat NaN via `pd.isna`; one `clean(value)` helper shared by every page (also P1-2). Better, show the *location label* (see P1-7).

#### P1-2 · Visa page quote cards print "(nan)"
- **Steps:** Visa intelligence → "Search for a company" → **Affirm** or **Scale AI** → "What this company's postings say".
- **Expected:** "(location not stated)". **Actual:** `**Analytics Lead, Deposit Fraud Risk** (nan): Sponsorship not available`. **6 of the 10** active quote cards (Affirm 3, Scale AI 3).
- **Cause:** same NaN-is-truthy pattern in `visa.py`. **Fix (S):** same helper.

#### P1-3 · No experience-level awareness: a student is shown Senior and Manager roles first (known issue)
- **Steps:** `01_student_analyst.txt` (intern, 1 year) → results.
- **Actual:** top result "Senior Data Analyst" (71%); the top 10 are 6 senior, 2 manager, 1 mid, 1 intern; of the top 50, 23 senior, 3 manager, 2 staff, 13 intern. The PhD resume gets 10 intern roles and several Manager and Fellowship roles in its top 50. There is no seniority filter or signal anywhere, although `fct_postings.seniority` exists (pool: senior 177, staff 123, mid 56, manager 47, intern 39, junior 9, director 14, principal 17, unspecified 18).
- **Test of the idea:** restricting the student's pool to intern/junior/mid/unspecified (124 postings) gives sensible results ("Data Analyst, Intern", "Analytics Engineer Intern", "Data Science, Metrics"). Feasible with no new data.
- **Fix (M):** add an "Experience level" multiselect (intern, junior, mid, senior, staff+, manager+), pre-selected from the resume (years ≤ 2 or an intern title → entry levels; PhD → senior plus staff). Keep it a **filter or default, never part of the score**, like sponsorship. Needs owner OK (new feature under the frozen scope).

#### P1-4 · Skill noise and mangled names
- **Steps:** `01_student_analyst.txt`. **Actual:** "Skills recognized: 28" of which **11 are noise or too generic** — Cte, Demo, English, Join, Language, Star, User storie, Problem solving, Visualization, Analytic, Business analytic (from "CTEs", "ran a demo", "Languages: English", "STAR method", "joins"). The senior resume shows Agent, Dag, Delta, Modeling, "Etl/elt", "Github action"; the PhD resume Research, Training, Inference, Vision.
- **Impact on ranking:** none measured (top-8 identical with and without the noise); it is a **credibility** problem because the list is shown to the user.
- **Two causes:**
  1. `norm_skill` strips a trailing "s" blindly: *stories → storie, queries → querie, strategies → strategie, series → serie, bias → bia, Windows → window, Kubernetes → kubernete, pandas → panda*. The display map covers about 100 names; 24 more mangled names remain (3 in at least 3 postings: "big data technologie", "indexing strategie", "retrieval strategie"). `api`/`apis` and `kpi`/`kpis` are separate canonical skills.
  2. `dim_skill` has 2,615 skills, 2,517 of them used by at least one of the 9,681 tracked postings; of those, **1,530 (61%) appear in a single posting** and only 354 in five or more. Hundreds are ordinary words (documentation, security, memory, option, text, identity, collection, filter, loop, mode, training, matching…). `ignore_skills` covers about 25.
- **Fix (M):** (a) fix `norm_skill` for *-ies → -y*, *-ses*, known "-s" words (needs a gold rebuild; until then extend `skill_display.yaml`); (b) for the **resume only**, require a minimum posting frequency (for example ≥ 3 open postings, config-driven) and extend `ignore_skills` with a reviewed stop-list (cte, demo, english, join, language, star, agent, delta, ci, risk, research, training, inference, vision, modeling, analytic, dag…); (c) show noise-free counts.

#### P1-5 · Home pay card contradicts the Salary page
- **Steps:** Home → "Median advertised pay"; then Salary → "Postings with an advertised salary".
- **Actual:** Home: "over **316 of 515** analyzed postings that **state a salary**". Salary: **73% (376 of 515)** state a salary. 316 is the USD-with-a-yearly-figure subset (55 are CAD, INR, PLN and others; 4 have no currency).
- **Fix (S):** reword to "316 of the 376 postings with a USD salary" and say what the median covers (data and AI roles, midpoint of range, USD, intern stipends annualized, see P2-13).

#### P1-6 · No apply links (known issue)
- **Actual:** no page links to a posting. `apply_url` exists in silver (`dim_postings`) but not in `gold.fct_postings`, so the export lacks it.
- **Fix (M, schema change):** add `apply_url` to `fct_postings` (+ `export-gold` test), show a `st.column_config.LinkColumn` in Resume match (and the Visa quote cards). **Security:** accept only `https`, strip credentials, allow-list hosts (`boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`) or a company's own https domain, and render as a link, never HTML. Fixtures show these three ATS hosts. Needs owner OK (gold schema).

#### P1-7 · Identical-looking rows in Resume match; location hidden
- **Steps:** PhD resume → rows 2 to 4. **Actual:** 7 of 50 rows share company + title (for example three "Manager, Machine Learning Engineering, Checkout" at 60%) and differ only by country and currency (GBP, PLN, EUR), but the City column is `None` (P1-1) so they look like duplicates. The Visa quote cards have a milder version: Scale AI shows three near-identical "Machine Learning Fellow…" cards whose titles differ only by "(UK)" / "(Canada)" (and their city is "(nan)", P1-2).
- **Fix (M):** expose a "Location" column built from the posting's place or country (gold has `city_country`), or collapse same company + title + skill set into one row with "3 locations".

#### P1-8 · Resume "title lines" are rendered as markdown and can hold personal data
- **Steps:** paste `Data Engineer ![t](https://attacker.example/p.png?u=1)` and `Analyst [click](https://evil.example)` (each ≤ 60 chars, with a title keyword).
- **Actual:** `st.write("; ".join(profile.titles))` passes them to the Markdown renderer unescaped: an external image would load (a tracking-pixel channel), links render, `$a$` renders as LaTeX, `:material/delete:` renders as an icon. (Streamlit sanitizes raw HTML: `<img onerror>` did not execute.) Self-inflicted for the uploader, but a shared "resume" file could do it.
- **Privacy:** "title lines" are any ≤ 60-char line containing *analyst/engineer/…*, so the student resume stored and displayed `Data Analyst Intern | priya@example.com`, and the emoji resume stored `👩‍💻 Alex Kim — Data Scientist 🚀` (a person's **name and email**). The privacy copy says "a few title lines", which understates this.
- **Fix (S):** render with `st.text`/an escaping helper (`escape_markdown`), strip emails, phone numbers, URLs and obvious names from title lines (or keep only the matched keyword phrase), and adjust the privacy sentence.

#### P1-9 · Thin or empty resumes get confident-looking results; wrong message for zero skills
- **Steps:** paste just `Python`. **Actual:** 50 rows, best 20%, no caution. Paste "I like gardening…": "Skills recognized 0" then "**No postings match with the current filters**" although no filter is set.
- **Also:** an empty paste says "No text found. A scanned PDF (an image) cannot be read." A non-English resume (Spanish, Chinese, Hindi) yields only the English tech words, "Not found" for years and titles, and no hint that the language is unsupported.
- **Fix (S):** below N recognized skills (config, say 3) show "Too few skills to rank. Add your tools and methods." and don't rank; use separate messages for empty text, zero skills and no matches; add a one-line "English resumes only" note.

#### P1-10 · "Years of experience" card (known issue) mixes things and the number is unreliable
- **Actual:** the right-hand card is titled "Years of experience mentioned" but also lists role lines; education isn't detected at all. The figure is the **maximum of any "N years"** in the text: "volunteer for 12 years" → 12; "20 years ago I founded a club" → 20; "10 years" inside a title line → 10; `2019-2026` is ignored.
- **Fix (M):** split into "Roles found", "Education" (degree keywords: PhD, MS, BS, bootcamp) and "Experience" computed from date ranges (or hidden when unsure); keep the heuristic out of the ranking.

### P2: misleading, slow, fragile or inconsistent

| ID | Page | Finding (steps → expected vs actual) | Fix | Effort |
|---|---|---|---|---|
| **P2-1** | Resume match | **No "Top skills to learn"** (known). Each row lists missing skills, but nothing aggregates them. A student needs "learn these first". | Count missing skills across the top N matches (weighted by score), filter with the same stop-list as P1-4, show a ranked list or bar with "appears in X of the top N". Needs owner OK (new section). | M |
| **P2-2** | Resume match | **Malformed PDFs escape as raw exceptions.** `_pdf_text` catches `PyPdfError, ValueError, KeyError, TypeError, RecursionError`. Fuzzing 1,500 mutated PDFs: 5 (0.3%) raised `NotImplementedError: Unsupported filter /HK…FlateDecode` or `AttributeError: 'NumberObject'…`. Streamlit would show a traceback, and its log would hold fragments of the file, which contradicts "never logged". | Catch `Exception` (excluding `ResumeError`) in `_pdf_text` and map to "could not be read". | S |
| **P2-3** | All | **Schema drift leaks raw errors.** A file with the right 16 tables but a missing column passes `_is_gold_only` and the pages show `query failed: Binder Error: Referenced column "is_target_role" not found… Candidate bindings: …` to visitors. Code and data deploy independently, so this can happen. | Add a `meta` table or file name with a schema version, compare at connect; replace `query failed: {e}` with a generic message and log detail server-side. | M |
| **P2-4** | Home, all | **Staleness is invisible outside Pipeline Health.** A 10-day-old copy keeps serving (tested) with no banner; Home only says "Data through 20 Nov 2026". The 30 h check exists only on Pipeline Health. | Shared banner in `streamlit_app.py` when newest snapshot > `max_age_hours`. | S |
| **P2-5** | All | **Outage behaviour.** Nothing is negatively cached and the spinner is off (`show_spinner=False`). A persistent 5xx costs **17.5 s per rerun** (5 attempts + backoff); a slow server up to 5 × 120 s ≈ 10 min of blank page. 404 and 403 fail instantly with "the published data could not be downloaded". | Negative-cache failures for 60 s, cap the app download at 2 attempts and 20 s, show a spinner and a "Retry" button with the data age. | S |
| **P2-6** | Market overview | **1.4 s of CPU per rerun.** `[r for r in city_rows if r[0] in load_city_coords(app_config())]` calls `load_city_coords` once per city (386 times), each re-parsing the YAML with pure-Python PyYAML (profile: 386 calls, 4.4 s under cProfile). Other pages take 0.06 s warm. It blocks the GIL for every visitor on every widget click. | Hoist the call (`coords = load_city_coords(...)` once) and wrap it in `lru_cache`. | S |
| **P2-7** | Home, Market | **Headline vs delta use different days.** The card shows today's 8,445 (11-20, closures not yet settled) with "+16 vs last week", which compares 11-19 (8,475) with 11-12 (8,459). 8,445 − 16 ≠ last week's value; the real change since yesterday is −30. | Show the settled-day value next to the delta, or label "as of 19 Nov". | S |
| **P2-8** | Hiring trends, Salary | **Tiny samples drive headlines.** The page opens on "Analytics Engineer" (first alphabetically, 16 postings): "SQL in 94%" is 15 of 16, without n. "BI Analyst / Engineer" has **1** posting (every skill 100%). The Salary insight crowns the highest median among groups with ≥ 5 salaries ("Principal ML/AI $328K from 5"). | Default to the largest role; show n beside shares; hide groups below a configured n (say 10) from insights; hide BI and Other from the picker or label as "thin data". | S |
| **P2-9** | README, NOTES | **Published numbers are stale** (see section 3): 441 (86%) read vs 515 (100%); 511 vs 515; 9,516 / 8,464 vs 9,681 / 8,445; 2,484 vs 2,615 skills; "$0.22" vs $0.25; limitation text "86% so far". The README claims "as of 2026-11-20". A recruiter comparing the README with the app sees two sets of numbers. | Re-run the cited scripts and update; add the data date and a "regenerate" command. | S |
| **P2-10** | Pipeline health | **"AI extraction cost" includes evaluation spend:** Total $0.52 and 756 calls = $0.249 production + **$0.269 / 100 calls of `eval` mode** (one day, the stronger model). The chart colours it as ordinary spend. README says $0.22 excluding eval. | Filter `mode <> 'eval'` for the metric, or show production vs evaluation separately. | S |
| **P2-11** | Pipeline health | **Ingest chart counts re-run attempts.** The y-axis "Company job boards" shows 205 on 11-10 and 86 on 11-11 and 11-20 for 43 employers (it sums `n_boards` over every attempt). | Count distinct boards (authoritative attempt) per day, or label as "board fetches". | S |
| **P2-12** | Pipeline health | **Unexplained failure on a public page.** "1 of the last 40 runs did not succeed" doesn't say which: a `silver` run (started 2026-11-19 22:26 ET, snapshot 2026-11-20, `status = failed`, yet `rows_kept = 9,008`). | Show failed runs first/highlighted with step and time, or explain; consider hiding developer-only detail. | S |
| **P2-13** | Salary, Home, Resume | **Annualization inflates internships and one outlier.** Of the 22 USD intern salaries, 14 are hourly (×2,080) and 5 monthly (×12): a PhD intern at $12,100/month becomes "$145K a year" and a $62/hour intern about $129K, for an internship of about 12 weeks. So the heatmap's intern cells ($134K and $142K) and the Home median ($213K, USD, all seniorities) read as annual pay. One inferred-hourly posting, Databricks "Staff Backline Engineer – ML/AI" `170.4–255.6` labelled `hour (inferred)`, becomes **$354K–$532K** (likely $170K–$256K a year) and is the highest salary shown in Resume match. The rule "< 500 means hour" is too weak. | Mark intern/monthly values as "annualized equivalent" or exclude interns from medians; tighten inference (require an hour cue in the quote or a plausible range, else leave the period empty); caption the Home median with scope and n. | M |
| **P2-14** | Market, Map | **City parsing leaks junk and duplicates.** Labels: `Location`, `Na`, `Sea`, `Usca`, `Amer`, `Asia Pacific`, `NYC-Privy`, `FI-Helsinki-MSO`, `Fins Only-AU-Sydney`, `US Hybrid`, `San Francsico`, `Costa Rica`; bare `Austin` (7) next to `Austin, Texas` (13); three Washington-DC spellings; wrong places `Munich, Delaware` (2), `Towson, Massachusetts`, `Chicago, New York`. The "Cities 385" KPI counts all of them. Well under 1% of postings are affected, but "Location" appears in a city list. | Add the junk strings to `locations.yaml`, list `Austin`, `Denver`, `Los Angeles`… in `known_places`, treat "DE"-style codes by context; cap the KPI to cities with ≥ 2 postings or label it "city labels". | M |
| **P2-15** | Resume match | **Table cells truncated.** Median "Missing skills" is 124 characters, **40 of 50 rows exceed 60** and are cut off; 32 of 50 titles exceed the 40-character visible width. The missing skills are the point of the page. | Move skills to an expander or `st.expander` per row/detail panel, or show the top 5 missing with "+N more"; widen Title. | M |
| **P2-16** | Salary | **Blank percentile cells.** Percentiles are NaN for 31 of 49 USD role × seniority rows and **696 of 818 (85%)** LCA wage rows. Streamlit shows missing numbers as "None" or blank depending on version (I cannot render); the owner already replaced `None` with "—" on Pipeline Health for this reason. | Same `dash()` treatment, or hide rows with n < 5 behind a toggle. | S |
| **P2-17** | All charts | **Accessibility.** Bar value labels (`#7F8FA6`) are **3.29 : 1** on white (AA needs 4.5 : 1) and 2.94 : 1 on the light panel; the 4-colour series palette has colours 1 and 3 only **1.34 : 1** apart (legend unreadable with ≥ 3 series, and color is the only cue); 2 of 18 heatmap cells fail 4.5 : 1 (`$236K` 4.13, `$250K` 3.99). Charts have no text alternative (dataframes do have `alt`). Streamlit's own viewport sets `user-scalable=no`; not fixable here. | Make `LABEL_COLOR` theme-aware: no single slate passes 4.5 : 1 on both themes (`#5B6B82` is 5.43 : 1 on white but 3.48 : 1 on dark; `#9AA9BF` is 7.92 : 1 on dark), so choose by `st.context.theme.type` or drop the bar-end labels and rely on the tooltip; pick palette stops ≥ 3 : 1 apart or add direct labels/patterns; shift the heatmap text switch; add a one-line text summary per chart (the key-insight line already does most of this). | S–M |
| **P2-18** | Mobile (390 px) | **Reasoned from code, not rendered.** `axis.labelOverlap = False` forces every label to draw: the 9 seniority labels over about 300 px on the heatmap will collide, and `$276K` bold in a 30 px cell overflows; `labelLimit = 600` on city and company axes lets 28-character labels eat half the width; Resume results and Salary tables have 9 to 10 columns (horizontal scroll, the key columns off-screen); the `segmented_control` with 8 role names may overflow; the pydeck map captures touch scroll. Columns do stack (Streamlit default). | Use `labelOverlap: "greedy"` plus 45° or abbreviations on narrow charts; set a smaller `labelLimit`; put the top-3 columns first and move the rest into a detail view; test in a real 390 px viewport. | M |
| **P2-19** | Visa | **Confusing framing.** (a) "22% of the 1,325 shown" divides by the top-15 sum, not all 1,665 data-role filings (17.7%). (b) "Top sponsors" (data roles, Pinterest first) and "All companies" (all roles, DoorDash first) rank differently and 83% of all filings (8,209 of 9,874) are in "Other roles", so the headline list reflects only 17% of filings; the caption says "lower bound" but not that it is the minority. (c) "No filings found" for Betterment (0 matched) reads as "doesn't sponsor" although it may be a name-match miss. | Compute shares over all companies; add "17% of filings are in the named data roles"; word it "No matching filings found". | S |
| **P2-20** | Market overview | **No filters at all.** A hiring manager cannot filter open roles by role family, seniority, remote policy or city; the page is one fixed overview. (Seniority is available in gold.) | Add a shared filter bar (role family, seniority, remote) applied to the charts, KPIs and map. Needs owner OK (feature). | L |

### P3: polish and hardening

| ID | Area | Finding | Fix | Effort |
|---|---|---|---|---|
| P3-1 | `app_data` | **URL guard bypass.** `url.startswith(prefix)` passes `…/releases/download/../../../evil/x.duckdb`; httpx normalizes it to `https://github.com/akshaygovindd/evil/x.duckdb`. Needs control of the secret, so low risk. Also no integrity check: anyone with repo write access can replace the mutable `gold-latest` asset. | Parse and compare the normalized path; reject `..`; publish a `.sha256` next to the asset and verify. | S |
| P3-2 | `validate_statement` | **Comma-join bypasses the table allow-list.** `select * from gold.dim_skill a, ref.lca_disclosures b` passes (only `FROM`/`JOIN` are inspected). No app query does this and the published file holds only gold tables, so there is no production exposure; the MotherDuck dev path is exposed. | Reject `,` between table references or parse with a SQL parser. | S |
| P3-3 | Error text | Visitors can see internal strings: env var names (`JOBPULSE_APP_DB`, `MOTHERDUCK_READ_TOKEN`) when config is missing, and DuckDB errors (P2-3). | Generic public message, detail to the log. | S |
| P3-4 | `streamlit_app.py` | `sys.path.insert(0, …)` runs on every rerun, so `sys.path` grows by one entry per rerun for the process lifetime. | Insert once (check membership). | S |
| P3-5 | Caching | `st.cache_data` keys don't include the data file's version, so after the hourly file refresh pages can show data up to ~2 h old. A corrupt-but-fresh local file is not re-downloaded (error for up to 1 h). The replaced connection is never closed. | Add the file mtime/etag to `source_key`; delete and re-download on open failure; close old connections. | S |
| P3-6 | Dates | Inconsistent formats: "20 Nov 2026" (Home), "2026-11-11" (insights), "Nov 11" (charts), "2026-11-16" (Trends caption). | One helper. | S |
| P3-7 | Visa copy | Typo "DoorDash, Inc.." (double period); "Withdrawn or denied" includes 677 of 777 "certified then withdrawn" filings; "fiscal years 2025 and 2026" and "43 companies" are hard-coded in 4 strings (`visa.py` ×3, `salary.py` ×1). | Compute from data; relabel. | S |
| P3-8 | Rounding | 99.7% and 99.8% display as "100%" (Salary "yearly figure known", LCA "usable wage"), which implies none are missing (1 and 22 are). | Show one decimal at the extremes, or "99.8%". | S |
| P3-9 | Resume copy | Oversize paste: 140,000 characters stay in `resume_text` after refusal and **no Delete button is offered** until a profile exists. Fullwidth ("ＳＱＬ") and zero-width-split ("P​y​t​h​o​n") spellings aren't recognized (no NFKC normalization). | Clear the text on refusal or offer Delete; `unicodedata.normalize("NFKC")` + strip zero-width characters. | S |
| P3-10 | Skills | `api`/`apis`, `kpi`/`kpis` are separate canonical skills; "Language"/"English" etc. See P1-4. | Handled with P1-4. | – |
| P3-11 | Copy | Developer language on public pages: "Run the pipeline, then `jobpulse build-gold`", "(`jobpulse load-ops`)", step names "Pick target roles / Clean and link / Load raw data". | Friendly wording; keep CLI hints for local mode only. | S |
| P3-12 | Data quality table | "Active postings with an extraction: **6%**" with no context looks like a failure although it is by design (title filter). | Add the sentence from Market overview, or hide that row. | S |
| P3-13 | Tests | Existing tests don't cover concurrency, decompression limits, NaN in tables/markdown, or ImportError after reload. `.df()` also emits a `Pandas4Warning` (`inplace` in DataFrame.drop) from DuckDB under pandas 3.1, a future-break signal. | Add the regression tests listed in the plan; pin or watch DuckDB/pandas. | M |
| P3-14 | Docs | README still has "Screenshots: _to be added_" placeholders. | Add screenshots (owner). | S |
| P3-15 | Weekend pattern | New postings on Sun 11-15 = 2 and closed on Mon 11-16 = 6 (neighbours 100+). Snapshot counts are nearly identical (Greenhouse 6,926 → 6,921), so it looks real, but the chart caption says "dips = incomplete fetch". | Note "weekends are quiet" or verify. | S |
| P3-16 | Home | Home says "refreshed every day" while the file can be up to 1 h cached and GitHub's scheduler can run late; "analyzed in depth" for LLM extraction is generous. | Soften wording. | S |
| P3-17 | Platform | Live response headers show only `x-content-type-options`; no CSP/HSTS/X-Frame-Options and `user-scalable=no` are Streamlit Cloud's and can't be changed here. | Note only. | – |
| P3-18 | Supply chain | `pypdf` parses untrusted files; no CVE audit was run. | `uv run pip-audit` (or equivalent, owner approval for the tool) before launch. | S |

## 5. Resume match test matrix

Real data (507 open target-role postings; 500 rankable). "Skills" is what the page would list.

| # | Input | Result | Verdict |
|---|---|---|---|
| 1 | Data analyst student (txt, 879 B) | 28 skills (11 noise), years 1, 2 title lines incl. email; top result **Senior** Data Analyst 71%; top 50 = 23 senior / 3 manager / 2 staff / 13 intern | ⚠️ P1-3, P1-4, P1-8 |
| 2 | Senior data engineer | 36 skills (Agent, Dag, Delta, Modeling noise), years 9; top results Staff and Principal (fits) | ⚠️ P1-4, duplicates P1-7 |
| 3 | ML PhD | 28 skills (Research, Training, Inference, Vision noise), years 15; top: Scale AI **fellowships** ("sponsorship not available"), 10 intern roles in top 50 | ⚠️ P1-3 |
| 4 | Empty text | "No text found. A scanned PDF (an image) cannot be read." | ⚠️ wrong wording P1-9 |
| 5 | One line "Python" | 50 results, best 20%, no caution | ⚠️ P1-9 |
| 6 | Non-English (es / zh / hi) | Only English tech words found (7 skills), years and titles "Not found", no language note | ⚠️ P1-9 |
| 7 | Emoji and special characters | 9 skills; name kept in "title"; markdown/HTML in title lines unescaped; fullwidth and zero-width variants missed | ⚠️ P1-8, P3-9 |
| 8 | 2 MB text (1,999,000 B) | Refused: "longer than 60,000 characters" | ✅ |
| 9 | 59,900-char text | 0.22 s, correct | ✅ |
| 10 | 2,000,001-byte file | Refused: "larger than 2 MB" | ✅ |
| 11 | Plain PDF | Same as txt | ✅ |
| 12 | PDF with tables | 12 skills, titles "Data Analyst", "Senior Data Analyst, Foo Corp", years 6 | ✅ |
| 13 | Scanned (image-only) PDF | "No text found. A scanned PDF (an image) cannot be read." | ✅ |
| 14 | 12-page PDF | "more than 10 pages" | ✅ |
| 15 | `.pdf` that is an EXE | "That file is not a PDF." | ✅ |
| 16 | `.docx` | "Only pdf, txt, md files are accepted." | ✅ |
| 17 | Binary `.txt` | "not plain text" | ✅ |
| 18 | Latin-1 `.txt` | "not UTF-8 text" | ✅ (could offer fallback decode) |
| 19 | Corrupt PDF (header + random bytes) | "could not be read" | ✅ |
| 20 | PDF over 60k characters | "longer than 60,000 characters" | ✅ |
| 21 | Decompression-bomb PDFs | 322 to 515 MB peak before refusal | ❌ P0-2 |
| 22 | 1,500 mutated PDFs | 5 uncaught exceptions | ❌ P2-2 |
| 23 | Pathological text (59k "(", 30k one-letter words, 59k newlines…) | all ≤ 0.7 s | ✅ |
| 24 | Delete | profile and all filters cleared; success message; no `resume_*` data left | ✅ |

**Ranking sanity.** Score = matched / posting's skills; only postings with ≥ 5 skills are ranked (median posting has 17). Noise skills did not change the top 8 (8 of 8 overlap). Ties and small skill lists favour short, generic postings (a 5-skill posting matching 3 beats a 14-skill posting matching 7: 60% vs 50%). That's a design choice worth a sentence in the caption. Sponsorship is a filter, never in the score (verified in code).

**Privacy guarantees checked.** Raw text is cleared after a successful parse; the uploader is replaced by a new key; Delete removes every `resume_*` key; queries are parameterized and cached only by SQL, never by resume content. Gaps: title lines can hold name, email and phone (P1-8); a refused paste stays in session (P3-9); uncaught PDF exceptions would put file fragments in the server log (P2-2).

## 6. Robustness matrix (published-file connector, mocked transports)

| Scenario | Result | Time |
|---|---|---|
| Healthy | serves 9,681 postings | 0.0 s |
| Asset missing (404) / 403 | "the published data could not be downloaded" | 0.0 s |
| 503 on every attempt | same message | **17.5 s** per rerun, 5 calls (P2-5) |
| 200 garbage / empty / half a database / wrong table set | same message; nothing replaces the old copy | 0.0 s |
| Right tables, a column missing (schema drift) | **passes**, pages later show `Binder Error … Candidate bindings` | P2-3 |
| 10-day-old copy, refresh 404 or garbage | keeps serving the old copy, silently | P2-4 |
| Fresh-by-mtime but corrupt local file | "could not be opened" for up to 1 h, no self-heal | P3-5 |
| `.part` files left behind | none | ✅ |
| URL guard: other host, `http`, look-alike prefix | refused | ✅ |
| URL guard: `…/download/../../../evil/x` | **accepted** | P3-1 |
| Empty filters (Visa roles none; resume filters cleared) | "Pick at least one role family." / unfiltered | ✅ |
| Every filter combination (200 runs) | no exception | ✅ |
| Concurrent sessions | **errors and wrong results** | P0-1 |

## 7. Performance

Measured with `AppTest` against the local file, so Cloud will add start-up and network time. SQL is not the bottleneck: every query takes ≤ 10 ms and a page's total SQL is ≤ 25 ms.

| Page | Cold run | Warm run | Queries (cold) | Note |
|---|---|---|---|---|
| Home | 0.14 s | 0.06 s | 10 | |
| Market overview | 1.54 s | **1.49 s** | 12 | YAML re-parsed 386× (P2-6) |
| Hiring trends | 0.11 s | 0.06 s | 6 | |
| Salary | 0.11 s | 0.06 s | 5 | |
| Visa | 0.11 s | 0.06 s | 7 | |
| Resume match | 0.10 s | 0.06 s | 1 (+0.22 s parse for a 59.9k-char resume) | |
| Pipeline health | 0.11 s | 0.06 s | 5 | |

- **First load:** importing streamlit, pandas, duckdb, pydeck and pypdf takes 0.4 to 0.6 s locally; the 6.0 MB download took 0.5 s. The live root URL answered the cookie redirect chain (3 hops) and returned the app shell in 1.5 s while the app was awake. True cold-start (sleeping container) was not measured.
- **Memory:** about 166 MB after imports, 190 to 197 MB after rendering all pages in one process; the file is 6 MB. The only large spike found is the PDF path (P0-2), 350 to 470 MB.
- **Cache:** every query is cached for 1 h with `max_entries = 128`. The base pages use about 46 distinct queries, but parameter variants (43 companies × 4 Visa queries, currency × city on Salary) can exceed 128 entries and evict hot ones. The connection is rebuilt hourly and the old one is not closed (P3-5). Cache expiry is synchronized, which at current volume is harmless.

## 8. UX and accessibility notes (beyond the findings above)

- **Wording that reads as developer-facing or unfinished:** "Pick target roles / Clean and link / Load raw data" on Pipeline Health; "Run the pipeline, then `jobpulse build-gold`"; "None", "nan", "Cte", "Demo", "Star", "User storie" in visible lists; "Active postings with an extraction 6%".
- **Number formats:** `$213K`, `GBP 142K to GBP 190K`, `PLN 365K`, `INR 6613K` mixed with `$173K to $233K` in one column; percentages without decimals; three date formats (P3-6).
- **Empty states** are mostly good ("Pick at least one role family", "Not enough salaries yet…"). Two are wrong or unhelpful: the zero-skill resume message (P1-9) and the unexplained failed-run warning (P2-12).
- **Truncation:** Resume table (P2-15). Chart labels are never truncated by design.
- **Color:** the app avoids red/green (good for color blindness) but then relies on slate and blue tints that are close together (P2-17).
- **Mobile:** see P2-18; verify in a 390 px viewport after the fixes.

## 9. What a recruiter or hiring manager would find confusing or unprofessional

1. A student's resume returns **Senior Data Analyst** and **Data Science Manager** first, and a PhD gets "Machine Learning **Fellow**" roles from a company that says it does not sponsor visas.
2. **Junk in "Skills recognized"**: Star, Demo, Language, English, Join, Cte, "User storie".
3. **"None" and "nan"** in City cells and in the Visa quote cards.
4. **No way to apply** and no way to filter by experience level on Resume match; Market overview has no filters.
5. **Home and Salary disagree** on how many postings state a salary (316 vs 376); **README and app disagree** on nearly every headline number.
6. Pipeline Health shows **"1 of the last 40 runs did not succeed"** with no explanation, a **$0.52** cost that includes evaluation, and an ingest chart showing **205 boards** for 43 companies.
7. **A $354K–$532K advertised salary** (inferred hourly) and **intern medians of $134K to $142K** (monthly stipends ×12) in a pay view.
8. **"Cities: 385"** while the list includes "Location", "Na" and "Sea"; "Principal ML/AI $328K" from five postings; "SQL in 94%" from 16 postings.
9. "Top sponsors" shows the minority (17%) of filings without saying so; "No filings found" can be read as "doesn't sponsor".
10. Placeholder screenshots in the README; developer instructions in empty states.

## 10. Prioritized fix plan and estimate

Everything below needs **no new dependency** (pypdf's Configuration, `unicodedata`, `html`/regex helpers are already available). Items marked ★ change scope or the gold schema and need your explicit OK under the frozen-scope rule.

| Phase | Items | Effort | Notes |
|---|---|---|---|
| **A: stop the bleeding** (do first, one commit each) | P0-1 cursor per call + threaded test · P0-2 pypdf limits + tests (the verified fix) · P0-3 module-stamp purge + `folderWatchList` + friendly ImportError · P2-2 catch-all in `_pdf_text` · P1-1/P1-2 shared `clean()` for NaN · P1-8 escaping + scrub emails/phones · P2-6 hoist YAML load | **≈ 6 to 8 h** | `fix:` commits; tests: threaded read, bomb PDF, `AppTest` for NaN and markdown |
| **B: correctness and credibility** | P1-5 Home caption · P2-7 headline basis · P2-8 default role and n · P2-10 eval cost · P2-11 ingest chart · P2-12 failed-run display · P2-13 salary caveats and inference tightening · P2-9 + P3-7/8/12/14 README and copy · P2-16 dashes · P1-9 thin/empty messages | **≈ 6 to 8 h** | The inference rule change (P2-13) alters gold (`extract/period.py`); rerun `build-gold`, then `export-gold` |
| **C: Resume match quality** | P1-4 stop-list + min frequency + `norm_skill`/display fixes · P1-7 location column or collapse · P1-10 profile card split · P2-15 table readability · P1-9 language note · P3-9 | **≈ 7 to 10 h** | `norm_skill` fix needs a gold rebuild; display-map-only interim is S |
| **D: new features** ★ | P1-3 experience level filter and default (M) · P1-6 apply links, gold column + export + `LinkColumn` + URL policy (M) · P2-1 "Top skills to learn" (M) · P2-20 Market filters (L) | **≈ 8 to 12 h** | ★ each needs your yes; P1-6 changes `fct_postings` and the export contract test |
| **E: robustness hardening** | P2-3 schema handshake + generic errors · P2-4 stale banner · P2-5 outage behaviour · P3-1…5 | **≈ 4 to 6 h** | |
| **F: accessibility and mobile** | P2-17 contrast and palette · P2-18 narrow-screen chart rules · P2-19 Visa framing · P2-14 city labels (`locations.yaml`, rebuild) | **≈ 5 to 7 h** | Verify in a real 390 px browser; needs your eyes or a screenshot |

**Total ≈ 36 to 51 hours** if everything is done; **A + B ≈ 12 to 16 hours** removes every P0 and the most visible P1/P2 problems and is what I would do before sending the link to more recruiters. Run `make check` after each phase; no `terraform` changes are involved.

### Open questions for you
1. Approve phases A and B as written? (They contain no scope change.)
2. Phase D: which of experience-level filter, apply links, "Top skills to learn" and Market-overview filters do you want?
3. For salaries: exclude interns from medians, or keep them labelled "annualized equivalent"?
4. For the resume stop-list and minimum posting frequency (P1-4): OK to start with "≥ 3 open postings" and a reviewed stop-list, or do you want to review the list first?
5. May I promote the QA scripts to `scripts/qa/` (so the numbers in this report are reproducible), and add the regression tests to `tests/unit/`?
6. Anything in the app you'd rather *remove* than fix (for example Pipeline Health on the public nav)?
