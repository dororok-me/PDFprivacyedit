"""PDF 개인정보 삭제 앱 — GUI (맥/윈도우 공통, Tkinter)"""
import os, sys, json, threading, queue, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# exe로 포장됐을 때 모듈 경로
if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)
from engine import redact_file, CATEGORIES, DEFAULT_ON
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _BASE = TkinterDnD.Tk
except Exception:          # tkinterdnd2 없으면 드래그는 안 되지만 버튼으로는 됨
    TkinterDnD, DND_FILES, _BASE = None, None, tk.Tk

CAT_KEYS = list(CATEGORIES)


class App(_BASE):
    def __init__(self):
        super().__init__()
        self.title("PDF 개인정보 삭제")
        self.geometry("980x560")
        self.minsize(820, 560)
        self.src = tk.StringVar()
        self.dst = tk.StringVar()
        self.files = []                  # 전체 경로 목록
        self.per_file = {}               # 경로 → set(카테고리)  (개별 설정)
        self.global_cats = {k: tk.BooleanVar(value=k in DEFAULT_ON) for k in CAT_KEYS}
        self.file_cats = {k: tk.BooleanVar() for k in CAT_KEYS}
        self.q = queue.Queue()
        self._build()

    # ------------------------------------------------------------ UI
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self); top.pack(fill="x", **pad)

        ttk.Label(top, text="① 원본 PDF 폴더 선택").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.src, width=70).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="선택…", command=self.pick_src).grid(row=0, column=2)

        # 드래그 앤 드롭 박스
        self.drop = tk.Label(top, text="또는 여기에 PDF 파일/폴더를 끌어다 놓으세요\n(클릭하면 파일 선택)",
                             relief="groove", bd=2, height=5, bg="#f4f4f4", fg="#555", cursor="hand2")
        self.drop.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 8))
        self.drop.bind("<Button-1>", lambda e: self.pick_files())
        if DND_FILES:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)
        else:
            self.drop.config(text="클릭해서 PDF 파일 선택 (드래그는 tkinterdnd2 설치 시 가능)")

        ttk.Label(top, text="② 저장 폴더 (없으면 새로 만듦)").grid(row=2, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.dst, width=70).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="선택…", command=self.pick_dst).grid(row=2, column=2)
        top.columnconfigure(1, weight=1)

        mid = ttk.Frame(self); mid.pack(fill="x", **pad)

        # 왼쪽: 파일 목록
        left = ttk.LabelFrame(mid, text="파일 목록 (클릭하면 개별 설정)")
        left.pack(side="left", fill="both", expand=True)
        self.lb = tk.Listbox(left, selectmode="extended", activestyle="none", height=8)
        sb = ttk.Scrollbar(left, command=self.lb.yview); self.lb.config(yscrollcommand=sb.set)
        btns = ttk.Frame(left); btns.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Button(btns, text="선택 제거", command=self.remove_selected).pack(side="left")
        ttk.Button(btns, text="목록 비우기", command=self.clear_files).pack(side="left", padx=4)
        self.lb.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", self.on_select)

        # 오른쪽: 삭제 항목
        right = ttk.Frame(mid); right.pack(side="right", fill="y", padx=(8, 0))
        g = ttk.LabelFrame(right, text="③ 삭제할 정보 — 전체 기본값"); g.pack(side="left", fill="y")
        for k in CAT_KEYS:
            ttk.Checkbutton(g, text=CATEGORIES[k], variable=self.global_cats[k],
                            command=self.refresh_marks).pack(anchor="w")
        ttk.Button(g, text="개별 설정 모두 초기화", command=self.reset_overrides).pack(fill="x", pady=(4, 2))

        self.fbox = ttk.LabelFrame(right, text="선택한 파일 개별 설정"); self.fbox.pack(side="left", fill="y", padx=(8, 0))
        for k in CAT_KEYS:
            ttk.Checkbutton(self.fbox, text=CATEGORIES[k], variable=self.file_cats[k],
                            command=self.apply_file_cats).pack(anchor="w")
        self.flabel = ttk.Label(self.fbox, text="(파일을 클릭하세요)", foreground="gray")
        self.flabel.pack(anchor="w", pady=(2, 0))
        self.set_fbox_state("disabled")

        # 하단: 실행 + 로그
        bot = ttk.Frame(self); bot.pack(fill="x", **pad)
        self.ocr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bot, text="스캔 PDF는 OCR 사용", variable=self.ocr_var).pack(side="left")
        self.run_btn = ttk.Button(bot, text="▶ 개인정보 삭제 실행", command=self.run)
        self.run_btn.pack(side="right")
        ttk.Button(bot, text="저장된 폴더 열기", command=self.open_dst).pack(side="right", padx=6)
        self.log_btn = ttk.Button(bot, text="로그 열기", command=self.toggle_log)
        self.log_btn.pack(side="right", padx=6)
        self.prog = ttk.Progressbar(self); self.prog.pack(fill="x", padx=8)
        self.log = tk.Text(self, height=6, state="disabled", font=("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        self.log_shown = False

    # ------------------------------------------------------------ 폴더
    def open_dst(self):
        d = self.dst.get()
        if not d or not os.path.isdir(d):
            messagebox.showinfo("안내", "아직 저장된 폴더가 없습니다."); return
        if sys.platform == "win32": os.startfile(d)
        elif sys.platform == "darwin": os.system(f'open "{d}"')
        else: os.system(f'xdg-open "{d}"')

    def toggle_log(self):
        if self.log_shown:
            self.log.pack_forget(); self.log_btn.config(text="로그 열기")
        else:
            self.log.pack(fill="both", expand=True, padx=8, pady=(4, 8)); self.log_btn.config(text="로그 닫기")
        self.log_shown = not self.log_shown

    def pick_src(self):
        d = filedialog.askdirectory(title="원본 PDF 폴더 선택")
        if not d: return
        self.src.set(d)
        self.add_paths([d])

    def pick_files(self):
        fs = filedialog.askopenfilenames(title="PDF 파일 선택", filetypes=[("PDF", "*.pdf")])
        if fs: self.add_paths(list(fs))

    def on_drop(self, event):
        self.add_paths(self.tk.splitlist(event.data))

    def add_paths(self, paths):
        added = 0
        for p in paths:
            p = p.strip("{}")
            if os.path.isdir(p):
                cands = [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith(".pdf")]
            elif p.lower().endswith(".pdf"):
                cands = [p]
            else:
                continue
            for c in cands:
                c = os.path.abspath(c)
                if c not in self.files:
                    self.files.append(c); added += 1
        if self.files and not self.dst.get():
            base = os.path.dirname(self.files[0])
            self.dst.set(os.path.join(os.path.dirname(base), os.path.basename(base) + "_삭제본"))
        self.refresh_marks()
        self.logln(f"PDF {added}개 추가 (총 {len(self.files)}개)")

    def remove_selected(self):
        for i in sorted(self.lb.curselection(), reverse=True):
            self.per_file.pop(self.files[i], None); del self.files[i]
        self.refresh_marks(); self.on_select()

    def clear_files(self):
        self.files.clear(); self.per_file.clear(); self.refresh_marks(); self.on_select()

    def pick_dst(self):
        d = filedialog.askdirectory(title="저장 폴더 선택 (새 폴더 만들기 가능)", mustexist=False)
        if d: self.dst.set(d)

    # ------------------------------------------------------------ 카테고리
    def cats_for(self, f):
        return self.per_file.get(f) or {k for k in CAT_KEYS if self.global_cats[k].get()}

    def refresh_marks(self):
        sel = self.lb.curselection()
        self.lb.delete(0, "end")
        for f in self.files:
            mark = "★ " if f in self.per_file else "   "
            self.lb.insert("end", mark + os.path.basename(f))
        for i in sel: self.lb.selection_set(i)

    def set_fbox_state(self, st):
        for c in self.fbox.winfo_children():
            if isinstance(c, ttk.Checkbutton): c.state(["!disabled"] if st == "normal" else ["disabled"])

    def on_select(self, _=None):
        sel = self.lb.curselection()
        if not sel:
            self.set_fbox_state("disabled"); self.flabel.config(text="(파일을 클릭하세요)"); return
        f = self.files[sel[0]]
        cats = self.cats_for(f)
        for k in CAT_KEYS: self.file_cats[k].set(k in cats)
        self.set_fbox_state("normal")
        self.flabel.config(text=f"{len(sel)}개 선택 — 바꾸면 선택한 파일에만 적용")

    def apply_file_cats(self):
        cats = {k for k in CAT_KEYS if self.file_cats[k].get()}
        for i in self.lb.curselection():
            self.per_file[self.files[i]] = cats
        self.refresh_marks()

    def reset_overrides(self):
        self.per_file.clear(); self.refresh_marks(); self.on_select()

    # ------------------------------------------------------------ 실행
    def logln(self, s):
        self.log.config(state="normal"); self.log.insert("end", s + "\n"); self.log.see("end"); self.log.config(state="disabled")

    def run(self):
        dst = self.dst.get()
        if not self.files:
            messagebox.showwarning("확인", "PDF 파일을 먼저 추가하세요."); return
        if not dst:
            messagebox.showwarning("확인", "저장 폴더를 선택하세요."); return
        if any(os.path.dirname(f) == os.path.abspath(dst) for f in self.files):
            messagebox.showwarning("확인", "원본이 있는 폴더를 저장 폴더로 쓸 수 없습니다."); return
        os.makedirs(dst, exist_ok=True)
        self.run_btn.state(["disabled"]); self.prog.config(maximum=len(self.files), value=0)
        jobs = [(f, self.cats_for(f)) for f in self.files]
        threading.Thread(target=self.worker, args=(dst, jobs, self.ocr_var.get()), daemon=True).start()
        self.after(100, self.poll)

    def worker(self, dst, jobs, ocr):
        report, used = {}, set()
        for n, (path, cats) in enumerate(jobs, 1):
            f = os.path.basename(path)
            if f in used:                                   # 다른 폴더의 같은 이름 → 번호 붙임
                stem, ext = os.path.splitext(f); k = 2
                while f"{stem}({k}){ext}" in used: k += 1
                f = f"{stem}({k}){ext}"
            used.add(f)
            try:
                hits, sources = redact_file(path, os.path.join(dst, f), cats, ocr=ocr)
                report[f] = {"원본": path,"pages": {str(k + 1): v for k, v in sources.items()},
                             "hits": [h.as_dict() for h in hits]}
                summary = ", ".join(f"{CATEGORIES[h.category]}" for h in hits[:6]) or "탐지 없음"
                if any(v == "none" for v in sources.values()):
                    summary += "  ⚠ 텍스트 없음(OCR 실패)"
                self.q.put(("log", f"[{n}/{len(jobs)}] {f}  →  {len(hits)}곳 삭제 ({summary})"))
            except Exception:
                self.q.put(("log", f"[{n}/{len(jobs)}] {f}  ✖ 오류\n{traceback.format_exc()}"))
            self.q.put(("prog", n))
        with open(os.path.join(dst, "_report.json"), "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=1)
        self.q.put(("done", dst))

    def poll(self):
        try:
            while True:
                kind, v = self.q.get_nowait()
                if kind == "log": self.logln(v)
                elif kind == "prog": self.prog.config(value=v)
                elif kind == "done":
                    self.run_btn.state(["!disabled"])
                    self.logln(f"완료. 저장 위치: {v}")
                    messagebox.showinfo("완료", f"처리가 끝났습니다.\n\n{v}\n\n_report.json 에서 삭제 내역을 확인할 수 있습니다.")
                    return
        except queue.Empty:
            pass
        self.after(100, self.poll)


if __name__ == "__main__":
    App().mainloop()
