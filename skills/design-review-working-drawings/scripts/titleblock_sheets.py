"""Stack the title-block strip of many pages into contact sheets, so 12 title blocks are read from one image.
usage: python -P titleblock_sheets.py <work_dir> <key> [x0 y0 x1 y1] [per_sheet=12] [dpi=110]
Default strip 0 0.86 1 1 (full-width bottom strip; narrow it once you know where the block is). Each strip is labelled with its page number. Prints the PNG paths.
On A1 sheets the full-width strip is too small to read: run it twice with narrow boxes (title cell, then number cell),
for example 0.45 0.89 0.72 1 and 0.72 0.89 1 1, or raise dpi to 200."""
import sys, os, json
import pymupdf
work, key = sys.argv[1], sys.argv[2]
a = [float(x) for x in sys.argv[3:7]] if len(sys.argv) > 6 else [0, 0.86, 1, 1]
per = int(sys.argv[7]) if len(sys.argv) > 7 else 12
dpi = int(float(sys.argv[8])) if len(sys.argv) > 8 else 110
d = pymupdf.open(json.load(open(os.path.join(work, 'sets.json')))[key])
os.makedirs(os.path.join(work, 'img'), exist_ok=True)
for s in range(0, len(d), per):
    pix = []
    for i in range(s, min(s + per, len(d))):
        r = d[i].rect
        clip = pymupdf.Rect(r.x0 + a[0]*r.width, r.y0 + a[1]*r.height, r.x0 + a[2]*r.width, r.y0 + a[3]*r.height)
        pix.append((i + 1, d[i].get_pixmap(dpi=dpi, clip=clip)))
    w = max(p.width for _, p in pix) + 90; h = sum(p.height + 6 for _, p in pix)
    out = pymupdf.open(); pg = out.new_page(width=w, height=h); y = 0
    for n, p in pix:
        pg.insert_text((6, y + 30), f'p{n}', fontsize=26)
        pg.insert_image(pymupdf.Rect(90, y, 90 + p.width, y + p.height), pixmap=p); y += p.height + 6
    path = os.path.join(work, 'img', f'{key}_titleblocks_' + '_'.join(f'{v:g}' for v in a) + f'_{s+1:03d}.png')
    pg.get_pixmap(dpi=72).save(path); print(path)
