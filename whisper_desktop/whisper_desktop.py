"""Whisper Desktop - 音声文字起こしデスクトップアプリ"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import threading
import queue
import wave
import tempfile
import os
import sys

import numpy as np
import sounddevice as sd

try:
    from faster_whisper import WhisperModel
    BACKEND = "faster-whisper"
except ImportError:
    import whisper
    BACKEND = "openai-whisper"


class WhisperDesktop:
    MODELS = ["tiny", "base", "small", "medium", "large-v3"]
    LANGUAGES = [
        ("自動検出", None),
        ("日本語", "ja"),
        ("英語", "en"),
        ("中国語", "zh"),
        ("韓国語", "ko"),
        ("フランス語", "fr"),
        ("ドイツ語", "de"),
        ("スペイン語", "es"),
    ]
    SAMPLE_RATE = 16000

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Whisper Desktop")
        self.root.geometry("700x560")
        self.root.minsize(500, 400)

        self.model = None
        self.model_name = None
        self.recording = False
        self.audio_frames = []
        self.msg_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="モデル:").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="base")
        cb = ttk.Combobox(top, textvariable=self.model_var,
                          values=self.MODELS, width=12, state="readonly")
        cb.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(top, text="言語:").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value="自動検出")
        lang_names = [name for name, _ in self.LANGUAGES]
        lb = ttk.Combobox(top, textvariable=self.lang_var,
                          values=lang_names, width=12, state="readonly")
        lb.pack(side=tk.LEFT, padx=(4, 12))

        self.load_btn = ttk.Button(top, text="モデル読込", command=self._load_model)
        self.load_btn.pack(side=tk.LEFT, padx=4)

        mid = ttk.Frame(self.root, padding=8)
        mid.pack(fill=tk.X)

        self.rec_btn = ttk.Button(mid, text="🎤 録音開始", command=self._toggle_rec)
        self.rec_btn.pack(side=tk.LEFT, padx=4)
        self.rec_btn.state(["disabled"])

        self.file_btn = ttk.Button(mid, text="📁 ファイル選択", command=self._pick_file)
        self.file_btn.pack(side=tk.LEFT, padx=4)
        self.file_btn.state(["disabled"])

        self.status_var = tk.StringVar(value="モデルを読み込んでください")
        ttk.Label(mid, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        txt_frame = ttk.Frame(self.root, padding=8)
        txt_frame.pack(fill=tk.BOTH, expand=True)

        self.text = scrolledtext.ScrolledText(txt_frame, wrap=tk.WORD, font=("Yu Gothic UI", 11))
        self.text.pack(fill=tk.BOTH, expand=True)

        bot = ttk.Frame(self.root, padding=8)
        bot.pack(fill=tk.X)

        ttk.Button(bot, text="コピー", command=self._copy).pack(side=tk.LEFT, padx=4)
        ttk.Button(bot, text="クリア", command=self._clear).pack(side=tk.LEFT, padx=4)

        self.progress = ttk.Progressbar(bot, mode="indeterminate", length=120)
        self.progress.pack(side=tk.RIGHT, padx=4)

    def _poll_queue(self):
        while not self.msg_queue.empty():
            action, data = self.msg_queue.get_nowait()
            if action == "status":
                self.status_var.set(data)
            elif action == "text":
                self.text.insert(tk.END, data + "\n\n")
                self.text.see(tk.END)
            elif action == "model_loaded":
                self.rec_btn.state(["!disabled"])
                self.file_btn.state(["!disabled"])
                self.load_btn.state(["!disabled"])
                self.progress.stop()
            elif action == "done":
                self.file_btn.state(["!disabled"])
                self.load_btn.state(["!disabled"])
                self.progress.stop()
        self.root.after(100, self._poll_queue)

    def _get_lang_code(self):
        name = self.lang_var.get()
        for n, code in self.LANGUAGES:
            if n == name:
                return code
        return None

    def _load_model(self):
        self.load_btn.state(["disabled"])
        self.rec_btn.state(["disabled"])
        self.file_btn.state(["disabled"])
        self.progress.start(15)
        self.status_var.set("モデル読み込み中...")
        threading.Thread(target=self._load_model_worker, daemon=True).start()

    def _load_model_worker(self):
        name = self.model_var.get()
        try:
            if BACKEND == "faster-whisper":
                self.model = WhisperModel(name, compute_type="int8")
            else:
                self.model = whisper.load_model(name)
            self.model_name = name
            self.msg_queue.put(("status", f"モデル [{name}] 準備完了"))
        except Exception as e:
            self.msg_queue.put(("status", f"読込失敗: {e}"))
        self.msg_queue.put(("model_loaded", None))

    def _toggle_rec(self):
        if not self.recording:
            self.recording = True
            self.audio_frames = []
            self.rec_btn.config(text="⏹ 録音停止")
            self.file_btn.state(["disabled"])
            self.load_btn.state(["disabled"])
            self.status_var.set("録音中...")
            self.stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE, channels=1, dtype="float32",
                callback=self._audio_callback
            )
            self.stream.start()
        else:
            self.recording = False
            self.stream.stop()
            self.stream.close()
            self.rec_btn.config(text="🎤 録音開始")
            self.status_var.set("文字起こし中...")
            self.progress.start(15)
            threading.Thread(target=self._transcribe_audio, daemon=True).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_frames.append(indata.copy())

    def _transcribe_audio(self):
        if not self.audio_frames:
            self.msg_queue.put(("status", "音声が録音されていません"))
            self.msg_queue.put(("done", None))
            return

        audio = np.concatenate(self.audio_frames, axis=0).flatten()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
            with wave.open(f, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.SAMPLE_RATE)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())

        try:
            self._transcribe_file(tmp_path)
        finally:
            os.unlink(tmp_path)

    def _pick_file(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("音声ファイル", "*.wav *.mp3 *.m4a *.flac *.ogg *.wma *.aac"),
                ("動画ファイル", "*.mp4 *.mkv *.avi *.mov *.webm"),
                ("すべて", "*.*"),
            ]
        )
        if not path:
            return
        self.file_btn.state(["disabled"])
        self.load_btn.state(["disabled"])
        self.rec_btn.state(["disabled"])
        self.status_var.set("文字起こし中...")
        self.progress.start(15)
        threading.Thread(target=self._transcribe_file, args=(path,), daemon=True).start()

    def _transcribe_file(self, path):
        lang = self._get_lang_code()
        try:
            if BACKEND == "faster-whisper":
                kwargs = {}
                if lang:
                    kwargs["language"] = lang
                segments, info = self.model.transcribe(path, **kwargs)
                text = "".join(seg.text for seg in segments).strip()
            else:
                kwargs = {}
                if lang:
                    kwargs["language"] = lang
                result = self.model.transcribe(path, **kwargs)
                text = result["text"].strip()

            if text:
                self.msg_queue.put(("text", text))
                self.msg_queue.put(("status", "完了"))
            else:
                self.msg_queue.put(("status", "テキストが検出されませんでした"))
        except Exception as e:
            self.msg_queue.put(("status", f"エラー: {e}"))

        self.msg_queue.put(("done", None))
        self.rec_btn.state(["!disabled"])

    def _copy(self):
        text = self.text.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("コピーしました")

    def _clear(self):
        self.text.delete("1.0", tk.END)
        self.status_var.set("クリアしました")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = WhisperDesktop()
    app.run()
