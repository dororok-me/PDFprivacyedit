"""
개인정보 삭제 엔진 (PyMuPDF 기반)

- 텍스트 PDF: 글자 좌표를 직접 읽어 탐지
- 스캔 PDF: (선택) Tesseract OCR로 글자 좌표를 얻어 같은 방식으로 탐지
- 삭제: 검은 박스 + 밑에 있는 텍스트/이미지 픽셀까지 완전 삭제 (진짜 redaction)
"""
import re
import pymupdf

# ---------------------------------------------------------------------------
# 카테고리 정의 — GUI 체크박스와 1:1 대응
# ---------------------------------------------------------------------------
CATEGORIES = {
    "name":     "성명(학생·보호자·담임)",
    "rrn":      "주민등록번호",
    "birth":    "생년월일",
    "phone":    "전화번호",
    "email":    "이메일",
    "address":  "주소",
    "photo":    "사진",
    "enroll":   "학적사항(입학·졸업 학교)",
    "class_no": "반/번호",
    "reg_no":   "졸업(학적) 대장 번호",
    "school":   "학교명",
    "footer":   "출력정보(IP/출력자)",
}
DEFAULT_ON = {"name", "rrn", "birth", "phone", "email", "address", "photo", "enroll", "class_no", "footer"}

# 라벨 → 카테고리. 라벨 바로 오른쪽(같은 줄) 값을 지운다.
INLINE_LABELS = {
    "성명": "name", "이름": "name", "학생성명": "name", "담임성명": "name",
    "담임": "name", "보호자": "name", "보호자명": "name", "보호자성명": "name",
    "부": "name", "모": "name", "학부모": "name", "지원자": "name", "지원자명": "name",
    "주민등록번호": "rrn", "주민번호": "rrn",
    "생년월일": "birth",
    "주소": "address", "현주소": "address", "본적": "address",
    "전화": "phone", "전화번호": "phone", "연락처": "phone", "휴대폰": "phone",
    "휴대전화": "phone", "핸드폰": "phone", "집전화": "phone",
    "이메일": "email", "e-mail": "email", "email": "email",
    "반": "class_no", "번호": "class_no",
    "졸업대장번호": "reg_no", "학적번호": "reg_no", "수험번호": "reg_no",
    "학교명": "school", "학교": "school", "출신학교": "school", "출신중학교": "school",
}
# 표의 머리글(세로 열)인 경우: 라벨 아래쪽 같은 열에 있는 값들을 지운다.
COLUMN_LABELS = {"담임성명": "name", "성명": "name", "이름": "name", "보호자": "name",
                 "학과": None, "반": "class_no", "번호": "class_no"}

# 학적사항 칸: 라벨 오른쪽 칸(여러 줄)의 입학·졸업 기록을 통째로 지운다.
ENROLL_LABELS = {"학적사항", "학적"}

# 값 추출을 멈추는 단어(다음 라벨). 라벨 사전의 키 + 몇 가지 구조 단어.
STOP_WORDS = set(INLINE_LABELS) | ENROLL_LABELS | {"성별", "학년", "구분", "학기", "졸업", "입학", "특기사항"}

