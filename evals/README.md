# Evals

Small, repeatable trials that show whether a skill, a model or a change to the server still does the job. Run one after
changing a skill or the checklist, and before relying on a different (smaller or newer) model.

## Drawing review

1. Make the material: `python make_review_set.py <work folder>` writes `review_set_01.pdf`, eight synthetic A3 sheets for
   one junction with 17 planted faults and 3 decoys (things that are correct and must not be reported). Nothing in it comes
   from a real project.
2. Give a fresh session or background reader ONLY the task text from `cases/review_synthetic_01.json`, the PDF path and a
   work folder. Do not show it the case file. Save its full report as text.
3. Score: `python score.py cases/review_synthetic_01.json <report file>`. Pass is 70 % of the planted faults found. Read the
   'missed' list by eye: the patterns are loose, and a fault described in unusual words can be missed by the scorer.

Record each run in `RESULTS.md` (date, model, skill version, score, what was missed). A fault that is missed twice goes into
the skill as an explicit check; a decoy flagged twice means the skill is making the reader over-report.

## Private cases

Cases built from real project sheets (with an answer key taken from an earlier careful review) are worth more than synthetic
ones, but they name clients and must not be published. Keep them outside this repository, in the same format, and point
`score.py` at them.

## Adding a case

Plant faults of the kinds real reviews found: a quantity table that is not the sum of its labels, another site's name left on
a sheet, a reference to a sheet that does not exist, a schedule total that is not the sum of its rows, a spacing string that
does not close, a bar length differing between view and schedule, a profile that does not close, a level differing between
profile and section, a table naming one type beside a sketch of another, an empty scale cell, a duplicate drawing number.
Always include decoys.
