"""PDF 개인정보 삭제 앱 — 웹 화면(pywebview) + 파이썬 엔진"""
import os, sys, json, threading, subprocess, traceback

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


class Api:
    def __init__(self):
        self.win = None
        self.running = False
        self.logs = []

    # ---------------- 상태 ----------------
    def categories(self):
        return {"all": CATEGORIES, "default": sorted(DEFAULT_ON)}

    # ---------------- 파일 선택 ----------------
    def pick_files(self):
        r = self.win.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                        file_types=("PDF 파일 (*.pdf)",))
        return self.expand(list(r or []))

    def pick_folder(self):
        r = self.win.create_file_dialog(webview.FOLDER_DIALOG)
        return self.expand(list(r or []))

    def pick_dst(self):
        r = self.win.create_file_dialog(webview.FOLDER_DIALOG)
        return (r or [None])[0]

    def expand(self, paths):
        """폴더는 안의 PDF로 풀고, 파일은 그대로. [{path, name}] 반환"""
        out = []
        for p in paths:
            p = p.strip("{}")
            if os.path.isdir(p):
                cands = [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith(".pdf")]
            elif p.lower().endswith(".pdf") and os.path.isfile(p):
                cands = [p]
            else:
                continue
            out += [{"path": os.path.abspath(c), "name": os.path.basename(c)} for c in cands]
        return out

    def default_dst(self, first_path):
        base = os.path.dirname(first_path)
        return os.path.join(os.path.dirname(base), os.path.basename(base) + "_삭제본")

    # ---------------- 실행 ----------------
    def run(self, jobs, dst, ocr):
        """jobs: [{path, cats:[...]}]"""
        if self.running:
            return {"ok": False, "msg": "이미 실행 중입니다."}
        if not jobs:
            return {"ok": False, "msg": "PDF 파일을 먼저 추가하세요."}
        if not dst:
            return {"ok": False, "msg": "저장 폴더를 정하세요."}
        dst = os.path.abspath(dst)
        if any(os.path.dirname(j["path"]) == dst for j in jobs):
            return {"ok": False, "msg": "원본이 있는 폴더를 저장 폴더로 쓸 수 없습니다."}
        os.makedirs(dst, exist_ok=True)
        self.running = True
        threading.Thread(target=self._work, args=(jobs, dst, ocr), daemon=True).start()
        return {"ok": True}

    def _emit(self, fn, payload):
        self.win.evaluate_js(f"{fn}({json.dumps(payload, ensure_ascii=False)})")

    def _log(self, s):
        self.logs.append(s)
        self._emit("onLog", s)

    def _work(self, jobs, dst, ocr):
        report, used = {}, set()
        for n, j in enumerate(jobs, 1):
            path, cats = j["path"], set(j["cats"])
            stem0, ext0 = os.path.splitext(os.path.basename(path))
            f = f"{stem0}_삭제본{ext0}"          # 원본과 구별되는 파일명
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
        self.running = False
        self._log(f"완료. 저장 위치: {dst}")
        self._emit("onAllDone", {"dst": dst})

    # ---------------- 드래그 앤 드롭 (파이썬 쪽 리스너로만 전체 경로를 받을 수 있음) ----------------
    def attach_drop(self):
        try:
            el = self.win.dom.get_element("#drop")
            el.events.drop += self._on_drop
        except Exception:
            self._log("드롭 리스너 등록 실패\n" + traceback.format_exc())

    def _on_drop(self, e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
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
        return self.logs


if __name__ == "__main__":
    try:
        api = Api()
        api.win = webview.create_window("PDF 개인 정보 제거기", UI, js_api=api, width=1060, height=720,
                                        min_size=(900, 620), background_color="#f3efe6")
        api.win.events.loaded += api.attach_drop
        webview.start(debug=False)
    except Exception:
        _crash(traceback.format_exc()); sys.exit(1)