# 정규식 — 띄어쓰기/하이픈이 섞여도 잡히도록 공백 제거 후 매칭
REGEX = {
    "rrn":   re.compile(r"\d{6}-?[1-8]\d{6}"),
    "phone": re.compile(r"(01[016789]|0[2-6]\d?)-?\d{3,4}-?\d{4}"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    # 본문 속 학년-반 표기: "(1-5맨손체조", "2-6반", "2학년 5반"  (횟수·기간 "1-2회" 등은 제외)
    "class_no": re.compile(r"(?<![\d.])[1-6]-\d{1,2}(?=반)"
                           r"|(?<=\()[1-6]-\d{1,2}(?=[가-힣A-Za-z])(?![회주월일번시명개차])"
                           r"|[1-6]학년\d{1,2}반"),
}
# ○○초등학교 / ○○중학교 / ○○고등학교 — 단어 하나 안에서 찾는다 (앞에 이름이 붙어 있어야 함)
SCHOOL_RE = re.compile(r"[가-힣]{1,12}(?:초등학교|중학교|고등학교)$")
SCHOOL_WORD = {"초등학교", "중학교", "고등학교"}
IP_RE = re.compile(r"\d{1,3}\.\d{1,3}\.[\d*]{1,3}[. ][\d*]{1,3}")


class Hit:
    """탐지된 영역 하나. pad_x: 삭제 박스 가로 여백(글자 일부만 잡았을 때는 0 — 옆 글자 보호)"""
    def __init__(self, page_no, rect, category, text, source, pad_x=None):
        self.page_no, self.rect, self.category, self.text, self.source = \
            page_no, pymupdf.Rect(rect), category, text, source
        self.pad_x = pad_x

    def as_dict(self):
        r = self.rect
        return {"page": self.page_no + 1, "category": self.category, "text": self.text,
                "rect": [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)],
                "source": self.source}


# ---------------------------------------------------------------------------
# 단어 추출 (텍스트 PDF / OCR)
# ---------------------------------------------------------------------------
def _norm(s):
    return s.replace(" ", "").replace("\u3000", "").strip(" :：")


def get_words(page, ocr=True):
    """[(x0,y0,x1,y1,text), ...] — 텍스트가 없으면 OCR 시도"""
    words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words") if w[4].strip()]
    if words:
        return words, "text"
    if ocr:
        try:
            from ocr import ocr_words
            return ocr_words(page), "ocr"
        except Exception as e:  # tesseract 없음 등
            print(f"  [OCR 불가] {e}")
    return [], "none"


