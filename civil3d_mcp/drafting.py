"""Drafting-standard and sheet-production steps over COM (v0.10, 14 Nov 2026).

Each was first a hand-run script on a seven-road production job (knowledge.md section 14); house-style values that were
fixed there (the font, capitals) are parameters here, because they are the client's settings:
- text_capitals: every text, mtext, leader, dimension override, table cell and attribute to capitals; unit symbols
  after a number stay lower case; web and e-mail addresses are left alone; MText format codes are kept.
- text_font: every text style and every inline font code to one font.
- style_text: scan the Civil 3D label / band / view / table styles for literal text (lowercase, or any), and
  optionally capitalise it and switch field flags so names print in capitals.
- plot_all_layouts: every layout to its own PDF through the Plot object, regenerating each layout's viewports first
  (profile lines do not plot otherwise). One call, no dialogs.
- rename_layouts: replace a string in layout names (e.g. an end chainage) together with the texts on that layout.
- side_slopes: fill / cut height at the road edge per station and side from the corridor's Hinge and Daylight
  feature lines (seconds; walking applied assemblies took an hour), with runs above a threshold.
- survey_lines: line work on layers matching key words (walls, fences, buildings) to JSON, for clearance checks.

Writes go through guard_write (working copies only) and refuse_shared (never into a shared project folder).
"""
from __future__ import annotations

import collections
import csv
import json
import math
import os
import re
import time
from pathlib import Path

from . import civil as civ
from . import roads
from .com_bridge import RPC_RETRY, Civil3D, Civil3DError, refuse_shared

# ----------------------------------------------------------------------------- pure text helpers (unit tested)
UNIT = re.compile(r"(\d)(\s?)(MM2|MM|M2|M3|KM/H|KM|KG/M|KG|KN/M2|KN|N/MM2|M)(?![A-Z])")
CODE_ARG = set("fFHWQTACcpSX")  # MText codes that run to ';'
FONT_CODE = re.compile(r"\\[fF][^|;]*(\|[^;]*)?;")
FIELD = re.compile(r"<\[[^\]]*\]>")


def _is_address(w: str) -> bool:
    lw = w.lower()
    return "www." in lw or "@" in w or "http" in lw


def up_plain(s: str) -> str:
    """Capitals, keeping SI unit symbols after a number in lower case and addresses untouched."""
    if _is_address(s):
        return " ".join(w if _is_address(w) else up_plain(w) for w in s.split(" "))
    u = UNIT.sub(lambda m: m.group(1) + m.group(2) + m.group(3).lower(), s.upper())
    return re.sub(r"\((MM|M|KM|KM/H|M2|M3|KG|KN)\)", lambda m: m.group(0).lower(), u)


def up_mtext(s: str) -> str:
    """up_plain on the visible text of an MText string; format codes (\\f..; \\H..; \\P, braces) are kept."""
    out, buf, i = [], [], 0

    def flush():
        if buf:
            out.append(up_plain("".join(buf)))
            buf.clear()

    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in CODE_ARG:
                j = s.find(";", i)
                if j < 0:
                    j = len(s) - 1
                flush()
                out.append(s[i:j + 1])
                i = j + 1
                continue
            flush()
            out.append(s[i:i + 2])
            i += 2
            continue
        if c in "{}":
            flush()
            out.append(c)
            i += 1
            continue
        buf.append(c)
        i += 1
    flush()
    return "".join(out)


def font_code(s: str, font: str) -> str:
    """Inline \\f font codes -> `font` (bold / italic flags kept)."""
    def rep(m):
        flags = m.group(1) or "|b0|i0|c0|p34"
        flags = re.sub(r"\|c\d+", "|c0", flags)
        flags = re.sub(r"\|p\d+", "|p34", flags)
        return "\\f" + font + flags + ";"

    return FONT_CODE.sub(rep, s)


