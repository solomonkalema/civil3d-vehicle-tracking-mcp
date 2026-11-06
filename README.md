# Civil 3D MCP

An MCP server that lets Claude Desktop work inside a running Civil 3D session over COM. The core gives general access to drawings and Civil 3D objects. Transport modules sit on top: swept paths (a Python envelope engine and Vehicle Tracking runs), junction geometry, and sight distance.

Version 0.9.0, 27 October 2026. Written for Civil 3D 2027 Metric with Vehicle Tracking 2027.

## What is new in 0.9 (27 October 2026)

Road production tools, from a pilot on a municipal roads design job, where every production step had to be done
with scripts outside the server. Each step now runs inside the server's guards (working copies and the private
staging folder only; never a shared project folder). See section 12 of `knowledge/knowledge.md`.

- New tools:
  - `road_sync_references`: synchronise data references, with the stale list before and after.
  - `road_rebuild_corridors`: rebuild corridors one at a time, reporting OutOfDate before and after.
  - `road_set_titleblock`: set title-block attributes by tag on every sheet, reporting the old values.
  - `road_plot_sheets`: plot layouts to PDF. Checks the plot style is installed (or installs one), runs REGENALL,
    plots with BACKGROUNDPLOT 0, never overwrites a PDF a viewer holds open, and returns what is left.
  - `road_quantities`: cut/fill and material volumes from the sample lines' material sections (average end area).
  - `road_stakeout`, `road_setout_data`: stake-out table and curve/PVI data, written as CSV/JSON.
  - `c3d_quit_civil3d`: quit Civil 3D cleanly between batches (refuses while anything is unsaved).
  - `c3d_focus`: bring Civil 3D to the front for computer use, instead of starting a second copy.
  - `task_preflight('road_production')`: the questions to ask before production work.
- Fixes:
  - Progress windows such as 'Plot Progress' are waited out. The watchdog no longer presses their Cancel button.
  - A dialog that closes while it is being read no longer fails the step ('Invalid window handle').
  - `c3d_status` says `restart_needed` when newer server code is installed than is running.

### Upgrading from 0.8

Nothing to install: the folder is an editable install. Restart the civil3d server (or the Claude app), then check
that `c3d_status` reports `server_version` 0.9.0.

## What is new in 0.8 (26 October 2026)

Road design production, written while reading a municipal roads data-shortcut project. Everything new here only reads; see section 11 of `knowledge/knowledge.md`.

- New tools:
  - `road_drawing_report` reads how the active drawing is built: which objects are its own and which come by data reference (stale or broken ones are flagged in `needs_sync`), profiles, profile views, sample line groups (count, spacing, section views), corridors (baselines, regions, assemblies), xrefs (found or not), sheets and model-space counts.
  - `road_report_file(path)` does the same for a drawing on disk. It opens the drawing read-only and closes it again afterwards.
  - `road_sheets` lists each sheet's plotter, plot style, paper, viewport scale and title-block attributes.
  - `road_shortcut_project(folder)` reads a data-shortcut project from disk: every shortcut, the drawing it comes from, and whether that drawing exists.
  - `c3d_pdf_page` shows a page of an issued PDF sheet as an image.
- Fixes:
  - `civil_inventory` no longer lists 200 empty COGO point names. Points are counted only.
  - `c3d_make_working_copy` saves the copy beside the original when the drawing loads xrefs by relative path, as every production drawing does. A copy one folder down would lose them all.
  - Opening a drawing no longer fails while another drawing is still opening or closing. Such a drawing answers COM without a FullName, and that is now allowed for.
  - PyMuPDF and ezdxf were missing from the server's Python, so they have been installed.

## What is new in 0.7 (26 October 2026)

