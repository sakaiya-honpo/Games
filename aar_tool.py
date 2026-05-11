"""
プレイメモメーカー
ボイスメモ・スクリーンショット・録画ファイルを統合し、
ローカルLLMでプレイメモを自動生成する。
"""

import os
import sys
import datetime

_BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
_SAVE_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "ゲームAAR")

_CONFIG_FILE = os.path.join(_BASE_DIR, "config.json")
_DEFAULT_HOTKEY: dict = {}


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
    import base64
    import threading
    import traceback
    from io import BytesIO
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
            f"モジュールの読み込みに失敗しました。\n{os.path.join(_BASE_DIR, 'error.log')}\n\nエラー: {_e}"
        )
    except Exception:
        pass
    sys.exit(1)

try:
    import sounddevice as _sd_check  # noqa: F401
    _VOICE_AVAILABLE = True
except Exception:
    _VOICE_AVAILABLE = False

try:
    from pynput import keyboard as _pynput_kb, mouse as _pynput_mouse
    _HOTKEY_AVAILABLE = True
except Exception:
    _HOTKEY_AVAILABLE = False

try:
    from notion_client import Client as _NotionClient
    _NOTION_AVAILABLE = True
except Exception:
    _NOTION_AVAILABLE = False

try:
    import cv2
    _CV2_AVAILABLE = True
except Exception:
    _CV2_AVAILABLE = False

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_VISION_MODEL = "llava:7b"
VIDEO_FPS = 0.2        # 5秒に1フレーム
MAX_FRAMES = 24        # 1クリップあたりの上限
BATCH_SIZE = 8         # LLM 1回あたりのフレーム数
WINDOW_ALL = "（全画面）"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}

MEMO_PROMPT = """\
以下のゲーム画面のスクリーンショット（時系列順）を見て、
何が起きたかを日本語で簡潔に記録してください。
・重要なイベント・場面転換・プレイヤーの行動を中心に
・UIラベルや数値の羅列は不要
・1〜3文程度でまとめてください
"""

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


def _safe_filename(title: str, max_len: int = 20) -> str:
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', title).strip('_')
    return safe[:max_len] if safe else "unknown"


# ---------------------------------------------------------------------------
# ホットキー管理
# ---------------------------------------------------------------------------
class VoiceHotkeyListener:
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
            if kl: kl.stop()
            if ml: ml.stop()
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