def caps_field(s: str) -> str:
    """A label-style text: literals to capitals, field flags CP/CL/CT -> CU so names and codes print in capitals."""
    def lit(seg, after_field):
        u = up_mtext(seg)
        if after_field:
            u = re.sub(r"^(MM|M|KM/H|KM|M2|M3|%)(?![A-Z0-9])", lambda m: m.group(1).lower(), u)
        return u

    out, last = [], 0
    for m in FIELD.finditer(s):
        out.append(lit(s[last:m.start()], last > 0))
        out.append(re.sub(r"(?<=[(|])C[PLT](?=[|)])", "CU", m.group(0)))
        last = m.end()
    out.append(lit(s[last:], last > 0))
    return "".join(out)


def literal(s: str) -> str:
    """The literal (non-field, non-code) text of a label-style component."""
    s = FIELD.sub("", s)
    return re.sub(r"\\[A-OQ-Za-oq-z][^;\\]*;|\\P|[{}]", " ", s)


def fill_runs(rows: list[dict], threshold: float, gap: float = 10.0) -> list[dict]:
    """Stretches per side where height >= threshold, merged when closer than `gap` m. rows: station, side, height."""
    out = []
    for side in "LR":
        cur: list[dict] = []
        for r in [r for r in rows if r["side"] == side]:
            if r["height"] >= threshold:
                if cur and r["station"] - cur[-1]["station"] > gap:
                    out.append({"side": side, "start": cur[0]["station"], "end": cur[-1]["station"], "max": max(x["height"] for x in cur)})
                    cur = []
                cur.append(r)
        if cur:
            out.append({"side": side, "start": cur[0]["station"], "end": cur[-1]["station"], "max": max(x["height"] for x in cur)})
    return out


# ----------------------------------------------------------------------------- COM helpers
def _items(c) -> list:
    try:
        return [c.Item(i) for i in range(c.Count)]
    except Exception:
        return []


def _g(o, name, default=None):
    try:
        return getattr(o, name)
    except Exception:
        return default


def _props(o) -> list[str]:
    """Property names of a COM object from its type information."""
    try:
        import pythoncom
        ti = o._oleobj_.GetTypeInfo()
        ta = ti.GetTypeAttr()
        out = []
        for i in range(ta.cFuncs):
            fd = ti.GetFuncDesc(i)
            if fd.invkind == pythoncom.INVOKE_PROPERTYGET and len(fd.args) == 0:
                out.append(ti.GetNames(fd.memid)[0])
        return out
    except Exception:
        return []


def _coll(o):
    try:
        n = o.Count
    except Exception:
        return None
    try:
        return [o.Item(i) for i in range(n)]
    except Exception:
        return None


def _own_blocks(doc) -> list:
    return [b for b in _items(doc.Blocks) if not _g(b, "IsXRef", False)]


def _rewrite(doc, plain, mtext, dry_run: bool) -> tuple[int, list[str], list[str]]:
    """Apply plain(str) / mtext(str) to every text-bearing entity of the drawing's own blocks."""
    n, samples, errors = 0, [], []

    def put(get, set_, fn):
        nonlocal n
        v = get()
        if not v:
            return
        u = fn(v)
        if u != v:
            if not dry_run:
                set_(u)
            n += 1
            if len(samples) < 25:
                samples.append(v[:60])

    for b in _own_blocks(doc):
        for e in _items(b):
            k = _g(e, "ObjectName", "")
            try:
                if k in ("AcDbText", "AcDbAttributeDefinition", "AcDbAttribute"):
                    put(lambda: e.TextString, lambda u: setattr(e, "TextString", u), plain)
                    if k == "AcDbAttributeDefinition":
                        put(lambda: e.PromptString, lambda u: setattr(e, "PromptString", u), plain)
                elif k in ("AcDbMText", "AcDbMLeader"):
                    put(lambda: e.TextString, lambda u: setattr(e, "TextString", u), mtext)
                elif "Dimension" in k:
                    put(lambda: e.TextOverride, lambda u: setattr(e, "TextOverride", u), mtext)
                elif k == "AcDbTable":
                    for r in range(e.Rows):
                        for c in range(e.Columns):
                            try:
                                put(lambda: e.GetText(r, c), lambda u: e.SetText(r, c, u), mtext)
                            except Exception:
                                pass
                elif k == "AcDbBlockReference" and _g(e, "HasAttributes", False):
                    for a in e.GetAttributes():
                        put(lambda: a.TextString, lambda u: setattr(a, "TextString", u),
                            mtext if _g(a, "MTextAttribute", False) else plain)
            except Exception as ex:  # noqa: BLE001
                if len(errors) < 10:
                    errors.append(f"{k}: {str(ex)[:60]}")
    return n, samples, errors


