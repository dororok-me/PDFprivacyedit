"""PDF 개인정보 삭제 앱 — 웹 화면(pywebview) + 파이썬 엔진"""
import os, re, sys, json, threading, subprocess, traceback

def _crash(msg):
    """창 없는 exe에서 오류가 나면 파일로 남기고 알림창을 띄움"""
    try:
        d = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
        p = os.path.join(d, "오류기록.txt")
        with open(p, "a", encoding="utf-8") as fp:
            fp.write("=" * 60 + "\n" + msg + "\n")
    except Exception:
        p = "(기록 실패)"
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, f"앱을 시작할 수 없습니다.\n\n{msg[-1500:]}\n\n기록: {p}", "PDF 개인 정보 제거기", 0x10)

try:
    import webview
except Exception:
    _crash(traceback.format_exc()); sys.exit(1)

if getattr(sys, "frozen", False):
    BASE = sys._MEIPASS
    sys.path.insert(0, BASE)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
from engine import redact_file, CATEGORIES, DEFAULT_ON

UI = os.path.join(BASE, "ui", "index.html")

# 저장 파일명 규칙. {이름}=원본 파일 이름(확장자 제외), {번호}=목록 순번(001, 002, …)
DEFAULT_NAME = "{이름}_삭제본"
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def clean_pattern(pattern):
    pattern = _BAD_CHARS.sub("", (pattern or "").strip())
    if not pattern:
        pattern = DEFAULT_NAME
    if "{이름}" not in pattern and "{번호}" not in pattern:   # 규칙에 변하는 부분이 없으면 순번을 붙여 겹치지 않게
        pattern += "_{번호}"
    return pattern


def make_name(pattern, path, n, total):
    stem, ext = os.path.splitext(os.path.basename(path))
    width = max(3, len(str(total)))
    name = pattern.replace("{이름}", stem).replace("{번호}", str(n).zfill(width))
    return name + (ext if ext.lower() == ".pdf" else ".pdf")


# pywebview 6는 js_api 객체의 공개 속성(밑줄 없는 이름)을 모두 재귀적으로 훑어 JS 스텁을 만든다.
# 창 객체(win)를 공개 속성으로 두면 win.native(.NET Form) 아래 수천 개 속성을 비UI 스레드에서 건드려
# 창이 "응답 없음"이 되므로, 창·상태는 반드시 밑줄(_) 이름으로 둔다.
_FD = getattr(webview, "FileDialog", None)
OPEN_DIALOG = _FD.OPEN if _FD else webview.OPEN_DIALOG
FOLDER_DIALOG = _FD.FOLDER if _FD else webview.FOLDER_DIALOG


