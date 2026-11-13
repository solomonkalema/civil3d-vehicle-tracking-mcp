# Civil 3D MCP server: knowledge base

The server loads this file, and Claude reads it, at the start of every task. It holds three kinds of content:
- what has been observed on the author's machine (marked **[observed]**);
- what Autodesk's documentation says (marked **[docs]**, with sources at the end);
- the house rules that follow from both.

Versions in use: Civil 3D 2027 Metric and Vehicle Tracking 2027. Several help pages cited below are from the 2016 help. Where they matter, check them against 2027 behaviour and update the entry.

---

## 1. How the server talks to Civil 3D

- **[v0.2]** Every command goes through the runner: its own COM thread plus a dialog watcher that works through Windows UI Automation. A modal dialog no longer freezes the server, and no screen access is needed.

- **[observed]** A Vehicle Tracking modal dialog stops Civil 3D from answering COM calls while it is open, and that includes `SendCommand` with "wait" switched off. The server's own wait-for-idle step also hangs. So any command that may open a dialog has to be started on a background thread with a timeout, and the dialog watcher has to work outside COM, through Windows UI Automation.
- **[observed]** DRIVEFOLLOW typed at the command line runs into the same dialogs, but it doesn't freeze the server's COM thread, because the server isn't waiting on that call.
- **[docs]** COM `SendCommand` runs synchronously. .NET `SendStringToExecute` is asynchronous: the command only runs once the calling .NET command has ended (Access the Command Line, .NET). A future in-process .NET helper could use it to queue commands without blocking.
- **[docs]** Each prompt needs its own argument, in order. A space or carriage return counts as Enter.
- **[observed]** A Task Manager window maximised over the screen masks every screenshot, and Claude can't click it because it runs with administrator rights. Close or minimise it before any screen-access fallback.

## 2. AutoCAD settings that cut down on dialogs (set at the start of a session)

| Variable | Setting | Effect | Notes |
|---|---|---|---|
| `DYNMODE` | 0 | Typed coordinates are read as absolute | **[observed]** Required; otherwise picks arrive as relative or partial input |
| `FILEDIA` | 0 during file operations, 1 afterwards | File commands prompt on the command line instead of opening dialogs | **[docs]** Type `~` at the first prompt to force the dialog anyway |
| `EXPERT` | 0 by default, up to 5 | Suppresses specific yes/no prompts (regen, redefine block, overwrite file, redefine linetype/UCS/dimstyle) as if the answer were **y** | **[docs]** Not saved with the drawing. **Rule:** don't raise it above 0 on client drawings. An overwrite or redefine is exactly the kind of prompt to read first, so the watcher should catch these instead. |
| Hyphen prefix | `-LAYER`, `-IMAGE`, `-INSERT` and similar | Command-line version of a dialog command | **[docs]** Works for many commands, not all. Vehicle Tracking commands don't have one. |

## 3. Vehicle Tracking: known dialogs for a Follow run

Fingerprints match on title plus control set. **The server answers a dialog automatically only when it matches exactly and the task has supplied the answer.** Anything else comes back to Claude.

| # | Dialog | When it appears | Fields | How often the answer changes | Answer comes from |
|---|---|---|---|---|---|
| D1 | "Autodesk Vehicle Tracking": *Do you wish to follow using the default vehicle (X)?* (Yes / No) | Every DRIVEFOLLOW, unless a path is preselected **[observed]** | the vehicle named in the text | per task | Yes if X is the task's vehicle, otherwise No |
| D2 | "Drawing Settings" (Scale / Surfaces / Paths): units, auto check scale, prompt for scale on first use, driving convention, angles | The first time Vehicle Tracking is used in a drawing **[observed]** | 1 drawing unit = 1 metre; driving convention; "Get below values from drawing where available" | fixed | Metres, drive on the **left** (Uganda). See §4. |
| D3 | "Vehicle Library Explorer" plus a separate "Vehicle Diagram" window, with Make Default / Print / Proceed / Close | After No in D1 **[observed]** | library tree: library > group > vehicle | per task | Select the task's vehicle path, e.g. *South African Design Vehicles > SANRAL > SU - Single Unit*, then Proceed |
| D4 | "Settings" (Follow): Direction, Position, Generate an editable path, Algorithm, Optimisations | Every Follow run **[observed]** | Direction (Forwards / Reverse / Keep Current / Automatic **[docs]**); Position (Automatic / Manual) | per run (stage) | Stage direction; Position Automatic |
| D5 | "Autodesk Vehicle Tracking": *Follow has been unable to track the selected line closely… reduce the design speed?* (Yes / No) | When the guide line is too tight for the design speed **[observed]** | none | **judgement** | Never answered silently. The task says either "accept and record" or "stop and report". Either way it's logged as a finding about the geometry. |

### Observed values for the SANRAL vehicles (Vehicle Diagram, 18 Oct 2026)

| Vehicle | Length | Width | Wall-to-wall turning radius |
|---|---|---|---|
| WB-15 semi-trailer | 17.000 m | 2.500 m | 11.0 m |
| SU+T single unit + trailer | 18.600 m | 2.500 m | 10.0 m |
| SU single unit | 9.100 m | 2.500 m | 10.000 m (body height 3.632 m, ground clearance 0.427 m, track 2.500 m, lock-to-lock 4.00 s) |
| P passenger car | 4.800 m | 1.800 m | 6.8 m |
| BUS single unit bus | 12.300 m | 2.600 m | 11.5 m |

## 4. Vehicle Tracking settings: best practice

