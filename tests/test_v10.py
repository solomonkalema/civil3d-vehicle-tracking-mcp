"""v0.10: sheet checks on PDFs and drafting-standard text helpers (14 Nov 2026)."""
import json

import pytest

from civil3d_mcp import drafting as d
from civil3d_mcp import sheetcheck as s
from civil3d_mcp.com_bridge import Civil3DError

pymupdf = pytest.importorskip("pymupdf")


# ---------------------------------------------------------------- capitals
@pytest.mark.parametrize("src, want", [
    ("Embankment, fill up to 1.1 m", "EMBANKMENT, FILL UP TO 1.1 m"),
    ("Asphalt Concrete, 50mm", "ASPHALT CONCRETE, 50mm"),
    ("30 km/h", "30 km/h"),
    ("Width (m)", "WIDTH (m)"),
    ("see www.example.org now", "SEE www.example.org NOW"),
    ("5.44kg/m bar", "5.44kg/m BAR"),
    ("Nov26", "NOV26"),
])
def test_up_plain(src, want):
    assert d.up_plain(src) == want


def test_up_mtext_keeps_format_codes():
    assert d.up_mtext("{\\fCalibri|b0|i0|c0|p34;Station}") == "{\\fCalibri|b0|i0|c0|p34;STATION}"
    assert d.up_mtext("\\A1;1000\\PMin.") == "\\A1;1000\\PMIN."
    assert d.up_mtext("{\\H0.7x;\\S1^2;} slope") == "{\\H0.7x;\\S1^2;} SLOPE"


def test_font_code_swaps_font_keeps_bold():
    assert d.font_code("{\\fCalibri|b1|i0|c0|p34;Title}", "Arial Narrow") == "{\\fArial Narrow|b1|i0|c0|p34;Title}"
    assert d.font_code("plain", "Arial Narrow") == "plain"


def test_caps_field_switches_flags_and_keeps_units():
    src = "Sta <[Station Value(Um|FS|P2|RN|AP|Sn|TP|B2|EN|W0|OF)]>m to <[Name(CP)]>"
    out = d.caps_field(src)
    assert out.startswith("STA <[Station Value(Um|")
    assert "]>m TO " in out and "<[Name(CU)]>" in out


def test_literal_drops_fields_and_codes():
    assert d.literal("\\fArial|b0;K=<[K Value(P2)]>").strip() == "K="


def test_fill_runs_merge_and_split():
    rows = [{"station": s_, "side": "L", "height": h} for s_, h in [(0, 0.2), (5, 1.2), (10, 1.5), (15, 0.3), (40, 1.1)]]
    runs = d.fill_runs(rows, 1.0, gap=10.0)
    assert runs == [{"side": "L", "start": 5, "end": 10, "max": 1.5}, {"side": "L", "start": 40, "end": 40, "max": 1.1}]


def test_densify():
    assert d.densify([(0, 0), (3, 0)], 1.0) == [[0, 0], [1, 0], [2, 0], [3, 0]]


# ---------------------------------------------------------------- sheet checks
def _pdf(path, pages):
    doc = pymupdf.open()
    for texts, thick in pages:
        p = doc.new_page(width=1191, height=842)
        for (x, y, t) in texts:
            p.insert_text((x, y), t, fontsize=10)
        if thick:
            sh = p.new_shape()
            pts = [pymupdf.Point(100 + 40 * i, 400 + (i % 3) * 5) for i in range(20)]
            sh.draw_polyline(pts)
            sh.finish(width=2.0, color=(0, 0, 0), closePath=False)
            sh.commit()
    doc.save(str(path))
    return str(path)


@pytest.fixture()
def pdf(tmp_path):
    return _pdf(tmp_path / "set.pdf", [
        ([(100, 100, "Removal Works Site A"), (900, 800, "Sheet C-1")], True),
        ([(100, 100, "Drainage Site A"), (100, 120, "control points of Site B"), (2000, 100, "Site Z off page")], False),
    ])


def test_dump_text_and_keys(pdf, tmp_path):
    out = s.dump_text([pdf], str(tmp_path / "work"))
    assert out["sets"][0]["key"] == "S01" and out["sets"][0]["pages"] == 2
    txt = (tmp_path / "work" / "txt" / "S01.txt").read_text(encoding="utf-8")
    assert "##### S01 page 2" in txt and "Site B" in txt
    assert json.loads((tmp_path / "work" / "sets.json").read_text())["S01"].endswith("set.pdf")
    assert s.dump_text([pdf], str(tmp_path / "work"))["sets"][0]["key"] == "S01"  # same pdf keeps its key


