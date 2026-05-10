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


_CONFIG_FILE = os.path.join(_BASE_DIR, "config.json")
_DEFAULT_HOTKEY: dict = {}  # 未設定: ユーザーが変更ダイアログで設定する


def _load_config() -> dict:
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as _f:
            json.dump(cfg, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _write_error_log(msg: str) -> None:
    log_path = os.path.join(_BASE_DIR, "error.log")
    try:
        with open(log_path, "a", encoding="utf-8") as _f:
            _f.write(f"\n[{datetime.datetime.now()}]\n{msg}\n")
    except Exception:
        pass


try:
    import re
    import json
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

# オプション: sounddevice（ボイスメモ録音）
try:
    import sounddevice as _sd_check  # noqa: F401
    _VOICE_AVAILABLE = True
except Exception:
    _VOICE_AVAILABLE = False

# オプション: pynput（キーボード・マウス グローバルホットキー）
try:
    from pynput import keyboard as _pynput_kb, mouse as _pynput_mouse
    _HOTKEY_AVAILABLE = True
except Exception:
    _HOTKEY_AVAILABLE = False

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
CAPTURE_INTERVAL = 10
SIMILARITY_THRESHOLD = 0.95
VISION_INTERVAL = 45
OCR_LANG = "ja"
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_VISION_MODEL = "moondream"
WINDOW_ALL = "（全画面）"
CAPTURE_MODE_SIMULATION = "simulation"
CAPTURE_MODE_ACTION = "action"
VISION_PROMPT = (
    "このゲーム画面で今何が起きているか、"
    "プレイヤーの状況・場所・重要なイベントを中心に日本語で1〜3文で説明してください。"
    "UIラベルや数値の羅列は無視し、ゲームの状況だけを説明してください。"
)

# ---------------------------------------------------------------------------
# AAR プロンプトテンプレート（3形式）
# ---------------------------------------------------------------------------
_PROMPT_HEADER = """\
以下はゲームプレイ中に画面テキストを定期的にOCRで読み取り、差分のみを時系列順に記録したプレイログです。
OCRは機械読み取りのため文字誤認識・行欠落が含まれることがあります。
ログで明確に確認できる事実のみ記載し、不明・矛盾する点は推測で補完せず「ログ不明」「〇〇と矛盾」と明記してください。

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
# ユーティリティ
# ---------------------------------------------------------------------------
def _format_elapsed(delta) -> str:
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _key_to_pynput_str(key) -> str:
    """pynput Key/KeyCode を設定保存用文字列に変換する。"""
    if _HOTKEY_AVAILABLE:
        Key, KeyCode = _pynput_kb.Key, _pynput_kb.KeyCode
        if isinstance(key, Key):
            name = key.name
            for suffix in ("_l", "_r"):
                if name.endswith(suffix):
                    name = name[:-2]
                    break
            return f"<{name}>"
        if isinstance(key, KeyCode):
            return key.char.lower() if key.char else f"<{key.vk}>"
    return str(key)


def _hotkey_display(keys_str: list[str]) -> str:
    mapping = {"<ctrl>": "Ctrl", "<shift>": "Shift", "<alt>": "Alt",
               "<cmd>": "Win", "<space>": "Space", "<tab>": "Tab",
               "<enter>": "Enter", "<backspace>": "BackSpace"}
    parts = []
    for s in keys_str:
        if s in mapping:
            parts.append(mapping[s])
        else:
            inner = s.strip("<>")
            parts.append(inner.upper() if len(inner) == 1 else inner.capitalize())
    return "+".join(parts)


class VoiceHotkeyListener:
    """pynput を使ってキーボード / マウスのグローバルホットキーを管理する。"""

    def __init__(self):
        self._listener = None

    def start(self, config: dict, callback) -> None:
        self.stop()
        if not _HOTKEY_AVAILABLE or not config.get("type"):
            return
        try:
            if config.get("type") == "mouse":
                self._start_mouse(config.get("button", ""), callback)
            else:
                self._start_keyboard(config.get("keys", []), callback)
        except Exception as e:
            _write_error_log(f"VoiceHotkeyListener.start error: {e}")

    def _start_keyboard(self, keys: list[str], callback) -> None:
        combo = "+".join(keys)
        hk = _pynput_kb.HotKey(_pynput_kb.HotKey.parse(combo), callback)
        listener = _pynput_kb.Listener(
            on_press=lambda k: hk.press(listener.canonical(k)),
            on_release=lambda k: hk.release(listener.canonical(k)),
        )
        self._listener = listener
        listener.start()

    def _start_mouse(self, button_name: str, callback) -> None:
        btn = _pynput_mouse.Button[button_name]
        self._listener = _pynput_mouse.Listener(
            on_click=lambda x, y, b, pressed: callback() if (pressed and b == btn) else None
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


def _capture_hotkey_dialog(root: tk.Tk) -> dict | None:
    """キー / マウスボタン入力待ちダイアログ。設定辞書か None を返す。"""
    if not _HOTKEY_AVAILABLE:
        return None

    result: dict | None = None
    dialog = tk.Toplevel(root)
    dialog.title("ホットキー設定")
    dialog.resizable(False, False)
    dialog.grab_set()
    dialog.focus_set()

    ttk.Label(dialog, text="キー / マウスボタンを押してください",
              font=("", 11)).pack(pady=(16, 4), padx=28)
    hint_var = tk.StringVar(value="入力待ち...")
    ttk.Label(dialog, textvariable=hint_var, foreground="gray",
              font=("", 10)).pack(pady=2, padx=28)
    ttk.Button(dialog, text="キャンセル", command=dialog.destroy).pack(pady=(8, 16))

    pressed_mods: set = set()
    _MODS = {
        _pynput_kb.Key.ctrl, _pynput_kb.Key.ctrl_l, _pynput_kb.Key.ctrl_r,
        _pynput_kb.Key.shift, _pynput_kb.Key.shift_l, _pynput_kb.Key.shift_r,
        _pynput_kb.Key.alt, _pynput_kb.Key.alt_l, _pynput_kb.Key.alt_r,
        _pynput_kb.Key.cmd, _pynput_kb.Key.cmd_l, _pynput_kb.Key.cmd_r,
    }
    kl = ml = None

    def _done(cfg: dict) -> None:
        nonlocal result
        result = cfg
        try:
            if kl:
                kl.stop()
            if ml:
                ml.stop()
        except Exception:
            pass
        root.after(0, dialog.destroy)

    def on_press(key):
        if key in _MODS:
            pressed_mods.add(key)
        else:
            all_keys = [_key_to_pynput_str(k) for k in pressed_mods] + [_key_to_pynput_str(key)]
            display = _hotkey_display(all_keys)
            root.after(0, lambda: hint_var.set(display))
            _done({"type": "keyboard", "keys": all_keys, "display": display})

    def on_release(key):
        pressed_mods.discard(key)

    def on_click(x, y, button, pressed):
        if pressed:
            bname = button.name
            display = f"Mouse {bname.replace('button', 'Button')}"
            _done({"type": "mouse", "button": bname, "display": display})

    kl = _pynput_kb.Listener(on_press=on_press, on_release=on_release)
    ml = _pynput_mouse.Listener(on_click=on_click)
    kl.start()
    ml.start()
    dialog.protocol("WM_DELETE_WINDOW",
                    lambda: (_done({"type": "_cancel"}) if result is None else None,
                             dialog.destroy()))
    dialog.wait_window()
    return result if (result and result.get("type") != "_cancel") else None


def _safe_filename(title: str, max_len: int = 20) -> str:
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', title).strip('_')
    return safe[:max_len] if safe else "unknown"


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
# ウィンドウ直接キャプチャ（Win32 PrintWindow / オーバーレイなし）
# ---------------------------------------------------------------------------
def capture_window_only(title: str) -> Image.Image | None:
    """指定タイトルのウィンドウ内容を PrintWindow で直接取得する。
    mss のような画面コピーと違い、他ウィンドウが重なっていても映り込まない。
    失敗時は None を返す。"""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # pygetwindow の部分一致でHWNDを取得
    hwnd = 0
    try:
        wins = gw.getWindowsWithTitle(title)
        if wins and hasattr(wins[0], "_hWnd"):
            hwnd = wins[0]._hWnd
    except Exception:
        pass
    if not hwnd:
        hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None

    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        return None

    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)

    PW_RENDERFULLCONTENT = 2
    ok = user32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)

    class _BmpInfo(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    bmi = _BmpInfo(biSize=ctypes.sizeof(_BmpInfo), biWidth=w, biHeight=-h,
                   biPlanes=1, biBitCount=32)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)

    if not ok:
        return None
    return Image.frombytes("RGBA", (w, h), buf.raw, "raw", "BGRA").convert("RGB")


def _capture_game(window_title: str) -> Image.Image:
    """指定ウィンドウ専用キャプチャ。失敗時のみ画面領域コピーにフォールバック。"""
    if window_title != WINDOW_ALL:
        img = capture_window_only(window_title)
        if img is not None:
            return img
    region = get_window_region(window_title)
    return capture_screen(region)


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
        return _clean_ocr(r.stdout.strip())
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _clean_ocr(text: str) -> str:
    # Windows OCRは日本語を1文字ずつスペース区切りで出力するため除去
    text = re.sub(
        r'(?<=[぀-鿿＀-￯])\s+(?=[぀-鿿＀-￯])',
        '', text
    )
    text = re.sub(r' {3,}', ' ', text)
    return text.strip()


def describe_image(pil_image: Image.Image, model: str) -> str:
    import base64
    from io import BytesIO
    buf = BytesIO()
    pil_image.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": VISION_PROMPT, "images": [img_b64]}],
    )
    return resp["message"]["content"].strip()


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
    RATE = 16000  # 16kHz: STT最適
    CHANNELS = 1

    def __init__(self, save_dir: str, game_title: str = ""):
        self._save_dir = save_dir
        self._game_title = game_title
        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self._stream = None
        self._start_time: datetime.datetime | None = None

    def start(self) -> None:
        import sounddevice as sd
        self._frames = []
        self._start_time = datetime.datetime.now()
        self._stream = sd.InputStream(
            samplerate=self.RATE,
            channels=self.CHANNELS,
            dtype="int16",
            blocksize=1024,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        with self._lock:
            self._frames.append(bytes(indata))

    def stop(self) -> str | None:
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        with self._lock:
            frames = list(self._frames)
        if frames:
            return self._transcribe_and_save(frames)
        return None

    def _transcribe_and_save(self, frames: list[bytes]) -> str:
        import speech_recognition as sr
        end_time = datetime.datetime.now()
        start_time = self._start_time or end_time
        elapsed = _format_elapsed(end_time - start_time)

        audio_bytes = b"".join(frames)
        recognizer = sr.Recognizer()
        audio_data = sr.AudioData(audio_bytes, self.RATE, 2)
        try:
            text = recognizer.recognize_google(audio_data, language="ja-JP")
        except sr.UnknownValueError:
            text = "（音声を認識できませんでした）"
        except Exception as e:
            text = f"（認識エラー: {e}）"

        os.makedirs(self._save_dir, exist_ok=True)
        ts = start_time.strftime("%Y%m%d_%H%M%S")
        game = _safe_filename(self._game_title) if self._game_title else "unknown"
        path = os.path.join(self._save_dir, f"VoiceMemo_{game}_{ts}.txt")
        ss_ref = f"スクリーンショット/Screenshot_{game}_{ts}.png"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# ボイスメモ\n")
            f.write(f"ゲーム         : {self._game_title or '全画面'}\n")
            f.write(f"開始           : {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"終了           : {end_time.strftime('%Y-%m-%d %H:%M:%S')} (+{elapsed})\n")
            f.write(f"スクリーンショット: {ss_ref}\n\n")
            f.write(text + "\n")
        return path


# ---------------------------------------------------------------------------
# キャプチャ・OCRワーカー
# ---------------------------------------------------------------------------
class CaptureWorker:
    def __init__(self, log_callback=None, window_title: str = WINDOW_ALL,
                 save_dir: str | None = None,
                 mode: str = CAPTURE_MODE_SIMULATION,
                 vision_model: str = DEFAULT_VISION_MODEL):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_text = ""
        self.log_lines: list[str] = []
        self._lock = threading.Lock()
        self._log_callback = log_callback
        self._window_title = window_title
        self._save_dir = save_dir or _SAVE_DIR
        self._memo_path: str | None = None
        self._start_time: datetime.datetime | None = None
        self._mode = mode
        self._vision_model = vision_model

    def start(self) -> None:
        self._stop_event.clear()
        self._start_time = datetime.datetime.now()
        os.makedirs(self._save_dir, exist_ok=True)
        ts = self._start_time.strftime("%Y%m%d_%H%M%S")
        game = (
            "全画面" if self._window_title == WINDOW_ALL
            else _safe_filename(self._window_title)
        )
        self._memo_path = os.path.join(self._save_dir, f"PlayMemo_{game}_{ts}.txt")
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
                screenshot = _capture_game(self._window_title)
                if self._mode == CAPTURE_MODE_ACTION:
                    current_text = describe_image(screenshot, self._vision_model)
                    save = bool(current_text.strip())
                else:
                    current_text = ocr_image(screenshot)
                    save = self._is_new_content(current_text)
                if save:
                    now = datetime.datetime.now()
                    elapsed = _format_elapsed(now - self._start_time) if self._start_time else "00:00"
                    timestamp = now.strftime("%H:%M:%S")
                    entry = f"[{timestamp} +{elapsed}]\n{current_text}"
                    with self._lock:
                        self.log_lines.append(entry)
                        self._append_to_file(entry)
                    if self._log_callback:
                        self._log_callback(entry)
            except Exception as e:
                if self._log_callback:
                    self._log_callback(f"[ERROR] キャプチャ中にエラー: {e}")
            interval = VISION_INTERVAL if self._mode == CAPTURE_MODE_ACTION else CAPTURE_INTERVAL
            self._stop_event.wait(timeout=interval)

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
                 voice_memos: list[str] | None = None,
                 stream_callback=None) -> str:
    tmpl = template or AAR_PROMPT_TEMPLATE
    prompt = tmpl.format(log=log_text)
    if voice_memos:
        voice_block = (
            "\n--- ボイスメモ（プレイヤーの肉声記録・信頼度高） ---\n"
            + "\n\n".join(voice_memos)
            + "\n--- ボイスメモ終了 ---\n"
            "矛盾がある場合はボイスメモの内容をOCRログより優先してください。\n"
        )
        prompt = prompt.replace("--- プレイログ終了 ---\n", "--- プレイログ終了 ---\n" + voice_block)
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
        self.root.title("プレイメモメーカー")
        self.root.resizable(True, True)

        self.worker: CaptureWorker | None = None
        self.voice_recorder: VoiceRecorder | None = None
        self.voice_on = False
        self._voice_memo_paths: list[str] = []
        self._custom_prompt_path: str | None = None
        self._hotkey_listener = VoiceHotkeyListener()
        self._ss_hotkey_listener = VoiceHotkeyListener()
        cfg = _load_config()
        self._hotkey_config: dict = cfg.get("voice_hotkey", dict(_DEFAULT_HOTKEY))
        self._ss_hotkey_config: dict = cfg.get("ss_hotkey", dict(_DEFAULT_HOTKEY))

        self.save_dir = tk.StringVar(value=_SAVE_DIR)
        self.model_name = tk.StringVar(value=DEFAULT_MODEL)
        self.window_title = tk.StringVar(value=WINDOW_ALL)
        self.aar_format = tk.StringVar(value=list(AAR_FORMATS.keys())[0])
        self.capture_mode = tk.StringVar(value=CAPTURE_MODE_SIMULATION)
        self.vision_model_name = tk.StringVar(value=DEFAULT_VISION_MODEL)
        self.hotkey_display = tk.StringVar(
            value=self._hotkey_config.get("display") or "未設定")
        self.ss_hotkey_display = tk.StringVar(
            value=self._ss_hotkey_config.get("display") or "未設定")
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

        ttk.Label(cfg, text="キャプチャモード:").grid(row=4, column=0, sticky="w", **pad)
        mode_frame = ttk.Frame(cfg)
        mode_frame.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
        ttk.Radiobutton(mode_frame, text="シミュレーション（OCR）",
                        variable=self.capture_mode, value=CAPTURE_MODE_SIMULATION,
                        command=self._on_mode_change).pack(side="left")
        ttk.Radiobutton(mode_frame, text="アクション（ビジョンAI）",
                        variable=self.capture_mode, value=CAPTURE_MODE_ACTION,
                        command=self._on_mode_change).pack(side="left", padx=(12, 0))

        ttk.Label(cfg, text="ビジョンモデル:").grid(row=5, column=0, sticky="w", **pad)
        self.vision_model_frame = ttk.Frame(cfg)
        self.vision_model_frame.grid(row=5, column=1, columnspan=2, sticky="w", **pad)
        self.vision_model_combo = ttk.Combobox(
            self.vision_model_frame, textvariable=self.vision_model_name, width=24)
        self.vision_model_combo.pack(side="left")
        self.vision_model_label = ttk.Label(
            self.vision_model_frame, text="（例: moondream, llava:7b）", foreground="gray")
        self.vision_model_label.pack(side="left", padx=(6, 0))

        if _VOICE_AVAILABLE and _HOTKEY_AVAILABLE:
            ttk.Label(cfg, text="ボイスメモキー:").grid(row=6, column=0, sticky="w", **pad)
            hk_frame = ttk.Frame(cfg)
            hk_frame.grid(row=6, column=1, columnspan=2, sticky="w", **pad)
            ttk.Entry(hk_frame, textvariable=self.hotkey_display,
                      state="readonly", width=20).pack(side="left")
            ttk.Button(hk_frame, text="変更...",
                       command=self._change_hotkey).pack(side="left", padx=(4, 0))

        if _HOTKEY_AVAILABLE:
            ttk.Label(cfg, text="スクショキー:").grid(row=7, column=0, sticky="w", **pad)
            ss_hk_frame = ttk.Frame(cfg)
            ss_hk_frame.grid(row=7, column=1, columnspan=2, sticky="w", **pad)
            ttk.Entry(ss_hk_frame, textvariable=self.ss_hotkey_display,
                      state="readonly", width=20).pack(side="left")
            ttk.Button(ss_hk_frame, text="変更...",
                       command=self._change_ss_hotkey).pack(side="left", padx=(4, 0))

        cfg.columnconfigure(1, weight=1)
        self._on_mode_change()

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

        self.ss_btn = ttk.Button(ctrl, text="📷 スクショ",
                                 command=self.take_screenshot, width=12, state="disabled")
        self.ss_btn.pack(side="left", padx=4)

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
    def _on_mode_change(self) -> None:
        is_action = self.capture_mode.get() == CAPTURE_MODE_ACTION
        state = "normal" if is_action else "disabled"
        self.vision_model_combo.config(state=state)
        self.vision_model_label.config(foreground="black" if is_action else "gray")

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
                "「日本語」の言語パックがインストールされているか確認してください。\n\n"
                "アクション（ビジョンAI）モードはOCR不要で使用できます。"
            )
            self.root.after(0, lambda: self.status.set("OCR利用不可（アクションモードは使用可）"))
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: messagebox.showwarning("OCR警告", msg))
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
                self.vision_model_combo["values"] = models
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

    def _change_hotkey(self) -> None:
        cfg = _capture_hotkey_dialog(self.root)
        if cfg:
            self._hotkey_config = cfg
            self.hotkey_display.set(cfg["display"])
            saved = _load_config()
            saved["voice_hotkey"] = cfg
            _save_config(saved)

    def _change_ss_hotkey(self) -> None:
        cfg = _capture_hotkey_dialog(self.root)
        if cfg:
            self._ss_hotkey_config = cfg
            self.ss_hotkey_display.set(cfg["display"])
            saved = _load_config()
            saved["ss_hotkey"] = cfg
            _save_config(saved)

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
        self._voice_memo_paths = []
        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.aar_btn.config(state="disabled")
        self.window_combo.config(state="disabled")
        hints = []
        if _VOICE_AVAILABLE and _HOTKEY_AVAILABLE:
            voice_hint = self._hotkey_config.get("display", "")
            if voice_hint:
                hints.append(f"{voice_hint}: ボイスメモ")
                self._hotkey_listener.start(
                    self._hotkey_config,
                    lambda: self.root.after(0, self.toggle_voice_memo),
                )
        if _HOTKEY_AVAILABLE:
            ss_hint = self._ss_hotkey_config.get("display", "")
            if ss_hint:
                hints.append(f"{ss_hint}: スクショ")
                self._ss_hotkey_listener.start(
                    self._ss_hotkey_config,
                    lambda: self.root.after(0, self.take_screenshot),
                )
        hint_str = "  [" + " / ".join(hints) + "]" if hints else ""
        self.status.set(f"記録中...{hint_str}")
        if _VOICE_AVAILABLE:
            self.voice_btn.config(state="normal")
        self.ss_btn.config(state="normal")

        self.log_area.config(state="normal")
        self.log_area.delete("1.0", "end")
        self.log_area.config(state="disabled")

        title = self.window_title.get()
        self._append_log(
            f"[システム] 記録開始 ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
            f"\nキャプチャ対象: {title}"
        )
        mode = self.capture_mode.get()
        self._append_log(
            f"[システム] モード: {'アクション（ビジョンAI）' if mode == CAPTURE_MODE_ACTION else 'シミュレーション（OCR）'}"
        )
        self.worker = CaptureWorker(
            log_callback=self._append_log,
            window_title=title,
            save_dir=self.save_dir.get() or _SAVE_DIR,
            mode=mode,
            vision_model=self.vision_model_name.get() or DEFAULT_VISION_MODEL,
        )
        self.worker.start()

    def stop_recording(self) -> None:
        if not self.worker:
            return
        self.stop_btn.config(state="disabled")
        self.voice_btn.config(state="disabled")
        self.ss_btn.config(state="disabled")
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
        self._hotkey_listener.stop()
        self._ss_hotkey_listener.stop()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.ss_btn.config(state="disabled")
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
            game_title = self.window_title.get()
            if game_title == WINDOW_ALL:
                game_title = "全画面"
            self.voice_recorder = VoiceRecorder(save_dir, game_title=game_title)
            self.voice_recorder.start()
            self.voice_on = True
            self.voice_btn.config(text="🎙 ボイスメモ: ON", style="Accent.TButton"
                                   if "Accent.TButton" in ttk.Style().theme_names() else "")
            self._append_log("[システム] ボイスメモ録音開始")
            # 録音開始タイミングのスクリーンショットを非同期保存
            self._capture_voice_screenshot(
                save_dir, game_title, self.voice_recorder._start_time)
        except Exception as e:
            messagebox.showerror("ボイスメモエラー", f"録音を開始できませんでした:\n{e}")

    def take_screenshot(self) -> None:
        if not self.worker:
            return
        save_dir = self.save_dir.get() or _SAVE_DIR
        game_title = self.window_title.get()
        if game_title == WINDOW_ALL:
            game_title = "全画面"
        window_title = self.window_title.get()
        ts = datetime.datetime.now()

        def _do():
            try:
                img = _capture_game(window_title)
                ss_dir = os.path.join(save_dir, "スクリーンショット")
                os.makedirs(ss_dir, exist_ok=True)
                game_safe = _safe_filename(game_title)
                fname = f"Screenshot_{game_safe}_{ts.strftime('%Y%m%d_%H%M%S')}.png"
                img.save(os.path.join(ss_dir, fname), "PNG")
                self.root.after(0, lambda: self._append_log(
                    f"[システム] スクリーンショット: スクリーンショット/{fname}"))
            except Exception as e:
                _write_error_log(f"Screenshot capture error: {e}")
        threading.Thread(target=_do, daemon=True).start()

    def _capture_voice_screenshot(self, save_dir: str, game_title: str,
                                   ts: datetime.datetime) -> None:
        window_title = self.window_title.get()

        def _do():
            try:
                img = _capture_game(window_title)
                ss_dir = os.path.join(save_dir, "スクリーンショット")
                os.makedirs(ss_dir, exist_ok=True)
                game_safe = _safe_filename(game_title)
                ts_str = ts.strftime("%Y%m%d_%H%M%S")
                fname = f"Screenshot_{game_safe}_{ts_str}.png"
                img.save(os.path.join(ss_dir, fname), "PNG")
                self.root.after(0, lambda: self._append_log(
                    f"[システム] スクリーンショット: スクリーンショット/{fname}"))
            except Exception as e:
                _write_error_log(f"Screenshot capture error: {e}")
        threading.Thread(target=_do, daemon=True).start()

    def _stop_voice_memo(self) -> None:
        self.voice_on = False
        self.voice_btn.config(text="🎙 ボイスメモ: OFF")
        if self.voice_recorder:
            def _save():
                path = self.voice_recorder.stop()
                if path:
                    self._voice_memo_paths.append(path)
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

        voice_contents: list[str] = []
        for vp in list(self._voice_memo_paths):
            try:
                with open(vp, encoding="utf-8") as f:
                    voice_contents.append(f.read().strip())
            except Exception:
                pass

        def _gen():
            try:
                aar_text = generate_aar(
                    log_text,
                    model=self.model_name.get(),
                    template=template,
                    voice_memos=voice_contents or None,
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
