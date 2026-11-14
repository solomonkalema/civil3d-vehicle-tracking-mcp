"""Make a synthetic 8-sheet working-drawing set with planted faults, for testing a drawing-review skill or model.

usage: python make_review_set.py <out_folder>
Writes review_set_01.pdf. The faults and the decoys (things that are correct and must not be reported) are listed in
cases/review_synthetic_01.json. Nothing here comes from a real project: places are 'Junction A' and 'Junction B'.
"""
import os
import sys

import pymupdf

W, H = 1191, 842  # A3 landscape, points


def frame(doc, number, title, scale, site="JUNCTION A", rev="00"):
    p = doc.new_page(width=W, height=H)
    sh = p.new_shape()
    sh.draw_rect(pymupdf.Rect(30, 30, W - 30, H - 30))
    sh.draw_rect(pymupdf.Rect(30, H - 110, W - 30, H - 30))
    for x in (420, 760, 900, 1040):
        sh.draw_line((x, H - 110), (x, H - 30))
    sh.finish(width=1)
    sh.commit()
    t = lambda x, y, s, fs=9: p.insert_text((x, y), s, fontsize=fs)  # noqa: E731
    t(40, H - 85, "EXAMPLE ROADS AUTHORITY", 11)
    t(40, H - 65, "JUNCTION IMPROVEMENT WORKS - WORKING DRAWINGS")
    t(430, H - 90, "TITLE:")
    t(430, H - 72, site, 11)
    t(430, H - 54, title, 11)
    t(770, H - 90, "SCALE:")
    t(770, H - 60, scale, 11)
    t(910, H - 90, "PREPARED: A.B.")
    t(910, H - 72, "CHECKED: C.D.")
    t(910, H - 54, "DATE: 2026-06   REV: " + rev)
    t(1050, H - 90, "DRAWING No.")
    t(1050, H - 58, number, 16)
    return p, t


def table(p, x, y, rows, widths, fs=9, rh=16):
    sh = p.new_shape()
    yy = y
    for r in rows:
        xx = x
        for c, w in zip(r, widths):
            sh.draw_rect(pymupdf.Rect(xx, yy, xx + w, yy + rh))
            p.insert_text((xx + 3, yy + rh - 4), str(c), fontsize=fs)
            xx += w
        yy += rh
    sh.finish(width=0.5)
    sh.commit()


def control(p, t, x, y, site):
    t(x, y, f"CONTROL POINTS ({site})", 10)
    table(p, x, y + 6, [["POINT", "E", "N", "Z"], ["A-1", "457363.284", "37128.012", "100.412"],
                        ["A-2", "457419.781", "37145.458", "100.951"], ["A-3", "457361.489", "37188.004", "101.326"]], [50, 80, 80, 60])


def plan(p, labels):
    sh = p.new_shape()
    sh.draw_line((120, 380), (1050, 380))
    sh.draw_line((120, 430), (1050, 430))
    sh.draw_line((560, 150), (560, 380))
    sh.draw_line((610, 150), (610, 380))
    sh.finish(width=1.2)
    sh.commit()
    for i in range(0, 6):
        p.insert_text((150 + i * 170, 372), f"0+{i * 20:03d}", fontsize=7)
    for x, y, s in labels:
        p.insert_text((x, y), s, fontsize=9)
    p.insert_text((1080, 120), "N", fontsize=14)
    sh = p.new_shape()
    sh.draw_line((1085, 130), (1085, 180))
    sh.finish(width=1)
    sh.commit()


