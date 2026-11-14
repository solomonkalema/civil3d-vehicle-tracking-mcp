"""Score a review report against a case's answer key.

usage: python score.py <case.json> <report.txt|.md> [--json]
A must-find item counts when any of its patterns matches the report (case-insensitive, across line breaks).
A decoy counts against the report when one of its 'flag_if' patterns matches. Pass = recall >= the case's pass mark.
The patterns are deliberately loose: read the 'missed' list by eye before trusting a fail.
"""
import json
import re
import sys


def score(case: dict, report: str) -> dict:
    text = re.sub(r"[ \t]+", " ", report)
    found, missed = [], []
    for item in case["must_find"]:
        hit = any(re.search(p, text, re.I | re.S) for p in item["any"])
        (found if hit else missed).append(item["id"])
    false_flags = [d["id"] for d in case.get("decoys", []) if any(re.search(p, text, re.I | re.S) for p in d["flag_if"])]
    n = len(case["must_find"])
    recall = len(found) / n if n else 0.0
    return {"case": case["case"], "found": len(found), "of": n, "recall": round(recall, 3), "pass_mark": case["pass_mark"],
            "passed": recall >= case["pass_mark"], "missed": missed, "decoys_flagged": false_flags}


if __name__ == "__main__":
    c = json.load(open(sys.argv[1], encoding="utf-8"))
    r = score(c, open(sys.argv[2], encoding="utf-8", errors="replace").read())
    if "--json" in sys.argv:
        print(json.dumps(r, indent=1))
    else:
        print(f"{r['case']}: {r['found']}/{r['of']} found, recall {r['recall']:.0%}, pass mark {r['pass_mark']:.0%} -> "
              f"{'PASS' if r['passed'] else 'FAIL'}")
        what = {i["id"]: i["what"] for i in c["must_find"]}
        for m in r["missed"]:
            print("  missed:", m, "-", what[m])
        for d in r["decoys_flagged"]:
            print("  flagged a decoy (something that is correct):", d)
