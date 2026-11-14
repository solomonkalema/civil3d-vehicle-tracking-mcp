# Eval results

| Date | Case | Model | Skill | Found | Decoys flagged | Result | Notes |
|---|---|---|---|---|---|---|---|
| 2026-11-14 | review_synthetic_01 | Opus 5.5 | design-review-working-drawings (14 Nov, after first trial fixes) | 17 / 17 | 0 | pass | Scorer first gave 16: the repeated-level fault was reported in words the pattern missed; pattern widened. Also reported that the synthetic sheets are not drawn to their stated scales (true, a known artefact). 40 findings in all. |
| 2026-11-14 | review_synthetic_01 | Sonnet 5.5 | same | 17 / 17 | 0 | pass | Scored by reading the report against the key (it was returned as text, not saved as a file). 30 findings. Looked at only 3 of 8 sheets as images. |
| 2026-11-14 | private case 1 (22 real as-built sheets, one junction) | Opus 5.5 | same, before the trial fixes | 6 / 6 known | n/a | pass | Blind. Also reported about ten further faults not in the key; those are unverified. |

Reading: both models clear the synthetic case, so it is too easy to separate them. It still catches a broken skill or a
broken script. Harder cases should come from real sheets (kept private) and should include outline-text tables, which the
synthetic set does not have.
