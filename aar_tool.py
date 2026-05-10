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
    import wave
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

# オプション: pyaudio（ボイスメモ機能）
try:
    import pyaudio
    _VOICE_AVAILABLE = True
except Exception:
    _VOICE_AVAILABLE = False

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
CAPTURE_INTERVAL = 10
SIMILARITY_THRESHOLD = 0.95
OCR_LANG = "ja"
DEFAULT_MODEL = "qwen2.5:7b"
WINDOW_ALL = "（全画面）"

# ---------------------------------------------------------------------------
# AAR プロンプトテンプレート（3形式）
# ---------------------------------------------------------------------------
_PROMPT_HEADER = """\
以下はゲームプレイ中に画面テキストを定期的にOCRで読み取り、差分のみを時系列順に記録したプレイログです。

--- プレイログ開始 ---
{log}
--- プレイログ終了 ---

"""

AAR_PROMPT_TEMPLATE = _PROMPT_HEADER + """\
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

REPLAY_PROMPT_TEMPLATE = _PROMPT_HEADER + """\
このログをもとに、プレイヤー視点の攻略リプレイレポートをMarkdown形式で日本語で作成してください。
攻略情報としての価値を重視し、戦略・思考プロセスを詳細に分析してください。

# リプレイレポート

## セッション概要
（プレイしたゲーム・状況・使用戦略の概要）

## 各フェーズの判断と思考プロセス
（各ターン・局面での意思決定の根拠を詳細に分析）

## 効果的だった戦略・戦術
（うまくいった判断を具体的に挙げ、なぜ機能したかを分析）

## 失敗した判断・改善すべき点
（次回に改善すべき具体的な行動とその理由）

## 次回攻略への具体的なアドバイス
（今後のプレイに直接活用できる攻略情報をまとめる）
"""

ROLEPLAY_PROMPT_TEMPLATE = _PROMPT_HEADER + """\
このログをもとに、ゲーム内のキャラクターの視点で書かれた一人称の日記・手記・回想録を
Markdown形式で日本語で作成してください。
ゲーム世界の固有名詞・世界観を活かし、物語として読み応えある内容にしてください。

# 手記

## 記録
（キャラクターの感情・内面描写を交えながら、出来事をドラマチックかつ詩的に一人称で記述する）
"""

HISTORICAL_PROMPT_TEMPLATE = _PROMPT_HEADER + """\
このログをもとに、第三者の歴史家・ドキュメンタリー研究者の視点で、
この戦役・作戦・出来事を記述した歴史書・ドキュメンタリー形式のAARを
Markdown形式で日本語で作成してください。

# 歴史的記録

## 序文・背景
（この作戦・戦役の歴史的背景と文脈）

## 経緯と展開
（出来事の原因・経緯・結果を論理的・俯瞰的に整理）

## 重要な転換点の分析
（意思決定の歴史的意義と影響を客観的に評価）

