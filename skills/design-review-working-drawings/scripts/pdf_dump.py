"""Dump the text of drawing PDFs, one text file per PDF, one block per page.
usage: python -P pdf_dump.py <work_dir> <pdf> [<pdf> ...]
Writes <work_dir>/txt/<key>.txt and <work_dir>/sets.json (key -> pdf path). Keys are S01, S02, ... in the order given.
One text file per PDF, all its pages inside under "##### <key> page N" headers.
A page with very few words usually has outline (non-extractable) text: read it from a render instead.
Tables, schedules and title blocks can be outlines even on a page with a normal word count."""
import sys, os, re, json
import pymupdf
work = sys.argv[1]; pdfs = sys.argv[2:]
os.makedirs(os.path.join(work, 'txt'), exist_ok=True)
sp = os.path.join(work, 'sets.json')
sets = json.load(open(sp)) if os.path.exists(sp) else {}
n = len(sets)
for f in pdfs:
    f = os.path.abspath(f)
    key = next((k for k, v in sets.items() if v == f), None)
    if key is None:
        n += 1; key = f'S{n:02d}'; sets[key] = f
    d = pymupdf.open(f)
    with open(os.path.join(work, 'txt', key + '.txt'), 'w', encoding='utf-8') as o:
        for i, p in enumerate(d):
            words = len(p.get_text('words'))
            o.write(f'\n##### {key} page {i+1} size{[round(x) for x in p.rect[2:]]} words{words}\n')
            o.write(re.sub(r'\s*\n\s*', ' | ', p.get_text().strip()) + '\n')
    print(key, len(d), 'pages', os.path.basename(f))
json.dump(sets, open(sp, 'w'), indent=1)
