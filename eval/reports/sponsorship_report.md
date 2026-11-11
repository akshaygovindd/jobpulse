# Sponsorship extraction (reported separately)

Visa data is evidence, never a promise. `offered` and `not_offered` require a verbatim quote from the posting; everything else is `unspecified`.

## Hand-labeled pilot (first rows of the stratified random sample)

- Labeled postings: **38**; accuracy: **100.0%**

| Class | Precision | Recall | Support | TP | FP | FN |
|---|---|---|---|---|---|---|
| offered (low support) | n/a | n/a | 0 | 0 | 0 | 0 |
| not_offered (low support) | 100.0% | 100.0% | 1 | 1 | 0 | 0 |
| unspecified | 100.0% | 100.0% | 37 | 37 | 0 | 0 |

Confusion (label -> prediction):

| label -> prediction | n |
|---|---|
| not_offered->not_offered | 1 |
| unspecified->unspecified | 37 |
