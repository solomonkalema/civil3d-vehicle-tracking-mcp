---
name: design-review-working-drawings
description: Review a civil design drawing set (roads, junctions, drainage, traffic signals) and turn it into working drawings a contractor can build from and a checker will pass. Use when asked to review or check drawings, compare a design set with shop, working or as-built drawings, list what a set lacks for construction, prepare working or shop drawings from a design, check a drawing list, title blocks, quantities tables, setting-out data, profiles, cross-sections, bar schedules or signal and cabling plans, or act as CAD engineer or drawing checker on a set of PDFs or DWGs.
---

# Design review and working drawings

You are the CAD engineer: first review the design set, then produce or check working drawings. The engineer you work for
decides every design matter. Your job is to find what is wrong or missing, fix what is drafting, and put the rest in front of
the engineer with evidence.

Read `references/checklist.md` before the first review step. It is the checklist; this file is how to run it.

## Rules
1. Originals are read-only. Copy what you need into a separate working folder and write every output there.
2. Client standards are settings, not assumptions. Fill `references/settings_template.json` from the engineer's answers or from
   the client's accepted reference set before judging any presentation point. Ask for all of them once, together.
3. Never decide a design matter (size, level, grade, slope, width, material, mix, item type, signal stage, scope, a deletion).
   Record it as class C with the evidence and ask.
4. Never send, share or publish anything without the engineer's explicit yes.
5. No tool or assistant names in any deliverable, file name or document property. Author is the firm.
6. Only one session drives the CAD program. Reading, comparing and document work goes to background readers.
7. Say what you checked, what you sampled and what you did not read. "Checked" means the check was run and its result seen.

## Finding format
One line per finding: `sheet no. | page | location on sheet | defect | class | severity | seen or inferred`
Also keep a short list of checks that were run and found nothing (`sheet | check | result: no defect`): the engineer needs
to know what was cleared, not only what failed.
- Class A drafting and presentation. Class B completeness for construction. Class C design matter (engineer decides).
- Severity: high (cannot build or pay from it, or it is wrong), medium (will raise a query), low (tidiness).
- "Seen" only if you looked at the sheet image or the exact text. A computed result (a sum, a grade, a bar length) is "seen"
  when every input was read from the sheet; give the arithmetic in the defect text. Otherwise "inferred".

## Step 1: set up (10 minutes)
The helper scripts need Python with PyMuPDF (`pip install --user pymupdf`). Run them with `python -P` from the skill's
`scripts` folder, and keep the work folder outside the project folder.
```
python -P pdf_dump.py <work> <pdf> [<pdf> ...]        # text of every page -> <work>/txt/S01.txt ..., keys in <work>/sets.json
python -P pdf_render.py <work> <key> <page> 70        # whole sheet, low resolution
python -P pdf_render.py <work> <key> <page> 200 0.5 0.5 1 1   # a tile, to read small text (fractions of the page)
python -P titleblock_sheets.py <work> <key>           # 12 title blocks per image
```
- `pdf_dump.py` writes ONE text file per PDF, with every page inside under a `##### <key> page N` header.
- If the civil3d server (v0.10 or later) is connected, `c3d_guide('review_checklist', <section>)` serves this checklist by
  section, `c3d_guide('tool_map')` says which tool does which job, and `c3d_note_lesson` records a gap or lesson the moment
  it happens (use it). Also, its `sheet_pdf_text`, `sheet_find_text`, `sheet_pdf_tile`,
  `sheet_titleblocks`, `sheet_fonts` and `sheet_profile_lines` tools do the same jobs: use them instead of the scripts.
- Text first: it is cheap and searchable. A page with very few words has outline text: read it from renders.
- Tables, schedules and title blocks are often drawn as outlines even when the page has a normal word count. If a table
  you can see on the sheet is not in the text, read it from a tile. Never conclude a table is absent from the text alone.
- Whole sheets at 70 dpi to see layout; tiles at 150 to 250 dpi only where something must be read or confirmed.
- Up to about 30 sheets: render every sheet once at 70 dpi. It is cheap and nothing replaces seeing the whole sheet.
  Beyond that, do not render every sheet of a long run of similar sheets: sample, and say so.
- `titleblock_sheets.py` defaults to the full-width bottom strip. Pass a narrower box once you know where the block sits;
  on A1 sheets always use narrow boxes (title cell, then number cell) or the lettering is unreadable.
- Use `python -P`, never `-I` (it hides the PDF library). Pass dpi as a whole number.
- Pages can be stored rotated. To measure a drawn scale, use the distance between the centres of two labels of known
  spacing (two chainage labels, two grid values) taken from the text positions, not a ruler on the image.
- If DWG files are supplied and a CAD connection is available, read layouts, viewport scales, layers and whether the title
  block is an attribute block or plain text. An attribute-based title-block tool finds nothing in a plain-text block.

