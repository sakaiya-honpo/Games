"""
ゲームプレイ自動記録・プレイメモ生成ツール

バックグラウンドでスクリーンキャプチャ＋Windows OCR を行い、
差分テキストをプレイメモとして自動保存する。
セッション終了後に Ollama 経由でAAR生成（任意）。
"""

import os
import sys
import datetime

_BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
_SAVE_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "ゲームAAR")


def _write_error_log(msg: str) -> None:
    log_path = os.path.join(_BASE_DIR, "error.log")
    try:
        with open(log_path, "a", encoding="utf-8") as _f:
            _f.write(f"\n[{datetime.datetime.now()}]\n{msg}\n")
    except Exception:
        pass


try:
    import subprocess
    import tempfile
    import threading
    import difflib
    import traceback
    import tkinter as tk
    from tkinter import ttk, scrolledtext, filedialog, messagebox
    import mss
    from PIL import Image
    import pygetwindow as gw
    import ollama
    os.makedirs(_SAVE_DIR, exist_ok=True)
except Exception as _e:
    import traceback as _tb
    _write_error_log(_tb.format_exc())
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _root = _tk.Tk()
        _root.withdraw()
        _mb.showerror(
            "起動エラー",
            f"モジュールの読み込みに失敗しました。\n"
            f"以下のファイルを確認してください:\n{os.path.join(_BASE_DIR, 'error.log')}\n\n"
            f"エラー: {_e}"
        )
    except Exception:
        pass
    sys.exit(1)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
CAPTURE_INTERVAL = 10
SIMILARITY_THRESHOLD = 0.95
OCR_LANG = "ja"
DEFAULT_MODEL = "qwen2.5:7b"
WINDOW_ALL = "（全画面）"

AAR_PROMPT_TEMPLATE = """\
以下はゲームプレイ中に画面テキストを定期的にOCRで読み取り、差分のみを時系列順に記録したプレイログです。

--- プレイログ開始 ---
{log}
--- プレイログ終了 ---

このログをもとに、以下の形式でAfter Action Review（AAR）をMarkdown形式で日本語で作成してください。

# AAR レポート

## セッション概要
（プレイしたゲーム・状況・時間帯などを簡潔にまとめる）

## 主要なイベント・ターン経過
（重要な出来事・決断・転換点を箇条書きで時系列にまとめる）

## 良かった点（Sustain）
（うまくいった戦略・判断を具体的に挙げる）

## 改善点（Improve）
（次回に改善すべき点・失敗した判断を具体的に挙げる）

## 次回への教訓
（今後のプレイに活かせる学びをまとめる）
"""


# ---------------------------------------------------------------------------
# スクリーンキャプチャ（mss: フリッカーなし）
# ---------------------------------------------------------------------------
def capture_screen(region=None) -> Image.Image:
    with mss.mss() as sct:
        mon = (
            {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
            if region else sct.monitors[1]
        )
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


# ---------------------------------------------------------------------------
# Windows OCR（PowerShell subprocess 経由）
# ---------------------------------------------------------------------------
_PS_OCR_SCRIPT = """\
Add-Type -AssemblyName System.Runtime.WindowsRuntime
function Await($t) {
    $a = [System.WindowsRuntimeSystemExtensions]::AsTask($t)
    $a.Wait() | Out-Null; $a.Result
}
[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
    [Windows.Globalization.Language]::new($env:OCR_LANG))
if (-not $engine) { Write-Error 'OCR engine unavailable'; exit 1 }
$stream = [System.IO.File]::OpenRead($env:IMG_PATH)
try {
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream))
    $bmp = Await ($dec.GetSoftwareBitmapAsync())
    (Await ($engine.RecognizeAsync($bmp))).Text
} finally { $stream.Close() }
"""


def ocr_image(pil_image) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        pil_image.save(tmp_path, "PNG")
        env = os.environ.copy()
        env["IMG_PATH"] = tmp_path
        env["OCR_LANG"] = OCR_LANG
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_OCR_SCRIPT],
            capture_output=True, text=True, timeout=30, env=env,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: コンソールウィンドウを非表示
        )
        if r.returncode != 0:
            _write_error_log(f"OCR stderr: {r.stderr}")
        return r.stdout.strip()
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def check_ocr_available() -> None:
    """起動時チェック: 日本語 OCR が使用可能か確認。失敗時は例外を送出。"""
    test_img = Image.new("RGB", (10, 10), color=(255, 255, 255))
    result = ocr_image(test_img)
    if result is None:
        raise RuntimeError("Windows OCR（日本語）を初期化できませんでした。")


# ---------------------------------------------------------------------------
# ウィンドウ一覧
# ---------------------------------------------------------------------------
def get_window_titles() -> list[str]:
    try:
        titles = [t for t in gw.getAllTitles() if t.strip()]
        seen: set[str] = set()
        unique = []
        for t in titles:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return [WINDOW_ALL] + unique
    except Exception:
        return [WINDOW_ALL]


