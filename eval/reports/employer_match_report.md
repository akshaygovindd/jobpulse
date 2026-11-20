# Employer-match precision

Evidence from public filings, not a promise of sponsorship. Labels were drafted with AI assistance and reviewed by the owner.

- Labeled pairs: **60** of 60 (target: at least 50: met)
- Matched pairs labeled yes/no: **35** (unsure: 1)
- **Precision of our matches: 100.0%** (35 yes, 0 no); 95% interval 90.1% to 100.0% (Wilson). With few pairs the interval is wide: read it, not just the point value.

| Method | yes | no | unsure | precision |
|---|---|---|---|---|
| alias | 8 | 0 | 0 | 100.0% |
| exact | 2 | 0 | 0 | 100.0% |
| normalized | 25 | 0 | 1 | 100.0% |

## Wrong matches (fix the rule or remove the alias)
- none

## Missed matches: suggested pairs labeled yes (candidates for config/lca_aliases.yaml)
- doordash: `doordash essentials`
- airbnb: `airbnb payments`
- sofi: `sofi bank na`
- snowflake: `snowflake public sector`

Suggested pairs labeled no: 16 (similar names, not the same employer: why similarity is never a match).
