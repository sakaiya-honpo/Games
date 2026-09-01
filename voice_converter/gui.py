#!/usr/bin/env python3
"""
Voice Converter GUI
録音データ → 話者分離 → VOICEVOX音声変換（ドラッグ&ドロップ対応）

使い方:
    python gui.py
"""

import json
import os
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from pathlib import Path

from convert import (
    extract_audio,
    transcribe,
    diarize,
    assign_speakers,
    save_transcript,
    synthesize_conversation,
    VOICEVOX_URL,
)

VOICEVOX_SPEAKERS = {
    "ずんだもん（ノーマル）": 3,
    "ずんだもん（あまあま）": 1,
    "四国めたん（ノーマル）": 2,
    "四国めたん（あまあま）": 0,
    "春日部つむぎ": 8,
    "雨晴はう": 10,
    "冥鳴ひまり": 14,
    "波音リツ": 9,
    "玄野武宏": 11,
    "白上虎太郎": 12,
    "青山龍星": 13,
}

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma",
}


class VoiceConverterGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Voice Converter - 録音→VOICEVOX変換")
        self.root.geometry("720x700")
        self.root.minsize(600, 550)

        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.is_running = False

        self._build_ui()
        self._setup_drag_drop()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # --- ドロップエリア ---
        self.drop_frame = tk.Frame(
            main, bg="#e8e8e8", relief=tk.GROOVE, bd=2, height=100,
        )
        self.drop_frame.pack(fill=tk.X, pady=(0, 10))
        self.drop_frame.pack_propagate(False)

        self.drop_label = tk.Label(
            self.drop_frame,
            text="ここに動画/音声ファイルをドラッグ&ドロップ\nまたはクリックしてファイルを選択",
            bg="#e8e8e8", fg="#666666", font=("", 12),
        )
        self.drop_label.pack(expand=True)
        self.drop_frame.bind("<Button-1>", lambda e: self._browse_input())
        self.drop_label.bind("<Button-1>", lambda e: self._browse_input())

        # --- 入力ファイル ---
        file_frame = ttk.LabelFrame(main, text="入力ファイル", padding=5)
        file_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Entry(file_frame, textvariable=self.input_path, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5),
        )
        ttk.Button(file_frame, text="参照", command=self._browse_input).pack(side=tk.RIGHT)

        # --- 設定 ---
        settings = ttk.LabelFrame(main, text="設定", padding=5)
        settings.pack(fill=tk.X, pady=(0, 5))

        row1 = ttk.Frame(settings)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Whisperモデル:").pack(side=tk.LEFT, padx=(0, 5))
        self.model_var = tk.StringVar(value="base")
        ttk.Combobox(
            row1, textvariable=self.model_var, values=WHISPER_MODELS,
            state="readonly", width=10,
        ).pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row1, text="話者A:").pack(side=tk.LEFT, padx=(0, 5))
        self.speaker_a_var = tk.StringVar(value="ずんだもん（ノーマル）")
        ttk.Combobox(
            row1, textvariable=self.speaker_a_var,
            values=list(VOICEVOX_SPEAKERS.keys()), state="readonly", width=20,
        ).pack(side=tk.LEFT)

        row2 = ttk.Frame(settings)
        row2.pack(fill=tk.X, pady=2)

        self.timing_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="タイミング調整", variable=self.timing_var).pack(
            side=tk.LEFT, padx=(0, 20),
        )

        ttk.Label(row2, text="話者B:").pack(side=tk.LEFT, padx=(0, 5))
        self.speaker_b_var = tk.StringVar(value="四国めたん（ノーマル）")
        ttk.Combobox(
            row2, textvariable=self.speaker_b_var,
            values=list(VOICEVOX_SPEAKERS.keys()), state="readonly", width=20,
        ).pack(side=tk.LEFT)

        self.transcript_only_var = tk.BooleanVar(value=False)
        row3 = ttk.Frame(settings)
        row3.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(
            row3, text="文字起こし+話者分離のみ（VOICEVOX不要）",
            variable=self.transcript_only_var,
        ).pack(side=tk.LEFT)

        # --- 出力 ---
        out_frame = ttk.LabelFrame(main, text="出力ファイル", padding=5)
        out_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Entry(out_frame, textvariable=self.output_path).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5),
        )
        ttk.Button(out_frame, text="参照", command=self._browse_output).pack(side=tk.RIGHT)

        # --- ボタン ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=5)

        self.run_btn = ttk.Button(
            btn_frame, text="変換開始", command=self._start_conversion,
        )
        self.run_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.progress = ttk.Progressbar(btn_frame, mode="indeterminate")
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- ログ ---
        log_frame = ttk.LabelFrame(main, text="ログ", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log = scrolledtext.ScrolledText(
            log_frame, height=12, font=("Courier", 10), state=tk.DISABLED,
        )
        self.log.pack(fill=tk.BOTH, expand=True)

    def _setup_drag_drop(self):
        try:
            self.root.tk.eval('package require tkdnd')
            from tkinterdnd2 import DND_FILES
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind('<<Drop>>', self._on_drop)
            self._dnd_available = True
        except Exception:
            self._dnd_available = False
            self.drop_label.config(
                text="クリックしてファイルを選択\n(tkinterdnd2をインストールするとドラッグ&ドロップ対応)",
            )

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")
        if os.path.isfile(path):
            self._set_input(path)

    def _set_input(self, path: str):
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            messagebox.showwarning("非対応形式", f"対応していないファイル形式です: {ext}")
            return
        self.input_path.set(path)
        if not self.output_path.get():
            out = str(Path(path).with_suffix(".converted.wav"))
            self.output_path.set(out)
        self.drop_label.config(text=f"選択済: {Path(path).name}", fg="#333333")
        self._log(f"ファイル選択: {path}")

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="動画/音声ファイルを選択",
            filetypes=[
                ("動画/音声ファイル", "*.mp4 *.mkv *.avi *.mov *.webm *.flv "
                 "*.mp3 *.wav *.ogg *.flac *.m4a *.aac *.wma"),
                ("すべてのファイル", "*.*"),
            ],
        )
        if path:
            self._set_input(path)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="出力ファイルを指定",
            defaultextension=".wav",
            filetypes=[("WAVファイル", "*.wav"), ("すべてのファイル", "*.*")],
        )
        if path:
            self.output_path.set(path)

    def _log(self, msg: str):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def _start_conversion(self):
        if self.is_running:
            return

        input_path = self.input_path.get()
        output_path = self.output_path.get()

        if not input_path:
            messagebox.showwarning("未選択", "入力ファイルを選択してください")
            return
        if not output_path:
            messagebox.showwarning("未選択", "出力ファイルを指定してください")
            return
        if not os.path.exists(input_path):
            messagebox.showerror("エラー", f"ファイルが見つかりません:\n{input_path}")
            return

        self.is_running = True
        self.run_btn.config(state=tk.DISABLED)
        self.progress.start(10)

        thread = threading.Thread(
            target=self._run_conversion,
            args=(input_path, output_path),
            daemon=True,
        )
        thread.start()

    def _run_conversion(self, input_path: str, output_path: str):
        import builtins
        original_print = builtins.print

        def gui_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            self.root.after(0, self._log, msg)
            original_print(*args, **kwargs)

        builtins.print = gui_print

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                wav_path = os.path.join(tmpdir, "audio.wav")
                extract_audio(input_path, wav_path)

                segments = transcribe(wav_path, self.model_var.get())
                if not segments:
                    self.root.after(0, self._log, "文字起こし結果が空です")
                    return

                diarization = diarize(wav_path, num_speakers=2)
                segments = assign_speakers(segments, diarization)

                save_transcript(segments, output_path)

                if self.transcript_only_var.get():
                    self.root.after(0, self._log, "完了（文字起こし+話者分離のみ）")
                    self.root.after(0, lambda: messagebox.showinfo(
                        "完了", f"トランスクリプト保存先:\n{Path(output_path).with_suffix('.json')}",
                    ))
                    return

                speaker_a_id = VOICEVOX_SPEAKERS[self.speaker_a_var.get()]
                speaker_b_id = VOICEVOX_SPEAKERS[self.speaker_b_var.get()]

                result = synthesize_conversation(
                    segments, speaker_a_id, speaker_b_id,
                    match_timing=self.timing_var.get(),
                )
                result.export(output_path, format="wav")

                self.root.after(0, self._log, f"\n完了: {output_path}")
                self.root.after(0, lambda: messagebox.showinfo(
                    "完了", f"変換完了!\n{output_path}",
                ))

        except Exception as e:
            self.root.after(0, self._log, f"エラー: {e}")
            self.root.after(0, lambda: messagebox.showerror("エラー", str(e)))
        finally:
            builtins.print = original_print
            self.root.after(0, self._on_conversion_done)

    def _on_conversion_done(self):
        self.is_running = False
        self.run_btn.config(state=tk.NORMAL)
        self.progress.stop()


def main():
    try:
        import tkinterdnd2
        root = tkinterdnd2.TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()

    VoiceConverterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
