"""Whisper Desktop - 音声文字起こしデスクトップアプリ"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import threading
import queue
import wave
import tempfile
import os
import time

import numpy as np
import sounddevice as sd

try:
    from faster_whisper import WhisperModel
    BACKEND = "faster-whisper"
except ImportError:
    import whisper
    BACKEND = "openai-whisper"


def format_timestamp(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


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
        self.root.geometry("750x600")
        self.root.minsize(500, 400)

        self.model = None
        self.model_name = None
        self.recording = False
        self.cancel_flag = False
        self.audio_frames = []
        self.msg_queue = queue.Queue()
        self.transcribe_start = 0

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

        self.cancel_btn = ttk.Button(mid, text="⛔ キャンセル", command=self._cancel)
        self.cancel_btn.pack(side=tk.LEFT, padx=4)
        self.cancel_btn.state(["disabled"])

        self.status_var = tk.StringVar(value="モデルを読み込んでください")
        ttk.Label(mid, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        self.timestamp_chk_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="タイムスタンプ表示",
                        variable=self.timestamp_chk_var).pack(side=tk.RIGHT, padx=4)

        txt_frame = ttk.Frame(self.root, padding=8)
        txt_frame.pack(fill=tk.BOTH, expand=True)

        self.text = scrolledtext.ScrolledText(txt_frame, wrap=tk.WORD, font=("Yu Gothic UI", 11))
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.tag_configure("timestamp", foreground="#888888", font=("Yu Gothic UI", 9))

        bot = ttk.Frame(self.root, padding=8)
        bot.pack(fill=tk.X)

        ttk.Button(bot, text="コピー", command=self._copy).pack(side=tk.LEFT, padx=4)
        ttk.Button(bot, text="テキストのみコピー", command=self._copy_text_only).pack(side=tk.LEFT, padx=4)
        ttk.Button(bot, text="クリア", command=self._clear).pack(side=tk.LEFT, padx=4)

        self.progress = ttk.Progressbar(bot, mode="indeterminate", length=120)
        self.progress.pack(side=tk.RIGHT, padx=4)

        self.elapsed_var = tk.StringVar(value="")
        ttk.Label(bot, textvariable=self.elapsed_var).pack(side=tk.RIGHT, padx=8)

    def _poll_queue(self):
        while not self.msg_queue.empty():
            action, data = self.msg_queue.get_nowait()
            if action == "status":
                self.status_var.set(data)
            elif action == "segment":
                ts, text = data
                if self.timestamp_chk_var.get():
                    self.text.insert(tk.END, ts, "timestamp")
                    self.text.insert(tk.END, f" {text}\n")
                else:
                    self.text.insert(tk.END, f"{text}\n")
                self.text.see(tk.END)
            elif action == "text":
                self.text.insert(tk.END, data + "\n\n")
                self.text.see(tk.END)
            elif action == "separator":
                self.text.insert(tk.END, "\n")
            elif action == "model_loaded":
                self.rec_btn.state(["!disabled"])
                self.file_btn.state(["!disabled"])
                self.load_btn.state(["!disabled"])
                self.progress.stop()
            elif action == "done":
                self.file_btn.state(["!disabled"])
                self.load_btn.state(["!disabled"])
                self.rec_btn.state(["!disabled"])
                self.cancel_btn.state(["disabled"])
                self.progress.stop()
                self.elapsed_var.set("")
            elif action == "elapsed":
                self.elapsed_var.set(data)
        self.root.after(100, self._poll_queue)

    def _get_lang_code(self):
        name = self.lang_var.get()
        for n, code in self.LANGUAGES:
            if n == name:
                return code
        return None

    def _cancel(self):
        self.cancel_flag = True
        self.status_var.set("キャンセル中...")

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
            self._start_transcription_ui()
            threading.Thread(target=self._transcribe_audio, daemon=True).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_frames.append(indata.copy())

    def _start_transcription_ui(self):
        self.cancel_flag = False
        self.transcribe_start = time.time()
        self.rec_btn.state(["disabled"])
        self.file_btn.state(["disabled"])
        self.load_btn.state(["disabled"])
        self.cancel_btn.state(["!disabled"])
        self.status_var.set("文字起こし中...")
        self.progress.start(15)

    def _transcribe_audio(self):
        if not self.audio_frames:
            self.msg_queue.put(("status", "音声が録音されていません"))
            self.msg_queue.put(("done", None))
            return

        audio = np.concatenate(self.audio_frames, axis=0).flatten()
        duration = len(audio) / self.SAMPLE_RATE
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
            with wave.open(f, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.SAMPLE_RATE)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())

        try:
            self._transcribe_file(tmp_path, duration)
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
        self._start_transcription_ui()
        threading.Thread(target=self._transcribe_file, args=(path, None), daemon=True).start()

    def _transcribe_file(self, path, duration=None):
        lang = self._get_lang_code()
        seg_count = 0
        try:
            if BACKEND == "faster-whisper":
                kwargs = {}
                if lang:
                    kwargs["language"] = lang
                segments, info = self.model.transcribe(path, **kwargs)
                audio_duration = info.duration if hasattr(info, 'duration') else duration

                for seg in segments:
                    if self.cancel_flag:
                        self.msg_queue.put(("status", "キャンセルしました"))
                        self.msg_queue.put(("separator", None))
                        break

                    ts = f"[{format_timestamp(seg.start)} → {format_timestamp(seg.end)}]"
                    self.msg_queue.put(("segment", (ts, seg.text.strip())))
                    seg_count += 1

                    elapsed = time.time() - self.transcribe_start
                    progress_str = f"経過: {format_timestamp(elapsed)}"
                    if audio_duration and seg.end > 0:
                        pct = min(seg.end / audio_duration * 100, 100)
                        progress_str += f" | 進捗: {pct:.0f}%"
                        if pct > 0:
                            eta = elapsed / pct * (100 - pct)
                            progress_str += f" | 残り約{format_timestamp(eta)}"
                    self.msg_queue.put(("elapsed", progress_str))
                    self.msg_queue.put(("status", f"文字起こし中... ({seg_count}セグメント)"))
                else:
                    if seg_count > 0:
                        elapsed = time.time() - self.transcribe_start
                        self.msg_queue.put(("separator", None))
                        self.msg_queue.put(("status",
                            f"完了 - {seg_count}セグメント ({format_timestamp(elapsed)})"))
                    else:
                        self.msg_queue.put(("status", "テキストが検出されませんでした"))
            else:
                kwargs = {}
                if lang:
                    kwargs["language"] = lang
                result = self.model.transcribe(path, **kwargs)

                for seg in result.get("segments", []):
                    if self.cancel_flag:
                        self.msg_queue.put(("status", "キャンセルしました"))
                        break
                    ts = f"[{format_timestamp(seg['start'])} → {format_timestamp(seg['end'])}]"
                    self.msg_queue.put(("segment", (ts, seg["text"].strip())))
                    seg_count += 1

                if not self.cancel_flag:
                    elapsed = time.time() - self.transcribe_start
                    self.msg_queue.put(("separator", None))
                    if seg_count > 0:
                        self.msg_queue.put(("status",
                            f"完了 - {seg_count}セグメント ({format_timestamp(elapsed)})"))
                    else:
                        self.msg_queue.put(("status", "テキストが検出されませんでした"))

        except Exception as e:
            self.msg_queue.put(("status", f"エラー: {e}"))

        self.msg_queue.put(("done", None))

    def _copy(self):
        text = self.text.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("コピーしました")

    def _copy_text_only(self):
        content = self.text.get("1.0", tk.END).strip()
        lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("[") and "→" in stripped:
                idx = stripped.find("]")
                if idx >= 0:
                    lines.append(stripped[idx+1:].strip())
                else:
                    lines.append(stripped)
            elif stripped:
                lines.append(stripped)
        text = "\n".join(lines)
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("テキストのみコピーしました")

    def _clear(self):
        self.text.delete("1.0", tk.END)
        self.status_var.set("クリアしました")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = WhisperDesktop()
    app.run()