# ----------------------------------------------------------------------------- capitals and fonts
def text_capitals(c3d: Civil3D, dry_run: bool = True) -> dict:
    if not dry_run:
        c3d.guard_write()
    doc = c3d.doc()
    n, samples, errors = _rewrite(doc, up_plain, up_mtext, dry_run)
    if n and not dry_run:
        doc.Regen(1)
    return {"drawing": doc.Name, "texts_changed" if not dry_run else "texts_to_change": n, "samples_before": samples,
            "errors": errors, "note": "Civil 3D labels are not text: use road_style_text for label, band and view styles."}


def text_font(c3d: Civil3D, font: str, dry_run: bool = True, pitch_family: int = 34) -> dict:
    if not font:
        raise Civil3DError("Give the font name from the client's standard (e.g. the font their accepted set uses).")
    if not dry_run:
        c3d.guard_write()
    doc = c3d.doc()
    styles = []
    for s in _items(doc.TextStyles):
        name = _g(s, "Name", "")
        ff = (_g(s, "fontFile", "") or "")
        if not name or "ltypeshp" in ff.lower():
            continue  # linetype shape styles
        try:
            f = s.GetFont()
            if f[0] == font:
                continue
            bold = bool(f[1]) or "bd" in ff.lower() or "nb" in ff.lower()
            if not dry_run:
                if _g(s, "BigFontFile", ""):
                    try:
                        s.BigFontFile = ""
                    except Exception:
                        pass
                s.SetFont(font, bold, bool(f[2]), 0, pitch_family)
            styles.append({"style": name, "was": f[0] or ff})
        except Exception as ex:  # noqa: BLE001
            styles.append({"style": name, "error": str(ex)[:60]})
    code = lambda v: font_code(v, font)  # noqa: E731
    n, samples, errors = _rewrite(doc, lambda v: v, code, dry_run)
    if not dry_run:
        doc.Regen(1)
    return {"drawing": doc.Name, "font": font, "text_styles": styles, "inline_codes": n, "errors": errors, "changed": not dry_run,
            "note": "Label-style texts carry their own inline font codes: road_style_text(font=...) rewrites those."}


# ----------------------------------------------------------------------------- Civil 3D style text
_SUB = ("TitleStyle", "GraphStyle", "BottomAxis", "TopAxis", "LeftAxis", "RightAxis", "CenterAxis", "GridStyle", "Title",
        "AxisTitleStyle", "ColumnStyles", "HeaderStyle")
_SKIP = ("Application", "Document", "Database", "Parent", "Children", "Descendants", "TextComponents")
ROAD_STYLE_ROOTS = ("GeneralLabels", "NoteLabels", "LinkLabelStyles", "MarkerLabelStyles", "ShapeLabelStyles", "CodeSetStyles")


