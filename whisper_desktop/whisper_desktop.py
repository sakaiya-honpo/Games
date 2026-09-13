"""Whisper Desktop - ファイルを選ぶだけで文字起こし"""

import customtkinter as ctk
from tkinter import filedialog
import threading
import queue
import os
import time

from faster_whisper import WhisperModel

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SPEED_FACTORS = {
    "tiny": 0.3, "base": 0.5, "small": 1.0,
    "medium": 2.5, "large-v3": 5.0,
}


def format_ts(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_srt_ts(seconds):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    ms = int((s % 1) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def get_audio_duration(path):
    try:
        from faster_whisper.audio import decode_audio
        audio = decode_audio(path)
        return len(audio) / 16000
    except Exception:
        return 0


def get_hotwords_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "hotwords")


def scan_hotword_files():
    hw_dir = get_hotwords_dir()
    if not os.path.isdir(hw_dir):
        return []
    files = []
    for f in sorted(os.listdir(hw_dir)):
        if f.endswith(".txt"):
            files.append(os.path.splitext(f)[0])
    return files


def load_hotword_file(name):
    path = os.path.join(get_hotwords_dir(), name + ".txt")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [w.strip() for w in f if w.strip()]


class WhisperDesktop:
    MODELS = ["tiny", "base", "small", "medium", "large-v3"]
    LANGUAGES = [
        ("自動検出", None), ("日本語", "ja"), ("英語", "en"),
        ("中国語", "zh"), ("韓国語", "ko"), ("フランス語", "fr"),
        ("ドイツ語", "de"), ("スペイン語", "es"),
    ]

    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("Whisper Desktop")
        self.root.geometry("800x650")
        self.root.minsize(550, 480)

        self.model = None
        self.cancel_flag = False
        self.segments_data = []
        self.msg_queue = queue.Queue()
        self.transcribe_start = 0
        self.current_file = None
        self.hotwords = []

        self._build_ui()
        self._poll_queue()
        self._auto_load_model()

    def _build_ui(self):
        top = ctk.CTkFrame(self.root)
        top.pack(fill="x", padx=12, pady=(12, 4))

        ctk.CTkLabel(top, text="モデル:").pack(side="left", padx=(12, 4))
        self.model_var = ctk.StringVar(value="small")
        ctk.CTkOptionMenu(top, variable=self.model_var,
                          values=self.MODELS, width=120
                          ).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(top, text="言語:").pack(side="left", padx=(0, 4))
        self.lang_var = ctk.StringVar(value="日本語")
        ctk.CTkOptionMenu(top, variable=self.lang_var,
                          values=[n for n, _ in self.LANGUAGES], width=120
                          ).pack(side="left", padx=(0, 16))

        ctk.CTkButton(top, text="モデル切替", width=100,
                      command=self._auto_load_model).pack(side="left", padx=4)

        hw_frame = ctk.CTkFrame(self.root)
        hw_frame.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(hw_frame, text="語彙リスト:").pack(side="left", padx=(12, 4))
        hw_files = scan_hotword_files()
        hw_options = ["なし"] + hw_files
        self.hw_var = ctk.StringVar(value="なし")
        self.hw_menu = ctk.CTkOptionMenu(hw_frame, variable=self.hw_var,
                                         values=hw_options, width=180,
                                         command=self._on_hw_changed)
        self.hw_menu.pack(side="left", padx=(0, 8))

        self.hw_status_var = ctk.StringVar(value="")
        ctk.CTkLabel(hw_frame, textvariable=self.hw_status_var,
                     text_color="#888888").pack(side="left", padx=4)

        mid = ctk.CTkFrame(self.root)
        mid.pack(fill="x", padx=12, pady=4)

        self.file_btn = ctk.CTkButton(mid, text="ファイルを選んで文字起こし",
                                      width=220, command=self._pick_file)
        self.file_btn.pack(side="left", padx=(12, 4), pady=8)
        self.file_btn.configure(state="disabled")

        self.cancel_btn = ctk.CTkButton(mid, text="キャンセル", width=100,
                                        fg_color="#c0392b", hover_color="#e74c3c",
                                        command=self._cancel)
        self.cancel_btn.pack(side="left", padx=4, pady=8)
        self.cancel_btn.configure(state="disabled")

        self.srt_btn = ctk.CTkButton(mid, text="SRT保存", width=100,
                                     fg_color="#27ae60", hover_color="#2ecc71",
                                     command=self._save_srt)
        self.srt_btn.pack(side="left", padx=4, pady=8)
        self.srt_btn.configure(state="disabled")

        self.status_var = ctk.StringVar(value="モデル読み込み中...")
        ctk.CTkLabel(mid, textvariable=self.status_var,
                     text_color="#aaaaaa").pack(side="left", padx=12, pady=8)

        txt_frame = ctk.CTkFrame(self.root)
        txt_frame.pack(fill="both", expand=True, padx=12, pady=4)

        self.text = ctk.CTkTextbox(txt_frame, font=("Yu Gothic UI", 13),
                                   wrap="word", corner_radius=8)
        self.text.pack(fill="both", expand=True, padx=4, pady=4)

        self.progress = ctk.CTkProgressBar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=16, pady=(4, 0))
        self.progress.set(0)

        bot = ctk.CTkFrame(self.root)
        bot.pack(fill="x", padx=12, pady=(4, 12))

        ctk.CTkButton(bot, text="コピー", width=80,
                      command=self._copy).pack(side="left", padx=(12, 4), pady=8)
        ctk.CTkButton(bot, text="テキストのみコピー", width=140,
                      command=self._copy_text_only).pack(side="left", padx=4, pady=8)
        ctk.CTkButton(bot, text="クリア", width=80, fg_color="#7f8c8d",
                      hover_color="#95a5a6",
                      command=self._clear).pack(side="left", padx=4, pady=8)

        self.elapsed_var = ctk.StringVar()
        ctk.CTkLabel(bot, textvariable=self.elapsed_var,
                     text_color="#aaaaaa").pack(side="right", padx=12, pady=8)

    def _on_hw_changed(self, choice):
        if choice == "なし":
            self.hotwords = []
            self.hw_status_var.set("")
        else:
            self.hotwords = load_hotword_file(choice)
            self.hw_status_var.set(f"{len(self.hotwords)}語読み込み済")

    def _poll_queue(self):
        while not self.msg_queue.empty():
            action, data = self.msg_queue.get_nowait()
            if action == "status":
                self.status_var.set(data)
            elif action == "segment":
                ts, txt = data
                self.text.insert("end", f"{ts} {txt}\n")
                self.text.see("end")
            elif action == "elapsed":
                self.elapsed_var.set(data)
            elif action == "ready":
                self.file_btn.configure(state="normal")
                self.progress.stop()
                self.progress.set(0)
            elif action == "done":
                self.file_btn.configure(state="normal")
                self.cancel_btn.configure(state="disabled")
                if self.segments_data:
                    self.srt_btn.configure(state="normal")
                self.progress.stop()
                self.progress.set(0)
                self.elapsed_var.set("")
            elif action == "progress":
                self.progress.set(data)
        self.root.after(100, self._poll_queue)

    def _get_lang(self):
        for n, code in self.LANGUAGES:
            if n == self.lang_var.get():
                return code
        return None

    def _cancel(self):
        self.cancel_flag = True

    def _auto_load_model(self):
        self.file_btn.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status_var.set("モデル読み込み中...")
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        name = self.model_var.get()
        try:
            self.model = WhisperModel(name, compute_type="int8")
            self.msg_queue.put(("status", f"準備完了 [{name}] - ファイルを選んでください"))
        except Exception as e:
            self.msg_queue.put(("status", f"読込失敗: {e}"))
        self.msg_queue.put(("ready", None))

    def _estimate_time(self, duration):
        model_name = self.model_var.get()
        factor = SPEED_FACTORS.get(model_name, 1.0)
        return duration * factor

    def _pick_file(self):
        path = filedialog.askopenfilename(filetypes=[
            ("音声/動画", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma "
                         "*.mp4 *.mkv *.avi *.mov *.webm"),
            ("すべて", "*.*"),
        ])
        if not path:
            return
        self.current_file = path
        self.segments_data = []
        self.srt_btn.configure(state="disabled")
        self.cancel_flag = False
        self.transcribe_start = time.time()
        self.file_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.text.delete("1.0", "end")

        duration = get_audio_duration(path)
        est = self._estimate_time(duration) if duration else 0
        base_msg = f"文字起こし中: {os.path.basename(path)}"
        if duration:
            base_msg += f" (音声{format_ts(duration)}"
            if est:
                base_msg += f" / 推定{format_ts(est)}"
            base_msg += ")"
        self.status_var.set(base_msg)

        threading.Thread(target=self._transcribe, args=(path,), daemon=True).start()

    def _transcribe(self, path):
        lang = self._get_lang()
        kwargs = {}
        if lang:
            kwargs["language"] = lang
        if self.hotwords:
            kwargs["hotwords"] = " ".join(self.hotwords)
        seg_count = 0
        try:
            segments, info = self.model.transcribe(path, **kwargs)
            duration = info.duration if hasattr(info, "duration") else 0

            for seg in segments:
                if self.cancel_flag:
                    self.msg_queue.put(("status", "キャンセルしました"))
                    break

                self.segments_data.append((seg.start, seg.end, seg.text.strip()))
                ts = f"[{format_ts(seg.start)} → {format_ts(seg.end)}]"
                self.msg_queue.put(("segment", (ts, seg.text.strip())))
                seg_count += 1

                elapsed = time.time() - self.transcribe_start
                info_str = f"経過: {format_ts(elapsed)}"
                if duration and seg.end > 0:
                    pct = min(seg.end / duration * 100, 100)
                    info_str += f" | {pct:.0f}%"
                    if pct > 0:
                        eta = elapsed / pct * (100 - pct)
                        info_str += f" | 残り約{format_ts(eta)}"
                    self.msg_queue.put(("progress", pct / 100))
                self.msg_queue.put(("elapsed", info_str))
                self.msg_queue.put(("status",
                    f"文字起こし中... {seg_count}セグメント"))
            else:
                if seg_count > 0:
                    elapsed = time.time() - self.transcribe_start
                    self.msg_queue.put(("status",
                        f"完了 - {seg_count}セグメント ({format_ts(elapsed)})"))
                else:
                    self.msg_queue.put(("status", "テキストが検出されませんでした"))
        except Exception as e:
            self.msg_queue.put(("status", f"エラー: {e}"))

        self.msg_queue.put(("done", None))

    def _save_srt(self):
        if not self.segments_data:
            return
        default_name = ""
        if self.current_file:
            default_name = os.path.splitext(os.path.basename(self.current_file))[0] + ".srt"
        path = filedialog.asksaveasfilename(
            defaultextension=".srt",
            initialfile=default_name,
            filetypes=[("SRT字幕", "*.srt"), ("すべて", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            for i, (start, end, txt) in enumerate(self.segments_data, 1):
                f.write(f"{i}\n")
                f.write(f"{format_srt_ts(start)} --> {format_srt_ts(end)}\n")
                f.write(f"{txt}\n\n")
        self.status_var.set(f"SRT保存: {os.path.basename(path)}")

    def _copy(self):
        text = self.text.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("コピーしました")

    def _copy_text_only(self):
        lines = []
        for start, end, txt in self.segments_data:
            if txt:
                lines.append(txt)
        text = "\n".join(lines)
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("テキストのみコピーしました")

    def _clear(self):
        self.text.delete("1.0", "end")
        self.segments_data = []
        self.srt_btn.configure(state="disabled")
        self.status_var.set("クリアしました")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = WhisperDesktop()
    app.run()
