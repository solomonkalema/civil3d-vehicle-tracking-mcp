# Lessons: reviewing a road design and producing working drawings (Civil 3D)

Consolidated on 14 Nov 2026 from a seven-road detailed-design job (plan and profile, cross sections, typical sections, books of
drawings, BoQs, reports). Written to be reused on other jobs: client-specific values are marked as settings. Detail behind each
point is in `knowledge.md` section 14 and in the job's own progress log.

## 1. The order that works
1. Read before changing: project structure, which drawing references which, what is stale.
2. Fix geometry and corridors on a staging copy; rebuild corridor and its surfaces.
3. Sheets: sync references, title blocks, plot, CHECK (section 3), only then assemble.
4. Typical sections, schedules, setting-out tables from the model, not from old sheets.
5. Book assembly, quantities, report: all generated from the same data so they agree.
6. Audit the assembled set as a reviewer would (section 4) BEFORE calling it ready. The audit found faults that every earlier
   "verified" check had missed.
7. Cosmetics last. Package in a neutrally named folder; share only on the engineer's explicit word.

## 2. Rules that cost time when broken
- Never edit shared originals. Work on a staging copy of the WHOLE project tree so relative references still resolve.
- One writer in Civil 3D at a time. Background workers do everything that needs no CAD (workbooks, reports, PDF assembly, audits).
- Many open drawings make Civil 3D crawl (a style pass went from 1 minute to 20). Quit and relaunch every 4 to 6 drawings.
- Do not close data-shortcut drawings over COM. A read-only drawing closes cleanly with `(command "_.CLOSE" "_Y")`.
- The first tool call after a relaunch often fails with "RPC server unavailable": repeat it once.
- Layout import / sheet-creation wizards into heavy drawings froze Civil 3D. Copy an existing layout instead, or plot model windows.
- Every plot opens its PDF in the default viewer, which then locks the file. Plot to a fresh folder each time; write rebuilt
  documents to a new name when the target is locked; never delete-and-replot in place.
- Unsigned add-ins: loading a compiled DLL raises a security prompt an assistant may not answer. Plan on COM plus the style
  dialogs, or have the engineer load the add-in.

## 3. Checks to run on every plotted sheet (automate them)
- PROFILE LINES PRESENT. Plan-and-profile sheets plot without the design and ground lines unless each layout is regenerated from
  inside its viewport first: `_.MSPACE _.REGENALL _.PSPACE`, then plot. Check the PDF for the long thick stroke on every page.
  This fault returned silently when sheets were replotted for an unrelated change.
- Levels against the model: band levels at every station against the design profile; cross-section centreline level against the
  profile, matched by chainage, not by order.
- Band labels: every curve and tangent labelled once. Hand-added text over a missing label becomes a duplicate when the real label
  reappears: delete overlays when the plotting method changes.
- Units and signs in label styles: feet in a metric drawing (`Uft`), a literal minus sign in a sag-curve label, missing unit suffix.
- Placeholders: "???", "####", empty labels.
- Title block per sheet: initials by the client's rule, drawing code, date, sub-title chainage range equal to the alignment's real
  end (check the alignment, not the layout name).
- Fonts and case if the client has a house style: list fonts in the PDF and any lowercase words.
- Text that PDF extraction returns is not always visible: text outside a viewport and zero-height text both extract. Confirm with
  the bounding box inside the page, size above 1 pt, and a render of the spot before chasing it.

## 4. Reviewing a drawing set (what a reviewer catches)
- One read-only reviewer per book, rendering each sheet whole and in tiles, plus one cross-check of quantities and report against
  the drawings. Findings as page, location, defect, severity.
- Typical faults found: missing profile lines; doubled or negative band labels; swapped initials on one sheet; side labels (LHS/RHS)
  contradicting a note after a mirrored section; end chainages differing between title, plan label and tables; quantities that
  ignore a one-sided walkway; workbooks never recalculated (blank in any viewer but Excel) or only opening in repair mode;
  bracketed placeholders and dead cross-references in reports; tables that disagree with the alignment data.