def _style_obj(o, path, fn, log, dry_run, depth=0):
    if depth > 4:
        return
    ps = _props(o)

    def apply(get, set_, what):
        try:
            v = get()
        except Exception:
            return
        if not isinstance(v, str) or not v:
            return
        u = fn(v)
        if u is None or u == v:
            return
        try:
            if not dry_run:
                set_(u)
            log.append({"style": path, "part": what, "text": v[:70]})
        except Exception as ex:  # noqa: BLE001
            log.append({"style": path, "part": what, "error": str(ex)[:60]})

    if "TextComponents" in ps:
        for t in _coll(o.TextComponents) or []:
            apply(lambda: t.Contents.Value, lambda u: setattr(t.Contents, "Value", u), _g(t, "Name", "?"))
    if "Text" in ps:
        apply(lambda: o.Text, lambda u: setattr(o, "Text", u), "Text")
    for p in ps:
        if "DisplayStyle" in p or p in _SKIP:
            continue
        if p.endswith("Style") or p in _SUB:
            sub = _g(o, p)
            if sub is not None and hasattr(sub, "_oleobj_"):
                _style_obj(sub, path + "." + p, fn, log, dry_run, depth + 1)
    if "Children" in ps:
        for k in _coll(o.Children) or []:
            _style_obj(k, path + "/" + _g(k, "Name", "?"), fn, log, dry_run, depth)


def _style_coll(o, path, fn, log, dry_run, depth=0):
    if depth > 4:
        return
    lst = _coll(o)
    if lst is not None:
        for it in lst:
            _style_obj(it, path + "/" + _g(it, "Name", "?"), fn, log, dry_run)
        return
    for p in _props(o):
        if p.endswith("Styles") or p.endswith("StyleSets"):
            sub = _g(o, p)
            if sub is not None:
                _style_coll(sub, path + "." + p, fn, log, dry_run, depth + 1)


def style_text(c3d: Civil3D, action: str = "scan", only: list[str] | None = None, font: str | None = None,
               max_rows: int = 60) -> dict:
    """action 'scan' (literal text with lowercase letters), 'list' (every text), 'capitals' (rewrite; with font= also
    switch inline font codes). only: style-collection name prefixes, e.g. ['ProfileLabelStyles', 'ProfileViewBandStyles']."""
    if action not in ("scan", "list", "capitals"):
        raise Civil3DError("action is 'scan', 'list' or 'capitals'.")
    dry = action != "capitals"
    if not dry:
        c3d.guard_write()

    def fn(v):
        if action == "list":
            return v + "\0"  # always 'different', never written (dry run)
        if action == "scan":
            return v + "\0" if re.search("[a-z]", literal(v)) else v
        u = caps_field(v)
        return font_code(u, font) if font else u

    log: list[dict] = []
    roots = []
    for doc, tag in ((civ.aecc_doc(c3d), "land"), (roads.road_doc(c3d), "road")):
        if doc is None:
            continue
        for p in _props(doc):
            land = tag == "land" and (p.endswith("LabelStyles") or p.endswith("BandStyles") or p.endswith("ViewStyles")
                                      or p.endswith("TableStyles"))
            if not (land or (tag == "road" and p in ROAD_STYLE_ROOTS)):
                continue
            if only and not any(p.startswith(o) for o in only):
                continue
            n0 = len(log)
            try:
                _style_coll(getattr(doc, p), f"{tag}:{p}", fn, log, dry)
            except Exception as ex:  # noqa: BLE001
                log.append({"style": p, "error": str(ex)[:60]})
            roots.append({"collection": f"{tag}:{p}", "texts": len(log) - n0})
    errs = [l for l in log if "error" in l]
    return {"action": action, "count": len(log) - len(errs), "errors": errs[:10], "collections": [r for r in roots if r["texts"]],
            "texts": [l for l in log if "error" not in l][:max_rows], "truncated": len(log) > max_rows, "changed": not dry,
            "note": "Not reachable over COM: band titles, code set styles, link / marker / shape label styles, expressions, "
                    "table styles. Edit those in Toolspace > Settings. With many drawings open this pass is slow: quit and "
                    "relaunch every few drawings."}


# ----------------------------------------------------------------------------- plotting and layouts
def _is_busy(ex) -> bool:
    return bool(getattr(ex, "args", None)) and ex.args[0] in RPC_RETRY