def group_lines(words, y_tol=3.0):
    """세로 위치가 비슷한 단어끼리 줄로 묶기 (왼쪽→오른쪽 정렬)"""
    lines = []
    for w in sorted(words, key=lambda w: (round((w[1] + w[3]) / 2), w[0])):
        cy = (w[1] + w[3]) / 2
        for ln in lines:
            if abs(ln["cy"] - cy) <= y_tol:
                ln["words"].append(w)
                break
        else:
            lines.append({"cy": cy, "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w[0])
    return lines


def union(ws):
    return pymupdf.Rect(min(w[0] for w in ws), min(w[1] for w in ws),
                        max(w[2] for w in ws), max(w[3] for w in ws))


def find_cell(page, cx, cy, max_size=300):
    """점 (cx,cy)를 둘러싼 표 칸 — 가장 가까운 상하좌우 선으로 구성"""
    left = right = top = bottom = None
    segs = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                segs.append((it[1], it[2]))
            elif it[0] == "re":
                r = it[1]
                segs += [((r.x0, r.y0), (r.x1, r.y0)), ((r.x0, r.y1), (r.x1, r.y1)),
                         ((r.x0, r.y0), (r.x0, r.y1)), ((r.x1, r.y0), (r.x1, r.y1))]
    for p1, p2 in segs:
        x1_, y1_, x2_, y2_ = p1[0], p1[1], p2[0], p2[1]
        if abs(x1_ - x2_) < 1 and min(y1_, y2_) <= cy <= max(y1_, y2_):      # 세로선
            if x1_ < cx and (left is None or x1_ > left): left = x1_
            if x1_ > cx and (right is None or x1_ < right): right = x1_
        elif abs(y1_ - y2_) < 1 and min(x1_, x2_) <= cx <= max(x1_, x2_):    # 가로선
            if y1_ < cy and (top is None or y1_ > top): top = y1_
            if y1_ > cy and (bottom is None or y1_ < bottom): bottom = y1_
    if None in (left, right, top, bottom):
        return None
    r = pymupdf.Rect(left, top, right, bottom)
    return r if r.width < max_size and r.height < max_size else None


def tight_rect(page, ws, text, source):
    """정규식이 잡은 문자열이 단어 하나의 일부일 때, 그 글자들만의 박스로 좁힌다 (텍스트 PDF만).
    반환: (박스, 좁혔는지 여부)"""
    box = union(ws)
    if source != "text" or len(ws) != 1 or ws[0][4].replace(" ", "") == text:
        return box, False
    try:
        found = [r for r in page.search_for(text, clip=box + (-1, -1, 1, 1)) if r.intersects(box)]
    except Exception:
        found = []
    if not found:
        return box, False
    return union([(r.x0, r.y0, r.x1, r.y1) for r in found]), True


def tight_hit(page, pno, ws, text, cat, source):
    rect, tight = tight_rect(page, ws, text, source)
    return Hit(pno, rect, cat, text, source, pad_x=0 if tight else None)


# 양식 라벨 판별: 본문 문장 속 "학교", "담임" 같은 낱말을 라벨로 오인하지 않기 위한 기준
LABEL_GAP = 20          # 양식에서는 라벨이 앞 단어와 이만큼 이상 떨어져 있다 (본문 띄어쓰기는 최대 ~14pt)
SCHOOL_VALUE_RE = re.compile(r"[가-힣A-Za-z·()]{1,15}(?:학교|초|중|고)")


def enroll_values(page, words, label):
    """'학적사항' 라벨 오른쪽 칸(여러 줄)에 있는 단어들. 선이 없으면(스캔본) 라벨 높이 ±14pt 오른쪽 단어."""
    cell = find_cell(page, (label[0] + label[2]) / 2, (label[1] + label[3]) / 2)
    right = find_cell(page, cell.x1 + 3, (cell.y0 + cell.y1) / 2, max_size=600) if cell else None
    if right:
        return [v for v in words if pymupdf.Rect(v[:4]).intersects(right)
                and (v[0] + v[2]) / 2 > right.x0 and _norm(v[4]) not in ENROLL_LABELS]
    y0, y1 = label[1] - 14, label[3] + 14
    return [v for v in words if v[0] > label[2] and v[3] > y0 and v[1] < y1
            and _norm(v[4]) not in ENROLL_LABELS]


# ---------------------------------------------------------------------------
# 탐지
# ---------------------------------------------------------------------------
def detect_page(page, enabled, ocr=True):
    hits = []
    pno = page.number
    words, source = get_words(page, ocr)
    lines = group_lines(words)

    # 1) 정규식 — 줄 단위, 공백 제거 후 매칭 → 해당 문자에 걸친 단어 박스
    for ln in lines:
        ws = ln["words"]
        # 각 단어의 공백 제거 문자열과 원본 인덱스 매핑
        joined, owner = "", []
        for i, w in enumerate(ws):
            t = w[4].replace(" ", "")
            joined += t
            owner += [i] * len(t)
        for cat, rx in REGEX.items():
            if cat not in enabled:
                continue
            for m in rx.finditer(joined):
                idxs = sorted(set(owner[m.start():m.end()]))
                hits.append(tight_hit(page, pno, [ws[i] for i in idxs], m.group(), cat, source))

    # 1-a) 학교 이름 (○○중학교 등) — 단어 단위. "해운대 중학교"처럼 띄어 쓴 경우 앞 단어까지 묶는다
    if "school" in enabled:
        for ln in lines:
            ws = ln["words"]
            for i, w in enumerate(ws):
                t = w[4].replace(" ", "")
                if SCHOOL_RE.search(t):
                    hits.append(tight_hit(page, pno, [w], SCHOOL_RE.search(t).group(), "school", source))
                elif t in SCHOOL_WORD and i > 0 and re.fullmatch(r"[가-힣]{2,12}", ws[i - 1][4]) \
                        and w[0] - ws[i - 1][2] < 8:
                    hits.append(Hit(pno, union(ws[i - 1:i + 1]), "school", ws[i - 1][4] + " " + t, source))

    # 1-c) 학적사항 칸 (입학·졸업 학교 기록 전체)
    if "enroll" in enabled:
        for w in words:
            if _norm(w[4]) in ENROLL_LABELS:
                vals = enroll_values(page, words, w)
                if vals:
                    hits.append(Hit(pno, union(vals), "enroll", " ".join(v[4] for v in vals), source))

    # 1-b) 출력 정보 줄 (학교명/날짜/IP/담임이름)
    if "footer" in enabled:
        for ln in lines:
            txt = " ".join(w[4] for w in ln["words"])
            if IP_RE.search(txt) or (txt.count("/") >= 3 and re.search(r"\d{4}\.\d{2}\.\d{2}", txt)):
                hits.append(Hit(pno, union(ln["words"]), "footer", txt, source))

    # 2) 라벨 기반 (같은 줄 오른쪽 값)
    multi_label_words = set()      # "졸업 대장 번호"처럼 여러 단어로 된 라벨에 쓰인 단어 (id)
    for ln in lines:
        ws = ln["words"]
        i = 0
        while i < len(ws):
            label = _norm(ws[i][4])
            # "성명 :" 처럼 라벨과 콜론이 붙은 경우 / 두 단어 라벨("졸업 대장 번호") 처리
            key = None
            for span in (3, 2, 1):
                if i + span > len(ws):
                    continue
                cand = _norm("".join(w[4] for w in ws[i:i + span]))
                if cand in INLINE_LABELS:
                    key, span_used = cand, span
                    break
            if key is not None and span_used > 1:
                multi_label_words.update(id(w) for w in ws[i:i + span_used])
            if key is None or INLINE_LABELS[key] not in enabled:
                i += span_used if key else 1
                continue
            cat = INLINE_LABELS[key]
            j = i + span_used
            raw_label = "".join(w[4] for w in ws[i:j]).strip()
            has_colon = raw_label.endswith((":", "：")) or (j < len(ws) and ws[j][4].lstrip().startswith((":", "：")))
            if len(key) <= 3 and not has_colon:
                # "학교", "담임", "반" 같은 짧은 낱말은 본문 문장에도 흔하다.
                # 줄 맨 앞이거나 앞 단어와 크게 떨어져 있고(양식 칸), 그 줄이 문장처럼 빽빽하지 않을 때만 라벨로 본다.
                prev_gap = ws[i][0] - ws[i - 1][2] if i > 0 else LABEL_GAP
                line_max_gap = max((ws[k][0] - ws[k - 1][2] for k in range(1, len(ws))), default=0)
                if prev_gap < LABEL_GAP or not (len(ws) <= 5 or line_max_gap >= LABEL_GAP):
                    i += span_used
                    continue
            # 콜론만 있는 단어는 건너뜀
            while j < len(ws) and _norm(ws[j][4]) == "":
                j += 1
            vals = []
            label_right = ws[i + span_used - 1][2]
            while j < len(ws):
                t = _norm(ws[j][4])
                if t in STOP_WORDS:
                    break
                gap = ws[j][0] - (vals[-1][2] if vals else label_right)
                if gap > 60:          # 표의 다른 칸으로 넘어감
                    break
                vals.append(ws[j])
                j += 1
            if vals and cat == "school" and not has_colon \
                    and not SCHOOL_VALUE_RE.fullmatch(_norm("".join(v[4] for v in vals))):
                vals = []            # "학교 폭력 예방…"처럼 뒤에 오는 말이 학교 이름이 아니면 라벨이 아님
            if vals:
                hits.append(Hit(pno, union(vals), cat, " ".join(v[4] for v in vals), source))
            i = j if vals else i + span_used

    # 3) 표 머리글 아래 세로 열 (담임성명 등) — 라벨 아래 150pt 안, 같은 x 범위
    for w in words:
        key = _norm(w[4])
        if id(w) in multi_label_words:      # "졸업 대장 번호"의 "번호" → 학년 열을 지우지 않도록
            continue
        if key in COLUMN_LABELS and COLUMN_LABELS[key] in enabled:
            # 같은 줄 오른쪽 60pt 안에 값이 있으면 인라인 라벨 → 세로열 규칙 제외
            right = [v for v in words if v is not w and abs((v[1]+v[3])/2 - (w[1]+w[3])/2) < 3
                     and w[2] <= v[0] < w[2] + 60 and _norm(v[4]) != ""]
            if right:
                continue
            x0, x1 = w[0] - 20, w[2] + 20
            numeric_ok = COLUMN_LABELS[key] == "class_no"      # 반/번호 열은 값이 숫자
            cands = sorted((v for v in words
                            if v[1] > w[3] + 2 and v[1] < w[3] + 150 and v[0] < x1 and v[2] > x0),
                           key=lambda v: v[1])
            last_y = w[3]
            for v in cands:
                t = _norm(v[4])
                if t in STOP_WORDS or v[1] - last_y > 30:     # 다음 라벨을 만나거나 표를 벗어나면 끝
                    break
                last_y = v[3]
                if t == "" or re.fullmatch(r"\.+", v[4]) or (not numeric_ok and re.fullmatch(r"[\d.]+", v[4])):
                    continue
                hits.append(Hit(pno, v[:4], COLUMN_LABELS[key], v[4], source))

    # 4) 사진 — "사진" 글자를 둘러싼 칸(선 4개)을 찾아 지우고, 페이지 내 모든 이미지도 지움
    if "photo" in enabled:
        for w in words:
            if _norm(w[4]) == "사진":
                cell = find_cell(page, (w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
                hits.append(Hit(pno, cell or pymupdf.Rect(w[0] - 40, w[1] - 60, w[2] + 40, w[3] + 60),
                                "photo", "사진칸", source))
        for img in page.get_images(full=True):
            for r in page.get_image_rects(img[0]):
                if r.width > 30 and r.height > 30:
                    hits.append(Hit(pno, r, "photo", "이미지", source))

    # 중복 제거 (같은 영역이 여러 규칙에 잡힌 경우)
    uniq = []
    for h in hits:
        if not any(h.rect in u.rect or (h.rect & u.rect).get_area() > 0.8 * h.rect.get_area() for u in uniq):
            uniq.append(h)
    return uniq, source


# ---------------------------------------------------------------------------
# 삭제 적용
# ---------------------------------------------------------------------------
def redact_file(src, dst, enabled=DEFAULT_ON, ocr=True, extra_rects=None, pad=1.5):
    """
    src → dst 로 개인정보 삭제 PDF 저장.
    extra_rects: {page_no: [Rect,...]} 사용자가 미리보기에서 추가한 박스
    반환: (hits 목록, 페이지별 소스 정보)
    """
    doc = pymupdf.open(src)
    all_hits, sources = [], {}
    for page in doc:
        hits, source = detect_page(page, enabled, ocr)
        sources[page.number] = source
        rects = [h.rect + (-(pad if h.pad_x is None else h.pad_x), -pad, (pad if h.pad_x is None else h.pad_x), pad)
                 for h in hits]
        rects += [pymupdf.Rect(r) for r in (extra_rects or {}).get(page.number, [])]
        for r in rects:
            page.add_redact_annot(r & page.rect, fill=(0, 0, 0))
        if rects:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        all_hits += hits
    # 메타데이터(작성자 등)도 제거
    doc.set_metadata({})
    doc.save(dst, garbage=4, deflate=True)
    doc.close()
    return all_hits, sources


def preview_png(src, page_no, hits, zoom=1.5):
    """탐지 결과를 빨간 박스로 그린 미리보기 이미지(bytes)"""
    doc = pymupdf.open(src)
    page = doc[page_no]
    for h in hits:
        if h.page_no == page_no:
            page.draw_rect(h.rect, color=(1, 0, 0), width=1.2)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    data = pix.tobytes("png")
    doc.close()
    return data
