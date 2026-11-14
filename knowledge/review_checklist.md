# Review checklist: from design drawings to working drawings

For road, junction, drainage and traffic-signal sets. Use it two ways: to REVIEW a design set before production, and to CHECK
a working set before issue. Built from a completed 25-junction job where 95 design sheets became 420 final sheets, including a
shop drawing that was rejected and then approved. Client standards are SETTINGS (section 0), never assumptions.

How to use it
- Work in the order given. Section 10 needs the report, bill and setting-out data: ask for them at the start.
- Never read a number from a render below about 150 dpi: a 0.90 read as 0.60 at 45 dpi has happened. Zoom, then quote.
- Work in the order given. Earlier steps are cheap and catch faults that make later steps pointless.
- Record every finding as: sheet number, location on sheet, defect, class, severity.
  Class: A drafting and presentation; B completeness for construction; C design matter.
- Class C is never fixed by the drafter. List it for the engineer with the evidence and stop.
- "Checked" means the check was run and the result seen, not that the sheet looks right. Say what was sampled and what was not.
- Never alter the originals. Work on copies in a separate working folder. Nothing is sent or shared without the engineer's word.

---

## 0. Settings to get before starting (ask once, together)
| Setting | Example values seen |
|---|---|
| Sheet size and how scale is written | A3; "1:1000 (A3)"; "as shown" with a scale under each view; "not to scale" wording |
| Standard scales by sheet type | plan 1:1000 or fit-to-sheet 1:250 to 1:750; sections 1:125; profile H 1:2000 V 1:400; details 1:10 to 1:50 |
| Title block: parties, fields, who signs each, date format | designed / drawn / checked / approved; or prepared / checked / date |
| Status wording | tender, for approval, for construction, as built |
| Drawing numbering rule | flat serial; or group-trade-detail |
| Revision rule | revision table and clouds on the sheet; letter or number; what goes in the file name |
| Text style, minimum text height at issue size, capitals or not | |
| Layer scheme and plot style table | one layer per trade; one plot style for the set |
| Coordinate system, axis order (E,N or N,E), level datum, benchmark list | |
| Bill of quantities item order and units | |
| Chainage interval for sections | 20 m plus every change |
| Revision scheme and document numbering parts | letters then numerals; project - type - area - discipline - serial |
| Folder structure for design files and plan production | numbered folders per sheet type, in book order |
| The client's accepted reference set | read it before judging any presentation point |
| Who decides design matters, and how instructions are recorded | |
| Is the title block an attribute block or plain text? | decides which tool can fill it |

## 1. Register and inventory (before opening any sheet in detail)
1. List every sheet in the set: number, title, scale, revision, status. One row per sheet.
2. Compare the drawing list with the sheets, both directions: listed but missing; present but unlisted; duplicate rows; one
   drawing under two numbers; rows copied from another row; unused numbers not marked.
3. Compare each list row with its title block word for word: title, junction or road name, chainage range, scale.
4. Count empty title-block cells (scale, names, date, revision). Any empty cell is a finding.
5. A book of drawings (design issue): cover names the right road and issue; one divider per section and no empty section;
   sections in the client's order; the common standard-detail sheets at the back carry their own numbers once each (a
   number used twice or skipped in the standard back repeats in every book that uses it, so fix it at the source);
   pasted-in pages (setting-out tables, schedules) have a title block and a number like any sheet.
6. Status and revision on every sheet match the issue: a final issue does not carry a draft status code.
7. If a previous issue or the design set exists, build the mapping: each earlier sheet to the sheets that replace it. Expect a
   working set to be several times the design set. A design sheet with no successor needs a reason.

## 2. Copy-paste leftovers (cheap, high yield)
1. Extract the text of every sheet. On each sheet search for the names and numbers of OTHER junctions, roads, pipe sizes and
   sheet numbers. A hit is either a leftover or a legitimate cross-reference: look at it.
2. Text that extracts but is not visible (under a mask, outside the frame, zero height) is still a finding: it marks a cloned
   sheet whose other content may also be stale. Read every label on that sheet.
3. Search for placeholders and raw codes: "???", "####", "%%", "_ _", "TBA", "XX", a level or value repeated in a run.
4. Cross-references ("see drawing ...", "ref. ..."): every target number must exist in THIS issue and show what is claimed.
5. Control point tables: the table on each sheet belongs to the junction named on that sheet; no tag twice; no two tags with
   the same coordinates.

## 3. Every sheet: identity and legibility
1. Title block complete and in the agreed form; title names the right place and work type; status correct.
2. Scale stated and true: measure a known dimension. One scale per sheet, or each view labelled.
3. Plans: north arrow, road names on the correct arms with "to ..." directions, chainage ticks, legend covering every symbol
   and line type used, key plan where the sheet is one of several.