def _busy(fn, tries: int = 20, wait: float = 1.5):
    """Call fn, waiting while Civil 3D answers 'call rejected'. Any other error is raised at once."""
    last = None
    for _ in range(tries):
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001
            if not _is_busy(ex):
                raise
            last = ex
            time.sleep(wait)
    raise Civil3DError(f"Civil 3D stayed busy: {last}")


def plot_all_layouts(c3d: Civil3D, folder: str, layouts: list[str] | None = None, regen: bool = True,
                     device: str | None = None, budget_s: float = 40.0) -> dict:
    """Each layout -> <folder>/<layout>.pdf. Existing files are skipped, not overwritten. device: plotter to use for
    every layout (default: each layout's own). Returns 'left' when the time budget ran out: call again."""
    refuse_shared(folder, "plot to")
    os.makedirs(folder, exist_ok=True)
    doc = c3d.doc()
    lays = _busy(lambda: sorted(((l, l.Name, l.ConfigName) for l in _items(doc.Layouts) if l.Name != "Model"),
                                key=lambda t: t[0].TabOrder))
    if layouts:
        want = set(layouts)
        unknown = sorted(want - {t[1] for t in lays})
        if unknown:
            raise Civil3DError(f"No layout named {unknown}. Layouts: {[t[1] for t in lays][:40]}")
        lays = [t for t in lays if t[1] in want]
    bp = _busy(lambda: doc.GetVariable("BACKGROUNDPLOT"))
    _busy(lambda: doc.SetVariable("BACKGROUNDPLOT", 0))
    out, left, t0, hint = [], [], time.time(), None
    try:
        for lay, name, cfg in lays:
            pdf = os.path.join(folder, re.sub(r'[\\/:*?"<>|]', "_", name) + ".pdf")
            if os.path.exists(pdf):
                out.append({"layout": name, "result": "exists"})
                continue
            if time.time() - t0 > budget_s:
                left.append(name)
                continue
            use = device or cfg
            try:
                _busy(lambda: setattr(doc, "ActiveLayout", lay))
                if regen:  # from inside the viewport: without it profile lines do not plot
                    _busy(lambda: doc.SendCommand("_.MSPACE\n_.REGENALL\n_.PSPACE\n"))
                    time.sleep(2)
                ok = _busy(lambda: doc.Plot.PlotToFile(pdf, use))
            except Civil3DError:
                raise
            except Exception as ex:  # noqa: BLE001
                out.append({"layout": name, "result": f"failed: {str(ex)[:90]}", "device": use})
                if not use.lower().endswith(".pc3"):
                    hint = (f"'{use}' is a system printer, which cannot plot to a file from here. Call again with "
                            "device='DWG To PDF.pc3' (check the paper size of the result against the layout's).")
                continue
            t = time.time()
            while not os.path.exists(pdf) and time.time() - t < 20:
                time.sleep(0.3)
            out.append({"layout": name, "result": "ok" if os.path.exists(pdf) else f"failed ({ok})", "pdf": pdf, "device": use})
    finally:
        try:
            _busy(lambda: doc.SetVariable("BACKGROUNDPLOT", bp), tries=10)
        except Exception:  # noqa: BLE001
            pass
    res = {"drawing": doc.Name, "folder": folder, "plotted": out, "left": left,
           "note": "Check plan-and-profile PDFs with sheet_profile_lines. For layouts with no viewport pass regen=false."}
    if hint:
        res["fix"] = hint
    return res


