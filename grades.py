"""
생기부 PDF에서 교과학습발달상황(7번) 표의 성취도(A~E)를 뽑아 엑셀로 저장
- 2·3학년 1·2학기 × 국어·영어·사회(사회 과목이 없으면 역사 과목)
- 접수번호는 파일명에서 첫 번째 숫자 덩어리를 읽음 (예: 012_홍길동.pdf → 12)
"""
import os, re
import pymupdf

GRADES = (2, 3)
SEMS = (1, 2)
GRADE_RE = re.compile(r"\[\s*(\d)\s*학년\s*\]")
ACH_RE = re.compile(r"^([A-E])(\(\d+\))?$")          # A(235), B
NUM_RE = re.compile(r"\d+")


def _words_by_rows(words, tol=3.0):
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        for r in rows:
            if abs(r["cy"] - cy) <= tol:
                r["w"].append(w); break
        else:
            rows.append({"cy": cy, "w": [w]})
    for r in rows:
        r["w"].sort(key=lambda w: w[0])
    return rows


def _find_header(words):
    """머리글(학기/교과/과목/성취도) 위치 → 열 경계. 없으면 None"""
    cols = {}
    for w in words:
        t = w[4].strip()
        if t.startswith("원점수"): t = "원점수"
        if t in ("학기", "교과", "과목", "학점수", "원점수", "성취도", "석차등급") and t not in cols:
            cols[t] = w
    if not all(k in cols for k in ("학기", "교과", "과목", "성취도")):
        return None
    y = cols["학기"][3]
    def cx(k): return (cols[k][0] + cols[k][2]) / 2
    # 각 열의 좌우 경계 = 이웃 열 중심과의 중간
    order = ["학기", "교과", "과목", "성취도"]
    mids = [cx(k) for k in order]
    edges = {}
    edges["학기"] = (0, (mids[0] + mids[1]) / 2)
    edges["교과"] = ((mids[0] + mids[1]) / 2, (mids[1] + mids[2]) / 2)
    nxt = cx("학점수") if "학점수" in cols else (cx("원점수") if "원점수" in cols else cx("성취도") - 80)
    edges["과목"] = ((mids[1] + mids[2]) / 2, (mids[2] + nxt) / 2)
    left = cx("원점수") if "원점수" in cols else (cx("학점수") if "학점수" in cols else cx("성취도") - 60)
    right = cx("석차등급") if "석차등급" in cols else cx("성취도") + 40
    edges["성취도"] = ((left + cx("성취도")) / 2, (cx("성취도") + right) / 2)
    return y, edges


def _col(edges, w):
    cx = (w[0] + w[2]) / 2
    for k, (a, b) in edges.items():
        if a <= cx < b:
            return k
    return None


def parse_pdf(path):
    """→ {(학년, 학기): [(교과, 과목, 성취도), ...]}, 성명"""
    doc = pymupdf.open(path)
    data, grade, sem, subj = {}, None, None, None
    name = ""
    for page in doc:
        words = [w for w in page.get_text("words") if w[4].strip()]
        if not name:
            m = re.search(r"성명\s*[:：]\s*(\S+)", page.get_text())
            if m: name = m.group(1)
        text = page.get_text()
        if "교과학습발달상황" not in text and grade is None:
            continue
        hdr = _find_header(words)
        rows = _words_by_rows(words)
        for r in rows:
            line = " ".join(w[4] for w in r["w"])
            gm = GRADE_RE.search(line.replace(" ", ""))
            if gm:
                grade, sem, subj = int(gm.group(1)), None, None
                continue
            if hdr is None or grade is None or r["cy"] < hdr[0]:
                continue
            if "이수학점" in line or "교과학습발달상황" in line or line.startswith("학기 교과"):
                continue
            edges = hdr[1]
            cells = {"학기": [], "교과": [], "과목": [], "성취도": []}
            for w in r["w"]:
                c = _col(edges, w)
                if c: cells[c].append(w[4])
            s = "".join(cells["학기"])
            if s in ("1", "2"): sem = int(s)
            if cells["교과"]: subj = "".join(cells["교과"])
            ach = "".join(cells["성취도"]).replace(" ", "")
            am = ACH_RE.match(ach)
            course = " ".join(cells["과목"]).strip()
            if am and sem and subj and course:
                data.setdefault((grade, sem), []).append((subj, course, am.group(1)))
    doc.close()
    return data, name


def pick(rows, key):
    """교과 목록에서 국/영/사 성취도 고르기 → (과목, 성취도) or ("", "")"""
    if key == "국":
        c = [r for r in rows if r[0].startswith("국어")]
    elif key == "영":
        c = [r for r in rows if r[0].startswith("영어")]
    else:
        soc = [r for r in rows if r[0].startswith("사회") or r[0].startswith("통합사회")]
        c = [r for r in soc if "사회" in r[1]]
        if not c:
            c = [r for r in soc if re.search(r"역사|한국사|세계사|동아시아", r[1])]
        if not c:
            c = soc
    if not c:
        return "", ""
    return " / ".join(r[1] for r in c), " / ".join(r[2] for r in c)


def recv_no(filename):
    m = NUM_RE.search(os.path.basename(filename))
    return int(m.group()) if m else None


def extract_to_xlsx(paths, out_path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = "성취도"
    det = wb.create_sheet("과목명")
    head = ["접수번호", "파일명", "성명"]
    for g in GRADES:
        for s in SEMS:
            for k in ("영", "국", "사"):
                head.append(f"{g}-{s} {k}")
    ws.append(head); det.append(head)
    results, errors = [], []
    for p in paths:
        try:
            data, name = parse_pdf(p)
            row = [recv_no(p), os.path.basename(p), name]
            drow = list(row)
            for g in GRADES:
                for s in SEMS:
                    rows = data.get((g, s), [])
                    for k in ("영", "국", "사"):
                        course, ach = pick(rows, k)
                        row.append(ach); drow.append(course)
            results.append((row, drow))
        except Exception as e:
            errors.append(f"{os.path.basename(p)}: {e}")
    results.sort(key=lambda r: (r[0][0] is None, r[0][0] if r[0][0] is not None else 0, r[0][1]))
    for row, drow in results:
        ws.append(row); det.append(drow)
    for sh in (ws, det):
        for c in sh[1]:
            c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor="FFF2CC"); c.alignment = Alignment(horizontal="center")
        sh.column_dimensions["B"].width = 28; sh.column_dimensions["C"].width = 10
        sh.freeze_panes = "D2"
    wb.save(out_path)
    return len(results), errors