4. Legible at the issue size: minimum text height, no text over lines or tables over views, no outline or exploded text,
   colours that survive black-and-white printing.
5. Spelling of place names identical on list, title block and plan across the set.

## 4. Survey and control (before believing any level)
1. Control points plotted and tabulated (E, N, Z) on every plan sheet; coordinate system, axis order and datum stated.
2. Existing levels come from the current survey tied to the stated benchmark. Compare a few design existing levels with the
   survey: on the reference job they were 0.5 to 0.8 m apart. A difference is a class C finding.
3. Coordinate frame, by procedure: take the control points' coordinates as the reference; compute the centre and extent of
   the site from them; then for EVERY table of coordinates in the set (pits, headwall corners, poles, handholes) check that
   each point falls inside that extent and that the distance between two tabulated points equals the dimension drawn between
   them. The same object given on two sheets must have the same coordinates. Detail sheets drawn in a local or shifted frame
   are a high-severity finding: nothing can be set out from them.
4. Existing utilities and structures shown as existing, with owner references, wherever new drainage, ducts or foundations go.

## 5. Drainage (the first technical check; it is what gets a submission rejected)
1. Every pit, pipe, drain run and headwall on the plan has a number, a type and size, and a detail sheet.
2. Every pit: coordinates and levels (cover, invert in, invert out). Every pipe: inverts at both ends, length, gradient.
3. Levels fall the right way: outlet below inlet, pipe invert not above pit invert, stated gradient equals levels over length.
4. Outfall or connection to an existing drain shown, with its level.
5. Each non-standard crossing has its own sheet: plan, long section on existing ground, skew, real length, headwall corners,
   cover to the road surface.
6. Counts and lengths agree: plan labels, quantity table, schedule rows, detail sheet, bar schedule.
7. Sizes are buildable and maintainable for the site, not copied from a template. Changing a size is class C.

## 6. Geometry, profiles and cross-sections
1. Kerb lines, lane widths, islands and tapers dimensioned or given by setting-out points; lane widths add to the carriageway
   width on the section at the same station.
2. Profile: existing and proposed level at every station; the existing row actually varies; vertical curve data present;
   tie-in levels equal existing at both ends; horizontal band matches the plan; for an overlay, proposed minus existing
   equals the overlay thickness or is explained.
   Profile closure, by procedure: walk the vertical points in order. For each tangent, start level + grade x length must
   equal the next point's level (tolerance 5 mm). For each vertical curve, K must equal curve length divided by the
   algebraic grade difference in percent, and the grades stated either side must be the ones the K implies. The last
   grade must reach the stated end level. Then compare the proposed-level row with the levels computed from the grades at
   three or more stations. A profile that does not close is class C: report the arithmetic, do not choose a value.
   Profile labels: levels and stations in the drawing's units (a low-point level near 3,900 on a site at 1,190 m is in
   feet; a station written 4+61.26 is in 100-unit format); curve lengths positive; every vertical curve labelled once;
   a grade break with no curve, or a very small K, is class C: list it with the grades either side.