def test_find_text_reports_page_and_visibility(pdf):
    r = s.find_text([pdf], ["Site B", "Site Z", "Site Q"])
    assert r["counts"] == {"Site B": 1, "Site Z": 1}
    by = {h["term"]: h for h in r["hits"]}
    assert by["Site B"]["page"] == 2 and by["Site B"]["visible"] is True
    assert by["Site Z"]["visible"] is False  # off the page: extracts but cannot be seen
    with pytest.raises(Civil3DError):
        s.find_text([pdf], [])


def test_profile_lines(pdf):
    r = s.profile_lines([pdf])["result"][0]
    assert r["missing"] == [2]


def test_fonts_and_lowercase(pdf):
    r = s.fonts(pdf, house_font="Narrow", capitals=True)
    assert r["not_house_font"] and dict(r["lowercase_words"]).get("control") == 1


def test_tile_and_titleblocks(pdf, tmp_path):
    t = s.tile_png(pdf, 1, 50, [0.5, 0.86, 1, 1], str(tmp_path / "img"))
    assert t.endswith(".png")
    sheets = s.titleblock_sheets(pdf, out_dir=str(tmp_path / "img"))
    assert len(sheets) == 1
    with pytest.raises(Civil3DError):
        s.tile_png(pdf, 9)
    with pytest.raises(Civil3DError):
        s.tile_png(pdf, 1, 50, [0.9, 0, 0.1, 1])


def test_merge_guards_count_and_never_overwrites(pdf, tmp_path):
    out = tmp_path / "book" / "Book.pdf"
    a = s.merge([pdf, pdf], str(out), author="The Firm", expect_pages=4)
    assert a["pages"] == 4 and a["sheet_count_ok"] and a["pdf"].endswith("Book.pdf")
    md = pymupdf.open(a["pdf"]).metadata
    assert md["author"] == "The Firm" and not md["producer"] and not md["creator"]
    b = s.merge([pdf], str(out), expect_pages=4)
    assert "(CHECK sheet count)" in b["pdf"] and not b["sheet_count_ok"]
    c = s.merge([pdf, pdf], str(out))
    assert c["pdf"].endswith("Book v2.pdf")


# ---------------------------------------------------------------- title-block audit
def _lay(name, attrs, vps, paper="A1", block="TB"):
    return {"name": name, "paper": paper, "ctb": "x.ctb", "device": "DWG To PDF.pc3",
            "viewports": [{"scale_if_mm_paper": v} for v in vps], "blocks": [{"name": block, "attrs": attrs}, {"name": "Scale Bar"}]}


def test_audit_flags_scale_initials_and_sequence():
    a = {"DESIGNED": "AB", "CHECKED": "ENG CD", "H_SCALE_A1": "1:500", "H_SCALE_A2": "1:1000", "SUB_TITLE": "KM0+000.00 TO KM0+350.00",
         "DWG_NUMBER": "1", "DATE": "Aug-25"}
    b = dict(a, CHECKED="ENG. CD", H_SCALE_A1="1:1000", SUB_TITLE="KM0+350.00 TO KM0+674.45", DWG_NUMBER="3")
    r = d.audit_rows([_lay("KM0+000.00 TO KM0+350.00", a, ["1:500"]), _lay("KM0+350.00 TO KM0+680.00", b, ["1:500", "1:500"]),
                      {"name": "Layout1", "paper": "A4", "viewports": [{"scale_if_mm_paper": "1:1933536"}], "blocks": []}],
                     constant_tags=["CHECKED", "DATE"])
    flags = " | ".join(f["flag"] for f in r["flags"])
    assert r["sheets"] == 2
    assert "H_SCALE_A1 says 1:1000 but the viewports are ['1:500']" in flags
    assert "differs from the layout name" in flags
    assert "CHECKED should be the same on every sheet" in flags and "DATE should" not in flags
    assert "sequence has a gap" in flags
    assert "no title block with attributes" in flags
    assert set(r["values_that_vary"]) == {"CHECKED", "H_SCALE_A1"}


