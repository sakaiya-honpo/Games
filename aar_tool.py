"""
ゲームプレイ自動記録・AAR生成ツール

バックグラウンドでスクリーンキャプチャ＋OCRを行い、
セッション終了後にOllama経由のローカルLLMでAAR/プレイメモを生成する。
"""

import threading
import difflib
import datetime
import os
import sys
import traceback
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

import pyautogui
import pygetwindow as gw
from paddleocr import PaddleOCR
import ollama

# PyInstaller の onedir ビルドでは実行ファイルのディレクトリを基準にする
_BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))

# AAR・ログの保存先: デスクトップ上の専用フォルダ
_SAVE_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "ゲームAAR")
os.makedirs(_SAVE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
CAPTURE_INTERVAL = 10          # キャプチャ間隔（秒）
SIMILARITY_THRESHOLD = 0.95    # これ以上類似していれば破棄
OCR_LANG = "japan"             # PaddleOCR言語設定
DEFAULT_MODEL = "qwen2.5:7b"   # Ollamaモデル名
SAVE_OCR_LOG = False           # 中間OCRログを保存するか（デバッグ用）
OCR_LOG_PATH = os.path.join(_SAVE_DIR, "ocr_log.txt")
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
（次回に改善すべき点・失敗した判断を具体的に挖げる）

## 次回への教訓
（今後のプレイに活かせる学びをまとめる）
"""


# ---------------------------------------------------------------------------
# OCRエンジン（シングルトン、初期化は初回のみ）
# ---------------------------------------------------------------------------
_ocr_engine: PaddleOCR | None = None
_ocr_lock = threading.Lock()


def get_ocr_engine() -> PaddleOCR:
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang=OCR_LANG, show_log=False)
    return _ocr_engine


def reset_ocr_engine():
    global _ocr_engine
    with _ocr_lock:
        _ocr_engine = None


# ---------------------------------------------------------------------------
# ウィンドウ一覧
# ---------------------------------------------------------------------------
def get_window_titles() -> list[str]:
    try:
        titles = [t for t in gw.getAllTitles() if t.strip()]
        seen = set()
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
    def __init__(self, log_callback=None, window_title: str = WINDOW_ALL):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_text = ""
        self.log_lines: list[str] = []
        self._lock = threading.Lock()
        self._log_callback = log_callback
        self._window_title = window_title

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)

    def get_log(self) -> str:
        with self._lock:
            return "\n".join(self.log_lines)

    def _run(self):
        import numpy as np
        ocr = get_ocr_engine()
        while not self._stop_event.is_set():
            try:
                region = get_window_region(self._window_title)
                screenshot = pyautogui.screenshot(region=region)
                img_array = np.array(screenshot)

                result = ocr.ocr(img_array, cls=True)
                texts = []
                if result and result[0]:
                    for line in result[0]:
                        if line and len(line) >= 2 and line[1]:
                            texts.append(line[1][0])
                current_text = "\n".join(texts)

                if self._is_new_content(current_text):
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    entry = f"[{timestamp}]\n{current_text}"
                    with self._lock:
                        self.log_lines.append(entry)
                        if SAVE_OCR_LOG:
                            self._save_ocr_log(entry)
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

    def _save_ocr_log(self, entry: str):
        with open(OCR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry + "\n\n")


# ---------------------------------------------------------------------------
# AAR生成
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
    filename = f"AAR_{timestamp}.md"
    filepath = os.path.join(save_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(aar_text)
    return filepath


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class AARToolApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ゲームプレイ AAR 生成ツール")
        self.root.resizable(True, True)

        self.worker: CaptureWorker | None = None
        self.save_dir = tk.StringVar(value=_SAVE_DIR)
        self.model_name = tk.StringVar(value=DEFAULT_MODEL)
        self.window_title = tk.StringVar(value=WINDOW_ALL)
        self.status = tk.StringVar(value="OCRエンジン初期化中...")
        self.is_running = False
        self._ocr_ready = False

        self._build_ui()
        threading.Thread(target=self._init_ocr, daemon=True).start()
        threading.Thread(target=self._load_ollama_models, daemon=True).start()

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        cfg_frame = ttk.LabelFrame(self.root, text="設定")
        cfg_frame.pack(fill="x", **pad)

        # Ollamaモデル
        ttk.Label(cfg_frame, text="Ollamaモデル:").grid(row=0, column=0, sticky="w", **pad)
        self.model_combo = ttk.Combobox(cfg_frame, textvariable=self.model_name, width=30)
        self.model_combo.grid(row=0, column=1, sticky="w", **pad)
        ttk.Button(cfg_frame, text="↻", width=3,
                   command=lambda: threading.Thread(target=self._load_ollama_models, daemon=True).start()
                   ).grid(row=0, column=2, **pad)

        # キャプチャ対象ウィンドウ
        ttk.Label(cfg_frame, text="キャプチャ対象:").grid(row=1, column=0, sticky="w", **pad)
        self.window_combo = ttk.Combobox(cfg_frame, textvariable=self.window_title, width=30)
        self.window_combo["values"] = get_window_titles()
        self.window_combo.grid(row=1, column=1, sticky="w", **pad)
        ttk.Button(cfg_frame, text="↻", width=3,
                   command=self._refresh_windows
                   ).grid(row=1, column=2, **pad)

        # 保存先
        ttk.Label(cfg_frame, text="保存先フォルダ:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(cfg_frame, textvariable=self.save_dir, width=40).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(cfg_frame, text="参照...", command=self._browse_dir).grid(row=2, column=2, **pad)
        cfg_frame.columnconfigure(1, weight=1)

        # コントロールフレーム
        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.pack(fill="x", **pad)

        self.start_btn = ttk.Button(ctrl_frame, text="▶ 記録開始", command=self.start_recording,
                                    width=16, state="disabled")
        self.start_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(ctrl_frame, text="■ 記録停止 & AAR生成", command=self.stop_recording,
                                   width=24, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        # OCR初期化失敗時の再試行ボタン（通常は非表示）
        self.retry_btn = ttk.Button(ctrl_frame, text="OCR 再試行", command=self._retry_ocr,
                                    width=12)

        ttk.Label(ctrl_frame, textvariable=self.status, foreground="gray").pack(side="left", padx=12)

        # ログエリア
        log_frame = ttk.LabelFrame(self.root, text="キャプチャログ（差分のみ）")
        log_frame.pack(fill="both", expand=True, **pad)

        self.log_area = scrolledtext.ScrolledText(log_frame, wrap="word", height=18,
                                                   state="disabled", font=("Consolas", 9))
        self.log_area.pack(fill="both", expand=True, padx=4, pady=4)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=8, pady=2)

    # ------------------------------------------------------------------
    # Ollamaモデル一覧取得
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # ウィンドウ一覧更新
    # ------------------------------------------------------------------
    def _refresh_windows(self):
        titles = get_window_titles()
        self.window_combo["values"] = titles
        if self.window_title.get() not in titles:
            self.window_title.set(WINDOW_ALL)

    # ------------------------------------------------------------------
    # OCRエンジン初期化
    # ------------------------------------------------------------------
    def _init_ocr(self):
        self.root.after(0, self.progress.start)
        self.root.after(0, lambda: self.status.set("OCRエンジン初期化中（初回はモデルDL約400MB）..."))
        try:
            get_ocr_engine()
            self._ocr_ready = True
            self.root.after(0, lambda: self.status.set("停止中"))
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: self.retry_btn.pack_forget())
        except Exception as e:
            tb = traceback.format_exc()
            log_path = os.path.join(_SAVE_DIR, "ocr_error.log")
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n[{datetime.datetime.now()}]\n{tb}\n")
            except Exception:
                pass
            msg = (
                f"OCRエンジンの初期化に失敗しました。\n\n"
                f"エラー: {e}\n\n"
                f"詳細ログ: {log_path}\n\n"
                f"初回起動時はインターネット接続が必要です（モデルDL約400MB）。\n"
                f"接続を確認して「OCR 再試行」ボタンを押してください。"
            )
            self.root.after(0, lambda: self.status.set("OCR初期化失敗 — 再試行してください"))
            self.root.after(0, lambda: self.retry_btn.pack(side="left", padx=4))
            self.root.after(0, lambda: messagebox.showerror("OCR初期化エラー", msg))
        finally:
            self.root.after(0, self.progress.stop)

    def _retry_ocr(self):
        reset_ocr_engine()
        self.retry_btn.pack_forget()
        threading.Thread(target=self._init_ocr, daemon=True).start()

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------
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
        self.worker = CaptureWorker(log_callback=self._append_log, window_title=title)
        self.worker.start()

    def stop_recording(self):
        if not self.worker:
            return

        self.stop_btn.config(state="disabled")
        self.status.set("停止中...")

        def _finish():
            self.worker.stop()
            log_text = self.worker.get_log()

            if not log_text.strip():
                self.root.after(0, lambda: messagebox.showwarning(
                    "ログなし", "キャプチャログが空です。AAR生成をスキップします。"))
                self.root.after(0, self._reset_ui)
                return

            self.root.after(0, lambda: self.status.set("AAR生成中（LLM処理）..."))
            self.root.after(0, self.progress.start)

            try:
                aar_text = generate_aar(log_text, model=self.model_name.get())
                filepath = save_aar(aar_text, save_dir=self.save_dir.get() or _SAVE_DIR)
                self.root.after(0, lambda: messagebox.showinfo(
                    "完了", f"AARを保存しました:\n{filepath}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "エラー", f"AAR生成中にエラーが発生しました:\n{e}"))
            finally:
                self.root.after(0, self.progress.stop)
                self.root.after(0, self._reset_ui)

        threading.Thread(target=_finish, daemon=True).start()

    def _reset_ui(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.window_combo.config(state="normal")
        self.status.set("停止中")
        self.worker = None


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
    main()