def build(folder):
    os.makedirs(folder, exist_ok=True)
    doc = pymupdf.open()

    # D-01 drawing list. Planted: D-09 listed, no such sheet; D-04 scale differs from the sheet.
    p, t = frame(doc, "D-01", "DRAWING LIST", "NTS")
    t(60, 70, "DRAWING LIST", 14)
    table(p, 60, 90, [["No.", "TITLE", "SCALE"], ["D-01", "DRAWING LIST", "NTS"], ["D-02", "REMOVAL WORKS", "1:500 (A3)"],
                      ["D-03", "DRAINAGE WORKS", "1:500 (A3)"], ["D-04", "CULVERT C1 REINFORCEMENT", "1:50 (A3)"],
                      ["D-05", "PLAN AND PROFILE - MAIN ROAD", "H 1:1000 V 1:100 (A3)"], ["D-06", "CROSS-SECTIONS 0+000 TO 0+100", "1:125 (A3)"],
                      ["D-07", "PAVEMENT WORKS", "1:500 (A3)"], ["D-08", "ROAD MARKING", "1:500 (A3)"],
                      ["D-09", "KERB SETTING-OUT POINTS", "1:250 (A3)"]], [50, 300, 160])

    # D-02 removal. Planted: table 420.0 against labels 150+120+130 = 400.0; control table headed JUNCTION B.
    # Decoy: pavement cutting 12.5 + 30.0 = 42.5 agrees.
    p, t = frame(doc, "D-02", "REMOVAL WORKS", "1:500 (A3)")
    plan(p, [(200, 405, "Removal: Pavement A=150.0m2"), (480, 405, "Removal: Pavement A=120.0m2"), (780, 405, "Removal: Pavement A=130.0m2"),
             (300, 450, "Pavement cutting L=12.5m"), (700, 450, "Pavement cutting L=30.0m")])
    t(60, 70, "QUANTITIES - REMOVAL WORKS", 10)
    table(p, 60, 76, [["ITEM", "UNIT", "QTY"], ["Pavement removal", "m2", "420.0"], ["Pavement cutting", "m", "42.5"],
                      ["Kerb removal", "m", "0.0"]], [160, 50, 60])
    control(p, t, 760, 520, "JUNCTION B")

    # D-03 drainage. Planted: CP3 outlet above inlet; P2 stated 1.0 % but levels give 0.5 %; reference to D-12.
    # Decoy: P1 0.50 % agrees (100.450 to 100.300 over 30.0 m).
    p, t = frame(doc, "D-03", "DRAINAGE WORKS", "1:500 (A3)")
    plan(p, [(220, 350, "CP1"), (400, 350, "CP2"), (640, 350, "CP3"), (300, 450, "P1 PC600 L=30.0m"), (520, 450, "P2 PC600 L=40.0m"),
             (760, 450, "FOR CULVERT DETAILS SEE DWG D-12")])
    t(60, 70, "CATCH PIT SCHEDULE", 10)
    table(p, 60, 76, [["PIT", "E", "N", "COVER", "INVERT IN", "INVERT OUT"], ["CP1", "457370.100", "37130.200", "101.650", "-", "100.450"],
                      ["CP2", "457398.900", "37138.600", "101.500", "100.300", "100.250"],
                      ["CP3", "457437.300", "37149.900", "101.300", "100.050", "100.120"]], [40, 80, 80, 60, 70, 75])
    t(520, 70, "PIPE SCHEDULE", 10)
    table(p, 520, 76, [["PIPE", "FROM", "TO", "LENGTH (m)", "GRADIENT"], ["P1", "CP1", "CP2", "30.0", "0.50 %"], ["P2", "CP2", "CP3", "40.0", "1.00 %"]],
          [40, 50, 50, 70, 70])
    control(p, t, 760, 520, "JUNCTION A")

    # D-04 rebar. Planted: schedule total 180.00 against rows 107.44 + 71.04 + 26.28 = 204.76; '19@200=4000';
    # R3 2960 on the view, 2296 in the schedule (legs 800 + 1360 + 800 = 2960).
    p, t = frame(doc, "D-04", "CULVERT C1 REINFORCEMENT", "1:50 (A3)")
    sh = p.new_shape()
    sh.draw_rect(pymupdf.Rect(100, 150, 520, 330))
    sh.finish(width=1.2)
    sh.commit()
    t(120, 140, "PLAN - TOP SLAB        R1 D16 19@200=4000")
    t(120, 350, "R3 D12 L=2960  (800 + 1360 + 800)")
    t(120, 370, "R2 D12 @100, LAP 700, COVER 50, CONCRETE CLASS 25/20")
    t(620, 130, "BAR BENDING SCHEDULE - CULVERT C1", 10)
    table(p, 620, 136, [["MARK", "DIA", "No.", "LENGTH (mm)", "kg/m", "MASS (kg)"], ["R1", "D16", "20", "3400", "1.580", "107.44"],
                        ["R2", "D12", "40", "2000", "0.888", "71.04"], ["R3", "D12", "10", "2296", "0.888", "26.28"],
                        ["", "", "", "", "TOTAL", "180.00"]], [45, 40, 40, 80, 50, 70])

    # D-05 profile. Planted: second tangent does not close (102.000 - 1.5 % x 80 = 100.800, stated 101.100);
    # proposed level row repeats 101.700 three times. Decoy: first tangent closes.
    p, t = frame(doc, "D-05", "PLAN AND PROFILE - MAIN ROAD", "H 1:1000 V 1:100 (A3)")
    sh = p.new_shape()
    sh.draw_polyline([(120, 300), (560, 180), (912, 245)])
    sh.finish(width=2.0, closePath=False)
    sh.commit()
    t(60, 70, "VERTICAL ALIGNMENT", 10)
    table(p, 60, 76, [["POINT", "STATION", "LEVEL", "GRADE OUT", "LENGTH (m)"], ["START", "0+000", "100.000", "+2.000 %", "100.0"],
                      ["PVI 1", "0+100", "102.000", "-1.500 %", "80.0"], ["END", "0+180", "101.100", "", ""]], [50, 60, 60, 70, 70])
    t(60, 480, "PROFILE BAND", 10)
    table(p, 60, 486, [["STATION", "0+000", "0+020", "0+040", "0+060", "0+080", "0+100", "0+120", "0+140", "0+160", "0+180"],
                       ["EXISTING LEVEL", "99.950", "100.310", "100.720", "101.080", "101.490", "101.900", "101.640", "101.330", "101.050", "100.760"],
                       ["PROPOSED LEVEL", "100.000", "100.400", "100.800", "101.200", "101.600", "102.000", "101.700", "101.700", "101.700", "101.100"]],
          [90] + [60] * 10)

    # D-06 sections. Planted: 0+040 FH 100.650 (profile 100.800); widths 1.5 + 3.5 + 3.5 + 1.5 = 10.0 stated 10.5.
    # Decoy: 0+020 FH 100.400 agrees.
    p, t = frame(doc, "D-06", "CROSS-SECTIONS 0+000 TO 0+100", "1:125 (A3)")
    for i, (sta, gh, fh, tot) in enumerate([("0+000", "99.950", "100.000", "10.0"), ("0+020", "100.310", "100.400", "10.0"),
                                            ("0+040", "100.720", "100.650", "10.5"), ("0+060", "101.080", "101.200", "10.0")]):
        y = 120 + i * 140
        sh = p.new_shape()
        sh.draw_polyline([(200, y + 40), (320, y + 30), (560, y + 24), (800, y + 30), (920, y + 40)])
        sh.finish(width=1.2, closePath=False)
        sh.commit()
        t(60, y, f"STA {sta}   GH={gh}   FH={fh}", 10)
        t(200, y + 60, f"FOOTWAY 1.5 | LANE 3.5 | LANE 3.5 | FOOTWAY 1.5 | TOTAL {tot} m   CROSSFALL 2.5 %   SIDE SLOPE 1:1.5")

    # D-07 pavement. Planted: table type A, sketch type C; scale cell empty. Decoy: areas 600.0 + 250.0 = 850.0 agree.
    p, t = frame(doc, "D-07", "PAVEMENT WORKS", "")
    plan(p, [(220, 405, "Carriageway pavement A=600.0m2"), (700, 405, "Carriageway pavement A=250.0m2")])
    t(60, 70, "QUANTITIES - PAVEMENT WORKS", 10)
    table(p, 60, 76, [["ITEM", "TYPE", "UNIT", "QTY"], ["Carriageway pavement", "TYPE A", "m2", "850.0"], ["Footway paving", "-", "m2", "0.0"]],
          [150, 60, 50, 60])
    t(760, 150, "PAVEMENT COMPOSITION TYPE C", 10)
    table(p, 760, 156, [["LAYER", "THICKNESS (mm)"], ["Wearing course", "50"], ["Base", "150"], ["Sub-base", "200"]], [140, 100])
    control(p, t, 760, 520, "JUNCTION A")

    # second sheet numbered D-07 (should be D-08). Planted: duplicate number; 'TBA' in the legend.
    p, t = frame(doc, "D-07", "ROAD MARKING", "1:500 (A3)")
    plan(p, [(220, 405, "Centre line W=100 L=180.0m"), (700, 405, "Stop line W=300 L=7.0m")])
    t(60, 70, "LEGEND", 10)
    table(p, 60, 76, [["SYMBOL", "MEANING"], ["solid white", "edge line"], ["broken white", "centre line"], ["yellow", "TBA"]], [100, 160])
    control(p, t, 760, 520, "JUNCTION A")

    out = os.path.join(folder, "review_set_01.pdf")
    doc.set_metadata({"title": "Review set 01 (synthetic)", "author": "", "producer": "", "creator": ""})
    doc.save(out)
    return out


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "."))
