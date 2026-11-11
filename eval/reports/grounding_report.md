# Validation and grounding pass rates (self-consistency, not accuracy)

These come from checks the pipeline already enforces when it accepts an extraction (structure, salary/sponsorship consistency, and 'the quoted text appears in the posting'). **No human labels are used and none of this measures whether a value is correct.** Produced by `eval/grounding_report.py`.

## Did the model's output pass validation?

- First-pass model (`gpt-6-luna`) calls that passed validation without escalation: **97.8% (492/503)**. Failures here include both structural and grounding failures; the pipeline does not record which, so they cannot be split.
- Postings accepted after validation: **100.0% (503/503)**; quarantined (failed both models): **0**.
- Accepted from the escalation model: **2.2% (11/503)**.

## Re-check of stored quotes against the posting text

| Quote field | Extractions with a quote | Text available | Quote found in text |
|---|---|---|---|
| salary_original_text | 363 | 354 | 100.0% (354/354) |
| sponsorship_original_text | 13 | 12 | 100.0% (12/12) |

Every accepted extraction passed this check when it was stored, so anything below 100% here means the posting text changed afterwards or the stored data was altered. 'Text available' excludes postings that have since left the active data.
