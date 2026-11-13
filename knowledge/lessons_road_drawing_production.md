# Lessons: reviewing a road design and producing working drawings (Civil 3D)

Consolidated on 14 Nov 2026 from a seven-road detailed-design job (plan and profile, cross sections, typical sections, books of
drawings, BoQs, reports). Written to be reused on other jobs: client-specific values are marked as settings. Detail behind each
point is in section 14 of `knowledge.md`.

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
caps (text to capitals, fonts), caps_styles (Civil 3D styles), plot_all (viewport regen + plot), proflines (profile-line check),
fontcheck (fonts and lowercase in a PDF), band_fix / band_gaps / band_overlay (schematic bands), replot_sheets (plot, merge, sheet
count guard), fix_end (sub-title chainage), sides (LHS/RHS boxes), side_slopes / slope_table, topo_walls / wall_clearance,
typicals, setout_pages, location_map (+ shp, ogr readers), build_book, report_typicals, style_scan.
