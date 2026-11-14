# Tool map: which tool for which job

Use the server for everything it can do. The screen (computer use) is the last resort and each use is a gap to record with
`c3d_note_lesson`. Read-only tools work on any drawing; changing tools work on working copies only.

## 1. Start of any job
| Job | Tool |
|---|---|
| Is Civil 3D connected, which drawing, is newer server code waiting for a restart | `c3d_status` |
| Questions to ask the person once, before starting | `task_preflight('<workflow>')`: vt_follow, road_production, drawing_review, working_drawings |
| What earlier sessions learnt or tripped on | `c3d_lessons_inbox`, `c3d_guide('lessons')` |
| The review checklist, by section | `c3d_guide('review_checklist', section)` |
| Record a gap, bug, lesson, rule or idea the moment it happens | `c3d_note_lesson` |

## 2. Reviewing a drawing set (PDF; no Civil 3D needed)
| Job | Tool |
|---|---|
| Text of every sheet, searchable | `sheet_pdf_text` |
| Another site's name, placeholders, cross-reference targets | `sheet_find_text` |
| Whole sheet | `c3d_pdf_page` (60 to 70 dpi) |
| Small text, a table drawn as outlines | `sheet_pdf_tile` (150 to 250 dpi) |
| Title blocks of the whole set | `sheet_titleblocks` |
| Profile line present on plan-and-profile pages | `sheet_profile_lines` |
| Fonts and capitals against the house style | `sheet_fonts` |
| Assemble a book | `sheet_merge_pdfs` |
Not yet a tool (do with a short script and record it): label sums against quantity tables, profile closure, bar schedule
arithmetic, one end chainage across documents.

## 3. Reading a CAD project
| Job | Tool |
|---|---|
| Data-shortcut project on disk | `road_shortcut_project` |
| How a drawing is built (references, profiles, sample lines, corridors, xrefs, layouts) | `road_report_file`, `road_drawing_report` (use `parts` to keep it short) |
| Sheets: paper, plot style, viewport scale, title-block attributes | `road_sheets` |
| Title blocks compared across sheets, with flags | `road_titleblock_audit` |
| Layers and what is on them (one master plan with a layer per trade) | `c3d_layers(with_counts)` |
| Alignments, profiles, surfaces, corridors | `civil_inventory`, `civil_alignments`, `civil_profile_elevations`, `civil_surface_elevation` |
| Curve and PVI data | `road_setout_data` |
| Label style wording and units | `road_label_styles`, `road_style_text('scan' or 'list')` |
| Fill and cut heights at the road edge | `road_side_slopes` |
| Survey walls, fences, buildings | `road_survey_lines` |

## 4. Producing sheets (working copies only)
| Job | Tool |
|---|---|
| Working copy | `c3d_make_working_copy` |
| Sync references, rebuild corridor and its surfaces | `road_sync_references`, `road_rebuild_corridors`, `road_rebuild_corridor_surfaces` |
| Profile revision | `road_profile_set_pvis`, `road_profile_levels` |
| Title-block attributes | `road_set_titleblock` (attribute blocks only) |
| A wrong word everywhere; a chainage in a layout name | `road_replace_text`, `road_rename_layouts` |
| Capitals, one font, style text | `road_text_capitals`, `road_text_font`, `road_style_text('capitals')` |
| Label wording, section label weeding | `road_set_label_style_text`, `road_section_label_weeding` |
| Plot | `road_plot_all_layouts` (all layouts, one call), `road_plot_sheets`, `road_plot_model_window` |
| Quantities, stake-out | `road_quantities`, `road_stakeout`, `road_setout_data` |
| Save; restart between batches | `c3d_save`, `c3d_quit_civil3d`, `c3d_launch` |

## 5. Still needs the screen
Band titles, code set styles, link / marker / shape label styles, expressions, table styles, Compute Materials surface
mapping, plain-text title blocks (no attributes), Vehicle Tracking library and dialogs not yet catalogued.
`c3d_focus` brings Civil 3D forward; never start Civil 3D from computer use.

## 6. Junctions, visibility, swept paths
`jn_kerb_return`, `jn_taper`, `jn_turn_test`, `rd_stopping_sight_distance`, `rd_visibility_splay`; `sp_*` for the Python swept
path engine; `vt_*` for Vehicle Tracking runs.