## Step 2: register (checklist section 1)
Build `register.csv`, one row per sheet: key, page, drawing number, title, place, scale on sheet, scale on list, revision,
status, empty title-block cells. Use the drawing list text plus the title-block contact sheets. Then report: listed but
missing, present but unlisted, duplicate rows, one drawing under two numbers, list and sheet disagreeing, empty cells.
This step alone usually yields the first ten findings. Do it before reading any sheet in depth.
No drawing list (an extract, or a set issued without one)? Say so as a finding, then check instead: that the numbers
follow one scheme with no gaps or repeats, that each title matches the sheet's content, and that every cross-reference
on the sheets points to a number that exists in the set.

## Step 3: leftover hunt (checklist section 2)
From the register, list the place names, site numbers and pipe or item sizes used in the set. For each sheet, search its text
for names and numbers that belong to OTHER sheets' places. Look at every hit. Also search for placeholders, raw control codes,
and every "see drawing" or "ref." target. Hidden text counts: it marks a cloned sheet, so read that whole sheet.

## Step 4: technical checks, in this order (checklist sections 3 to 9)
1. Sheet identity and legibility.
2. Survey and control. If design levels disagree with the survey, stop and report: everything after depends on it.
3. Drainage. It is what gets submissions rejected.
4. Geometry, profiles, cross-sections.
5. Trade sheets and quantities: sum the labels on the plan and compare with the table on the same sheet, then with sister
   sheets. Do the arithmetic with a script, not by eye.
6. Details and structures: n at pitch, bar lengths against shapes, schedule counts, materials.
7. Signals, ducts, power, network.
Arithmetic checks are where a careful reader beats a quick one, and they found the most serious faults in trials. Do each
of these on every sheet that has the numbers, with a script or written working, never by eye:
- quantity table = sum of the located labels; bar schedule total = sum of its rows; each row = number x length x unit mass;
- `n@pitch=total` closes, and the number of bars is spaces + 1; bar length = sum of the legs in its shape;
- profile closure and coordinate frame, by the two procedures in checklist section 6 and section 4.

## Step 4b: across the documents (checklist section 10)
Ask for the design report, the bill of quantities with its preamble, the specification and the setting-out data. Dump their
text the same way as the drawings. Then run section 10 in order: end chainage in every document, benchmarks and datum,
setting-out levels against the profile, report values against typical sections, pavement in five places, structures in report
against drawings against bill, bill arithmetic by rule. Disagreements between documents are class C unless one side is an
obvious typing slip: report both values and where each is, and let the engineer say which stands.

## Step 5: design against working set (when both exist)
Build the mapping (each design sheet to its successors) and the design-to-working register: every design item kept, changed or
deleted, with the instruction behind it. Sort differences into A, B and C. Anything in C without an instruction on file is a
question for the engineer, not a finding against the drafter.

## Step 6: report
Write the report to a file in the work folder. If you are running as a background reader and cannot write the report
file, return the full report as text in your reply and say so: never drop findings because a file could not be written.
1. Summary: counts by class and severity, the ten findings that matter most.
2. Questions for the engineer (class C), each with sheet and evidence.
3. Findings table.
4. What was not checked.
Markdown or the format the engineer asks for. Neutral file names.

## Large sets: split the reading
Over about 60 sheets, split by sheet group (for example by site, or junction civil / signals / standard details / network)
among read-only background readers. Give each: the work folder, the keys and page ranges, the finding format, the relevant
checklist sections, and an instruction to write findings to a file and reply in under 25 lines with what it did not read.
One reader does the register for the whole set. Do not tell a reader what you expect it to find. Readers never touch the CAD
program and never write into the project folder. Merge their files yourself and re-check anything surprising on the sheet.

## Producing working drawings from a design
Follow "Order of production" at the end of the checklist. Points that are easy to get wrong:
- One master plan per site with a layer per trade; each trade sheet is a layer state of it. Never copy the plan per trade.
- Quantity tables are generated from the located labels (or from the objects), never typed separately.
- Layouts named by drawing number. Revision on the sheet (table and cloud), not in the file name. Superseded files moved out.
- Drainage levels, profiles and sections are agreed with the engineer before anything else is detailed.
- When a comment is fixed on one sheet, check every sister sheet for the same fault and record it in a comment-response list.
- After plotting, run steps 2 and 3 again on the PDFs, then look at every sheet once beside the previous issue.

## The client's accepted reference set
If the client has an accepted set from an earlier job, read it before judging presentation: it defines numbering, title block,
scales stated against drawn, sheet order and how much is tolerated. Accepted sets contain faults too (a stated scale that is
not the drawn one, initials differing between sheets, labels in the wrong units). Do not copy those faults into new work and
do not "correct" a house convention without asking: list what you see and let the engineer decide which is convention and
which is error.

## What this skill does not cover
Civil 3D model work (corridors, data shortcuts, profile bands, label styles, plotting from the model): use the road production
skill and the CAD server's knowledge file for that. Structural adequacy, hydraulic design and signal timing design are the
engineer's: this skill checks that the drawings are complete and consistent, not that the design is right.