- A pop-up watchdog. The server now finds, reads, pictures and (where it's safe) clears any pop-up holding Civil 3D. That includes WebView2 dialogs, such as the post-crash Drawing Recovery notice, that screenshots can't see.
  - Every step is watched.
  - A sweeper closes catalogued notices all the time.
  - Pop-ups that touch your work (save, overwrite, licence, sign-in and the like) are never answered automatically. The task stops and reports them in full instead.
- There are two new tools. `c3d_blockers` reads every pop-up, and can answer one on the person's word. `c3d_screen` takes a picture of the Civil 3D window as it is on the monitor.
- See section 10 of `knowledge/knowledge.md`.

## What is new in 0.6 (26 October 2026)

- `c3d_launch(folder, name)` opens a fresh Civil 3D session. If Civil 3D isn't running, it starts Civil 3D 2027 Metric from its Start-menu shortcut, waits in the background until Civil 3D is ready, and then saves a new working drawing. Call it again to follow progress. It never starts a second copy. See section 9 of `knowledge/knowledge.md`.
- When no Civil 3D is running, `c3d_status` now points to `c3d_launch` and reports how far a launch has got.

## What is new in 0.5 (21 October 2026)

Fixes for everything that stalled the server on a TIA job (20 Oct 2026). The details are in `knowledge/knowledge.md`, section 8.

- **The server always answers.**
  - A tool call now waits at most 45 s for the previous step and 52 s for its own, then says what is busy instead of hanging past the 60 s bridge limit.
  - `c3d_status` answers within about 3 s, even mid-step.
  - `c3d_clear_command` sends Esc straight through the window when another step holds the server.
- **No more `((((_>` lock-ups.**
  - `c3d_lisp` refuses unbalanced brackets before sending anything.
  - Long expressions go through a temp `.lsp` file and a short `(load ...)`.
  - A LISP continuation prompt is detected, cleared with Esc and reported.
- **Dialogs that close by themselves no longer block the run.**
  - "Please Wait" splashes are waited out.
  - A dialog closed by you or by Civil 3D is forgotten, and the run carries on.
  - `c3d_run_cancel(force=True)` clears a stuck state without restarting Claude.
- **The Vehicle Library Explorer.**
  - Its 40 nameless tree rows are counted, not listed.
  - Tree selection falls back to keyboard type-ahead (`tree_keys`).
  - Unlabelled controls can be addressed by id.
- **Vehicle check.** Runs with names that several libraries share (e.g. "P - Passenger Car": SANRAL 4.8 m or AASHTO 5.79 m) come back with a note to confirm the vehicle from its profile.
- **New tools.**
  - `c3d_draw_polylines` draws hundreds of polylines in one call.
  - `c3d_import_dxf` brings in a DXF of traced walls and columns and explodes it, with counts per layer.
  - `vt_circulation_arrows` draws green ingress and red egress arrows, with numbered stage circles, along a run.
- **PyMuPDF** is now a required dependency, so `vt_attach_pdf_page` works out of the box.

### Upgrading from 0.4

1. Copy the new files over `C:\Tools\civil3d-mcp`, keeping the `knowledge` folder.
2. In a terminal in that folder, run `python -m pip install -e .` (this adds PyMuPDF).
3. Restart Claude Desktop.

## What is new in 0.2

- **Dialogs no longer freeze the server.** COM `SendCommand` does not return while a modal dialog is open, so v0.1 hung behind any pop-up. Every command now runs on its own thread with a dialog watcher beside it.
- **Dialogs are read without the screen.** Windows UI Automation reads the full contents of a pop-up: title, message, buttons, checkboxes and their state, dropdowns with their options, text fields and tree items. It answers through the controls themselves, not mouse clicks, so you can keep working in other programs.
- **Nothing is clicked past unseen.** The server answers a dialog by itself only when it is a confirmed, unchanged catalogue entry and the task supplied the answer. Everything else comes back to Claude with its contents. Claude answers from the task, or stops and asks you.
- **It learns.** Every dialog is recorded in `knowledge/dialog_catalogue.json`. The first time a dialog is confirmed, its exact control fingerprint is stored, and later runs answer it with no round trip.
- **Pre-flight questions.** `task_preflight` lists what to ask before a task, so the answers are given once at the start.
- **Run logs.** `knowledge/run_logs/` has one file per run, listing each dialog, who answered it and why, and any findings.
- `vt_follow_start` / `vt_follow_finish` are replaced by `vt_follow_run`.

## What has been tested

The geometry engine (guide lines, bicycle and tractrix kinematics, envelopes, clearance, kerb returns, sight distance) has unit tests that pass. Run them with `python -m pytest tests`.

The COM side of v0.1 was tested live on 18 October 2026 with car and SU truck Follow runs. In v0.2, the catalogue and runner logic have tests that replay those runs' five dialogs (`tests/test_dialogs_flow.py`). Reading real dialogs through UI Automation has not yet been run live; the first Follow run on v0.2 is that test.

## Upgrading from v0.1

v0.2 replaced v0.1 in this folder on 18 October 2026. The v0.1 files were removed.

1. In a terminal: `python -m pip install pywinauto` (or re-run `python -m pip install -e .` in this folder).
2. With Civil 3D open, run `python check_connection.py`. Every line should say `[ok]`.
3. Restart Claude Desktop. The config does not change: it still runs `-m civil3d_mcp.server`.

## First install on a new laptop

1. Install Python 3.11 or later from python.org. Tick "Add python.exe to PATH".
2. Copy this folder somewhere permanent, for example `C:\Tools\civil3d-mcp`.
3. In a terminal in that folder:

   ```
   python -m pip install -e .
   python -m pip install pywinauto
   python -m pywin32_postinstall -install
   ```

4. Open Civil 3D and any drawing, then run:

   ```
   python check_connection.py
   ```

   Every line should say `[ok]`. Send me the output if any line fails.

5. Add the server to Claude Desktop. Open `%APPDATA%\Claude\claude_desktop_config.json` (Claude Desktop > Settings > Developer > Edit Config) and add:

   ```json
   {
     "mcpServers": {
       "civil3d": {
         "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
         "args": ["-m", "civil3d_mcp.server"]
       }
     }
   }
   ```

   Use the full path that `where python` prints. Restart Claude Desktop. The tools appear under "civil3d".

## Ground rules built into the server

- Drawing-changing tools refuse to run on an original. `c3d_make_working_copy` saves a copy into a `Claude outputs` folder beside the drawing and switches to it. Files whose name ends in `_claude` or that sit in a `Claude outputs` folder count as working copies.
- An empty command is refused, because Enter on an empty prompt repeats the last command.
- The server attaches to the Civil 3D that is already running. It never starts a second one.
- Swept path drawing tools QSAVE after each successful run.

## Tools

Core (any Civil 3D work)

| Tool | Does |
|---|---|
| `c3d_status` | Version, active drawing, working-copy state, command state |
| `c3d_make_working_copy`, `c3d_open_drawing`, `c3d_save` | Drawing handling |
| `c3d_command`, `c3d_lisp`, `c3d_wait_idle` | Commands and AutoLISP; dialogs they open are watched |
| `task_preflight` | Questions to ask before a task, and the dialogs it will meet |
| `c3d_run_status`, `c3d_run_input`, `c3d_run_cancel` | Follow a run; type at a prompt; cancel at a prompt |
| `c3d_dialog_act` | Answer the waiting dialog (click, select, check, type, tree), with a reason; `learn` records it |
| `c3d_dialog_image` | Picture of the waiting dialog, drawn by the window itself (not a screenshot) |
| `c3d_dialog_catalogue`, `c3d_knowledge` | What has been learned; best-practice notes |
| `c3d_get_vars`, `c3d_set_var` | System variables |
| `c3d_layers`, `c3d_query_entities`, `c3d_entity_info`, `c3d_set_entity_property` | Read and change the drawing |
| `c3d_draw_polyline`, `c3d_draw_text`, `c3d_delete` | Draw and remove |
| `c3d_draw_polylines`, `c3d_import_dxf` | Bulk linework in one call (traced walls, columns, bays) |
| `c3d_export_png` | PNG of a window, no dialogs |

Civil 3D objects

| Tool | Does |
|---|---|
| `civil_inventory` | Alignments, profiles, surfaces, corridors, points, feature lines, pipes |
| `civil_alignments`, `civil_point_at_station`, `civil_station_offset` | Alignment queries |
| `civil_profile_elevations`, `civil_surface_elevation` | Levels |
| `civil_alignment_from_polyline` | Create an alignment |

Swept paths, Python engine

| Tool | Does |
|---|---|
| `sp_vehicles`, `sp_define_vehicle` | Design vehicles (P, SU, WB-15 built in) |
| `sp_pixels_to_metres` | Raster layout pixels to drawing metres |
| `sp_build_guide` | Guide line from vertices and fillet radii, optional build-up, txt export |
| `sp_check_guide` | Steering, articulation and clearance to DWG layers, before any VT run |
| `sp_draw_swept_path` | Draws envelope, wheel tracks and outlines straight from DWG linework |

Vehicle Tracking

| Tool | Does |
|---|---|
| `vt_prepare_drawing`, `vt_attach_image` | DYNMODE 0, metres, true-scale raster |
| `vt_follow_run` | DRIVEFOLLOW with direction, vehicle, library path and speed policy; dialogs handled as above |
| `vt_list_paths`, `vt_insert_profile` | Paths and vehicle profiles |
| `vt_check_path` | Clearance of what VT actually drew, against obstacle layers |
| `vt_annotate_png` | Crop, scale bar, north arrow for report figures |
| `vt_circulation_arrows` | Green ingress / red egress arrows and stage numbers along a run |

Junctions and roads

| Tool | Does |
|---|---|
| `jn_kerb_return` | Simple or three-centred kerb return |
| `jn_taper` | Flare or lane-gain taper |
| `jn_turn_test` | Turn a design vehicle through a corner and check it |
| `rd_stopping_sight_distance`, `rd_visibility_splay` | Access sight checks |

## Vehicle Tracking and its dialogs

Vehicle Tracking has no public API, so its dialogs are part of every run. The catalogue starts with the five seen live:

| Catalogue id | Dialog | Answer comes from |
|---|---|---|
| `vt.default_vehicle` | "Do you wish to follow using the default vehicle (X)?" | Yes only if X is the task's `vehicle` |
| `vt.drawing_settings` | Drawing Settings, the first time in a drawing | Fixed once confirmed: metres, drive on the left |
| `vt.vehicle_library` | Vehicle Library Explorer (+ Vehicle Diagram) | The task's `vehicle_path` |
| `vt.follow_settings` | Follow Settings | The task's `direction`; Position Automatic |
| `vt.speed_reduction` | "unable to track the selected line… reduce speed?" | Judgement: the task's `speed_policy`, always recorded as a finding |

All five start unconfirmed. On the first v0.2 run, each one comes back to Claude once and is confirmed with `learn`. After that, a run with the same answers goes through with no stops.

## Vehicle dimensions

The built-in P, SU and WB-15 use the overall sizes and wall-to-wall radii from earlier TIA jobs. Wheelbases and overhangs are indicative. Before a result goes into a report, open each vehicle in the VT library, read its diagram, and enter the values with `sp_define_vehicle`. They are saved in `civil3d_mcp/vehicles.json` and override the built-ins.

## Adding a module later

Each module is a Python file in `civil3d_mcp/` with plain functions, plus a few `@mcp.tool()` wrappers in `server.py`. Use `C.guard_write()` before changing the drawing and `C._retry()` around COM calls. Likely next modules: signal layouts, parking layouts with bay counts, road safety audit markups. Where the COM API runs out (corridor rebuilds, assemblies), a small C# plugin loaded with NETLOAD can expose commands that this server calls.