def get_window_region(title: str) -> tuple[int, int, int, int] | None:
    if title == WINDOW_ALL:
        return None
    try:
        wins = gw.getWindowsWithTitle(title)
        if wins:
            w = wins[0]
            if w.width > 0 and w.height > 0:
                return (w.left, w.top, w.width, w.height)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# キャプチャ・OCRワーカー
# ---------------------------------------------------------------------------
class CaptureWorker:
    def __init__(self, log_callback=None, window_title: str = WINDOW_ALL,
                 save_dir: str | None = None):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_text = ""
        self.log_lines: list[str] = []
        self._lock = threading.Lock()
        self._log_callback = log_callback
        self._window_title = window_title
        self._save_dir = save_dir or _SAVE_DIR
        self._memo_path: str | None = None

    def start(self):
        self._stop_event.clear()
        os.makedirs(self._save_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._memo_path = os.path.join(self._save_dir, f"PlayMemo_{ts}.txt")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)

    def get_log(self) -> str:
        with self._lock:
            return "\n".join(self.log_lines)

    def get_memo_path(self) -> str | None:
        return self._memo_path

    def _run(self):
        while not self._stop_event.is_set():
            try:
                region = get_window_region(self._window_title)
                screenshot = capture_screen(region)
                current_text = ocr_image(screenshot)

                if self._is_new_content(current_text):
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    entry = f"[{timestamp}]\n{current_text}"
                    with self._lock:
                        self.log_lines.append(entry)
                        self._append_to_file(entry)
                    if self._log_callback:
                        self._log_callback(entry)

            except Exception as e:
                if self._log_callback:
                    self._log_callback(f"[ERROR] キャプチャ中にエラー: {e}")

            self._stop_event.wait(timeout=CAPTURE_INTERVAL)

    def _is_new_content(self, text: str) -> bool:
        if not text.strip():
            return False
        if not self._last_text:
            self._last_text = text
            return True
        ratio = difflib.SequenceMatcher(None, self._last_text, text).ratio()
        if ratio >= SIMILARITY_THRESHOLD:
            return False
        self._last_text = text
        return True

    def _append_to_file(self, entry: str):
        if not self._memo_path:
            return
        try:
            with open(self._memo_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AAR生成・保存（任意）
# ---------------------------------------------------------------------------
def generate_aar(log_text: str, model: str = DEFAULT_MODEL) -> str:
    prompt = AAR_PROMPT_TEMPLATE.format(log=log_text)
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response["message"]["content"]


def save_aar(aar_text: str, save_dir: str | None = None) -> str:
    if save_dir is None:
        save_dir = _SAVE_DIR
    os.makedirs(save_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(save_dir, f"AAR_{timestamp}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(aar_text)
    return filepath


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class AARToolApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ゲームプレイ記録ツール")
        self.root.resizable(True, True)

        self.worker: CaptureWorker | None = None
        self.save_dir = tk.StringVar(value=_SAVE_DIR)
        self.model_name = tk.StringVar(value=DEFAULT_MODEL)
        self.window_title = tk.StringVar(value=WINDOW_ALL)
        self.status = tk.StringVar(value="OCR確認中...")
        self.is_running = False

        self._build_ui()
        threading.Thread(target=self._check_ocr, daemon=True).start()
        threading.Thread(target=self._load_ollama_models, daemon=True).start()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        cfg_frame = ttk.LabelFrame(self.root, text="設定")
        cfg_frame.pack(fill="x", **pad)

        ttk.Label(cfg_frame, text="Ollamaモデル:").grid(row=0, column=0, sticky="w", **pad)
        self.model_combo = ttk.Combobox(cfg_frame, textvariable=self.model_name, width=30)
        self.model_combo.grid(row=0, column=1, sticky="w", **pad)
        ttk.Button(cfg_frame, text="↻", width=3,
                   command=lambda: threading.Thread(
                       target=self._load_ollama_models, daemon=True).start()
                   ).grid(row=0, column=2, **pad)

        ttk.Label(cfg_frame, text="キャプチャ対象:").grid(row=1, column=0, sticky="w", **pad)
        self.window_combo = ttk.Combobox(cfg_frame, textvariable=self.window_title, width=30)
        self.window_combo["values"] = get_window_titles()
        self.window_combo.grid(row=1, column=1, sticky="w", **pad)
        ttk.Button(cfg_frame, text="↻", width=3,
                   command=self._refresh_windows).grid(row=1, column=2, **pad)

        ttk.Label(cfg_frame, text="保存先フォルダ:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(cfg_frame, textvariable=self.save_dir, width=40).grid(
            row=2, column=1, sticky="ew", **pad)
        ttk.Button(cfg_frame, text="参照...", command=self._browse_dir).grid(row=2, column=2, **pad)
        cfg_frame.columnconfigure(1, weight=1)

        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.pack(fill="x", **pad)

        self.start_btn = ttk.Button(ctrl_frame, text="▶ 記録開始",
                                    command=self.start_recording, width=16, state="disabled")
        self.start_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(ctrl_frame, text="■ 記録停止",
                                   command=self.stop_recording, width=16, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.aar_btn = ttk.Button(ctrl_frame, text="✦ AAR生成（任意）",
                                  command=self.generate_aar_action, width=18, state="disabled")
        self.aar_btn.pack(side="left", padx=4)

        ttk.Label(ctrl_frame, textvariable=self.status, foreground="gray").pack(
            side="left", padx=12)

        log_frame = ttk.LabelFrame(self.root, text="キャプチャログ（差分のみ・自動保存）")
        log_frame.pack(fill="both", expand=True, **pad)

        self.log_area = scrolledtext.ScrolledText(
            log_frame, wrap="word", height=18, state="disabled", font=("Consolas", 9))
        self.log_area.pack(fill="both", expand=True, padx=4, pady=4)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=8, pady=2)

    def _check_ocr(self):
        self.root.after(0, self.progress.start)
        try:
            check_ocr_available()
            self.root.after(0, lambda: self.status.set("停止中"))
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
        except Exception as e:
            _write_error_log(traceback.format_exc())
            msg = (
                "Windows OCR（日本語）が使用できません。\n\n"
                f"エラー: {e}\n\n"
                "Windowsの設定 → 時刻と言語 → 言語 →\n"
                "「日本語」の言語パックがインストールされているか確認してください。"
            )
            self.root.after(0, lambda: self.status.set("OCR利用不可"))
            self.root.after(0, lambda: messagebox.showerror("OCRエラー", msg))
        finally:
            self.root.after(0, self.progress.stop)

    def _load_ollama_models(self):
        try:
            result = ollama.list()
            models = [m.model for m in result.models]
            if not models:
                return
            def _update():
                self.model_combo["values"] = models
                if self.model_name.get() not in models:
                    self.model_name.set(models[0])
            self.root.after(0, _update)
        except Exception:
            pass

    def _refresh_windows(self):
        titles = get_window_titles()
        self.window_combo["values"] = titles
        if self.window_title.get() not in titles:
            self.window_title.set(WINDOW_ALL)

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.save_dir.get())
        if d:
            self.save_dir.set(d)

    def _append_log(self, text: str):
        def _update():
            self.log_area.config(state="normal")
            self.log_area.insert("end", text + "\n\n")
            self.log_area.see("end")
            self.log_area.config(state="disabled")
        self.root.after(0, _update)

    def start_recording(self):
        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.aar_btn.config(state="disabled")
        self.window_combo.config(state="disabled")
        self.status.set("記録中...")

        self.log_area.config(state="normal")
        self.log_area.delete("1.0", "end")
        self.log_area.config(state="disabled")

        title = self.window_title.get()
        self._append_log(
            f"[システム] 記録開始 ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
            f"\nキャプチャ対象: {title}"
        )
        self.worker = CaptureWorker(
            log_callback=self._append_log,
            window_title=title,
            save_dir=self.save_dir.get() or _SAVE_DIR,
        )
        self.worker.start()

    def stop_recording(self):
        if not self.worker:
            return

        self.stop_btn.config(state="disabled")
        self.status.set("停止中...")

        def _finish():
            self.worker.stop()
            log_text = self.worker.get_log()
            memo_path = self.worker.get_memo_path()

            if not log_text.strip():
                self.root.after(0, lambda: messagebox.showwarning(
                    "ログなし",
                    "キャプチャログが空です。\n"
                    "OCRが正常に動作しているか debug.bat で確認してください。\n"
                    f"詳細は {os.path.join(_BASE_DIR, 'error.log')} を確認してください。"
                ))
            else:
                self.root.after(0, lambda: messagebox.showinfo(
                    "保存完了", f"プレイメモを保存しました:\n{memo_path}"))

            self.root.after(0, self._reset_ui_stopped)

        threading.Thread(target=_finish, daemon=True).start()

    def generate_aar_action(self):
        if not self.worker:
            return
        log_text = self.worker.get_log()
        if not log_text.strip():
            messagebox.showwarning("ログなし", "プレイメモが空のためAAR生成できません。")
            return

        self.aar_btn.config(state="disabled")
        self.status.set("AAR生成中（LLM処理）...")
        self.progress.start()

        def _gen():
            try:
                aar_text = generate_aar(log_text, model=self.model_name.get())
                filepath = save_aar(aar_text, save_dir=self.save_dir.get() or _SAVE_DIR)
                self.root.after(0, lambda: messagebox.showinfo(
                    "完了", f"AARを保存しました:\n{filepath}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "エラー", f"AAR生成中にエラー:\n{e}"))
            finally:
                self.root.after(0, self.progress.stop)
                self.root.after(0, lambda: self.aar_btn.config(state="normal"))
                self.root.after(0, lambda: self.status.set("停止中"))

        threading.Thread(target=_gen, daemon=True).start()

    def _reset_ui_stopped(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.aar_btn.config(state="normal")
        self.window_combo.config(state="normal")
        self.status.set("停止中")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()
    root.minsize(640, 520)
    app = AARToolApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (
        app.worker.stop() if app.worker and app.is_running else None,
        root.destroy()
    ))
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _write_error_log(traceback.format_exc())