- **Driving convention [docs].** This sets which side the driver sits on. That matters when the tracking point is the driver, and it sets the direction of angled parking bays relative to traffic. It's greyed out while "Get below values from drawing where available" is ticked. **Rule:** for Ugandan jobs, untick that box once, set "Vehicles drive on the left", then tick it again or click Make Default. **[observed]** The template default is "right".
- **Prompt for scale on first use [docs].** This is on by default for novice users. Turning it off removes D2 from new drawings after the first set-up.
- **Default vehicle [docs].** In the Library Explorer, select a vehicle and click **Make Default**, or right-click and choose "Set as Default Vehicle". Follow uses the default vehicle. If a path is already selected when Follow starts, it uses that path's vehicle and position, and the Library Explorer is skipped.
- **Design speed [docs].** Forward and reverse design speeds are set separately. The vehicle is always modelled at the design speed. Autodesk strongly recommends limiting the turn radius for turns above 15 km/h. **[observed]** On a 15 m and a 20 m bend with no transition, Follow asked to cut the speed. The truck run ended at 2.34 km/h forward, which is unrealistically slow for anything other than a manoeuvring check. **Rule:** guide lines get a steering build-up (a transition) between straight and arc, and arcs at least 1 m wider than the vehicle's minimum radius. That way D5 shouldn't appear on routine runs, and when it does, it means something.
- **Steering limits [docs].** Path Properties > Manoeuvres > Edit > Model tab. Limits are set as a percentage, an angle or a radius. Practitioners cap steering below the vehicle's maximum so that no driver is expected to turn at full lock (Arkance). Use the vehicle's specification or the client's standards for the value.
- **Clearance [docs/practice].** Add a clearance offset to the envelope. In Vehicle Tracking Properties > Reports > Clearance Envelope > Body Outline > Edit, set the **Offset Envelope**. Set it in the Report Wizard and it applies to all future paths. The AutoDrive system settings also have a Default Clearance Envelope with a settable offset. **House rule:** report clearance against **300 mm**. Where a vehicle only just fits, record that as a finding.
- **Tracking point [practice, CDOT].** Forward: the frontmost axle or the driver. Reverse: the rear of the body. Use the radius reference (steered-axle centre or wheel offset) that matches the kerb return being tested.
- **Tight 90° turns [practice, CDOT].** The default minimum radius can give poor results at tight junctions. Set the minimum radius to match the return, track from the inner wheel, and add a clearance offset.
- **Calculation interval [practice, CDOT].** A smaller interval is more accurate but slower. Keep the default for TIA figures.
- **Check manoeuvres [docs].** The "cat's whiskers" and "cat's tail" show the tightest turn possible ahead of and behind the vehicle. They're useful for parking bays and turning heads.
- **Verify what was drawn [observed + skill].** Follow may drop points it can't reach. Check the envelope it actually produced, not the guide line, then export a PNG and inspect every stop and park position.
- **Path editing [docs/practice].** Paths stay editable after insertion (move, rotate, trim start or end, edit target points), but small placement errors grow in later manoeuvres. Fix them at the first stage.

## 5. Questions to ask before a Vehicle Tracking task

These come from §3 and §4. Ask them once, together, before anything runs:

1. Which drawing? It's always run on a working copy, never the original.
2. Which vehicles, as full library paths (SANRAL by default for Uganda)?
3. For each vehicle: which stages (in, park, reverse, out) and the direction of each?
4. Is a raster layout attached? Its file, scale and dpi?
5. Speed reduction (D5): accept and record, or stop and report?
6. The clearance to report against (default 300 mm), and the obstacle layers?
7. Should the drawing defaults be set now (drive on the left, prompt for scale off)? This is once per drawing or template.
8. Outputs: PNG windows, vehicle profiles, a saved DWG location?

## 6. Where the learning lives