def rename_layouts(c3d: Civil3D, old: str, new: str, dry_run: bool = True) -> dict:
    """Replace `old` with `new` in layout names and in the texts and attributes on those layouts."""
    if not old:
        raise Civil3DError("Give the text to replace.")
    if not dry_run:
        c3d.guard_write()
    doc = c3d.doc()
    hits = []
    for l in _items(doc.Layouts):
        name = l.Name
        if name == "Model":
            continue
        texts = 0
        for e in _items(l.Block):
            k = _g(e, "ObjectName", "")
            try:
                if k in ("AcDbText", "AcDbMText") and old in (e.TextString or ""):
                    if not dry_run:
                        e.TextString = e.TextString.replace(old, new)
                    texts += 1
                elif k == "AcDbBlockReference" and _g(e, "HasAttributes", False):
                    for a in e.GetAttributes():
                        if old in (a.TextString or ""):
                            if not dry_run:
                                a.TextString = a.TextString.replace(old, new)
                            texts += 1
            except Exception:
                pass
        renamed = old in name
        if renamed and not dry_run:
            l.Name = name.replace(old, new)
        if renamed or texts:
            hits.append({"layout": name, "renamed_to": name.replace(old, new) if renamed else None, "texts": texts})
    if hits and not dry_run:
        doc.Regen(1)
    return {"old": old, "new": new, "layouts": hits, "changed": not dry_run,
            "note": "Check the new value against the alignment's real end, not against another layout name. Not saved."}


# ----------------------------------------------------------------------------- title-block audit
_SCALE = re.compile(r"1\s*:\s*([\d,\.]+)")
_CHAIN = re.compile(r"(\d+)\s*\+\s*(\d+(?:\.\d+)?)")


def _scale_num(v) -> float | None:
    m = _SCALE.search(str(v or ""))
    return float(m.group(1).replace(",", "")) if m else None


def audit_rows(layouts: list[dict], scale_tags: list[str] | None = None, constant_tags: list[str] | None = None,
               number_tag: str = "DWG_NUMBER", subtitle_tag: str = "SUB_TITLE") -> dict:
    """Pure part of titleblock_audit: `layouts` as returned by roads.layouts(). Unit tested."""
    rows, flags = [], []
    for lay in layouts:
        tb = None
        for b in lay.get("blocks", []):
            a = b.get("attrs") or {}
            if number_tag in a or subtitle_tag in a or "TITLE" in a:
                tb = b
                break
        vps = sorted({v.get("scale_if_mm_paper") for v in lay.get("viewports", []) if v.get("scale_if_mm_paper")})
        if tb is None:
            if lay.get("viewports") or lay.get("blocks"):
                rows.append({"layout": lay["name"], "block": None, "viewport_scales": vps})
                if lay.get("paper"):
                    flags.append({"layout": lay["name"], "flag": "no title block with attributes (plain-text block, or a stray layout)"})
            continue
        a = tb["attrs"]
        rows.append({"layout": lay["name"], "block": tb["name"], "paper": lay.get("paper"), "ctb": lay.get("ctb"),
                     "device": lay.get("device"), "viewport_scales": vps, "attrs": a})
        vnums = {_scale_num(v) for v in vps} - {None}
        for tag in (scale_tags if scale_tags is not None else [t for t in a if "SCALE" in t.upper() and "A2" not in t.upper() and t.upper().startswith("H")]):
            sn = _scale_num(a.get(tag))
            if sn and vnums and not any(abs(sn - v) / v < 0.02 for v in vnums):
                flags.append({"layout": lay["name"], "flag": f"{tag} says 1:{sn:g} but the viewports are {vps}"})
        st = str(a.get(subtitle_tag, ""))
        if _CHAIN.search(st) and _CHAIN.search(lay["name"]):
            if [m.groups() for m in _CHAIN.finditer(st)] != [m.groups() for m in _CHAIN.finditer(lay["name"])]:
                flags.append({"layout": lay["name"], "flag": f"{subtitle_tag} '{st}' differs from the layout name"})
        for tag, v in a.items():
            if not str(v).strip():
                flags.append({"layout": lay["name"], "flag": f"{tag} is empty"})
    tbs = [r for r in rows if r.get("attrs")]
    varying = {}
    tags = sorted({t for r in tbs for t in r["attrs"]})
    for tag in tags:
        vals = collections.Counter(str(r["attrs"].get(tag, "")) for r in tbs)
        if len(vals) > 1 and tag not in (number_tag, subtitle_tag):
            varying[tag] = dict(vals)
    for tag in (constant_tags if constant_tags is not None else []):
        if tag in varying:
            flags.append({"layout": "*", "flag": f"{tag} should be the same on every sheet: {varying[tag]}"})
    nums = [str(r["attrs"].get(number_tag, "")).strip() for r in tbs]
    ints = [int(n) for n in nums if n.isdigit()]
    if ints and sorted(ints) != list(range(min(ints), min(ints) + len(ints))):
        flags.append({"layout": "*", "flag": f"{number_tag} sequence has a gap or a repeat: {nums}"})
    for k in ("paper", "ctb", "device"):
        vals = collections.Counter(str(r.get(k)) for r in tbs)
        if len(vals) > 1:
            flags.append({"layout": "*", "flag": f"{k} differs between sheets: {dict(vals)}"})
    return {"sheets": len(tbs), "rows": rows, "values_that_vary": varying, "flags": flags}


