"""Whisper Desktop - ファイルを選ぶだけで文字起こし"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import threading
import queue
import os
import time
import sys

from faster_whisper import WhisperModel


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


class WhisperDesktop:
    MODELS = ["tiny", "base", "small", "medium", "large-v3"]
    LANGUAGES = [
        ("自動検出", None), ("日本語", "ja"), ("英語", "en"),
        ("中国語", "zh"), ("韓国語", "ko"), ("フランス語", "fr"),
        ("ドイツ語", "de"), ("スペイン語", "es"),
    ]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Whisper Desktop")
        self.root.geometry("750x580")
        self.root.minsize(500, 400)

        self.model = None
        self.cancel_flag = False
        self.segments_data = []
        self.msg_queue = queue.Queue()
        self.transcribe_start = 0
        self.current_file = None

        self._build_ui()
        self._poll_queue()
        self._auto_load_model()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="モデル:").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="small")
        ttk.Combobox(top, textvariable=self.model_var,
                     values=self.MODELS, width=10, state="readonly"
                     ).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(top, text="言語:").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value="日本語")
        ttk.Combobox(top, textvariable=self.lang_var,
                     values=[n for n, _ in self.LANGUAGES], width=10, state="readonly"
                     ).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Button(top, text="モデル切替", command=self._auto_load_model).pack(side=tk.LEFT, padx=4)

        mid = ttk.Frame(self.root, padding=8)
        mid.pack(fill=tk.X)

        self.file_btn = ttk.Button(mid, text="ファイルを選んで文字起こし",
                                   command=self._pick_file)
        self.file_btn.pack(side=tk.LEFT, padx=4)
        self.file_btn.state(["disabled"])

        self.cancel_btn = ttk.Button(mid, text="キャンセル", command=self._cancel)
        self.cancel_btn.pack(side=tk.LEFT, padx=4)
        self.cancel_btn.state(["disabled"])

        self.srt_btn = ttk.Button(mid, text="SRT保存", command=self._save_srt)
        self.srt_btn.pack(side=tk.LEFT, padx=4)
        self.srt_btn.state(["disabled"])

        self.status_var = tk.StringVar(value="モデル読み込み中...")
        ttk.Label(mid, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        txt_frame = ttk.Frame(self.root, padding=8)
        txt_frame.pack(fill=tk.BOTH, expand=True)

        self.text = scrolledtext.ScrolledText(txt_frame, wrap=tk.WORD,
                                              font=("Yu Gothic UI", 11))
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.tag_configure("ts", foreground="#888888", font=("Yu Gothic UI", 9))

        bot = ttk.Frame(self.root, padding=8)
        bot.pack(fill=tk.X)

        ttk.Button(bot, text="コピー", command=self._copy).pack(side=tk.LEFT, padx=4)
        ttk.Button(bot, text="テキストのみコピー",
                   command=self._copy_text_only).pack(side=tk.LEFT, padx=4)
        ttk.Button(bot, text="クリア", command=self._clear).pack(side=tk.LEFT, padx=4)

        self.elapsed_var = tk.StringVar()
        ttk.Label(bot, textvariable=self.elapsed_var).pack(side=tk.RIGHT, padx=8)

        self.progress = ttk.Progressbar(bot, mode="indeterminate", length=120)
        self.progress.pack(side=tk.RIGHT, padx=4)

    def _poll_queue(self):
        while not self.msg_queue.empty():
            action, data = self.msg_queue.get_nowait()
            if action == "status":
                self.status_var.set(data)
            elif action == "segment":
                ts, txt = data
                self.text.insert(tk.END, ts, "ts")
                self.text.insert(tk.END, f" {txt}\n")
                self.text.see(tk.END)
            elif action == "elapsed":
                self.elapsed_var.set(data)
            elif action == "ready":
                self.file_btn.state(["!disabled"])
                self.progress.stop()
            elif action == "done":
                self.file_btn.state(["!disabled"])
                self.cancel_btn.state(["disabled"])
                if self.segments_data:
                    self.srt_btn.state(["!disabled"])
                self.progress.stop()
                self.elapsed_var.set("")
        self.root.after(100, self._poll_queue)

    def _get_lang(self):
        for n, code in self.LANGUAGES:
            if n == self.lang_var.get():
                return code
        return None

    def _cancel(self):
        self.cancel_flag = True

    def _auto_load_model(self):
        self.file_btn.state(["disabled"])
        self.progress.start(15)
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
        self.srt_btn.state(["disabled"])
        self.cancel_flag = False
        self.transcribe_start = time.time()
        self.file_btn.state(["disabled"])
        self.cancel_btn.state(["!disabled"])
        self.progress.start(15)
        self.text.delete("1.0", tk.END)
        self.status_var.set(f"文字起こし中: {os.path.basename(path)}")
        threading.Thread(target=self._transcribe, args=(path,), daemon=True).start()

    def _transcribe(self, path):
        lang = self._get_lang()
        kwargs = {}
        if lang:
            kwargs["language"] = lang
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
        text = self.text.get("1.0", tk.END).strip()
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
        self.text.delete("1.0", tk.END)
        self.segments_data = []
        self.srt_btn.state(["disabled"])
        self.status_var.set("クリアしました")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = WhisperDesktop()
    app.run()
