"""Checks on plotted drawing sheets (PDF), with no Civil 3D needed (v0.10, 14 Nov 2026).

Each one was first a hand-run script on a road production job or a drawing-set review:
- dump_text: text of every page to a file per PDF, so a set can be searched cheaply before any page is looked at.
- find_text: where given words occur (the copy-paste leftover hunt: another site's name on this site's sheet), with
  whether each hit is actually visible (text under a mask, off the page or of zero height still extracts).
- tile_png: one page or a part of it as an image (whole sheet at low dpi, a tile at high dpi to read small text).
- titleblock_sheets: the title-block strip of many pages stacked into contact sheets (title blocks drawn as
  outlines do not extract as text; 12 are read from one image this way).
- profile_lines: is the thick design-profile stroke present on each plan-and-profile page? (It plots missing unless
  every layout is regenerated from inside its viewport first, and the fault returns silently on a replot.)
- fonts: fonts other than the house font, and lowercase words where the house style is capitals.
- merge: assemble sheets into one PDF with neutral metadata and a sheet-count guard.

Nothing here writes into a shared project folder (refuse_shared), and nothing overwrites an existing file.
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

from .com_bridge import Civil3DError, refuse_shared


def _mu():
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        try:
            import fitz  # type: ignore
            return fitz
        except ImportError as e:
            raise Civil3DError('PyMuPDF is missing for the server\'s Python. Run: python -m pip install --user pymupdf') from e


def _out_dir(folder: str) -> Path:
    refuse_shared(folder, "write to")
    p = Path(folder)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _free(p: Path) -> Path:
    """A name that does not exist yet ('x.pdf' -> 'x v2.pdf'): outputs never overwrite."""
    if not p.exists():
        return p
    n = 2
    while True:
        q = p.with_name(f"{p.stem} v{n}{p.suffix}")
        if not q.exists():
            return q
        n += 1


def _key(i: int) -> str:
    return f"S{i:02d}"


# ----------------------------------------------------------------------------- text
def dump_text(pdfs: list[str], out_dir: str) -> dict:
    """<out_dir>/txt/<key>.txt per PDF ('##### <key> page N' headers, lines joined with ' | ') and <out_dir>/sets.json."""
    mu = _mu()
    out = _out_dir(out_dir)
    (out / "txt").mkdir(exist_ok=True)
    sp = out / "sets.json"
    sets = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    rows = []
    for f in pdfs:
        f = os.path.abspath(f)
        key = next((k for k, v in sets.items() if v == f), None) or _key(len(sets) + 1)
        sets[key] = f
        d = mu.open(f)
        thin = []
        with open(out / "txt" / f"{key}.txt", "w", encoding="utf-8") as o:
            for i, p in enumerate(d, 1):
                words = len(p.get_text("words"))
                if words < 15:
                    thin.append(i)
                o.write(f"\n##### {key} page {i} size{[round(x) for x in p.rect[2:]]} words{words}\n")
                o.write(re.sub(r"\s*\n\s*", " | ", p.get_text().strip()) + "\n")
        rows.append({"key": key, "pdf": f, "pages": len(d), "text": str(out / "txt" / f"{key}.txt"),
                     "pages_with_little_text": thin[:60]})
    sp.write_text(json.dumps(sets, indent=1), encoding="utf-8")
    return {"sets": rows, "note": "Pages with little text usually carry outline lettering: read them from tile_png. "
                                  "Tables, schedules and title blocks can be outlines even on a page with a normal word count: "
                                  "if a table seen on the sheet is not in the text, read it from a tile."}


def _visible(page, bbox, size: float) -> bool:
    mu = _mu()
    return size >= 1 and page.rect.contains(mu.Rect(bbox))


def find_text(pdfs: list[str], terms: list[str], ignore_case: bool = True, limit: int = 300) -> dict:
    """Every page where each term occurs, with the text line and whether it is visible on the page."""
    mu = _mu()
    if not terms:
        raise Civil3DError("Give the words to look for (e.g. the names and numbers of the other sites in the set).")
    pats = [(t, re.compile(re.escape(t), re.I if ignore_case else 0)) for t in terms]
    hits, per_term = [], collections.Counter()
    for f in pdfs:
        d = mu.open(f)
        for i, p in enumerate(d, 1):
            for b in p.get_text("dict", clip=mu.INFINITE_RECT())["blocks"]:  # text off the page too
                for ln in b.get("lines", []):
                    line = "".join(s["text"] for s in ln["spans"])
                    for t, pat in pats:
                        if pat.search(line):
                            s0 = ln["spans"][0]
                            per_term[t] += 1
                            if len(hits) < limit:
                                hits.append({"pdf": os.path.basename(f), "page": i, "term": t, "line": line.strip()[:120],
                                             "visible": _visible(p, ln["bbox"], s0["size"]),
                                             "at": [round(ln["bbox"][0] / p.rect.width, 2), round(ln["bbox"][1] / p.rect.height, 2)]})
    return {"counts": dict(per_term), "hits": hits, "truncated": sum(per_term.values()) > len(hits),
            "note": "visible=false still matters: hidden text marks a sheet cloned from another; read that whole sheet. "
                    "Text under a mask reports visible=true (it is inside the page): confirm on a tile at 'at' (fractions of the page)."}


# ----------------------------------------------------------------------------- images
def tile_png(pdf: str, page: int = 1, dpi: int = 70, box: list[float] | None = None, out_dir: str | None = None) -> str:
    """One page, or the part box=[x0, y0, x1, y1] (fractions of the page), as a PNG. Returns the path."""
    mu = _mu()
    d = mu.open(pdf)
    if not 1 <= page <= len(d):
        raise Civil3DError(f"{os.path.basename(pdf)} has {len(d)} pages.")
    p = d[page - 1]
    r = p.rect
    clip, tag = None, ""
    if box:
        if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise Civil3DError("box is [x0, y0, x1, y1] as fractions of the page, e.g. [0.5, 0.86, 1, 1].")
        clip = mu.Rect(r.x0 + box[0] * r.width, r.y0 + box[1] * r.height, r.x0 + box[2] * r.width, r.y0 + box[3] * r.height)
        tag = "_" + "_".join(f"{v:g}" for v in box)
    import tempfile
    folder = _out_dir(out_dir) if out_dir else Path(tempfile.gettempdir()) / "sheetcheck"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{Path(pdf).stem[:40]}_p{page:03d}_{dpi}{tag}.png"
    p.get_pixmap(dpi=dpi, clip=clip).save(str(out))
    return str(out)


def titleblock_sheets(pdf: str, box: list[float] | None = None, per_sheet: int = 12, dpi: int = 110,
                      out_dir: str | None = None, pages: list[int] | None = None) -> list[str]:
    """Contact sheets of the title-block strip (default the full-width bottom strip [0, 0.86, 1, 1]), each strip tagged 'pN'."""
    mu = _mu()
    a = box or [0, 0.86, 1, 1]
    d = mu.open(pdf)
    import tempfile
    folder = _out_dir(out_dir) if out_dir else Path(tempfile.gettempdir()) / "sheetcheck"
    folder.mkdir(parents=True, exist_ok=True)
    nums = [n for n in (pages or range(1, len(d) + 1)) if 1 <= n <= len(d)]
    outs = []
    for s in range(0, len(nums), per_sheet):
        pix = []
        for n in nums[s:s + per_sheet]:
            r = d[n - 1].rect
            clip = mu.Rect(r.x0 + a[0] * r.width, r.y0 + a[1] * r.height, r.x0 + a[2] * r.width, r.y0 + a[3] * r.height)
            pix.append((n, d[n - 1].get_pixmap(dpi=dpi, clip=clip)))
        w = max(p.width for _, p in pix) + 90
        h = sum(p.height + 6 for _, p in pix)
        o = mu.open()
        pg = o.new_page(width=w, height=h)
        y = 0
        for n, p in pix:
            pg.insert_text((6, y + 30), f"p{n}", fontsize=26)
            pg.insert_image(mu.Rect(90, y, 90 + p.width, y + p.height), pixmap=p)
            y += p.height + 6
        region = "_".join(f"{v:g}" for v in a)
        path = folder / f"{Path(pdf).stem[:40]}_titleblocks_{region}_{nums[s]:03d}.png"
        pg.get_pixmap(dpi=72).save(str(path))
        outs.append(str(path))
    return outs


# ----------------------------------------------------------------------------- plotted content
def profile_lines(pdfs: list[str], min_width_pt: float = 1.5, min_length_pt: float = 300.0, min_segments: int = 8) -> dict:
    """Per page: the longest thick stroke (the design profile line). 'missing' lists pages without one."""
    mu = _mu()
    out = []
    for f in pdfs:
        pages, missing = [], []
        for i, p in enumerate(mu.open(f), 1):
            longest = 0.0
            for d in p.get_drawings():
                if (d.get("width") or 0) >= min_width_pt and d.get("type") in ("s", "fs"):
                    if sum(1 for it in d["items"] if it[0] in ("l", "c")) >= min_segments:
                        r = d["rect"]
                        longest = max(longest, r.width, r.height)
            ok = longest > min_length_pt
            pages.append({"page": i, "ok": ok, "longest_pt": round(longest)})
            if not ok:
                missing.append(i)
        out.append({"pdf": os.path.basename(f), "pages": len(pages), "missing": missing, "detail": pages if missing else []})
    return {"result": out, "note": "A page that is not a plan-and-profile sheet reports missing: read it against the sheet type. "
                                   "Thresholds suit an A1 sheet with a thick design line; lower them for A3."}


_UNIT_WORD = re.compile(r"\d(mm|m|km)\b|^\(?(mm|m|km|m2|m3|kg|kN)\)?$|www\.|@|http", re.I)


def fonts(pdf: str, house_font: str | None = None, capitals: bool = False, top: int = 10) -> dict:
    """Fonts of the visible text. house_font: part of the font name every text should use; capitals=True also lists
    words with lowercase letters (unit symbols and web addresses left out)."""
    mu = _mu()
    d = mu.open(pdf)
    all_fonts, other, low = collections.Counter(), collections.Counter(), collections.Counter()
    pages_other = collections.defaultdict(set)
    for i, p in enumerate(d, 1):
        for b in p.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    if not s["text"].strip() or not _visible(p, s["bbox"], s["size"]):
                        continue
                    all_fonts[s["font"]] += 1
                    if house_font and house_font.lower() not in s["font"].lower():
                        other[(s["font"], s["text"].strip()[:18])] += 1
                        pages_other[s["font"]].add(i)
                    if capitals:
                        for w in s["text"].split():
                            if re.search("[a-z]{2,}", w) and not _UNIT_WORD.search(w):
                                low[w] += 1
    return {"pages": len(d), "fonts": all_fonts.most_common(top),
            "not_house_font": [{"font": k[0], "sample": k[1], "count": v} for k, v in other.most_common(top)],
            "pages_by_other_font": {k: sorted(v)[:40] for k, v in pages_other.items()},
            "lowercase_words": low.most_common(top * 2) if capitals else None,
            "note": "Text drawn as outlines has no font and is not listed."}


# ----------------------------------------------------------------------------- assembly
def merge(files: list[str], out_pdf: str, title: str | None = None, author: str | None = None,
          expect_pages: int | None = None) -> dict:
    """Merge PDFs in the order given into out_pdf (never over an existing file). Metadata: title, author; producer
    and creator are cleared. expect_pages: when the total differs, the file is written with '(CHECK sheet count)'."""
    mu = _mu()
    if not files:
        raise Civil3DError("Give the PDFs to merge, in order.")
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise Civil3DError(f"Not found: {missing[:5]}")
    out = Path(out_pdf)
    _out_dir(str(out.parent))
    o = mu.open()
    parts = []
    for f in files:
        s = mu.open(f)
        o.insert_pdf(s)
        parts.append({"pdf": os.path.basename(f), "pages": len(s)})
        s.close()
    total = len(o)
    if expect_pages is not None and total != expect_pages:
        out = out.with_name(f"{out.stem} (CHECK sheet count){out.suffix}")
    out = _free(out)
    o.set_metadata({"title": title or out.stem, "author": author or "", "producer": "", "creator": "", "subject": "", "keywords": ""})
    o.save(str(out), garbage=3, deflate=True)
    return {"pdf": str(out), "pages": total, "expected": expect_pages, "parts": parts,
            "sheet_count_ok": expect_pages is None or total == expect_pages}
