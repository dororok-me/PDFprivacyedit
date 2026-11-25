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


HEADER_ORDER = ["학기", "교과", "과목", "학점수", "원점수", "성취도", "석차등급", "비고"]
KNOWN_SUBJ = ["사회(역사포함)/도덕", "사회(역사/도덕포함)", "사회(역사/도덕 포함)", "기술·가정/정보", "기술가정/정보",
              "예술(음악/미술)", "예술(음악)", "예술(미술)", "국어", "영어", "수학", "과학", "사회", "역사", "도덕",
              "체육", "음악", "미술", "정보", "한문", "선택", "교양"]


def _vlines(page, y0, y1):
    """y0~y1 구간을 지나는 세로 괘선 x 좌표 (표 칸 경계)"""
    xs = set()
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l" and abs(it[1].x - it[2].x) < 1:
                ya, yb = sorted((it[1].y, it[2].y))
                if ya <= y0 and yb >= y1: xs.add(round(it[1].x))
            elif it[0] == "re":
                r = it[1]
                if r.y0 <= y0 and r.y1 >= y1:
                    xs.add(round(r.x0)); xs.add(round(r.x1))
    return sorted(xs)


def _find_header(words, page=None):
    """머리글 행 → (머리글 y, {열이름: (x0, x1)}). 열 구성이 양식마다 달라서 있는 열만 써서 경계를 만든다."""
    cols, present = None, None
    for anchor in [w for w in words if w[4].strip() == "학기"]:
        y = (anchor[1] + anchor[3]) / 2
        line = [w for w in words if abs((w[1] + w[3]) / 2 - y) < 6]
        c = {}
        for w in line:
            t = w[4].strip()
            for k in HEADER_ORDER:
                if t.startswith(k) and len(t) <= len(k) + 8 and k not in c:
                    c[k] = w; break
        if all(k in c for k in ("학기", "교과", "과목", "성취도")):
            cols = c; present = [k for k in HEADER_ORDER if k in c]; break
    if cols is None:
        return None
    centers = [(cols[k][0] + cols[k][2]) / 2 for k in present]
    edges = {}
    # 1순위: 표 세로 괘선으로 칸 경계 결정
    if page is not None:
        xs = _vlines(page, cols["학기"][1] + 1, cols["학기"][3] - 1)
        if len(xs) >= len(present) + 1:
            for k, c in zip(present, centers):
                for a, b in zip(xs, xs[1:]):
                    if a <= c < b:
                        edges[k] = (a, b); break
            if len(edges) == len(present):
                return cols["학기"][3], edges
            edges = {}
    # 2순위: 머리글 중심점 사이의 중간값
    for i, k in enumerate(present):
        left = 0 if i == 0 else (centers[i - 1] + centers[i]) / 2
        right = 10000 if i == len(present) - 1 else (centers[i] + centers[i + 1]) / 2
        edges[k] = (left, right)
    return cols["학기"][3], edges


def _col(edges, w):
    cx = (w[0] + w[2]) / 2
    for k, (a, b) in edges.items():
        if a <= cx < b:
            return k
    return None


def _split_subj(text):
    """교과와 과목이 한 단어로 붙은 경우 분리 ("사회(역사포함)/도덕사회" → ("사회(역사포함)/도덕", "사회"))"""
    for k in sorted(KNOWN_SUBJ, key=len, reverse=True):
        if text.startswith(k) and len(text) > len(k):
            return k, text[len(k):]
    return text, ""


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
        hdr = _find_header(words, page)
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
                if c in cells: cells[c].append(w[4])
            s = "".join(cells["학기"])
            if s in ("1", "2"): sem = int(s)
            course = " ".join(cells["과목"]).strip()
            if cells["교과"]:
                subj = "".join(cells["교과"])
                if not course:
                    subj, course = _split_subj(subj)
            ach = "".join(cells["성취도"]).replace(" ", "")
            am = ACH_RE.match(ach)
            if am and sem and subj and course:
                data.setdefault((grade, sem), []).append((subj, course, am.group(1)))
    doc.close()
    return data, name


ATT_COLS = ["결석질병", "결석미인정", "결석기타", "지각질병", "지각미인정", "지각기타",
            "조퇴질병", "조퇴미인정", "조퇴기타", "결과질병", "결과미인정", "결과기타"]
ATT_OUT = [("미인정결석", "결석미인정"), ("미인정지각", "지각미인정"), ("미인정조퇴", "조퇴미인정"), ("미인정결과", "결과미인정")]


def parse_attendance(path):
    """출결상황 표 → {항목: 전 학년 합계} (숫자 없는 칸은 0)"""
    doc = pymupdf.open(path)
    tot = {k: 0 for k in ATT_COLS}
    found = False
    for page in doc:
        words = [w for w in page.get_text("words") if w[4].strip()]
        anchors = [w for w in words if w[4].strip() == "수업일수"]
        if not anchors:
            continue
        hdr = anchors[0]
        rows = _words_by_rows([w for w in words if w[1] > hdr[3] + 5])
        for r in rows:
            first = r["w"][0][4].strip()
            if not re.fullmatch(r"[1-6]", first):
                if r["cy"] > hdr[3] + 15 and found: break   # 표 끝
                continue
            xs = _vlines(page, r["cy"] - 2, r["cy"] + 2)
            cells = list(zip(xs, xs[1:]))
            cells = [c for c in cells if c[1] - c[0] > 3]
            hc = (hdr[0] + hdr[2]) / 2
            idx = next((i for i, c in enumerate(cells) if c[0] <= hc < c[1]), None)
            if idx is None or len(cells) < idx + 13:
                continue
            data_cells = cells[idx + 1: idx + 13]
            found = True
            for k, (a, b) in zip(ATT_COLS, data_cells):
                txt = "".join(w[4] for w in r["w"] if a <= (w[0] + w[2]) / 2 < b)
                m = re.search(r"\d+", txt)
                if m: tot[k] += int(m.group())
        if found:
            break
    doc.close()
    return tot if found else None


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
    head += [n for n, _ in ATT_OUT] + ["미인정 합계"]
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
            att = parse_attendance(p)
            if att is None:
                row += [""] * (len(ATT_OUT) + 1); drow += ["출결표 못 찾음"] + [""] * len(ATT_OUT)
            else:
                vals = [att[k] for _, k in ATT_OUT]
                row += vals + [sum(vals)]
                drow += [f"질병{att[k[:2]+'질병']}/미인정{att[k]}/기타{att[k[:2]+'기타']}" for _, k in ATT_OUT] + [""]
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
    try:
        wb.save(out_path)
    except PermissionError:                      # 엑셀에서 열어 둔 경우 → 시간 붙여 새 이름으로
        import datetime
        stem, ext = os.path.splitext(out_path)
        out_path = f"{stem}_{datetime.datetime.now():%H%M%S}{ext}"
        wb.save(out_path)
    return len(results), errors, out_path
