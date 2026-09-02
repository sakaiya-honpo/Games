#!/usr/bin/env python3
"""発話区間を検出して JSON を出力する（口パク自動配置用）。

各話者の WAV（Premiere でカット確定後に書き出したもの）を渡すと、
「音が鳴っている区間 = 喋っている区間」を検出して
place_mouth.jsx が読める JSON を書き出す。

文字起こしはしない（口パクは音の有無だけで決まるため）。
依存は numpy と標準ライブラリ wave のみ。

使い方:
    python detect_speech.py A.wav B.wav
    python detect_speech.py A.wav -o A_mouth.json --min-speech 120 --min-silence 300

入力は WAV（16bit / 32bit int / 32bit float, モノ or ステレオ）。
FLAC しか無い場合は Premiere か Audacity で WAV に書き出してから渡す。
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """WAV を float32 モノ波形（-1..1）とサンプルレートで返す。"""
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sampwidth == 4:
        # 32bit int か 32bit float かは wave からは判別できないので int を仮定。
        # 値域から float っぽければ float とみなす。
        as_int = np.frombuffer(raw, dtype="<i4")
        if as_int.size and np.abs(as_int).max() <= 1_000_000:
            data = np.frombuffer(raw, dtype="<f4").astype(np.float32)
        else:
            data = as_int.astype(np.float32) / 2147483648.0
    elif sampwidth == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(
            f"未対応のビット深度です（{sampwidth * 8}bit）。16bit の WAV で書き出してください: {path}"
        )

    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)
    return data, framerate


def detect_segments(
    data: np.ndarray,
    sr: int,
    win_ms: float = 30.0,
    threshold_offset_db: float = 8.0,
    min_speech_ms: float = 120.0,
    min_silence_ms: float = 300.0,
    pad_ms: float = 80.0,
) -> list[dict]:
    """RMS ベースの発話区間検出。

    ノイズフロアからの相対しきい値なので、トラックごとの音量差を吸収する。
    Craig の話者別トラックのように各人が独立した音声なら十分機能する。
    """
    win = max(1, int(sr * win_ms / 1000.0))
    n_win = int(np.ceil(len(data) / win))
    if n_win == 0:
        return []

    padded = np.zeros(n_win * win, dtype=np.float32)
    padded[: len(data)] = data
    frames = padded.reshape(n_win, win)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms + 1e-9)

    # ノイズフロア = 下位パーセンタイル。しきい値はそこから +offset。
    noise_floor = np.percentile(db, 10.0)
    peak = np.percentile(db, 95.0)
    # 無音トラック対策: ピークとフロアの差が小さい場合は全区間無音扱い。
    if peak - noise_floor < 3.0:
        return []
    threshold = noise_floor + threshold_offset_db

    voiced = db > threshold

    # フレーム index -> 秒
    def f2s(i: int) -> float:
        return i * win / sr

    # 連続する voiced をまとめる
    segments: list[list[float]] = []
    start = None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segments.append([f2s(start), f2s(i)])
            start = None
    if start is not None:
        segments.append([f2s(start), f2s(len(voiced))])

    if not segments:
        return []

    # 前後にパディング（語頭・語尾の欠けと口の追従を自然にする）
    pad = pad_ms / 1000.0
    total = len(data) / sr
    segments = [[max(0.0, s - pad), min(total, e + pad)] for s, e in segments]

    # 短い無音ギャップを結合（口のチカチカ防止）
    min_sil = min_silence_ms / 1000.0
    merged = [segments[0]]
    for s, e in segments[1:]:
        if s - merged[-1][1] <= min_sil:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # 極端に短い区間を除去
    min_sp = min_speech_ms / 1000.0
    result = [
        {"start": round(s, 3), "end": round(e, 3)}
        for s, e in merged
        if e - s >= min_sp
    ]
    return result


def process(path: Path, args) -> dict:
    data, sr = read_wav(path)
    segments = detect_segments(
        data,
        sr,
        win_ms=args.win,
        threshold_offset_db=args.threshold,
        min_speech_ms=args.min_speech,
        min_silence_ms=args.min_silence,
        pad_ms=args.pad,
    )
    return {
        "source": path.name,
        "sample_rate": sr,
        "duration": round(len(data) / sr, 3),
        "count": len(segments),
        "segments": segments,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="発話区間を検出して JSON 出力")
    p.add_argument("inputs", nargs="+", help="WAV ファイル（複数可）")
    p.add_argument("-o", "--out", help="出力 JSON（入力1つのときのみ有効。省略時は <入力名>_mouth.json）")
    p.add_argument("--win", type=float, default=30.0, help="解析窓 ms（既定 30）")
    p.add_argument("--threshold", type=float, default=8.0, help="ノイズフロアからのしきい値 dB（既定 8。小さいほど敏感）")
    p.add_argument("--min-speech", type=float, default=120.0, help="最小発話長 ms（既定 120）")
    p.add_argument("--min-silence", type=float, default=300.0, help="結合する最大無音 ms（既定 300）")
    p.add_argument("--pad", type=float, default=80.0, help="区間前後のパディング ms（既定 80）")
    args = p.parse_args(argv)

    inputs = [Path(x) for x in args.inputs]
    for path in inputs:
        if not path.exists():
            print(f"ファイルが見つかりません: {path}", file=sys.stderr)
            return 1

    if args.out and len(inputs) > 1:
        print("-o は入力が1つのときだけ使えます。複数のときは自動命名します。", file=sys.stderr)
        return 1

    for path in inputs:
        result = process(path, args)
        out = Path(args.out) if args.out else path.with_name(path.stem + "_mouth.json")
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name}: {result['count']} 区間 -> {out.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