def titleblock_audit(c3d: Civil3D, scale_tags: list[str] | None = None, constant_tags: list[str] | None = None,
                     number_tag: str = "DWG_NUMBER", subtitle_tag: str = "SUB_TITLE", compact: bool = True) -> dict:
    doc = c3d.doc()
    out = audit_rows(roads.layouts(doc, True), scale_tags, constant_tags, number_tag, subtitle_tag)
    out["drawing"] = doc.Name
    if compact:  # one line per sheet: the tags that vary plus number and sub-title
        keep = set(out["values_that_vary"]) | {number_tag, subtitle_tag}
        tbs = [r for r in out["rows"] if r.get("attrs")]
        out["same_on_every_sheet"] = {t: v for t, v in (tbs[0]["attrs"].items() if tbs else []) if t not in keep}
        out["rows"] = [{"layout": r["layout"], "block": r.get("block"), "viewport_scales": r.get("viewport_scales"),
                        **{t: v for t, v in (r.get("attrs") or {}).items() if t in keep}} for r in out["rows"]]
    out["note"] = ("Flags are things to look at, not verdicts: check a stated-versus-drawn scale against the client's accepted "
                   "reference set before calling it wrong, and take initials from the client's rule.")
    return out


# ----------------------------------------------------------------------------- corridor side slopes
def side_slopes(c3d: Civil3D, alignment: str, out_csv: str | None = None, step: float = 5.0,
                threshold: float | None = None, gap: float = 10.0) -> dict:
    rd = roads.road_doc(c3d)
    data: dict = collections.defaultdict(dict)
    found = False
    for c in _items(rd.Corridors):
        for b in _items(c.Baselines):
            al = _g(b, "Alignment")
            if al is None or al.Name != alignment:
                continue
            found = True
            for col in _items(b.MainBaselineFeatureLines.FeatureLinesCol):
                for fl in _items(col):
                    code = _g(fl, "CodeName", "") or ""
                    kind = "hinge" if code.startswith("Hinge") else ("day" if code.startswith("Daylight") else None)
                    if not kind:
                        continue
                    pts = fl.FeatureLinePoints
                    last = None
                    for i in range(pts.Count):
                        p = pts.Item(i)
                        bk = round(p.Station / step) * step
                        if last == bk:
                            continue
                        last = bk
                        x, y, z = p.XYZ
                        try:
                            _, off = al.StationOffset(x, y)
                        except Exception:
                            continue
                        side = "L" if off < 0 else "R"
                        cur = data[(bk, side)].get(kind)
                        if kind == "day" and cur and abs(cur[0]) > abs(off):
                            continue  # outermost daylight
                        if kind == "hinge" and cur and abs(cur[0]) < abs(off):
                            continue  # innermost hinge
                        data[(bk, side)][kind] = (round(off, 2), round(z, 3), code)
    if not found:
        raise Civil3DError(f"No corridor baseline on an alignment named '{alignment}' in this drawing (open the corridor drawing).")
    rows = []
    for (bk, side), v in sorted(data.items()):
        if "hinge" in v and "day" in v:
            h, d = v["hinge"], v["day"]
            rows.append({"station": bk, "side": side, "edge": abs(h[0]), "daylight": abs(d[0]),
                         "height": round(h[1] - d[1], 3), "code": d[2]})
    out: dict = {"alignment": alignment, "station_sides": len(rows), "height_sign": "+ fill, - cut (edge level minus daylight level)"}
    for side in "LR":
        s = [r for r in rows if r["side"] == side]
        if s:
            hi, lo = max(s, key=lambda r: r["height"]), min(s, key=lambda r: r["height"])
            out[side] = {"max_fill": hi["height"], "at": hi["station"], "max_cut": -lo["height"], "cut_at": lo["station"]}
    if out_csv:
        refuse_shared(out_csv, "write")
        Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, ["station", "side", "edge", "daylight", "height", "code"])
            w.writeheader()
            w.writerows(rows)
        out["csv"] = out_csv
    if threshold is not None:
        out["fill_runs"] = fill_runs(rows, threshold, gap)
        out["cut_runs"] = fill_runs([dict(r, height=-r["height"]) for r in rows], threshold, gap)
        out["threshold_note"] = "The threshold for slope protection or retaining is the engineer's decision."
    return out