class Api:
    def __init__(self):
        self._win = None
        self._running = False
        self._logs = []

    # ---------------- 상태 ----------------
    def categories(self):
        return {"all": CATEGORIES, "default": sorted(DEFAULT_ON), "name_pattern": DEFAULT_NAME}

    # ---------------- 파일 선택 ----------------
    def pick_files(self):
        r = self._win.create_file_dialog(OPEN_DIALOG, allow_multiple=True,
                                        file_types=("PDF 파일 (*.pdf)",))
        return self.expand(list(r or []))

    def pick_folder(self):
        """폴더 하나를 고르면 그 폴더 바로 안의 PDF만 (하위 폴더 제외). {folder, items} 반환"""
        r = self._win.create_file_dialog(FOLDER_DIALOG)
        self._log(f"폴더 선택 대화상자 결과: {r!r}")
        folder = self._one_path(r)
        if not folder or not os.path.isdir(folder):
            return {"folder": None, "items": []}
        items = self.expand([folder])
        self._log(f"폴더 '{folder}' 안의 PDF {len(items)}개")
        return {"folder": folder, "items": items}

    def pick_dst(self):
        r = self._win.create_file_dialog(FOLDER_DIALOG)
        return self._one_path(r)

    @staticmethod
    def _one_path(r):
        """대화상자 결과(튜플/리스트/문자열/None)에서 경로 하나만 안전하게 꺼낸다"""
        if not r:
            return None
        if isinstance(r, str):
            return r
        return r[0] if len(r) else None

    def expand(self, paths):
        """폴더는 그 폴더 바로 안의 PDF로 풀고(하위 폴더는 안 봄), 파일은 그대로. [{path, name, dir}] 반환"""
        out = []
        if isinstance(paths, str):          # 문자열 하나가 오면 글자 단위로 쪼개지지 않게
            paths = [paths]
        for p in paths:
            if not isinstance(p, str):
                continue
            p = p.strip("{}")
            if os.path.isdir(p):
                cands = [os.path.join(p, f) for f in sorted(os.listdir(p))
                         if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(p, f))]
            elif p.lower().endswith(".pdf") and os.path.isfile(p):
                cands = [p]
            else:
                continue
            for c in cands:
                c = os.path.abspath(c)
                out.append({"path": c, "name": os.path.basename(c), "dir": os.path.dirname(c)})
        return out

    def default_dst(self, first_path):
        base = os.path.dirname(first_path)
        return os.path.join(os.path.dirname(base), os.path.basename(base) + "_삭제본")

    # ---------------- 실행 ----------------
    def run(self, jobs, dst, ocr, name_pattern=DEFAULT_NAME):
        """jobs: [{path, cats:[...]}], name_pattern: 저장 파일명 규칙 ({이름}=원본 이름, {번호}=순번)"""
        if self._running:
            return {"ok": False, "msg": "이미 실행 중입니다."}
        if not jobs:
            return {"ok": False, "msg": "PDF 파일을 먼저 추가하세요."}
        if not dst:
            return {"ok": False, "msg": "저장 폴더를 정하세요."}
        missing = [j["path"] for j in jobs if not os.path.isfile(j["path"])]
        if missing:
            return {"ok": False, "msg": f"찾을 수 없는 파일이 있습니다: {os.path.basename(missing[0])}"}
        dst = os.path.abspath(dst)
        if any(os.path.dirname(j["path"]) == dst for j in jobs):
            return {"ok": False, "msg": "원본이 있는 폴더를 저장 폴더로 쓸 수 없습니다."}
        name_pattern = clean_pattern(name_pattern)
        os.makedirs(dst, exist_ok=True)
        dirs = sorted({os.path.dirname(j["path"]) for j in jobs})
        self._log(f"실행: 파일 {len(jobs)}개 (원본 폴더 {len(dirs)}곳: {'; '.join(dirs)}) → {dst}  파일명 규칙: {name_pattern}")
        self._running = True
        threading.Thread(target=self._work, args=(jobs, dst, ocr, name_pattern), daemon=True).start()
        return {"ok": True}

    def _emit(self, fn, payload):
        self._win.evaluate_js(f"{fn}({json.dumps(payload, ensure_ascii=False)})")

    def _log(self, s):
        self._logs.append(s)
        self._emit("onLog", s)

    def _work(self, jobs, dst, ocr, name_pattern=DEFAULT_NAME):
        report, used = {}, set()
        for n, j in enumerate(jobs, 1):
            path, cats = j["path"], set(j["cats"])
            f = make_name(name_pattern, path, n, len(jobs))
            if f in used:
                stem, ext = os.path.splitext(f); k = 2
                while f"{stem}({k}){ext}" in used: k += 1
                f = f"{stem}({k}){ext}"
            used.add(f)
            try:
                hits, sources = redact_file(path, os.path.join(dst, f), cats, ocr=ocr)
                report[f] = {"원본": path, "pages": {str(k + 1): v for k, v in sources.items()},
                             "hits": [h.as_dict() for h in hits]}
                warn = any(v == "none" for v in sources.values())
                self._emit("onFileDone", {"path": path, "hits": len(hits), "warn": warn, "n": n, "total": len(jobs)})
                src = {"text": 0, "ocr": 0, "none": 0}
                for v in sources.values(): src[v] = src.get(v, 0) + 1
                bycat = {}
                for h in hits: bycat[CATEGORIES[h.category]] = bycat.get(CATEGORIES[h.category], 0) + 1
                detail = ", ".join(f"{k} {v}" for k, v in bycat.items()) or "탐지 없음"
                self._log(f"[{n}/{len(jobs)}] {f} → {len(hits)}곳 삭제 ({detail})  "
                          f"페이지: 텍스트 {src['text']}, OCR {src['ocr']}, 없음 {src['none']}  항목: {','.join(sorted(cats))}"
                          + ("  ⚠ 텍스트 없음(OCR 실패)" if warn else ""))
            except Exception:
                self._emit("onFileDone", {"path": path, "hits": 0, "error": True, "n": n, "total": len(jobs)})
                self._log(f"[{n}/{len(jobs)}] {f} ✖ 오류\n{traceback.format_exc()}")
        with open(os.path.join(dst, "_report.json"), "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=1)
        self._running = False
        self._log(f"완료. 저장 위치: {dst}")
        self._emit("onAllDone", {"dst": dst})

    # ---------------- 드래그 앤 드롭 (파이썬 쪽 리스너로만 전체 경로를 받을 수 있음) ----------------
    def attach_drop(self):
        try:
            el = self._win.dom.get_element("#drop")
            el.events.drop += self._on_drop
        except Exception:
            self._log("드롭 리스너 등록 실패\n" + traceback.format_exc())

    def _on_drop(self, e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        self._log(f"드롭된 경로: {paths!r}")
        items = self.expand(paths)
        self._emit("onDropped", {"items": items, "raw": len(files)})

    # ---------------- 기타 ----------------
    def open_folder(self, d):
        if not d or not os.path.isdir(d):
            return False
        if sys.platform == "win32": os.startfile(d)
        elif sys.platform == "darwin": subprocess.Popen(["open", d])
        else: subprocess.Popen(["xdg-open", d])
        return True

    def get_logs(self):
        return list(self._logs)


if __name__ == "__main__":
    try:
        api = Api()
        api._win = webview.create_window("PDF 개인 정보 제거기", UI, js_api=api, width=1060, height=720,
                                        min_size=(900, 620), background_color="#f3efe6")
        api._win.events.loaded += api.attach_drop
        webview.start(debug=False)
    except Exception:
        _crash(traceback.format_exc()); sys.exit(1)