def test_audit_clean_set_has_no_flags():
    a = {"H_SCALE_A1": "1:500", "SUB_TITLE": "KM0+000 TO KM0+300", "DWG_NUMBER": "1"}
    b = dict(a, SUB_TITLE="KM0+300 TO KM0+600", DWG_NUMBER="2")
    r = d.audit_rows([_lay("KM0+000 TO KM0+300", a, ["1:500"]), _lay("KM0+300 TO KM0+600", b, ["1:500"])])
    assert r["flags"] == [] and r["values_that_vary"] == {}


# ---------------------------------------------------------------- lessons inbox
def test_lessons_inbox_round_trip(tmp_path, monkeypatch):
    from civil3d_mcp import lessons as les
    monkeypatch.setenv("CIVIL3D_LESSONS_INBOX", str(tmp_path / "inbox.local.md"))
    a = les.note("Plot to file fails on a layout set to a system printer: pass a pc3 device.", "gap", "plotting", "X.dwg | PlotToFile",
                 today="2026-11-14")
    assert a["added"] and a["open"] == 1 and "| gap | plotting |" in a["entry"]
    assert not les.note("plot to file fails on a layout set to a system printer: pass a pc3 device.", "gap")["added"]  # duplicate
    les.note("Client rule: initials by discipline,\n checked on every sheet | not only the first.", "rule", "title blocks")
    r = les.read()
    assert r["open"] == 2 and r["by_kind_open"]["rule"] == 1
    assert "|" not in r["entries"][1]["text"] and "\n" not in r["entries"][1]["text"]
    assert les.read(kind="gap")["entries"][0]["context"] == "X.dwg / PlotToFile"
    assert les.done("system printer", "tool road_plot_all_layouts(device=)")["closed"] == 1
    r = les.read()
    assert r["open"] == 1 and r["total"] == 2
    assert "done: tool road_plot_all_layouts" in (tmp_path / "inbox.local.md").read_text(encoding="utf-8")
    with pytest.raises(Civil3DError):
        les.note("too short")
    with pytest.raises(Civil3DError):
        les.note("A perfectly long enough sentence here.", kind="rant")


# ---------------------------------------------------------------- eval kit
def test_eval_set_builds_and_scores(tmp_path):
    import importlib.util
    import json
    root = __import__("pathlib").Path(__file__).resolve().parent.parent / "evals"
    def load(name):
        spec = importlib.util.spec_from_file_location(name, root / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    pdf = load("make_review_set").build(str(tmp_path))
    doc = pymupdf.open(pdf)
    assert len(doc) == 8
    text = "\n".join(p.get_text() for p in doc)
    for planted in ("JUNCTION B", "D-12", "19@200=4000", "180.00", "TBA", "101.100", "100.650"):
        assert planted in text
    assert s.find_text([pdf], ["JUNCTION B"])["hits"][0]["page"] == 2
    case = json.loads((root / "cases" / "review_synthetic_01.json").read_text(encoding="utf-8"))
    sc = load("score").score
    empty = sc(case, "Nothing found. All sheets are in order.")
    assert empty["found"] == 0 and not empty["passed"] and empty["decoys_flagged"] == []
    full = "\n".join(i["what"] for i in case["must_find"])
    r = sc(case, full)
    assert r["missed"] == [] and r["passed"]
    noisy = sc(case, full + "\nD-02 pavement cutting table 42.5 does not match the labels. P1 gradient is wrong.")
    assert set(noisy["decoys_flagged"]) == {"cutting_ok", "p1_ok"}


# ---------------------------------------------------------------- guidance served by the server
def test_guide_index_sections_and_preflight():
    from civil3d_mcp import guide as g
    from civil3d_mcp.catalogue import Catalogue
    idx = {r["topic"]: r for r in g.guide("index")["topics"]}
    assert all(idx[k]["present"] for k in ("lessons", "review_checklist", "tool_map", "knowledge"))
    r = g.guide("review_checklist", "5")
    assert r["sections"][0].startswith("5.") and "Drainage" in r["sections"][0] and "invert" in r["text"]
    assert "end chainage" in g.guide("review_checklist", "across the documents")["text"].lower()
    assert "too_long" in g.guide("knowledge") and g.guide("knowledge", "14")["sections"][0].startswith("14.")
    assert "sheet_find_text" in g.guide("tool_map")["text"]
    with pytest.raises(Civil3DError):
        g.guide("nope")
    with pytest.raises(Civil3DError):
        g.guide("lessons", "no such heading")
    pf = Catalogue().preflight("drawing_review")
    assert len(pf["ask_before_running"]) >= 6 and pf["read_first"]
    assert Catalogue().preflight("working_drawings")["ask_before_running"]
