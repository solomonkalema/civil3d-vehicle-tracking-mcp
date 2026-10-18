# Civil 3D MCP

An MCP server that lets Claude Desktop work inside a running Civil 3D session over COM. The core gives general access to drawings and Civil 3D objects. Transport modules sit on top: swept paths (a Python envelope engine and Vehicle Tracking runs), junction geometry, and sight distance.

Version 0.2.0, 18 October 2026. Written for Civil 3D 2027 Metric with Vehicle Tracking 2027.

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

The built-in P, SU and WB-15 use the overall sizes and wall-to-wall radii from the Leceny and Biplous jobs. Wheelbases and overhangs are indicative. Before a result goes into a report, open each vehicle in the VT library, read its diagram, and enter the values with `sp_define_vehicle`. They are saved in `civil3d_mcp/vehicles.json` and override the built-ins.

## Adding a module later

Each module is a Python file in `civil3d_mcp/` with plain functions, plus a few `@mcp.tool()` wrappers in `server.py`. Use `C.guard_write()` before changing the drawing and `C._retry()` around COM calls. Likely next modules: signal layouts, parking layouts with bay counts, road safety audit markups. Where the COM API runs out (corridor rebuilds, assemblies), a small C# plugin loaded with NETLOAD can expose commands that this server calls.