## 後世への教訓
（歴史的観点からの分析と評価）
"""

AAR_FORMATS: dict[str, str | None] = {
    "リプレイ形式（攻略）": REPLAY_PROMPT_TEMPLATE,
    "ロールプレイ（RP）形式": ROLEPLAY_PROMPT_TEMPLATE,
    "歴史書・ドキュメンタリー形式": HISTORICAL_PROMPT_TEMPLATE,
    "カスタム...": None,
}


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
# Windows OCR（ocr_helper.exe 経由 / .NET Framework 4.8）
# ---------------------------------------------------------------------------
def _ocr_exe() -> str:
    return os.path.join(_BASE_DIR, "ocr_helper.exe")


def ocr_image(pil_image) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        pil_image.save(tmp_path, "PNG")
        r = subprocess.run(
            [_ocr_exe(), tmp_path, OCR_LANG],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=0x08000000,
        )
        if r.returncode != 0:
            _write_error_log(f"OCR error: {r.stderr}")
        return r.stdout.strip()
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def check_ocr_available() -> None:
    exe = _ocr_exe()
    if not os.path.exists(exe):
        raise RuntimeError(f"ocr_helper.exe が見つかりません:\n{exe}")
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
# ボイスレコーダー（pyaudio / オプション）
# ---------------------------------------------------------------------------
class VoiceRecorder:
    CHUNK = 1024
    CHANNELS = 1
    RATE = 44100

    def __init__(self, save_dir: str):
        self._save_dir = save_dir
        self._frames: list[bytes] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pa = None
        self._stream = None
        self._sample_size = 2

    def start(self) -> None:
        import pyaudio as _pa
        self._frames = []
        self._stop_event.clear()
        self._pa = _pa.PyAudio()
        self._sample_size = self._pa.get_sample_size(_pa.paInt16)
        self._stream = self._pa.open(
            format=_pa.paInt16,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK,
        )
        self._thread = threading.Thread(target=self._record, daemon=True)
        self._thread.start()

    def stop(self) -> str | None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
        if self._frames:
            return self._save()
        return None

    def _record(self) -> None:
        while not self._stop_event.is_set():
            try:
                data = self._stream.read(self.CHUNK, exception_on_overflow=False)
                self._frames.append(data)
            except Exception:
                break

    def _save(self) -> str:
        os.makedirs(self._save_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._save_dir, f"VoiceMemo_{ts}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self._sample_size)
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(self._frames))
        return path


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

    def start(self) -> None:
        self._stop_event.clear()
        os.makedirs(self._save_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._memo_path = os.path.join(self._save_dir, f"PlayMemo_{ts}.txt")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)

    def get_log(self) -> str:
        with self._lock:
            return "\n".join(self.log_lines)

    def get_memo_path(self) -> str | None:
        return self._memo_path

    def _run(self) -> None:
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

    def _append_to_file(self, entry: str) -> None:
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
def generate_aar(log_text: str, model: str = DEFAULT_MODEL,
                 template: str | None = None,
                 stream_callback=None) -> str:
    tmpl = template or AAR_PROMPT_TEMPLATE
    prompt = tmpl.format(log=log_text)
    if stream_callback:
        full = ""
        for chunk in ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        ):
            piece = chunk["message"]["content"]
            full += piece
            stream_callback(piece)
        return full
    resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
    return resp["message"]["content"]


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
        self.voice_recorder: VoiceRecorder | None = None
        self.voice_on = False
        self._custom_prompt_path: str | None = None

        self.save_dir = tk.StringVar(value=_SAVE_DIR)
        self.model_name = tk.StringVar(value=DEFAULT_MODEL)
        self.window_title = tk.StringVar(value=WINDOW_ALL)
        self.aar_format = tk.StringVar(value=list(AAR_FORMATS.keys())[0])
        self.status = tk.StringVar(value="OCR確認中...")
        self.is_running = False

        self._build_ui()
        threading.Thread(target=self._check_ocr, daemon=True).start()
        threading.Thread(target=self._load_ollama_models, daemon=True).start()

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # ── 設定フレーム ───────────────────────────────────────────────
        cfg = ttk.LabelFrame(self.root, text="設定")
        cfg.pack(fill="x", **pad)

        ttk.Label(cfg, text="Ollamaモデル:").grid(row=0, column=0, sticky="w", **pad)
        self.model_combo = ttk.Combobox(cfg, textvariable=self.model_name, width=28)
        self.model_combo.grid(row=0, column=1, sticky="w", **pad)
        ttk.Button(cfg, text="↻", width=3,
                   command=lambda: threading.Thread(
                       target=self._load_ollama_models, daemon=True).start()
                   ).grid(row=0, column=2, **pad)

        ttk.Label(cfg, text="AARフォーマット:").grid(row=1, column=0, sticky="w", **pad)
        fmt_frame = ttk.Frame(cfg)
        fmt_frame.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        self.format_combo = ttk.Combobox(
            fmt_frame, textvariable=self.aar_format,
            values=list(AAR_FORMATS.keys()), state="readonly", width=28)
        self.format_combo.pack(side="left")
        self.format_combo.bind("<<ComboboxSelected>>", self._on_format_change)
        self.prompt_file_btn = ttk.Button(
            fmt_frame, text="ファイル選択...", command=self._browse_prompt, state="disabled")
        self.prompt_file_btn.pack(side="left", padx=(4, 0))

        ttk.Label(cfg, text="キャプチャ対象:").grid(row=2, column=0, sticky="w", **pad)
        self.window_combo = ttk.Combobox(cfg, textvariable=self.window_title, width=28)
        self.window_combo["values"] = get_window_titles()
        self.window_combo.grid(row=2, column=1, sticky="w", **pad)
        ttk.Button(cfg, text="↻", width=3,
                   command=self._refresh_windows).grid(row=2, column=2, **pad)

        ttk.Label(cfg, text="保存先フォルダ:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(cfg, textvariable=self.save_dir, width=38).grid(
            row=3, column=1, sticky="ew", **pad)
        ttk.Button(cfg, text="参照...", command=self._browse_dir).grid(row=3, column=2, **pad)
        cfg.columnconfigure(1, weight=1)

        # ── コントロールフレーム ────────────────────────────────────────
        ctrl = ttk.Frame(self.root)
        ctrl.pack(fill="x", **pad)

        self.start_btn = ttk.Button(ctrl, text="▶ 記録開始",
                                    command=self.start_recording, width=14, state="disabled")
        self.start_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(ctrl, text="■ 記録停止",
                                   command=self.stop_recording, width=14, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.voice_btn = ttk.Button(ctrl, text="🎙 ボイスメモ: OFF",
                                    command=self.toggle_voice_memo, width=18,
                                    state="disabled" if not _VOICE_AVAILABLE else "disabled")
        self.voice_btn.pack(side="left", padx=4)

        self.aar_btn = ttk.Button(ctrl, text="✦ AAR生成（任意）",
                                  command=self.generate_aar_action, width=18, state="disabled")
        self.aar_btn.pack(side="left", padx=4)

        ttk.Label(ctrl, textvariable=self.status, foreground="gray").pack(
            side="left", padx=12)

        # ── ノートブック（ログ / AAR プレビュー）───────────────────────
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)

        log_tab = ttk.Frame(self.nb)
        self.nb.add(log_tab, text="キャプチャログ（差分・自動保存）")
        self.log_area = scrolledtext.ScrolledText(
            log_tab, wrap="word", height=16, state="disabled", font=("Consolas", 9))
        self.log_area.pack(fill="both", expand=True, padx=4, pady=4)

        aar_tab = ttk.Frame(self.nb)
        self.nb.add(aar_tab, text="AAR プレビュー")
        self.aar_preview = scrolledtext.ScrolledText(
            aar_tab, wrap="word", height=16, state="disabled", font=("Consolas", 9))
        self.aar_preview.pack(fill="both", expand=True, padx=4, pady=4)

        # ── プログレスバー ────────────────────────────────────────────
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=8, pady=2)

    # ------------------------------------------------------------------
    # 設定ハンドラ
    # ------------------------------------------------------------------
    def _on_format_change(self, _event=None) -> None:
        is_custom = self.aar_format.get() == "カスタム..."
        self.prompt_file_btn.config(state="normal" if is_custom else "disabled")

    def _browse_prompt(self) -> None:
        path = filedialog.askopenfilename(
            title="プロンプトファイルを選択",
            filetypes=[("テキスト / Markdown", "*.txt *.md"), ("すべて", "*.*")],
        )
        if path:
            self._custom_prompt_path = path

    def _get_prompt_template(self) -> str | None:
        fmt = self.aar_format.get()
        if fmt == "カスタム...":
            if not self._custom_prompt_path:
                messagebox.showwarning("未選択", "カスタムプロンプトファイルを選択してください。")
                return None
            try:
                with open(self._custom_prompt_path, encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                messagebox.showerror("読み込みエラー", f"プロンプトファイルの読み込みに失敗しました:\n{e}")
                return None
        return AAR_FORMATS.get(fmt, AAR_PROMPT_TEMPLATE)

    # ------------------------------------------------------------------
    # OCR / Ollama 初期化
    # ------------------------------------------------------------------
    def _check_ocr(self) -> None:
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

    def _load_ollama_models(self) -> None:
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

    def _refresh_windows(self) -> None:
        titles = get_window_titles()
        self.window_combo["values"] = titles
        if self.window_title.get() not in titles:
            self.window_title.set(WINDOW_ALL)

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.save_dir.get())
        if d:
            self.save_dir.set(d)

    # ------------------------------------------------------------------
    # ログ表示ヘルパー
    # ------------------------------------------------------------------
    def _append_log(self, text: str) -> None:
        def _upd():
            self.log_area.config(state="normal")
            self.log_area.insert("end", text + "\n\n")
            self.log_area.see("end")
            self.log_area.config(state="disabled")
        self.root.after(0, _upd)

    def _append_aar_preview(self, text: str) -> None:
        def _upd():
            self.aar_preview.config(state="normal")
            self.aar_preview.insert("end", text)
            self.aar_preview.see("end")
            self.aar_preview.config(state="disabled")
        self.root.after(0, _upd)

    # ------------------------------------------------------------------
    # 記録開始 / 停止
    # ------------------------------------------------------------------
    def start_recording(self) -> None:
        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.aar_btn.config(state="disabled")
        self.window_combo.config(state="disabled")
        self.status.set("記録中...")
        if _VOICE_AVAILABLE:
            self.voice_btn.config(state="normal")

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

    def stop_recording(self) -> None:
        if not self.worker:
            return
        self.stop_btn.config(state="disabled")
        self.voice_btn.config(state="disabled")
        self.status.set("停止中...")

        # ボイスメモが ON なら停止して保存
        if self.voice_on:
            self._stop_voice_memo()

        def _finish():
            self.worker.stop()
            log_text = self.worker.get_log()
            memo_path = self.worker.get_memo_path()
            if not log_text.strip():
                self.root.after(0, lambda: messagebox.showwarning(
                    "ログなし",
                    "キャプチャログが空です。\n"
                    f"詳細は {os.path.join(_BASE_DIR, 'error.log')} を確認してください。"
                ))
            else:
                self.root.after(0, lambda: messagebox.showinfo(
                    "保存完了", f"プレイメモを保存しました:\n{memo_path}"))
            self.root.after(0, self._reset_ui_stopped)

        threading.Thread(target=_finish, daemon=True).start()

    def _reset_ui_stopped(self) -> None:
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.aar_btn.config(state="normal")
        self.window_combo.config(state="normal")
        self.status.set("停止中")

    # ------------------------------------------------------------------
    # ボイスメモ
    # ------------------------------------------------------------------
    def toggle_voice_memo(self) -> None:
        if not self.voice_on:
            self._start_voice_memo()
        else:
            self._stop_voice_memo()

    def _start_voice_memo(self) -> None:
        try:
            save_dir = self.save_dir.get() or _SAVE_DIR
            self.voice_recorder = VoiceRecorder(save_dir)
            self.voice_recorder.start()
            self.voice_on = True
            self.voice_btn.config(text="🎙 ボイスメモ: ON", style="Accent.TButton"
                                   if "Accent.TButton" in ttk.Style().theme_names() else "")
            self._append_log("[システム] ボイスメモ録音開始")
        except Exception as e:
            messagebox.showerror("ボイスメモエラー", f"録音を開始できませんでした:\n{e}")

    def _stop_voice_memo(self) -> None:
        self.voice_on = False
        self.voice_btn.config(text="🎙 ボイスメモ: OFF")
        if self.voice_recorder:
            def _save():
                path = self.voice_recorder.stop()
                if path:
                    self.root.after(0, lambda: self._append_log(
                        f"[システム] ボイスメモ保存: {os.path.basename(path)}"))
            threading.Thread(target=_save, daemon=True).start()
            self.voice_recorder = None

    # ------------------------------------------------------------------
    # AAR生成（ストリーミング表示）
    # ------------------------------------------------------------------
    def generate_aar_action(self) -> None:
        if not self.worker:
            return
        log_text = self.worker.get_log()
        if not log_text.strip():
            messagebox.showwarning("ログなし", "プレイメモが空のためAAR生成できません。")
            return
        template = self._get_prompt_template()
        if template is None:
            return

        # AAR プレビュータブをクリアして切り替え
        self.aar_preview.config(state="normal")
        self.aar_preview.delete("1.0", "end")
        self.aar_preview.config(state="disabled")
        self.nb.select(1)

        self.aar_btn.config(state="disabled")
        self.status.set("AAR生成中（LLM処理）...")
        self.progress.start()

        def _gen():
            try:
                aar_text = generate_aar(
                    log_text,
                    model=self.model_name.get(),
                    template=template,
                    stream_callback=self._append_aar_preview,
                )
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


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main() -> None:
    root = tk.Tk()
    root.minsize(680, 560)
    app = AARToolApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (
        app.worker.stop() if app.worker and app.is_running else None,
        app.voice_recorder.stop() if app.voice_recorder else None,
        root.destroy()
    ))
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _write_error_log(traceback.format_exc())
