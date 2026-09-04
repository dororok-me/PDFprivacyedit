"""폴더 일괄 처리: python batch.py 입력폴더 출력폴더 [--cat name,rrn,...]"""
import sys, os, json, argparse
from engine import redact_file, DEFAULT_ON, CATEGORIES

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("dst")
ap.add_argument("--cat", default=",".join(sorted(DEFAULT_ON)), help="쉼표로 구분: " + ",".join(CATEGORIES))
ap.add_argument("--no-ocr", action="store_true")
a = ap.parse_args()

enabled = set(a.cat.split(","))
os.makedirs(a.dst, exist_ok=True)
report = {}
files = [f for f in sorted(os.listdir(a.src)) if f.lower().endswith(".pdf")]
for n, f in enumerate(files, 1):
    print(f"[{n}/{len(files)}] {f}")
    hits, sources = redact_file(os.path.join(a.src, f), os.path.join(a.dst, f), enabled, ocr=not a.no_ocr)
    report[f] = {"pages": {str(k + 1): v for k, v in sources.items()}, "hits": [h.as_dict() for h in hits]}
    for h in hits:
        print(f"    p{h.page_no+1} [{CATEGORIES[h.category]}] {h.text}")
with open(os.path.join(a.dst, "_report.json"), "w", encoding="utf-8") as fp:
    json.dump(report, fp, ensure_ascii=False, indent=1)
print("완료:", len(files), "개")