- Separate what is the drafter's to fix from what is a design decision (traffic class, sub-minimum grade, scope of a bill). List the
  second kind for the engineer; do not resolve it.

## 5. What is reachable without the screen, and what is not
- Over COM: text styles (`SetFont`), label styles and their text components, profile/section view titles and axes, band label
  styles (`...BandStyles.<kind>[i].<X>LabelStyle.TextComponents`), block attributes, tables, layouts, plotting.
- Field flags: `(CP)` to `(CU)` prints names in capitals. Inline `\f` codes override the text style and must be rewritten too.
- NOT over COM: band titles (a "Band Title" label style opened from Band Details), code set styles, link/marker/shape label styles,
  expressions, table styles. Use Toolspace > Settings.
- Dialog work can be scripted: Toolspace dialogs open at fixed positions, so once one edit is done by hand the whole click sequence
  replays in a single batch per drawing, with a zoom after each dialog as the check.
- System text cannot be edited: a slope label on a vertical link prints "Vertical". Hide it with a text-height expression
  `IF({Link Length}<0.005,0.0000001,<height in metres>)`.
- Reading GIS data with no GIS software: the GDAL library inside the Python `rasterio` package reads File Geodatabases, KMZ and
  shapefiles through ctypes; a plain reader handles `.shp/.dbf`.

## 6. Generating from the model, not by hand
- Side-slope heights: corridor feature lines (Hinge/Daylight) are seconds; walking applied-assembly points took an hour.
- Slope-protection schedule: threshold on fill/cut height at the road edge, sampled, runs merged. Thresholds are the engineer's.
- Boundary walls: apply an allowance only where a wall exists in the survey; compute clearance from survey lines to the road edge.
- Typical sections: build each from the corridor's actual regions; after mirroring a section check every side label and note.
- Setting-out tables, location map, report tables: one script per output, fed by the same alignment data, so numbers cannot drift.
- Quantities: read from the material sections; state for each bill item whether it is measured, formula-based, an allowance, or
  inferred. Remove what has no design behind it instead of estimating it.

## 7. Office documents
- Workbooks written by a library have no cached values: open, recalculate and save in Excel before issue. If Excel will only open
  one in repair mode, save the repaired copy and compare every cell.
- Word automation: `Documents.Open(path, False, True, False)` then `SaveAs2(pdf, 17)` on a temp copy. Export to PDF to see dead
  cross-references ("Error! Reference source not found"), which the raw file hides.
- Metadata and names: author as the firm, no tool or assistant names in file names, sheet names, comments or properties. Share the
  leaf folder only, so parent folder names never show.

## 8. Settings to ask for at the start of any job (they were learned late here)
- House style: text case, font, unit notation, title-block initials rule, drawing numbering, date format.
- Sheet conventions the client's accepted reference set uses (scales stated versus drawn, section order of the book).
- What is in scope for quantities and report (e.g. drainage excluded), and items the client does not use (e.g. road studs).
- Map style for location plans and whether an online basemap may be downloaded.
- Who decides geometry changes, and where the engineer's sign-off is needed.

## 9. Reusable scripts written on this job (to become MCP tools)
Folded into the server in v0.10 (14 Nov 2026): caps, caps_styles, style_scan, plot_all, proflines, fontcheck, fix_end,
side_slopes, topo_walls, and the merge step of replot_sheets. See knowledge.md section 16. The rest remain job scripts.
caps (text to capitals, fonts), caps_styles (Civil 3D styles), plot_all (viewport regen + plot), proflines (profile-line check),
fontcheck (fonts and lowercase in a PDF), band_fix / band_gaps / band_overlay (schematic bands), replot_sheets (plot, merge, sheet
count guard), fix_end (sub-title chainage), sides (LHS/RHS boxes), side_slopes / slope_table, topo_walls / wall_clearance,
typicals, setout_pages, location_map (+ shp, ogr readers), build_book, report_typicals, style_scan.