# ---------------------------------------------------------------------------
# スクリーンキャプチャ
# ---------------------------------------------------------------------------
def capture_screen(region=None) -> Image.Image:
    with mss.mss() as sct:
        mon = (
            {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
            if region else sct.monitors[1]
        )
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


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


def _capture_game(window_title: str) -> Image.Image:
    region = get_window_region(window_title)
    return capture_screen(region)


# ---------------------------------------------------------------------------
# ボイスレコーダー
# ---------------------------------------------------------------------------
class VoiceRecorder:
    RATE = 16000
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
            samplerate=self.RATE, channels=self.CHANNELS,
            dtype="int16", blocksize=1024, callback=self._callback,
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
        return self._transcribe_and_save(frames) if frames else None

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
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# ボイスメモ\n")
            f.write(f"開始: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"終了: {end_time.strftime('%Y-%m-%d %H:%M:%S')} (+{elapsed})\n\n")
            f.write(text + "\n")
        return path


# ---------------------------------------------------------------------------
# 動画分析エンジン
# ---------------------------------------------------------------------------
class VideoAnalyzer:
    """録画ファイルからフレームを抽出してLLMでプレイメモを生成する。"""

    def __init__(self, vision_model: str = DEFAULT_VISION_MODEL,
                 fps: float = VIDEO_FPS, max_frames: int = MAX_FRAMES):
        self._vision_model = vision_model
        self._fps = fps
        self._max_frames = max_frames

    def extract_frames(self, video_path: str) -> list[Image.Image]:
        if not _CV2_AVAILABLE:
            raise RuntimeError("opencv-python がインストールされていません。\npip install opencv-python")
        cap = cv2.VideoCapture(video_path)
        try:
            video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / video_fps

            interval_sec = 1.0 / self._fps
            target_times = []
            t = 0.0
            while t < duration:
                target_times.append(t)
                t += interval_sec

            if len(target_times) > self._max_frames:
                step = len(target_times) / self._max_frames
                target_times = [target_times[int(i * step)] for i in range(self._max_frames)]

            frames = []
            for t in target_times:
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ret, frame = cap.read()
                if ret:
                    frames.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            return frames
        finally:
            cap.release()

    def _encode(self, img: Image.Image) -> str:
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _describe_batch(self, frames: list[Image.Image]) -> str:
        resp = ollama.chat(
            model=self._vision_model,
            messages=[{
                "role": "user",
                "content": MEMO_PROMPT,
                "images": [self._encode(f) for f in frames],
            }],
        )
        return resp["message"]["content"].strip()

    def analyze_clip(self, video_path: str, progress_cb=None) -> str:
        frames = self.extract_frames(video_path)
        if not frames:
            return "（フレーム抽出失敗）"
        parts = []
        total = (len(frames) + BATCH_SIZE - 1) // BATCH_SIZE
        for i in range(0, len(frames), BATCH_SIZE):
            if progress_cb:
                progress_cb(f"フレーム分析中 {i // BATCH_SIZE + 1}/{total}...")
            parts.append(self._describe_batch(frames[i:i + BATCH_SIZE]))
        return "\n".join(parts)

    def process_session(self, video_paths: list[str], voice_memo_texts: list[str],
                        game_title: str, start_time: datetime.datetime,
                        end_time: datetime.datetime, progress_cb=None) -> str:
        lines = [
            "# プレイメモ",
            f"ゲーム   : {game_title}",
            f"開始     : {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"終了     : {end_time.strftime('%Y-%m-%d %H:%M:%S')} (+{_format_elapsed(end_time - start_time)})",
            f"録画数   : {len(video_paths)} クリップ",
            "",
        ]
        for idx, path in enumerate(sorted(video_paths), 1):
            fname = os.path.basename(path)
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            lines.append(f"## クリップ {idx}  [{mtime.strftime('%H:%M:%S')}]  {fname}")
            if progress_cb:
                progress_cb(f"クリップ {idx}/{len(video_paths)} を分析中...")
            try:
                lines.append(self.analyze_clip(path, progress_cb))
            except Exception as e:
                lines.append(f"（分析失敗: {e}）")
            lines.append("")

        if voice_memo_texts:
            lines.append("## ボイスメモ")
            for vm in voice_memo_texts:
                lines.append(vm.strip())
                lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# プレイメモ保存
# ---------------------------------------------------------------------------
def save_play_memo(text: str, save_dir: str | None = None) -> str:
    if save_dir is None:
        save_dir = _SAVE_DIR
    os.makedirs(save_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(save_dir, f"PlayMemo_{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------------------
# Notion連携
# ---------------------------------------------------------------------------
class NotionExporter:
    _MAX_TEXT = 1900
    _MAX_BLOCKS = 95

    def __init__(self, token: str, database_id: str):
        self._client = _NotionClient(auth=token)
        self._db_id = database_id.strip()

    def test_connection(self) -> str:
        db = self._client.databases.retrieve(self._db_id)
        name = "".join(t.get("plain_text", "") for t in db.get("title", []))
        return name or "(名前なし)"

    def push_memo(self, game_title: str, start_time: datetime.datetime,
                  end_time: datetime.datetime, memo_text: str) -> str:
        elapsed = _format_elapsed(end_time - start_time)
        page = self._client.pages.create(
            parent={"database_id": self._db_id},
            properties={"Name": {"title": [{"text": {"content":
                f"{game_title}  {start_time.strftime('%Y-%m-%d %H:%M')}"
            }}]}},
        )
        page_id = page["id"]
        self._append_blocks(page_id, [{
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content":
                    f"ゲーム: {game_title}\n"
                    f"開始: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"終了: {end_time.strftime('%Y-%m-%d %H:%M:%S')} (+{elapsed})"
                }}],
                "icon": {"emoji": "🎮"},
            },
        }])
        self._append_blocks(page_id, self._log_blocks(memo_text))
        return page_id

    def _para(self, content: str) -> dict:
        return {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
        }

    def _log_blocks(self, text: str) -> list[dict]:
        blocks = []
        chunk = ""
        for line in text.splitlines(keepends=True):
            if len(chunk) + len(line) > self._MAX_TEXT:
                if chunk:
                    blocks.append(self._para(chunk.rstrip()))
                chunk = line
            else:
                chunk += line
        if chunk.strip():
            blocks.append(self._para(chunk.rstrip()))
        return blocks or [self._para("（ログなし）")]

    def _append_blocks(self, page_id: str, blocks: list[dict]) -> None:
        for i in range(0, len(blocks), self._MAX_BLOCKS):
            self._client.blocks.children.append(
                block_id=page_id, children=blocks[i:i + self._MAX_BLOCKS])


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class AARToolApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("プレイメモメーカー")
        self.root.resizable(True, True)

        self.voice_recorder: VoiceRecorder | None = None
        self.voice_on = False
        self._voice_memo_paths: list[str] = []
        self._replay_clip_paths: list[str] = []
        self._session_start: datetime.datetime | None = None
        self._hotkey_listener = VoiceHotkeyListener()
        self._ss_hotkey_listener = VoiceHotkeyListener()
        self._replay_hotkey_listener = VoiceHotkeyListener()
        cfg = _load_config()
        self._hotkey_config: dict = cfg.get("voice_hotkey", dict(_DEFAULT_HOTKEY))
        self._ss_hotkey_config: dict = cfg.get("ss_hotkey", dict(_DEFAULT_HOTKEY))
        self._replay_hotkey_config: dict = cfg.get("replay_hotkey", dict(_DEFAULT_HOTKEY))

        self.save_dir = tk.StringVar(value=_SAVE_DIR)
        self.rec_folder = tk.StringVar(value=cfg.get("rec_folder", ""))
        self.model_name = tk.StringVar(value=cfg.get("model", DEFAULT_MODEL))
        self.vision_model_name = tk.StringVar(value=cfg.get("vision_model", DEFAULT_VISION_MODEL))
        self.window_title = tk.StringVar(value=WINDOW_ALL)
        self.hotkey_display = tk.StringVar(value=self._hotkey_config.get("display") or "未設定")
        self.ss_hotkey_display = tk.StringVar(value=self._ss_hotkey_config.get("display") or "未設定")
        self.replay_hotkey_display = tk.StringVar(value=self._replay_hotkey_config.get("display") or "未設定")
        self.notion_token = tk.StringVar(value=cfg.get("notion_token", ""))
        self.notion_db_id = tk.StringVar(value=cfg.get("notion_db_id", ""))
        self.notion_auto_push = tk.BooleanVar(value=cfg.get("notion_auto_push", False))
        self.status = tk.StringVar(value="待機中")
        self.is_running = False

        self._build_ui()
        threading.Thread(target=self._load_ollama_models, daemon=True).start()

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # ── 設定フレーム ──────────────────────────────────────────────
        cfg_frame = ttk.LabelFrame(self.root, text="設定")
        cfg_frame.pack(fill="x", **pad)

        ttk.Label(cfg_frame, text="ビジョンモデル:").grid(row=0, column=0, sticky="w", **pad)
        self.vision_model_combo = ttk.Combobox(cfg_frame, textvariable=self.vision_model_name, width=28)
        self.vision_model_combo.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(cfg_frame, text="例: llava:7b, qwen2.5vl:7b",
                  foreground="gray").grid(row=0, column=2, sticky="w", **pad)

        ttk.Label(cfg_frame, text="録画保存フォルダ:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(cfg_frame, textvariable=self.rec_folder, width=36).grid(
            row=1, column=1, sticky="ew", **pad)
        ttk.Button(cfg_frame, text="参照...",
                   command=self._browse_rec_folder).grid(row=1, column=2, **pad)

        ttk.Label(cfg_frame, text="メモ保存フォルダ:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(cfg_frame, textvariable=self.save_dir, width=36).grid(
            row=2, column=1, sticky="ew", **pad)
        ttk.Button(cfg_frame, text="参照...",
                   command=self._browse_save_dir).grid(row=2, column=2, **pad)

        ttk.Label(cfg_frame, text="キャプチャ対象:").grid(row=3, column=0, sticky="w", **pad)
        self.window_combo = ttk.Combobox(cfg_frame, textvariable=self.window_title, width=28)
        self.window_combo["values"] = get_window_titles()
        self.window_combo.grid(row=3, column=1, sticky="w", **pad)
        ttk.Button(cfg_frame, text="↻", width=3,
                   command=self._refresh_windows).grid(row=3, column=2, **pad)

        if _VOICE_AVAILABLE and _HOTKEY_AVAILABLE:
            ttk.Label(cfg_frame, text="ボイスメモキー:").grid(row=4, column=0, sticky="w", **pad)
            hk_f = ttk.Frame(cfg_frame)
            hk_f.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
            ttk.Entry(hk_f, textvariable=self.hotkey_display,
                      state="readonly", width=20).pack(side="left")
            ttk.Button(hk_f, text="変更...",
                       command=self._change_hotkey).pack(side="left", padx=(4, 0))

        if _HOTKEY_AVAILABLE:
            ttk.Label(cfg_frame, text="スクショキー:").grid(row=5, column=0, sticky="w", **pad)
            ss_f = ttk.Frame(cfg_frame)
            ss_f.grid(row=5, column=1, columnspan=2, sticky="w", **pad)
            ttk.Entry(ss_f, textvariable=self.ss_hotkey_display,
                      state="readonly", width=20).pack(side="left")
            ttk.Button(ss_f, text="変更...",
                       command=self._change_ss_hotkey).pack(side="left", padx=(4, 0))

            ttk.Label(cfg_frame, text="リプレイ保存キー:").grid(row=6, column=0, sticky="w", **pad)
            rp_f = ttk.Frame(cfg_frame)
            rp_f.grid(row=6, column=1, columnspan=2, sticky="w", **pad)
            ttk.Entry(rp_f, textvariable=self.replay_hotkey_display,
                      state="readonly", width=20).pack(side="left")
            ttk.Button(rp_f, text="変更...",
                       command=self._change_replay_hotkey).pack(side="left", padx=(4, 0))
            ttk.Label(rp_f, text="ReLive/ShadowPlayと同じキーを設定",
                      foreground="gray").pack(side="left", padx=(8, 0))

        cfg_frame.columnconfigure(1, weight=1)

        # ── Notion連携フレーム ────────────────────────────────────────
        ncfg = ttk.LabelFrame(self.root, text="Notion連携（オプション）")
        ncfg.pack(fill="x", **pad)

        ttk.Label(ncfg, text="インテグレーションToken:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(ncfg, textvariable=self.notion_token, width=38, show="*").grid(
            row=0, column=1, sticky="ew", **pad)
        ttk.Label(ncfg, text="データベースID:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(ncfg, textvariable=self.notion_db_id, width=38).grid(
            row=1, column=1, sticky="ew", **pad)
        n_btn = ttk.Frame(ncfg)
        n_btn.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(n_btn, text="接続テスト", command=self._test_notion).pack(side="left")
        ttk.Button(n_btn, text="保存", command=self._save_notion_config).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(n_btn, text="分析完了時に自動送信",
                        variable=self.notion_auto_push,
                        command=self._save_notion_config).pack(side="left", padx=(12, 0))
        if not _NOTION_AVAILABLE:
            ttk.Label(n_btn, text="（notion-client未インストール）",
                      foreground="gray").pack(side="left", padx=(8, 0))
        ncfg.columnconfigure(1, weight=1)

        # ── コントロールフレーム ──────────────────────────────────────
        ctrl = ttk.Frame(self.root)
        ctrl.pack(fill="x", **pad)

        self.start_btn = ttk.Button(ctrl, text="▶ 記録開始",
                                    command=self.start_session, width=14)
        self.start_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(ctrl, text="■ 記録停止",
                                   command=self.stop_session, width=14, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.voice_btn = ttk.Button(ctrl, text="🎙 ボイスメモ: OFF",
                                    command=self.toggle_voice_memo, width=18, state="disabled")
        self.voice_btn.pack(side="left", padx=4)

        self.ss_btn = ttk.Button(ctrl, text="📷 スクショ",
                                 command=self.take_screenshot, width=12, state="disabled")
        self.ss_btn.pack(side="left", padx=4)

        self.analyze_btn = ttk.Button(ctrl, text="🎬 動画を分析",
                                      command=self.analyze_recordings, width=14, state="disabled")
        self.analyze_btn.pack(side="left", padx=4)

        ttk.Label(ctrl, textvariable=self.status, foreground="gray").pack(side="left", padx=12)

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        log_tab = ttk.Frame(self.nb)
        self.nb.add(log_tab, text="プレイメモ")
        self.log_area = scrolledtext.ScrolledText(
            log_tab, wrap="word", height=18, state="disabled", font=("Consolas", 9))
        self.log_area.pack(fill="both", expand=True, padx=4, pady=4)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=8, pady=2)

    # ------------------------------------------------------------------
    # 設定ハンドラ
    # ------------------------------------------------------------------
    def _load_ollama_models(self) -> None:
        try:
            models = [m.model for m in ollama.list().models]
            if models:
                def _upd():
                    self.vision_model_combo["values"] = models
                self.root.after(0, _upd)
        except Exception:
            pass

    def _refresh_windows(self) -> None:
        titles = get_window_titles()
        self.window_combo["values"] = titles
        if self.window_title.get() not in titles:
            self.window_title.set(WINDOW_ALL)

    def _browse_rec_folder(self) -> None:
        d = filedialog.askdirectory(initialdir=self.rec_folder.get() or os.path.expanduser("~"))
        if d:
            self.rec_folder.set(d)
            cfg = _load_config()
            cfg["rec_folder"] = d
            _save_config(cfg)

    def _browse_save_dir(self) -> None:
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

    def _change_replay_hotkey(self) -> None:
        cfg = _capture_hotkey_dialog(self.root)
        if cfg:
            self._replay_hotkey_config = cfg
            self.replay_hotkey_display.set(cfg["display"])
            saved = _load_config()
            saved["replay_hotkey"] = cfg
            _save_config(saved)

    def _save_notion_config(self) -> None:
        saved = _load_config()
        saved["notion_token"] = self.notion_token.get()
        saved["notion_db_id"] = self.notion_db_id.get()
        saved["notion_auto_push"] = self.notion_auto_push.get()
        _save_config(saved)

    def _test_notion(self) -> None:
        if not _NOTION_AVAILABLE:
            messagebox.showwarning("Notion", "notion-clientがインストールされていません。")
            return
        token = self.notion_token.get().strip()
        db_id = self.notion_db_id.get().strip()
        if not token or not db_id:
            messagebox.showwarning("Notion", "TokenとデータベースIDを入力してください。")
            return
        self._save_notion_config()
        def _test():
            try:
                name = NotionExporter(token, db_id).test_connection()
                self.root.after(0, lambda: messagebox.showinfo("Notion", f"接続成功！\nデータベース: {name}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Notion", f"接続失敗:\n{e}"))
        threading.Thread(target=_test, daemon=True).start()

    # ------------------------------------------------------------------
    # ログ表示
    # ------------------------------------------------------------------
    def _append_log(self, text: str) -> None:
        def _upd():
            self.log_area.config(state="normal")
            self.log_area.insert("end", text + "\n")
            self.log_area.see("end")
            self.log_area.config(state="disabled")
        self.root.after(0, _upd)

    # ------------------------------------------------------------------
    # セッション管理
    # ------------------------------------------------------------------
    def start_session(self) -> None:
        self._voice_memo_paths = []
        self._replay_clip_paths = []
        self._session_start = datetime.datetime.now()
        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.analyze_btn.config(state="disabled")
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
            rp_hint = self._replay_hotkey_config.get("display", "")
            if rp_hint:
                hints.append(f"{rp_hint}: リプレイ保存")
                self._replay_hotkey_listener.start(
                    self._replay_hotkey_config,
                    lambda: self.root.after(0, self._on_replay_hotkey),
                )

        hint_str = "  [" + " / ".join(hints) + "]" if hints else ""
        self.status.set(f"記録中...{hint_str}")
        if _VOICE_AVAILABLE:
            self.voice_btn.config(state="normal")
        self.ss_btn.config(state="normal")

        self.log_area.config(state="normal")
        self.log_area.delete("1.0", "end")
        self.log_area.config(state="disabled")
        self._append_log(f"[システム] 記録開始 {self._session_start.strftime('%Y-%m-%d %H:%M:%S')}")

    def stop_session(self) -> None:
        self.stop_btn.config(state="disabled")
        self.voice_btn.config(state="disabled")
        self.ss_btn.config(state="disabled")
        self._hotkey_listener.stop()
        self._ss_hotkey_listener.stop()
        self._replay_hotkey_listener.stop()
        if self.voice_on:
            self._stop_voice_memo()
        self.is_running = False
        self.start_btn.config(state="normal")
        self.window_combo.config(state="normal")
        self.status.set("待機中")
        clip_count = len(self._replay_clip_paths)
        clip_note = f"リプレイ {clip_count} クリップ保存済み。" if clip_count else "リプレイなし。"
        self._append_log(
            f"[システム] 記録停止 {datetime.datetime.now().strftime('%H:%M:%S')}\n"
            f"{clip_note}録画を停止したら「🎬 動画を分析」を押してください。"
        )
        self.analyze_btn.config(state="normal")

    # ------------------------------------------------------------------
    # インスタントリプレイ検知
    # ------------------------------------------------------------------
    def _on_replay_hotkey(self) -> None:
        triggered_at = datetime.datetime.now()
        self._append_log(f"[リプレイ] 保存キー検知 {triggered_at.strftime('%H:%M:%S')} — クリップ待機中...")
        threading.Thread(
            target=self._watch_for_new_clip,
            args=(triggered_at,),
            daemon=True,
        ).start()

    def _watch_for_new_clip(self, triggered_at: datetime.datetime) -> None:
        rec_folder = self.rec_folder.get().strip()
        if not rec_folder or not os.path.isdir(rec_folder):
            return

        import time
        deadline = triggered_at.timestamp() + 90
        new_clip = None
        while time.time() < deadline:
            time.sleep(2)
            for root_dir, _, files in os.walk(rec_folder):
                for fname in files:
                    if os.path.splitext(fname)[1].lower() not in VIDEO_EXTENSIONS:
                        continue
                    fpath = os.path.join(root_dir, fname)
                    if os.path.getmtime(fpath) >= triggered_at.timestamp():
                        new_clip = fpath
                        break
                if new_clip:
                    break
            if new_clip:
                break

        if not new_clip:
            self.root.after(0, lambda: self._append_log(
                "[リプレイ] クリップが見つかりませんでした（90秒タイムアウト）"))
            return

        self._replay_clip_paths.append(new_clip)
        count = len(self._replay_clip_paths)
        fname = os.path.basename(new_clip)
        self.root.after(0, lambda: self._append_log(
            f"[リプレイ] 📼 保存確認: {fname}  （累計 {count} クリップ）\n"
            f"  → ゲーム終了後「🎬 動画を分析」で一括分析されます"))

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
            self.voice_recorder = VoiceRecorder(save_dir, game_title=game_title)
            self.voice_recorder.start()
            self.voice_on = True
            self.voice_btn.config(text="🎙 ボイスメモ: ON")
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
                    self._voice_memo_paths.append(path)
                    self.root.after(0, lambda: self._append_log(
                        f"[システム] ボイスメモ保存: {os.path.basename(path)}"))
            threading.Thread(target=_save, daemon=True).start()
            self.voice_recorder = None

    # ------------------------------------------------------------------
    # スクリーンショット
    # ------------------------------------------------------------------
    def take_screenshot(self) -> None:
        save_dir = self.save_dir.get() or _SAVE_DIR
        window_title = self.window_title.get()
        game_title = window_title if window_title != WINDOW_ALL else "全画面"
        ts = datetime.datetime.now()

        def _do():
            try:
                img = _capture_game(window_title)
                ss_dir = os.path.join(save_dir, "スクリーンショット")
                os.makedirs(ss_dir, exist_ok=True)
                fname = f"Screenshot_{_safe_filename(game_title)}_{ts.strftime('%Y%m%d_%H%M%S')}.png"
                img.save(os.path.join(ss_dir, fname), "PNG")
                self.root.after(0, lambda: self._append_log(f"[システム] スクリーンショット: {fname}"))
            except Exception as e:
                _write_error_log(f"Screenshot error: {e}")
        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # 動画分析
    # ------------------------------------------------------------------
    def analyze_recordings(self) -> None:
        if not _CV2_AVAILABLE:
            messagebox.showerror("エラー", "opencv-pythonがインストールされていません。\npip install opencv-python")
            return

        rec_folder = self.rec_folder.get().strip()
        if not rec_folder or not os.path.isdir(rec_folder):
            messagebox.showwarning("録画フォルダ未設定",
                                   "設定で録画保存フォルダを指定してください。\n（AMD ReLive / OBSの保存先）")
            return

        start_time = self._session_start or datetime.datetime.now() - datetime.timedelta(hours=6)
        end_time = datetime.datetime.now()

        video_paths = []
        for root_dir, _, files in os.walk(rec_folder):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in VIDEO_EXTENSIONS:
                    fpath = os.path.join(root_dir, fname)
                    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                    if mtime >= start_time:
                        video_paths.append(fpath)

        if not video_paths:
            messagebox.showwarning("動画なし",
                                   f"セッション開始以降の動画が見つかりませんでした。\n{rec_folder}")
            return

        self._append_log(f"[システム] 動画分析開始: {len(video_paths)} クリップ")
        self.analyze_btn.config(state="disabled")
        self.progress.start()
        self.status.set("動画分析中...")

        voice_contents: list[str] = []
        for vp in list(self._voice_memo_paths):
            try:
                with open(vp, encoding="utf-8") as f:
                    voice_contents.append(f.read().strip())
            except Exception:
                pass

        game_title = self.window_title.get()
        vision_model = self.vision_model_name.get() or DEFAULT_VISION_MODEL
        save_dir = self.save_dir.get() or _SAVE_DIR

        def _run():
            try:
                analyzer = VideoAnalyzer(vision_model=vision_model)
                memo_text = analyzer.process_session(
                    video_paths, voice_contents, game_title,
                    start_time, end_time,
                    progress_cb=lambda msg: self.root.after(0, lambda: self.status.set(msg)),
                )
                path = save_play_memo(memo_text, save_dir)
                self.root.after(0, lambda: self._append_log(
                    f"[システム] 完了: {os.path.basename(path)}\n\n{memo_text}"))

                notion_note = ""
                if (_NOTION_AVAILABLE and self.notion_auto_push.get()
                        and self.notion_token.get().strip()
                        and self.notion_db_id.get().strip()):
                    try:
                        NotionExporter(
                            self.notion_token.get().strip(),
                            self.notion_db_id.get().strip(),
                        ).push_memo(game_title, start_time, end_time, memo_text)
                        notion_note = "\nNotionページに送信しました。"
                    except Exception as e:
                        _write_error_log(f"Notion push error: {e}")
                        notion_note = f"\nNotion送信失敗: {e}"

                self.root.after(0, lambda: messagebox.showinfo(
                    "完了", f"プレイメモを保存しました:\n{path}{notion_note}"))
            except Exception as e:
                _write_error_log(traceback.format_exc())
                self.root.after(0, lambda: messagebox.showerror("エラー", f"分析中にエラー:\n{e}"))
            finally:
                self.root.after(0, self.progress.stop)
                self.root.after(0, lambda: self.analyze_btn.config(state="normal"))
                self.root.after(0, lambda: self.status.set("待機中"))

        threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main() -> None:
    root = tk.Tk()
    root.minsize(660, 500)
    app = AARToolApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (
        app.voice_recorder.stop() if app.voice_recorder else None,
        root.destroy()
    ))
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _write_error_log(traceback.format_exc())