3. Cross-sections at the agreed interval (the contract's measurement clause governs if it states one) and at every change: the station list equals the profile and the plan; level at the
   centreline equals the profile at the same chainage (match by chainage, not by order); widths add to the total.
4. Every section states: lane, shoulder, verge and berm widths; crossfalls; pavement layers by type; side-drain invert; side
   slopes. These were the missing items in the rejected submission.

## 7. Trade sheets and quantities
1. One sheet per trade in construction order (removal, drainage, kerbs, road safety, pavement, marking), cut from one master
   plan by layer state, so geometry cannot differ between trades.
2. Each sheet has a quantity table in bill-item order, zero rows kept, with units.
3. Each quantity is also written at its location on the plan. Sum the labels: the sum equals the table.
4. The same item has the same value on every sheet where it appears, and equals the bill or the previous revision, or the
   difference is explained. Items drawn on the plan with a zero or empty row in the table, and item types labelled on the
   plan with no row at all, are findings.
5. Item names identical in table, plan label and detail. Type named in a table equals the sketch beside it.
6. Derived items recompute (volume = area times thickness).
7. Added or varied work (repairs, relocations) carries both the measured extent and the specification of the work.

## 8. Details and structures
1. One sheet per size or type actually used; a family table is not a working detail.
2. Dimensions close: n at pitch equals the overall; sizes in tables equal the views; cover stated.
3. Reinforcement: every bar has mark, diameter, spacing, shape and cut length; laps, cover, steel grade and concrete class
   stated; a schedule for every reinforced item. Then re-do the arithmetic on every schedule: leg lengths sum to the bar
   length; the same bar has the same length on the view and in the schedule; the spacing string closes (n x pitch = the
   stated total) and the number of bars is spaces + 1; each row's mass = number x length x unit mass; the schedule TOTAL
   equals the sum of its rows. Re-add the total even when every row looks right.
   A sheet cloned from another size keeps the other size's heading, dimension table or bar counts: compare each schedule
   heading and dimension table with the size named in the sheet title.
4. A sheet titled "reinforcement" shows reinforcement.
5. Materials on every structure sheet: concrete and steel grade, finish, pipe material and class, bedding and backfill.
6. Buildability shown: joints, lifting points, openings for each pipe or duct, anchor pockets and grout, reinstatement layers.
7. Bought-in or fabricated items: drawn as supplied, load class or rating stated, matching the approved submittal; fabrication
   sheets cite the calculation, and the calculation is on file.
8. Schedules tie details to places: which type at which location, counts per type summing to the totals.

## 9. Traffic signals, ducts and power
1. Poles: count by label on the plan = coordinate table = quantity table, by type and arm length. Cancelled numbers marked,
   never reused.
2. Heads, pedestrian heads, push buttons, detectors: counted on the plan against the table. Every item type has a row.
3. Coordinates for every pole foundation, controller, handhole and supply pole; plot a sample for clearance from kerb ramps,
   drainage and utilities.
4. Every duct run ends at a handhole, pole or controller; type, number of ways and length given; carriageway crossings and
   their method identified; existing ducts distinguished from new.
5. Plan-to-cabling tie: each cable length on the plan equals a run on the cabling sheet; every pole appears on both.
6. Cable schedule: one naming convention; core counts sufficient for the groups at each pole; spares stated; core allocation
   and terminal schedules exist as drawings, not only as workbooks.
7. Power: source, meter, breaker, cable type, size and length identical on every sheet that shows them; utility approval noted.
8. Stage diagram: every signal group on the plan appears in a stage and the reverse; no pedestrian group conflicts with a
   running vehicle group; timings and intergreens referenced. A changed stage sequence is class C.
9. Equipment notes from the design carried forward (hoods, special mountings, display-only units) or their removal authorised.
10. Network plans: true-scale base, key plan, run lengths, third-party chambers named, installation method per run, quantity
    summary by duct and cable type.

## 10. Across the documents (drawings, report, bill, specification, setting-out data)
A drawing set is issued with a design report, a bill of quantities, a specification and setting-out data. They are written
by different people at different times, and accepted packages have been found to disagree on every point below.
0. Stated scale against drawn scale on one sheet of each type: measure a known length (two chainage labels, a grid
   interval). Where the scale box has a full-size row and a reduced row, the reduced value is double the full-size one.
1. One end chainage everywhere: plan-and-profile title and band, drawing list, limits on the typical sections, last
   cross-section, last row of the setting-out data, signs-and-markings sheet title, bill header, report. List them side by
   side. (One accepted road carried six different values.)
2. Benchmarks: identifiers, easting / northing column order and values on the benchmark sheet equal the report's table and the
   tables on the plan sheets. Check the magnitudes are plausible for the grid (swapped columns pass a value-only comparison).
3. Coordinate system and height datum stated on the general sheet, on each plan and on the setting-out data. The setting-out
   pages carry a title block, a number and a revision like any other sheet.
4. Setting-out table itself: every interval present from the start to the end chainage, including 0+000 and the end;
   no blank or corrupt row (a level of 0.000, a crossfall of thousands of percent); offsets symmetrical where the section
   is; the same number of decimals throughout. Then, setting-out data against the drawings: centre-line levels equal the profile at five or more stations; first and last
   chainage equal the alignment's; edge offsets equal the typical-section widths for that stretch; crossfall direction equals
   the report's drainage side; horizontal and vertical curve data included or referenced.
5. Report against typical sections: lane, shoulder, walkway and parking widths; the chainage limits of each section type;
   crossfall; grades on the profile inside the range the report states; design speed, design class and traffic class shown
   on the drawings.
6. Pavement, five places that must agree: report, typical section, pavement schedule, bill, specification. Compare material
   class (for example the sub-base class), each layer thickness, the mix name of the wearing course and the walkway surface.
7. Every structure and drain the report names (culverts by chainage and size, box culverts, outfalls) has a drawing and a bill
   line, and the reverse. Schedules that exist only in the report (drain runs, culvert lists with coordinates, sign schedules)
   must come onto the drawings for a working set.
8. Bill arithmetic by rule: surfacing area = widths x length + listed junctions; prime, tack and asphalt all derive from that
   one area; kerb = number of kerbed sides x length + returns; markings = number of lines x length; signs counted per post and
   per plate, not per label (overlapping viewports double-count). Counts identical on two different roads are probably copied.
9. Bill preamble and specification against the drawings: the measurement rule (for example earthworks by average end area on
   sections at a stated maximum interval) must be met by the sections drawn; item variants must match (paint or thermoplastic,
   one mix name); a specification clause exists for every item drawn.
10. Earthworks: if the bill carries cut and fill volumes, the cross-section sheets or a table must let someone re-derive them
    (areas per section, or a volume table).
11. The drawing list is a register built from the title blocks, not a typed copy: a status column that is filled, no rows from
    another project, and the same wording as the sheets.
12. The issued CAD folder holds no lock, backup or error files, no personal copies, and no superseded bills.

## 11. Design-to-working register and revision control
1. For every design item (each labelled run, structure, pole, sign, marking): kept, changed or deleted, and the instruction
   or approval behind each change or deletion. Unsupported changes are class C findings.
2. Revision table on the sheet and clouds on the changes. The file name is not a revision record.
   A common scheme: letters before approval (A internal, B first issue), numerals after (0 issued for approval, then 1, 2);
   each row dated and initialled by issuer, checker and approver. Where one drawing number covers many folios, a revision to
   one folio revises the number: a working set is easier to control with one number per sheet.
3. Comment-response register: each review comment, the sheet changed, and the SISTER sheets checked for the same fault.
4. Conditions of approval ("to be submitted", "with comments") tracked until closed.
5. Only current files in the issue folder; superseded files moved out.
6. For an as-built: site changes shown as built; cancelled items shown as cancelled and not referenced elsewhere; the
   setting-out sheets used for construction retained; approval history kept on the sheets.

## 12. Before issue
1. Re-run sections 1, 2 and 10 on the final PDFs, not on the CAD files.
2. Look at every plotted sheet once, whole, next to the previous issue.
3. Neutral file and folder names; author set to the firm; no tool names in names or properties.
4. Give the engineer: the findings list by class, the class C queries, and a plain statement of what was not checked.

---

## What a working drawing must carry that a design drawing does not
| Subject | Design drawing | Working drawing |
|---|---|---|
| Sheet system | one plan showing all trades | one sheet per trade in construction order, plus details; hierarchical numbers |
| Identity | junction name | junction, work type, chainage range, status, revision |
| Quantities | none or totals elsewhere | bill-order table on the sheet, each quantity located on the plan |
| Control | none | control points E, N, Z plotted and tabulated; chainage; datum |
| Setting out | scale off the plan | coordinates and levels for every pit, headwall, kerb point, pole, handhole |
| Ground | design levels, or "follow existing" | current survey under every profile and section |
| Vertical | profile on main roads only | profile on every paved road; sections every 20 m with all widths, falls, layers, slopes |
| Drainage | type and approximate run | numbered nodes, inverts, gradients, tie-ins, a sheet per non-standard crossing |
| Structures | typical section, size and pitch | bar marks, shapes, schedule with weights, concrete class, laps, joints, lifting |
| Standard details | family tables | one sheet per size used, drawn as supplied |
| Steelwork | outline and schedule | fabrication drawings with parts, welds, finish and calculation reference |
| Signals | one plan with cable tags | plan plus cabling sheet; coordinates; typed and measured ducts; all cable types |
| Power | a symbol | source, meter, breaker, route, cable, poles, utility approval |
| Existing | background | existing items named as existing, reused or removed; utilities with owners |
| Removal | not drawn | its own sheet and quantities |
| Review record | blank sign-off boxes | signed check, revision table, approval stamp, comments closed |

## Order of production for a working set (what to draw first)
1. Settings (section 0) and the sheet register with the numbering rule.
2. Survey, control points, benchmark check against the design levels. Stop for the engineer if they disagree.
3. Master plan per junction or road with a layer per trade; existing utilities on it.
4. Drainage scheme with levels, then profiles and cross-sections. Get these agreed before detailing anything else.
5. Trade sheets from layer states, with located labels; then quantity tables generated from the labels.
6. Site-specific structure sheets and schedules; standard details reduced to what is used.
7. Signal plan, cabling, power; network.
8. Title blocks, list, cross-references. Then sections 1 to 11 of this checklist as an independent check.

## Stop and ask the engineer (never decide)
Any change of size, level, grade, slope, width, material, mix, item type, stage sequence or scope; any disagreement between
design levels and survey; any quantity that cannot be traced to the drawing; any item with no specification; any deletion
without an instruction on file.