## 10. From design drawings to working drawings (added 14 Nov 2026)
From a completed 25-junction improvement and traffic-signal job: 95 design sheets against 420 final sheets, plus one shop
drawing that was rejected and then approved. Plain AutoCAD geometry, no Civil 3D objects, so sections 3 and 5 above (model,
bands, COM reach) did not apply; sections 1, 4, 6 and 8 held. The full checklist is in the `design-review-working-drawings`
skill; this section is the summary.

### 10.1 What a checker rejects on
The first submission came back with one line: reconsider drainage, shoulder and slope. The approved one added: shoulder, berm
and verge-fall dimensions and side slopes on every section; reset drain inverts; chainage on plans; a signed check box and a
revision table; one scale per sheet. Order of a checker's questions: does it drain, are the section elements defined, can it be
set out, is there a signed check and revision record. Presentation comes after these.

### 10.2 What a working drawing carries that a design drawing does not
- Sheet system: one sheet per trade in construction order (removal, drainage, kerbs, road safety, pavement, marking), cut from
  ONE master plan by layer state; numbers `group-trade-detail`; expect about four times the design sheet count.
- Title: place, work type, chainage range, status, revision. Scale written with the paper size.
- A bill-order quantity table on every sheet (zero rows kept) and each quantity also labelled where it occurs.
- Control points (E, N, Z) plotted and tabulated on every plan; axis order and datum stated; 20 m chainage.
- Setting-out per object: coordinates and levels for every pit, headwall corner, kerb point, pole, handhole.
- Current survey under every profile and section. Design levels were 0.5 to 0.8 m off the contractor's survey: check first.
- Profile on every paved road; sections at 20 m with widths, crossfalls, layers, drain inverts, side slopes.
- Drainage: numbered nodes, inverts both ends, gradients, tie-ins, a sheet per non-standard crossing.
- Structures: bar marks, shapes, bending schedule with weights, concrete class, laps, joints, lifting; one sheet per size used.
- Signals: a cabling sheet beside each plan; typed, numbered, measured duct runs; every cable type; the power supply drawn
  (source, meter, breaker, route). Power was the least designed item and grew the most.
- Existing utilities with owners; removal as its own sheet; varied work with both extent and specification.

### 10.3 Faults that reached the final issue (hunt for these)
- Copy-paste leftovers: another site's control points, table heading, pipe size or pasted table. The PDF text layer finds them:
  search every sheet for the names and numbers of the OTHER sites. Hidden text under a mask marks a cloned sheet.
- Quantity table not equal to the sum of its own labels; same item with different values on sister sheets.
- Cross-references to sheet numbers that no longer exist; references to cancelled items.
- Bar length on the view differing from the schedule; counts copied across sizes; outlet level above pit invert.
- Profile rows with a repeated placeholder level; band grade not reproducing the levels; proposed below existing on an overlay.
- Drawing list: duplicate row, a sheet under two numbers, a row copied from another, empty scale cells.
- A correction made on the commented sheet but not on its sister sheets.
- Revision recorded only in file names; superseded files left beside current ones; approval stamps and setting-out sheets
  dropped from the as-built.

### 10.4 Working method that was cheap and reliable
- One text dump per PDF (page by page) and a render helper (whole sheet at about 70 dpi, tiles at 150 to 250). Text first.
- Title blocks drawn as outlines do not extract: tile many title-block strips into one image and read them in batches.
- Title blocks made of plain text are invisible to an attribute-based title-block tool: check which kind before promising it.
- Split a large set among read-only reviewers by sheet group, each writing findings to a file; one session keeps the CAD.
- Keep a design-to-working register: every design item kept, changed or deleted, and the instruction behind it.

### 10.5 Limits of this evidence
One project, sampled in places, and one rejection on file. Treat 10.1 as a working assumption and correct it on the next job.