- `dialog_catalogue.json` (next to this file): every dialog the server has met, with its fingerprints, an example of its contents, how often it was seen, and whether it is confirmed. Unknown dialogs are kept as `unknown.<fingerprint>` until they are explained with the `learn` argument of `c3d_dialog_act`.
- `run_logs/<run id>.jsonl`: one file per command run, listing every dialog, who answered it (the server from the task's answers, or Claude with a reason) and any findings. This is the audit trail for a submission.
- This file: best practice and house rules. Edit it by hand; the server only reads it.

---

### Sources

- Vehicle Tracking: [To Use Follow](https://help.autodesk.com/cloudhelp/ENU/Autodesk-VehicleTracking-Help/files/GUID-97D4D0E3-7638-4A1E-A683-1A3C6F9D26D4.htm); [Drawing Settings: Scale](https://help.autodesk.com/cloudhelp/2016/ENU/Autodesk-VehicleTracking-Help/files/GUID-3EAF5C00-BCB2-4036-A0F2-B763094B22DD.htm); [Drawing Settings: Paths: Speed](https://help.autodesk.com/cloudhelp/2016/ENU/Autodesk-VehicleTracking-Help/files/GUID-5524544E-F797-41BB-B878-30B856A8B3EA.htm); [To Change the Absolute Steering Limits](https://help.autodesk.com/cloudhelp/2016/ENU/Autodesk-VehicleTracking-Help/files/GUID-58894B9F-0B83-4155-94C3-E7AB3F11D3A3.htm); [System Settings: Paths: AutoDrive](https://help.autodesk.com/cloudhelp/ENU/Autodesk-VehicleTracking-Help/files/GUID-0EA11510-D7CE-4D63-85CC-47E9D689A88B.htm); [System Settings: Start Up](https://help.autodesk.com/cloudhelp/2016/ENU/Autodesk-VehicleTracking-Help/files/GUID-EB5ACE9E-A70E-480B-B3DE-3DF7727DD2F6.htm)
- AutoCAD: [Access the Command Line (.NET)](https://help.autodesk.com/cloudhelp/2016/ENU/AutoCAD-NET/files/GUID-F4A36181-39FB-4923-A2AF-3333945DB289.htm); [About Accessing the Command Line (ActiveX)](https://help.autodesk.com/cloudhelp/2019/ENU/AutoCAD-ActiveX/files/GUID-0A56CD7D-C18E-4A94-B0A7-6F70956A21A9.htm); [Switching Between Dialog Boxes and the Command Line](https://help.autodesk.com/cloudhelp/2018/ENU/AutoCAD-LT/files/GUID-A02E1B47-AED7-4308-A6A3-94368852C2D8.htm); [EXPERT](https://help.autodesk.com/cloudhelp/2018/ENU/AutoCAD-LT/files/GUID-28C09385-C00B-48F6-9765-7CB178CDD891.htm)
- Practice: [Arkance, Vehicle Tracking guide](https://ukcommunity.arkance.world/hc/en-us/article_attachments/21569844411154); [IMAGINiT, Margin of error in swept path analysis](https://resources.imaginit.com/civil-solutions-blog/margin-of-error-applied-to-swept-path-analysis-autodesk-vehicle-tracking); [CDOT, Using AutoTrack](https://codot.gov/content/business/designsupport/CADDmanual/CDOTUsingAutoTrack.pdf)


## 7. Lessons from a swept-path job, 18 Oct 2026 (server v0.3)

- **[observed] Pick point.** At "select object to Follow" Vehicle Tracking holds the command line and Civil 3D refuses every COM call, so a pick sent with SendCommand never arrives. v0.3 types the pick through the window (WM_COPYDATA), which works while COM is blocked. Order seen live: DRIVEFOLLOW -> default-vehicle question -> (library, if No) -> select-object prompt -> Follow Settings -> path.
- **[observed] What a pick lands on.** A pick over the attached raster picked the image; a pick where an earlier ATR path overlapped the guide picked the path. Both ended Follow silently with no path. v0.3 locks image and obstacle layers, hides other ATR paths and guides, checks the pick hits only the guide, and restores the layers afterwards.
- **[observed] PAN blocks COM.** A live PAN (or any transparent command) made every call fail as "AttributeError: <unknown>.GetVariable". v0.3 treats that as busy and c3d_clear_command sends Esc through the window.
- **[observed] Front axle.** Follow steers the front axle along the guide when driving forwards. A rear-axle check passed at 0.45 m where Follow's envelope touched the planter. sp_check_guide now defaults to reference "auto" (front axle forwards, rear axle in reverse).
- **[observed] Explode.** `(command "_.EXPLODE" ent "")` re-fires a command through the extra Enter and hangs; COPY in multiple mode does the same. Use COPYMODE 1 and EXPLODE with the entity only.
- **[observed] Profiles.** INSERTPROFILE a second time on the same path adds a second profile; it does not move the first. The server records each insert and refuses a repeat. With the Standard style on the "txt" font the profile values overprint the labels: vt_prepare_drawing sets Arial.
- **[observed] Follow Settings combos** expose their labels, not their items, to UI Automation ("Direction, Direction, Close"). The server leaves a combo alone when it already shows the wanted value and otherwise sets it through the live control.
- **[observed] Floating command line.** While a modal is open, the floating command line is a top-level window and was being read as part of the dialog, which changed the fingerprint ("controls differ"). It is now excluded.
- **[observed] SANRAL P car profile:** 4.800 x 1.800 m, body height 1.520 m, ground clearance 0.305 m, front overhang 0.7 m, wheelbase 3.1 m, lock-to-lock 4.0 s, wall-to-wall radius 6.800 m. The engine's P now uses these.
- **Rule: design vehicles.** The TIA decides the design vehicles. Run the standard DV-1 car and every other design vehicle the TIA names, each found in the full library (vt_vehicle_library) and given its full stage set. A single-vehicle run is not acceptable.
- **Rule: keep good work.** Never delete a working path to fix something cosmetic. Save after every successful run.
- **Turn-round.** Follow cannot do multi-point turns; sp_turn_search answers "can it leave in forward gear?" as a labelled kinematic check. On that job: no three-point turn in the 8.8 m yard with bays occupied; with every bay empty, only 0.09 m clearance.

## Full swept-path cycle (house rule, 18 Oct 2026, v0.4)
- A swept path for a vehicle that parks is not one manoeuvre. Show the full cycle with the same vehicle:
  ingress from the public road in forward gear -> park in a proposed bay -> egress back to the road in forward gear.
- Pick the most constrained bay of each bay type (deepest in the site, narrowest aisle, furthest from the access).
- Stages: usually forward in, then forward or reverse into the bay, then out forwards (a reverse out of the bay
  followed by a forward exit is fine inside the site). Reversing onto or off the public road is a finding.
- Pre-check the whole sequence with sp_cycle_check (stages join up, the parked body is inside the bay, clearances),
  then run each stage in Vehicle Tracking with the same vehicle and report them as one cycle (runs VT-n in order).
- Service vehicles that do not park: ingress -> stand or turn -> egress.

## Ramps in section (v0.4)
- Plan swept paths do not prove a ramp. For each vehicle that uses a ramp, run sp_ramp_section on the long section:
  breakover at crests, front overhang at the foot / rear overhang at the head (and the reverse when leaving),
  roof to soffit, beam or door head.
- Floor and soffit: trace the architect's section at true scale (vt_attach_pdf_page, check one dimension) as
  polylines with x = chainage and y = level, drawn left to right, or pass points. A Civil 3D profile works too.
- Side profile: take 'Min Body Ground Clearance' and 'Overall Body Height' from the vehicle's Vehicle Tracking
  profile (vt_insert_profile shows them). SANRAL SU: 0.427 m and 3.632 m; SANRAL P: 0.305 m and 1.520 m (the defaults).
  The VT car clearance is generous: a low saloon sits nearer 0.12 m, so for a car ramp also run ground_clearance=0.12.
- Defaults: underside margin 0.05 m, headroom margin 0.10 m over the body height. The report also gives grades,
  grade changes and the static headroom of the ramp itself; compare those with the standard the TIA cites.
- Transitions: a sharp grade change of 10% or more at a single point grounds a 0.12 m car on a 3.1 m wheelbase;
  transition lengths of about 2 m in steps of half the grade change usually cure it. Prove it with the tool.

## 8. Lessons from a TIA job, 20 Oct 2026 (server v0.5)

What went wrong on that job, and what v0.5 does about it:

- **[observed] Long LISP was cut off.** A 5 kB `c3d_lisp` expression (190 polylines) arrived incomplete and left the command line at `((((((((((_>`. Civil 3D then refused COM, and every later call, including `c3d_status`, timed out at the 60 s bridge limit. **v0.5:** `c3d_lisp` refuses unbalanced brackets before sending. Anything over about 900 characters is written to a temp `.lsp` file and sent as a short `(load ...)`. If a `(_>` prompt is seen anyway, the runner sends Esc and fails the run with a plain message.
- **[observed] Bulk linework.** 2,448 obstacle polylines went in reliably as one DXF (written with ezdxf), using `-INSERT` and then EXPLODE. **v0.5:** `c3d_import_dxf` does this in one call, and `c3d_draw_polylines` draws a list of polylines through batched entmake data.
- **[observed] Tool calls must answer within 60 s.** A long step held the server's lock, so even status calls hung. **v0.5:** a call waits at most 45 s for the lock and 52 s for its step, then reports what is busy. `c3d_status` always answers within about 3 s, falling back to a status that does not use COM. `c3d_clear_command` sends Esc straight through the window if another step holds the server.
- **[observed] Stale dialogs.** The watcher recorded the "Vehicle Tracking. Please Wait...." splash as a dialog. It kept reporting it after it closed, while the Library Explorer opened behind it. Later, when the person closed the Explorer by hand, the run stayed "dialog" and blocked every call until the MCP was restarted. **v0.5:**
  - Splashes with no controls are waited out for up to 90 s.
  - A pending dialog whose window has gone is forgotten (logged as `dialog_closed`), and the run carries on.
  - `c3d_dialog_act` on a dialog that has already closed just continues the run.
  - `c3d_run_cancel(force=True)` presses Esc on a dialog that is still open.
- **[observed] Library Explorer tree items have no UI Automation names** in VT 2027. 40 unnamed TreeItems flooded every reply. **v0.5:**
  - Unnamed items are counted, not listed.
  - `{"tree": path}` falls back to keyboard type-ahead (`{"tree_keys": path}`: Home, type each name, Right twice to expand). Check the result with `c3d_dialog_image` before Proceed.
  - `{"keys": {"id": "c60", "text": "..."}}` types into any control, and `{"label": "c60"}` now means control id c60.
- **[observed] Ambiguous vehicle names.** "P - Passenger Car" exists in SANRAL (4.8 x 1.8 m) and in AASHTO 2011 (Metric) (5.79 x 2.13 m). The default-vehicle question only shows the name, so the server answered Yes and ran the wrong car. **v0.5:** `vt_follow_run` adds a `vehicle_check` note for shared names. **Rule:** confirm the vehicle from its Insert Profile dimensions after the first run. The quickest set-up is for the person to select the TIA's car once and click **Make Default**.
- **[observed] `vt_attach_pdf_page` failed** because PyMuPDF was not installed in the server's Python. **v0.5:** PyMuPDF is now a required dependency, and the error gives the exact `python -m pip install` command.
- **[observed] The obstacle trace picked up section-cut lines.** The dashed section markers (L-11, L-12) were drawn as many short solid segments and showed up as false wall conflicts. **Rule:** when tracing walls from an ArchiCAD PDF, drop collinear runs of short segments along grid or section lines, and check the obstacle layer visually before the clearance checks.
- **[observed] Ramps.** A 1 in 7 ramp with no transitions grounds a low saloon (0.12 m underbody, 0.18 m overhang). 2 m transitions at half grade leave about 0.03 m, and 2.5 m restores the 0.05 m margin.

### Circulation arrows (house rule, 20 Oct 2026)

Every swept-path sheet carries direction arrows along each run: **green for ingress** (layer CIRC-IN) and **red for egress** (CIRC-OUT), with numbered stage circles (CIRC-STAGE). They go into every report figure and are offered to the developer as the basis of the signed circulation. Draw them with `vt_circulation_arrows(kind, guide_handle, stage_number)`.

### When the MCP itself is failing

If the MCP stalls twice on the same task, switch to direct computer control (screenshots and clicks) to finish the swept paths, then note what broke here. The person's time matters more than the route.

## 9. Starting Civil 3D from the server (v0.6, 26 Oct 2026)

- `c3d_launch(folder, name)` opens a fresh session. If Civil 3D isn't running, it starts **Civil 3D 2027 Metric** from its Start-menu shortcut ("Autodesk Civil 3D 2027 - English" folder), then saves a new working drawing `<folder>\<name>.dwg`, the same as `c3d_new_drawing`.
- Starting takes 1 to 3 minutes, longer than one tool call may take. The launch runs in the background, and each call waits up to 40 s. Call it again until `launch_state` is `ready` and `new_drawing` is filled. `c3d_status` also reports the launch.
- The shortcut is opened through Windows Explorer, as a double-click would be. Civil 3D then doesn't belong to the MCP server and stays open when the server restarts. If Explorer doesn't start it within 25 s, the server runs the shortcut's target directly.
- It never starts a second Civil 3D. If acad.exe is already running, even if it's still loading, the launcher waits for it.
- **[observed 26 Oct 2026]** An acad.exe left over from an earlier session can keep running with no window and refuse COM, which blocks a new start. After 40 s the launcher reports `blocked`. Ask the person first, then `c3d_launch(..., end_hidden=True)` ends it (only if it has no window) and starts Civil 3D.
- Ready means Civil 3D answers COM and has stayed idle (quiescent) for 4 s. After a minute, a slow start lists Civil 3D's open windows. A licence, sign-in or first-run dialog shows up there and needs answering.
- To override the launch, use `knowledge/launch.json`: `{"shortcut": "C:\\...\\Civil 3D 2027 - Metric.lnk"}` or `{"exe": "...acad.exe", "args": "/ld ... /p \"<<C3D_Metric>>\" /product \"C3D\" /language \"en-US\""}`.
- **[observed 26 Oct 2026] Startup notices.** After a crash, Civil 3D 2027 shows a "Drawing Recovery" notice ("the program terminated unexpectedly... Drawing Recovery Manager") with Close and "Do not show me this message again".
  - The notice is a WebView2 window owned by msedgewebview2.exe, not acad.exe, so computer-use screenshots never show it and acad.exe's window list misses it.
  - It's modal: every COM call hangs while it's open. In the 26 Oct test it appeared about 2 minutes after Civil 3D first reported idle.
  - The launcher closes it by its exact title (WM_CLOSE), repeatedly during start-up and for 20 s after ready, and again before making the drawing. Nothing is lost by closing it: the backups stay in the Drawing Recovery Manager palette.
  - If any Civil 3D step hangs for no visible reason, suspect a WebView2 dialog and ask the person what's on screen.

## 10. Pop-ups when no one is watching (v0.7, 26 Oct 2026)

House rule: the server has to cope with pop-ups by itself, because he won't always be there. Computer-use screenshots can't be trusted to show dialogs. Civil 3D 2027 draws some dialogs in WebView2 (msedgewebview2.exe), and those windows are hidden from screenshots. The server runs on the PC, so it does the looking (`watchdog.py`).

- **Every step is watched.** Every 5 s while a step runs, the server looks for windows of acad.exe and its child processes that could be holding Civil 3D. It reads them by UI Automation and acts on one rule set:
  - **safe_notice** (catalogued in `SAFE_NOTICES`, such as Drawing Recovery): closed.
  - **notice** (a standard dialog with only OK): OK is pressed.
  - **cancelable** (a Cancel button and no risky words): Cancel is pressed. The step stops and nothing changes.
  - **protected** (mentions save, overwrite, replace, delete, licence, sign-in, recover and the like) and **unknown**: never answered. The step returns at once with `blockers`: the text, the buttons and a picture.
- **The person decides protected and unknown pop-ups.** Quote the dialog to the person. If they may be away, stop the task there and send a push notification quoting it. Answer only on their word, with `c3d_blockers(hwnd=..., click=..., reason=...)`.
- **A sweeper runs all the time.** Every 3 s it closes catalogued notices, even between tasks.
- **There's a tool for each check.** `c3d_blockers()` reads everything holding Civil 3D. `c3d_screen()` takes a picture of the Civil 3D window as it really is on the monitor, dialogs included, with other programs cropped away. When Civil 3D is busy, `c3d_status` lists the pop-up titles.
- **There's an audit trail.** Every action goes to `run_logs/watchdog.jsonl`, with a picture taken before acting in `run_logs/watchdog/`.
- **Vehicle Tracking runs keep their own handling** (runner.py and the dialog catalogue). The watchdog stays out of an open run, apart from the catalogued-notice sweeper.
- **Adding a notice to the catalogue.** Once a new pop-up has been judged purely informational, add its exact title to `SAFE_NOTICES` with one line saying why it's safe.

## 11. Road design production: data-shortcut projects (v0.8, 26 Oct 2026)

Project-specific notes (the project map, people, findings and figures for a particular job) live in
`knowledge.local.md` beside this file. It is not published; c3d_knowledge appends it when it exists.

### How such a project is usually built [observed]
- A Civil 3D **data-shortcut project** (Autodesk's standard folders). Each road is a chain:
  1. **Survey xrefs**: topo and grid/border drawings under `External References`.
  2. **Alignment source**: the topo surface, the centreline with its design and existing-ground profiles, and the
     offset alignments the corridor targets (edges, walkways, parking lanes, drains), each with a profile.
  3. **Corridor source**: alignments, profiles and surface by data reference; junction alignments, assemblies, the
     corridor (main baseline split into regions plus one baseline per junction leg), and Top and Datum surfaces.
  4. **Plan and profile**: view frames, match lines, one profile view and one layout per sheet; xrefs by relative path.
  5. **Cross sections**: a sample line group, section views and sheets (AeccDbSheet), one layout per sheet.
  6. **General drawings**: plain AutoCAD details copied per road.
- A `.dst` sheet set sits beside each production drawing and loads into the Sheet Set palette.
- Shortcut XML stores the creator's absolute path as well as `DwgRelPath`; the relative path is resolved against the
  project's parent (the working folder).
- One road's geometry may be published from another road's drawing; check the shortcut XML, not the folder names.

### Scale check [observed]
- A viewport's CustomScale is in paper mm per drawing metre: 2.0 = 1:500, 5.0 = 1:200 on the sheet size used.
- Compare the drawn scale with the title block's stated scale, but **compare with the client's accepted reference
  project before calling a mismatch a defect**: a client's template may state the scale for a different paper size.

### Rules
- Read first: road_shortcut_project, then road_report_file per drawing (alignment, corridor, PnP, CS).
  `needs_sync` lists stale data references.
- **Never write into a shared project folder.** List shared folders and their private staging folders in
  `shared_roots.json` (see `shared_roots.example.json`). Working copies are redirected to staging at the same
  relative path; saves, new drawings and copies into a shared folder are refused.
- Production drawings xref by relative path, so the staging folder holds a copy of the whole project tree.
- **A working copy of a source drawing is not what the sheets reference** until it replaces the original and the
  references are synchronised. Agree this promote step with the person (and the geometry owner) before editing.
- **Opening is slow**: 30-60 s for a large corridor or PnP drawing. COM calls are rejected meanwhile
  (-2147418111): retry. The window may paint black for a while; check documents_open before concluding anything.
- Never walk ModelSpace one entity at a time over COM on survey drawings (tens of thousands of COGO points);
  count with filtered selection sets.
- Computer-use screenshots mask apps that aren't granted; a dark rectangle over Civil 3D is usually that, not a
  dialog. c3d_screen shows what is really there.
- A document that is still opening or closing answers COM without FullName.
- **[observed, four crashes] Never open, read and close data-shortcut drawings in one step, and don't close them
  over COM at all.** Leave them open, keep to a few per session, and restart Civil 3D between batches
  (c3d_quit_civil3d). After a crash, answer No to `_recover.dwg` prompts for drawings opened read-only.

## 12. Road production on a staging copy: lessons from a road pilot (v0.9, 27 Oct 2026)

On the pilot, every production step was first done with scripts round the server because the server only read.
v0.9 moves each step into a tool that keeps the guards.

### Why the server was worked round [observed]
- **The running server was older than the code on disk** (it only reloads when restarted). c3d_status now reports
  `restart_needed`. Tell the person and wait; outside scripts skip the guards.
- Turning off original-protection was refused by the permission check, rightly: staging counts as a working area.
- Long steps passed the 52 s limit; production tools stop at about 35 s of work and return what is `left`.

### Data references in a copied project [observed]
- In a copy of the whole project tree, data references resolve **relative to the host drawing**, so a staging copy
  references staging sources. No repathing was needed.
- Opening for write synchronises references; `_AeccSynchronizeReferences` does the rest.
- **'Object Name Change'** appears when a source object was renamed. It is protected: the person decides.
- Stale flags can stay on edge alignments and EG profiles with geometry identical to the source; compare first.

### Corridors [observed]
- The COM property is `OutOfDate` (not IsOutOfDate). `Rebuild()` can take tens of seconds.
- Assembly groups and subassembly parameters can't be read over COM; check thicknesses from material sections.

### Plotting [observed]
- `(command "_.-PLOT" "_N" <layout> "" "" <file> "_N" "_Y")` plots a layout with its own page set-up, with
  BACKGROUNDPLOT 0 (restored afterwards).
- **REGENALL first**: the first PnP plots had no profile lines. On a PnP with data references and xrefs it took 90 s,
  so road_plot_sheets gives it a call of its own.
- **The DWG To PDF driver opens each PDF in the viewer, which locks it**; a re-plot to the same name stalls at the
  file-name prompt. road_plot_sheets never overwrites a locked or existing file ('... v2.pdf').
- **Plot styles must be installed** in `%APPDATA%\Autodesk\C3D 2027\enu\plotters\Plot Styles`; a missing ctb plots
  in object colours without a warning.
- The plot window is titled 'Plot Job Progress' (no readable controls) or 'Plot Progress' (with Cancel). Both are
  waited out as progress windows; the watchdog never presses their Cancel.
- A window that closes while being read ('Invalid window handle', 1400) is skipped.

### Quantities [observed]
- The Total Volume Table is a custom object: its numbers can only be read by exploding it twice, and its dialog
  leaves Civil 3D at 'Select upper left corner'. Don't use it for numbers.
- road_quantities reads material sections (DataType 5, closed loops of links), classes Earthworks loops as cut or
  fill against the existing-ground section, and sums by average end area. On the pilot, pavement layers matched
  exactly and cut agreed within 0.3%; fill differed by about 10%, so check fill. Run Compute Materials first.
- A CS drawing may sample the corridor itself as a surface section: don't take it for existing ground.
- Reading links over COM is slow (over a minute for ~34 sections): the tool resumes across calls.

### Stake-out and setting-out [observed]
- `PointLocation(s, o)` / `StationOffset(x, y)` return tuples with typed dispatch; older bindings need VT_BYREF.
- The last station must be the exact `EndingStation`: a rounded one raises 'Invalid input'.
- Alignment.Entities is not in station order; road_setout_data sorts it and gives angles in degrees.
- Confirm the coordinate datum against the client's benchmark list before issuing.

### Title blocks
- road_set_titleblock sets tags only where given and reports old values. Take values from the client's initials
  rule; check every sheet, not just the first.

### Computer use and concurrency [observed]
- **open_application on Civil 3D started a second Civil 3D.** Use c3d_focus to bring the running one forward.
- **Only one client should drive Civil 3D at a time.** Another session switched the active drawing during a test
  plot; road_plot_sheets now pins its drawing and stops if the active drawing changes.
- c3d_quit_civil3d quits cleanly when nothing open has unsaved changes; use it to restart between batches.

## 13. Sheet clean-up on a staging copy: lessons from a four-road production run (10 Nov 2026, server v0.9.0)

What worked, in the order to do it per road. Project and client specifics are in knowledge.local.md.

**Plotting**
- **Profile lines can vanish from a PnP layout viewport** (seen on the last sheet of two roads). The profile shows in
  model space but not in the layout, on screen or on the PDF. road_plot_sheets' own REGENALL does not cure it, and
  neither does setting CTAB and regenerating. What does: `(setvar "CTAB" "<layout>") _.MSPACE _.REGENALL _.PSPACE`
  through c3d_command, then road_plot_sheets(layouts=[that one], regen=False). Do it for every PnP sheet.
  TO BUILD IN: road_plot_sheets should do this itself for each layout that has viewports.
- **road_plot_sheets plots one layout per call** when the "Plot Job Progress" window appears: the run comes back
  "dialog_pending" (catalogue unknown.cf7b5bf0fcde / unknown.97d170e1550e) and the remaining layouts are dropped.
  Call once per layout and follow each with c3d_run_status. Do not switch CTAB while a plot is still pending.
  TO BUILD IN: treat that window as progress and carry on through the list.
- A PDF viewer set to open new PDFs locks each file as it is written, so a replot becomes "... v2.pdf". Plot each
  round into a fresh folder and merge from there.
- Check a PnP PDF for a missing profile line without looking at it: count thick stroked segments (width >= 1.2 pt)
  inside the profile grid; zero means the line is missing. Compare with the designer's own PDF of that sheet.
- Sheet PDFs are rotated pages: text coordinates from PyMuPDF need `point * page.rotation_matrix` before use.

**Styles that COM cannot reach (use the style dialogs with computer use)**
- c3d_lisp can reach `AeccXUiLand.AeccApplication.13.9` (Civil 3D 2027), and ProfileLabelStyles.CurveLabelStyles
  lists parent styles and their text components, but child styles (where a "Crest Only" / "Sag Only" pair keeps its
  high/low point text), point table styles, band styles and section label groups are not reachable. Item(i) on the
  component collections fails. Do not spend calls probing: go to Toolspace > Settings.
- `_SHOWTS` opens Toolspace; `_MAPWSPACE _OFF` hides the Task Pane so Toolspace has room. A first click on a tree
  after a drawing switch only gives focus: zoom on the tree and confirm it expanded before the next click.
- A dialog opened from another dialog (Label Style Composer from a band style, Pick Band Style, Section Labels) does
  not show in computer-use screenshots until c3d_focus is called; c3d_screen always shows it.
- Text Component Editor: click the text pane, Ctrl+A, type the whole contents (property fields such as
  `<[Low Point Elevation(Um|P2|RN|AP|Sn|OF)]>` are accepted as typed). Retyping drops the font override: on the
  Format tab select all and pick the font again.
- Feet in a metric drawing: look for `Uft` and station base `B2` in the property fields; metric is `Um` and `B3`.
- A point table column is edited by double-clicking its Column Value cell; elevation is `<[Point Elevation(...)]>`.
- A band label that the tangent line strikes through: Border > Background Mask = True on that label component.
- A band that prints "??" with its labels piled at the view start: that profile view uses a different band style
  from the others. Profile View Properties > Bands: click the row, then the small icon in the Style cell, pick the
  style the other views use.
- **Section grade-break labels are driven by "Weeding"** on each section's label group. At 1.0 m the centreline
  label came and went with the geometry; at 0.3 m every section kept centreline, lane edges, kerbs and walkway
  backs. Bulk edit: select all AECC_SECTION_GRADEBREAK_LABEL_GROUP objects (ssget + sssetfirst), `_.PROPERTIES`,
  edit "Weeding" once. Sample Line Group Properties has no label-set column.
- **Band labels are dropped on split (stacked) profile views.** No style setting brings them back. Add them as
  paper-space MTEXT at the band row (vla-addmtext on PaperSpace after setting CTAB), positions from the chainage
  labels on the plotted PDF (mm = pt x 25.4/72, origin bottom-left on a full-bleed sheet), values from the curve
  labels and civil_profile_elevations. They are plain text and must be redone if the profile changes.

**c3d_lisp**
- An expression too long for the command line is written to a .lsp and loaded, which raises "Security - Unsigned
  Executable File" every time (catalogue unknown.c4108e194a12). "Load Once" is the answer; the result then has to be
  read from the matching lisp_*.txt in the server's temp folder because the run returns no value.
  TO BUILD IN: add the temp folder to TRUSTEDPATHS for the session, or return the result after the dialog.
- vlax-curve-getpointatdist / getclosestpointto work on AECC alignments, and surface FindElevationAtXY works through
  vlax-invoke: enough to check an offset alignment or a corridor surface against a profile without the broken
  civil_point_at_station (TypeError on VARIANT).

**Tools that failed**
- civil_point_at_station: `TypeError: float() argument must be ... not 'VARIANT'`.
- road_drawing_report(parts=['corridors']) on a sheet drawing: `AttributeError: <unknown>.Corridors`.
- road_sync_references: the first call after opening can fail with `Item.ProfileViews` or an RPC error; call again.
- c3d_quit_civil3d refuses while a plotted drawing is "unsaved" (plotting dirties it): activate each one with
  c3d_open_drawing and c3d_save, then quit. After the quit, wait for acad.exe to go before c3d_launch.

**Checking**
- Compare the PnP band to the profile at every labelled station, and each cross section's centreline label to the
  profile by chainage (not by order). Where a section has no centreline label, the lane-edge label is centreline
  minus lane width x crossfall, and the corridor top surface can be checked directly with FindElevationAtXY.
- A count such as "N of M sections confirmed" must be made on de-duplicated files: a folder holding both a sheet
  and its "v2" doubles the count.

## 14. Seven-road production run: what works, what stalls, how to keep it cheap (11 Nov 2026, server v0.9.0)

Read with section 13. Written after revising three vertical alignments and producing sheets, volumes and books for seven roads.

### 14.1 Do it through the server, not the screen
Computer use (screenshots and clicks) was the largest cost of the run. These steps need NO screen work:
| Step | How |
|---|---|
| Open, sync, rebuild corridor, save | `c3d_open_drawing(read_only=false)`, `road_sync_references`, `road_rebuild_corridors`, `c3d_save` |
| Rebuild corridor surfaces (rebuild_corridors does not) | short `c3d_lisp`: loop model space, `AeccDbSurfaceTin` named `*Corridor*`, `(vlax-invoke o 'Rebuild)` |
| Title block values | `road_set_titleblock` |
| "CONTRAOT" and other title text | short `c3d_lisp` over blocks: MText `vl-string-subst` |
| Profile revision (PVIs) | COM, see 14.3 |
| Setting-out data | `road_setout_data(alignment, out_json, profiles=[design profile])` |
| Plot a PnP layout | `c3d_command('(setvar "CTAB" "<layout>") _.MSPACE _.REGENALL _.PSPACE ')`, then `road_plot_sheets(folder, layouts=[one], regen=false)`, then `c3d_run_status` |
| Plot section pages without layouts | model-space `-PLOT`, see 14.5 |
| Volumes | `road_quantities` after Compute Materials (14.4) |
| Check a sheet | parse the PDF text (levels, labels); look at the page image once, at low dpi |
**Added in server v0.9.1 (11 Nov 2026), each tested on a staging drawing:**
| Tool | Replaces | Tested |
|---|---|---|
| `road_profile_set_pvis(alignment, pvis, profile)` | long LISP + security dialogs for PVI edits | round trip of an 8-PVI profile: identical; overlapping curve refused before any change |
| `road_profile_levels(alignment, interval, profiles, out_txt)` | long LISP `ElevationAt` dump | yes |
| `road_rebuild_corridor_surfaces()` | LISP loop | yes |
| `road_replace_text(old, new, dry_run)` | LISP over blocks ("CONTRAOT") | dry run only |
| `road_section_label_weeding(0.3)` | Properties palette clicks | 45 label groups set (COM `WeedingValue` on `AeccDbSectionGradeBreakLabeling`) |
| `road_label_styles(collection, style)` / `road_set_label_style_text(...)` | Toolspace > Label Style Composer > Text Component Editor | read; write of the same text back (no change). A real wording change has not been plotted yet: plot one sheet and read it before trusting it |
| `road_plot_model_window(x1, y1, x2, y2, pdf, scale)` | hand-typed `-PLOT` | 9 section views at 1:200 |
Label style collections: `ProfileLabelStyles.CurveLabelStyles` (children under a parent are addressed 'Crest and Sag/Sag Only'), also
`GradeBreakLabelStyles`, `LineLabelStyles`, `MajorStationLabelStyles`; other roots `SectionLabelStyles`, `AlignmentLabelStyles`, `PointLabelStyles`.
Lines in a component are separated by `\P`. Feet show as `Uft` in a field's format string, metres as `Um`.

Screen work is STILL needed for: band styles (`ProfileViewBandStyles`, not explored), table styles (benchmark table ELEV column),
and the Compute Materials surface mapping. Next tools to build: `road_compute_materials(alignment, eg_surface, datum_surface)`, band and
table style text, and a quiet mode for `road_plot_sheets` / `c3d_dialog_act` (the Plot Job Progress and Security dialog listings are long).
The running server loads new code only after a restart: `c3d_status` shows `restart_needed` until then.

### 14.2 Keep LISP short
- An expression over roughly 250 characters is written to a temp .lsp and raises "Security - Unsigned Executable File". Each one costs a
  `c3d_dialog_act(... "Load Once")` round trip plus a file read for the result.
- Avoid it: define two or three SHORT helper functions with separate `c3d_lisp` calls, then call them. Functions are per drawing: redefine after
  switching drawings.
- AutoLISP is case-insensitive and dynamically scoped: a function named `PV` is destroyed by `(setq pv ...)`; a parameter named `a` is overwritten
  by a helper that does `(setq a ...)`. Use distinct names (`GPV`, `zz`).
- `c3d_command` accepts a long `(progn ...)` directly without the security dialog; use it for loops such as plotting several windows.
- Never print long results into the conversation (a Quantity basis sheet, a 150-control dialog). Write to a file and read a few lines.

### 14.3 Editing a design profile over COM
1. `road_setout_data` BEFORE (json), dump "station EG design" every 2.5 m with `ElevationAt` to a text file.
2. Fit the new PVIs outside Civil 3D (`profile_redesign.py` in the project's handover tools: one PVI per crest/sag, K and grade limits, least-squares
   to the current levels so cut and fill stay small). Try two or three groupings and take the one with the smallest level change.
3. In Civil 3D: remove the interior PVIs, then add the new ones. `(vlax-get profile 'PVIs)` must be fetched again after EVERY Remove or Add.
   `(vlax-invoke pvis 'Add sta elev 3 length)` adds a symmetric parabola; `'Add sta elev 1` a tangent PVI (use for junction tie-in grade breaks).
   Editing Station / Elevation / CurveLength in place worked on one profile and was refused ("parameter is incorrect") on another: rebuild is the
   reliable way.
4. A curve that touches the next curve or the profile start is added as a TANGENT PVI with no error. Shorten by 0.1 to 1 m and confirm with
   `road_setout_data` AFTER (every curve PVI must show a CurveLength).
5. Save the alignment drawing; corridor drawing: sync, rebuild, rebuild surfaces, check TOP surface against the profile along the centreline.
6. Junction return profiles are NOT adjusted by this. Record it for the designer.

### 14.4 Volumes
`_AeccComputeMaterials` (once per CS drawing): OK the sample line group; criteria "Cut and Fill"; EG "<Click here to set all>" = topo surface;
DATUM = corridor datum surface. The dropdown order differs per drawing (alphabetical): read it before clicking. Then `road_quantities` repeatedly
until it stops returning `partial` (20 to 25 sections per call). Volumes cover first to last sample line only.

### 14.5 Things that froze Civil 3D (20+ minutes, had to be ended)
- `-LAYOUT` Template into a drawing holding many section views (twice, with a 13 MB and a 5 MB template).
- Create Section Sheets wizard against a template that was open in the same session ("unable to clone layout").
- COM calls while only the Start tab is open: always `c3d_launch(folder, name)` so a document exists.
Recovery: end acad.exe, end the `python -m civil3d_mcp.server` processes (the client respawns the server clean), `c3d_launch(folder, name)`.
Save every drawing as soon as a step succeeds, so ending Civil 3D never loses work.
Section pages without layouts: `(command "_.-PLOT" "_Y" "Model" "DWG To PDF.pc3" "ISO full bleed A1 (841.00 x 594.00 MM)" "_M" "_L" "_N" "_W" ll ur
"1=0.2" "_C" "_Y" "<ctb>" "_Y" "_A" pdf "_N" "_Y")` per page (1=0.2 is 1:200 in a metre drawing), then set the plot into the sheet frame as a PDF.

### 14.6 Other traps met
- A source folder can hold an unused duplicate drawing (old profile, 0.4 m off). Check which drawing the data shortcuts publish from before
  reading levels; always compare a new extract with levels already verified on the sheets.
- `road_sync_references` can report a corridor reference as stale/invalid while the sections still update. Verify from the plotted levels.
- `road_plot_sheets` with several CS layouts sometimes plots only the first: one layout per call, then list the folder.
- `road_setout_data` / first call on a just-opened drawing can fail with "no alignment named": retry once before investigating.
- Run the long, screen-free work (BoQ workbooks, report drafts, PDF assembly) in background agents while one session drives Civil 3D.

### 14.7 Added 11 Nov (typical sections)
- Plot all layouts of a drawing in ONE step with the COM Plot object: `doc.ActiveLayout = lay; doc.Plot.PlotToFile(pdf, lay.ConfigName)` with
  BACKGROUNDPLOT 0 (retry ActiveLayout while Civil 3D reports busy). `road_plot_sheets` plotted only the first layout of a list on several drawings.
- Corridor layout without the screen: `BaselineRegions` -> `AppliedAssemblies` (one per frequency station) -> `GetShapes()` (codes via
  `CorridorCodes`, offsets via `CalculatedLinks` -> `CalculatedPoints` -> `GetStationOffsetElevationToBaseline()`). Region's assembly: `AssemblyDbEntity.Name`.
  Subassembly parameters were NOT reachable on assemblies over COM.
- AutoCAD tables: `GetText(r,c)` / `SetText(r,c,txt)` keep cell formats if the `{\f...;` prefix is kept.
- `Mirror` honours MIRRTEXT=0 (texts stay readable).

### 14.8 Added 11 Nov (walls, side slopes, schedules)
- Side-slope heights fast: corridor baseline `MainBaselineFeatureLines.FeatureLinesCol` -> Hinge*/Daylight* feature lines -> `FeatureLinePoints`
  (Station, XYZ). Walking applied-assembly points per station took > 55 min on one road; feature lines take seconds.
- Topo walls/fences: a selection set with a layer filter (WALL/FENC/BUILD/RETAIN) via pythoncom VARIANT arrays is fast; compare to the setting-out
  road edge with `alignment.StationOffset`.
- A new schedule sheet in a heavy typical drawing: `(command "_.-LAYOUT" "_C" "<pavement sheet>" "<new>")`, `CTAB`, `_.MSPACE _.ZOOM _C x,y 23 _.PSPACE`.
  Never `-LAYOUT Template` there (froze Civil 3D).
- Inline MText fonts: build `\f` codes with chr(92) in Python source ('\f' became a form feed). A note's font can fall back to the style's SHX and
  plot grey/spaced: set the inline font to match the neighbouring table and colour 7.
- A plotted PDF just read by c3d_pdf_page may stay locked: plot to a fresh folder instead of deleting it.

### 14.9 Added 11 Nov (capitals / one font across a road project)
- Text styles: `TextStyle.SetFont("Arial Narrow", bold, italic, 0, 34)` over COM changes every label that uses the style. Inline `\f` codes in
  MText, table cells, label-style text components and view titles need rewriting too (regex on `\[fF][^|;]*`).
- Label, band-label, profile/section view title and axis texts ARE reachable over COM (`TextComponents[i].Contents.Value`, `GraphStyle.TitleStyle.Text`,
  band style `MajorIncrementLabelStyle` etc.). Field flags `(CP)` -> `(CU)` make names print in capitals.
- NOT reachable over COM: band TITLE (it is a "Band Title" label style opened from Band Details > Compose label; COM's `TitleStyle.Text` is a dead
  default), code set styles, link/marker/shape label styles, expressions. Use Toolspace > Settings (see section on style dialogs).
- "Vertical" / "Horizontal" printed by a slope label on a vertical link is system text: hide it with a Text Height expression
  `IF({Link Length}<0.005,0.0000001,<height in m>)`.
- Walk only label/band/view/table style collections: object styles carry no text and make the pass 10x slower. With > 10 drawings open a style
  pass goes from 1 min to 20 min: quit and relaunch every few drawings.
- PDF text extraction also returns text outside viewports and zero-height text: test visibility (bbox inside the page, size >= 1 pt, render the
  spot) before chasing it.
- Every plot opens its PDF in the default viewer (Foxit here) and the viewer locks the file: write rebuilt PDFs to a new name when the target is
  locked, and plot to a fresh folder each time.
- Toolspace dialogs open at fixed positions: once one style has been edited by hand, the whole click sequence can be replayed in a single
  computer_batch per drawing (check with a zoom after each dialog).

## 15. Lessons from a swept-path run on a TIA site, 13 Nov 2026

### Vehicle Tracking behaviour [observed]
- **Follow runs on past the end of the guide** until the trailing axle reaches the end point: about one wheelbase
  plus ~0.5 m. Forwards, the car finished with its rear axle on the guide end and its front 3.9 m further on;
  in reverse, the rear ran ~4 m past (into a wall). Stop guide lines that much short of the intended stop.
  A reverse swing that a driver would stop mid-turn cannot be modelled with Follow: report it from sp_check_guide
  and label it a kinematic check.
- **Library Explorer:** the Search box is reliable (it matches vehicle names, not group names); tree_keys type-ahead
  is not on the full tree (it landed on the wrong group three times). After a search, the highlight often lands on
  the vehicle's sub-unit row ('... Tractor'): click the vehicle row itself. The drawing canvas tooltip 'Adjust
  vertex curve' can sit over Proceed: move the mouse away before clicking. One run silently used the previous
  vehicle - **confirm every run's vehicle from its inserted profile title.**
- INSERTPROFILE with a point in the same (command ...) is rejected and waits at 'Select Location'. Working pattern:
  c3d_command('(command "INSERTPROFILE" (handent "<path>")) ', wait=False), then c3d_run_input('x,y').
  Each profile's text block is ~15 m wide: space profiles 20 m apart or on separate rows.
- VT paths may not draw after layer changes until REGENALL. The ATR layer numbers are reused after deletions.
- Obstacles traced from a PDF picked up hatch strokes of a covered channel: filter short hatch strokes off the
  obstacle layers, and leave overhead outlines (gate canopy) out.

### Server bugs to fix [observed]
- guide_handle (sp_check_guide, vt_follow_run) with an all-digit handle such as 1641 arrives as an integer and is
  rejected by validation. Type the parameter as a string.
- layers_restored after vt_follow_run turns ATR layers OFF (including the one holding the new path) and guide layers
  too; it should restore the previous state.
- File-loaded LISP (long c3d_lisp, vt_circulation_arrows, c3d_draw_polylines) raises SECURELOAD 'Unsigned Executable
  File' in a fresh session; answered with Load Once each time (catalogue: acad.secureload_unsigned). Avoid the file
  route for short code, or ask the person once about adding the temp folder to TRUSTEDPATHS.
- -LAYER _OFF on a pattern that includes the current layer stalls at 'really turn off the current layer?': set
  CLAYER 0 first inside every layer helper.
- The watchdog saves every pop-up of one sweep to the same picture file name, so later pictures overwrite earlier ones.

### 14.10 Consolidated lessons (14 Nov 2026)
The job-independent summary of sections 11 to 14 is in `lessons_road_drawing_production.md` beside this file: order of work, the sheet
checks to automate, how to review a set, what COM can and cannot reach, generating outputs from the model, office documents, and the
settings to ask for at the start of a job. Read it first on any road drawing review or production task.
