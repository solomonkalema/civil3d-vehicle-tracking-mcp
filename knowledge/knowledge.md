# Civil 3D MCP server: knowledge base

The server loads this file, and Claude reads it, at the start of every task. It holds three kinds of content:
- what has been observed on Solomon's machine (marked **[observed]**);
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


## 7. Lessons from the Hoima job, 18 Oct 2026 (server v0.3)

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
- **Turn-round.** Follow cannot do multi-point turns; sp_turn_search answers "can it leave in forward gear?" as a labelled kinematic check. Hoima: no three-point turn in the 8.8 m yard with bays occupied; with every bay empty, only 0.09 m clearance.