## 11. What accepted design packages look like, and where they disagree with themselves (added 14 Nov 2026)
From a client's accepted final-design books for twelve urban roads (Civil 3D, data shortcuts) and a complete detailed-design
issue for an 8 km road (drawings, signals, structures, bill, specification, report). Read through the server only: no screen
work was needed. The checks are in the review checklist, sections 1, 6 and 10.

### 11.1 Anatomy of an accepted road book (treat as that client's settings, not as a rule)
- Cover, then a divider per section: general information (drawing list, location map, benchmark list), typical sections and
  pavement schedule, plan and profile, setting-out table, signs and markings layout, cross-sections, then a common back of
  standard details shared by every road.
- Numbering `client / project / road code - sheet type / serial / status / revision`, each part in its own title-block cell
  (attribute tags), with a status-code legend and a scale box holding a full-size row and a reduced row.
- A project folder tree numbered in book order: design files (survey, geometry) feeding plan-production folders, one per sheet
  type, each with its own sheet set. Gaps in the numbering mark sheet types the job did not need.
- Plan and profile at 1:500 / 1:100 on A1 with band rows for chainage, design level, existing level, cut/fill, horizontal and
  vertical schematics, superelevation. Sections every 10 or 20 m. Stake-out of centre line and lane and kerb edges every 10 m.
- A design issue of this kind carries NO quantities on sheets, no trade sheets, no drainage levels, no kerb or pit setting-out
  and no revision entries. Those arrive with working drawings (section 10.2).

### 11.2 What the checker let through (do not copy it; do not assume it is wanted)
Draft status code on a final issue; low-point labels in feet and negative curve lengths on most roads; a stated scale that is
not the drawn one; sheet numbers repeated or skipped (and the same slip in the shared standard back of every book); general
sheets carrying the wrong title; a road with no drawing list; setting-out pages with no title block and a corrupt row; the
same wrong chainage range pasted onto three roads' sheets; different approver initials on sheets of one drawing.
Lesson: an accepted set shows the client's conventions AND the faults nobody caught. Separate the two with the engineer.

### 11.3 Where packages disagree with themselves
- One road, six end chainages (title, band, list, stake-out, bill header, signs sheet).
- Benchmark sheet against report: identifiers shifted and easting / northing swapped.
- Pavement in five places: sub-base class in the bill differing from the drawings; a walkway surface specified three ways;
  asphalt thickness differing between typical section and schedule; wearing-course mix named differently in bill and drawing.
- Report against typical sections: lane, walkway and parking widths, crossfall direction, grade range, section limits.
- Structures named in the report (a box culvert, twin pipes) with no drawing and no bill line.
- Schedules that exist only in the report (drain runs, culvert lists, sign schedules), so the bill cannot be traced to a
  drawing: pipe lengths by diameter, manhole counts and sign counts did not reconcile.
- Bill rules: one surfacing area drives asphalt, prime and tack; kerb = sides x length; counts identical on two roads were
  copied. The bill preamble required sections at a closer interval than the drawings had.
- What did reconcile: profile closure (every vertical point), section centre-line levels against the profile, stake-out
  levels against the profile, culvert register against the structural schedule, bar schedule totals. The model-derived
  numbers were right; the typed ones were not.

### 11.4 Document control seen
Letters before approval and numerals after, with a dated, initialled revision table on each sheet; a transmittal cover per
volume; one drawing number covering many folios (short list, but a change to one folio revises them all).

### 11.5 Reading such a project through the server
`road_shortcut_project`, `road_report_file(parts=[...])`, `road_titleblock_audit`, `road_sheets` gave: reference structure,
viewport scales against stated scales, title-block tags and values, sample-line and corridor structure, assemblies by region.
Shortcut paths stored from an earlier folder name are reported missing although the drawings resolve. Typical sections and
general sheets are plain drawings: their stated scale is a label, not a viewport property.
