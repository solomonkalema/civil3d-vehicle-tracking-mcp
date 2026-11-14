"""Render one page, or a part of it, to PNG.
usage: python -P pdf_render.py <work_dir> <key> <page> [dpi=70] [x0 y0 x1 y1]
x0..y1 are fractions of the page (0 0 1 1 = whole page; 0.55 0.86 1 1 = a bottom-right title block).
Whole sheet: 70 dpi. Reading small text: 150 to 250 dpi on a tile. Prints the PNG path."""
import sys, os, json
import pymupdf
work, key, page = sys.argv[1], sys.argv[2], int(sys.argv[3])
dpi = int(float(sys.argv[4])) if len(sys.argv) > 4 else 70  # whole number: a float dpi fails
p = pymupdf.open(json.load(open(os.path.join(work, 'sets.json')))[key])[page - 1]
r = p.rect; clip = None; tag = ''
if len(sys.argv) > 8:
    a = [float(x) for x in sys.argv[5:9]]
    clip = pymupdf.Rect(r.x0 + a[0]*r.width, r.y0 + a[1]*r.height, r.x0 + a[2]*r.width, r.y0 + a[3]*r.height)
    tag = '_' + '_'.join(sys.argv[5:9])
os.makedirs(os.path.join(work, 'img'), exist_ok=True)
out = os.path.join(work, 'img', f'{key}_p{page:03d}_{dpi}{tag}.png')
p.get_pixmap(dpi=dpi, clip=clip).save(out); print(out)
