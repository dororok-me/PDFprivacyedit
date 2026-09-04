"""PDF 개인정보 삭제 앱 — 웹 화면(pywebview) + 파이썬 엔진"""
import os, sys, json, threading, subprocess, traceback
import webview

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
            f = os.path.basename(path)
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
                self._log(f"[{n}/{len(jobs)}] {f} → {len(hits)}곳 삭제" + ("  ⚠ 텍스트 없음(OCR 실패)" if warn else ""))
            except Exception:
                self._emit("onFileDone", {"path": path, "hits": 0, "error": True, "n": n, "total": len(jobs)})
                self._log(f"[{n}/{len(jobs)}] {f} ✖ 오류\n{traceback.format_exc()}")
        with open(os.path.join(dst, "_report.json"), "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=1)
        self.running = False
        self._log(f"완료. 저장 위치: {dst}")
        self._emit("onAllDone", {"dst": dst})

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
    api = Api()
    api.win = webview.create_window("PDF개인정보삭제기", UI, js_api=api, width=1060, height=720,
                                    min_size=(900, 620), background_color="#f3efe6")
    webview.start(debug=False)