# ----------------------------------------------------------------------------- survey line work
def densify(pts: list, step: float = 1.0) -> list[list[float]]:
    out: list[list[float]] = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
        out += [[x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n] for k in range(n)]
    return out + [list(pts[-1])] if pts else out


def survey_lines(c3d: Civil3D, out_json: str, keys: list[str] | None = None, step: float = 1.0) -> dict:
    """Polylines and lines on layers whose name contains one of `keys` -> {layer: [[x, y], ...] per object}."""
    import pythoncom
    import win32com.client
    refuse_shared(out_json, "write")
    keys = [k.upper() for k in (keys or ["WALL", "FENC", "BUILD", "RETAIN"])]
    d = c3d.doc()
    want = [l.Name for l in _items(d.Layers) if any(k in l.Name.upper() for k in keys) and "TEXT" not in l.Name.upper()]
    if not want:
        return {"drawing": d.Name, "layers": [], "note": f"No layer name contains any of {keys}."}
    try:
        d.SelectionSets.Item("MCP_SL").Delete()
    except Exception:
        pass
    ss = d.SelectionSets.Add("MCP_SL")
    try:
        ft = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, [8])
        fd = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, [",".join(want)])
        ss.Select(5, pythoncom.Empty, pythoncom.Empty, ft, fd)
        res: dict[str, list] = {}
        for e in _items(ss):
            k, pts = _g(e, "ObjectName", ""), None
            try:
                if k == "AcDbPolyline":
                    c = list(e.Coordinates)
                    pts = [(c[j], c[j + 1]) for j in range(0, len(c), 2)]
                    if e.Closed:
                        pts.append(pts[0])
                elif k in ("AcDb2dPolyline", "AcDb3dPolyline"):
                    c = list(e.Coordinates)
                    pts = [(c[j], c[j + 1]) for j in range(0, len(c), 3)]
                elif k == "AcDbLine":
                    pts = [tuple(e.StartPoint[:2]), tuple(e.EndPoint[:2])]
            except Exception:
                pass
            if pts and len(pts) > 1:
                res.setdefault(e.Layer, []).append(densify(pts, step))
    finally:
        try:
            ss.Delete()
        except Exception:
            pass
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w") as f:
        json.dump(res, f)
    return {"drawing": d.Name, "json": out_json, "objects_per_layer": {l: len(v) for l, v in res.items()}}
