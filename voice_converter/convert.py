#!/usr/bin/env python3
"""
音声/動画ファイルから文字起こし → 話者分離 → VOICEVOX合成

使い方:
    python convert.py input.mp4 --output output.wav
    python convert.py input.wav --speaker-a 3 --speaker-b 1

前提:
    - VOICEVOXエンジンが localhost:50021 で起動していること
    - ffmpeg がインストールされていること
"""

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
import requests
import torch
import whisper
from pydub import AudioSegment

VOICEVOX_URL = "http://localhost:50021"

# VOICEVOX話者ID (デフォルト)
# 3: ずんだもん（ノーマル）, 2: 四国めたん（ノーマル）
DEFAULT_SPEAKER_A = 3
DEFAULT_SPEAKER_B = 2


def extract_audio(input_path: str, output_path: str) -> str:
    """動画/音声ファイルからWAV(16kHz mono)を抽出"""
    print(f"[1/4] 音声抽出中: {input_path}")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-ac", "1", "-ar", "16000", "-f", "wav", output_path,
        ],
        capture_output=True, check=True,
    )
    return output_path


def transcribe(audio_path: str, model_size: str = "base") -> list[dict]:
    """Whisperで文字起こし（タイムスタンプ付き）"""
    print(f"[2/4] 文字起こし中 (model={model_size})...")
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, language="ja", verbose=False)
    segments = []
    for seg in result["segments"]:
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })
    print(f"  → {len(segments)}セグメント検出")
    return segments


def diarize(audio_path: str, num_speakers: int = 2) -> list[dict]:
    """speechbrainで話者分離し、各時間帯のラベルを返す"""
    print(f"[3/4] 話者分離中 (speakers={num_speakers})...")
    from speechbrain.inference.speaker import EncoderClassifier
    classifier = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": "cpu"},
    )

    audio = AudioSegment.from_wav(audio_path)
    chunk_ms = 2000  # 2秒チャンク
    embeddings = []
    chunks = []

    for i in range(0, len(audio), chunk_ms):
        chunk = audio[i:i + chunk_ms]
        if len(chunk) < 500:
            continue
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            chunk.export(f.name, format="wav")
            try:
                emb = classifier.encode_batch(
                    classifier.load_audio(f.name).unsqueeze(0)
                )
                embeddings.append(emb.squeeze().detach().numpy())
                chunks.append({"start": i / 1000.0, "end": (i + len(chunk)) / 1000.0})
            finally:
                os.unlink(f.name)

    if not embeddings:
        print("  → 音声チャンクが不足しています")
        return []

    emb_matrix = np.stack(embeddings)

    from sklearn.cluster import KMeans
    kmeans = KMeans(n_clusters=num_speakers, random_state=42, n_init=10)
    labels = kmeans.fit_predict(emb_matrix)

    for i, chunk in enumerate(chunks):
        chunk["speaker"] = int(labels[i])

    print(f"  → {len(chunks)}チャンクを{num_speakers}話者に分離")
    return chunks


def assign_speakers(segments: list[dict], diarization: list[dict]) -> list[dict]:
    """文字起こしセグメントに話者ラベルを割り当て"""
    for seg in segments:
        seg_mid = (seg["start"] + seg["end"]) / 2.0
        best = None
        best_dist = float("inf")
        for d in diarization:
            d_mid = (d["start"] + d["end"]) / 2.0
            dist = abs(seg_mid - d_mid)
            if dist < best_dist:
                best_dist = dist
                best = d
        seg["speaker"] = best["speaker"] if best else 0
    return segments


def voicevox_synth(text: str, speaker_id: int, speed: float = 1.0) -> bytes:
    """VOICEVOXで音声合成"""
    query = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": speaker_id},
    )
    query.raise_for_status()
    query_data = query.json()

    query_data["speedScale"] = speed
    query_data["outputSamplingRate"] = 16000

    synth = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": speaker_id},
        json=query_data,
    )
    synth.raise_for_status()
    return synth.content


def calc_speed(target_duration: float, audio_bytes: bytes) -> float:
    """元の発話時間に合うようにspeedScaleを計算"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        f.flush()
        try:
            audio = AudioSegment.from_wav(f.name)
            current_duration = len(audio) / 1000.0
        finally:
            os.unlink(f.name)

    if current_duration <= 0 or target_duration <= 0:
        return 1.0
    ratio = current_duration / target_duration
    return max(0.5, min(2.0, ratio))


def synthesize_conversation(
    segments: list[dict],
    speaker_a_id: int,
    speaker_b_id: int,
    match_timing: bool = True,
) -> AudioSegment:
    """話者付きセグメントからVOICEVOX音声を合成"""
    print("[4/4] VOICEVOX合成中...")

    try:
        requests.get(f"{VOICEVOX_URL}/version")
    except requests.ConnectionError:
        print("エラー: VOICEVOXエンジンに接続できません")
        print("  VOICEVOXを起動して localhost:50021 で待機させてください")
        sys.exit(1)

    speaker_map = {0: speaker_a_id, 1: speaker_b_id}
    output = AudioSegment.silent(duration=0)
    last_end = 0.0

    for i, seg in enumerate(segments):
        if not seg["text"]:
            continue

        speaker_id = speaker_map.get(seg["speaker"], speaker_a_id)
        target_duration = seg["end"] - seg["start"]

        # まず通常速度で合成
        audio_bytes = voicevox_synth(seg["text"], speaker_id)

        if match_timing:
            speed = calc_speed(target_duration, audio_bytes)
            if abs(speed - 1.0) > 0.1:
                audio_bytes = voicevox_synth(seg["text"], speaker_id, speed)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            f.flush()
            try:
                seg_audio = AudioSegment.from_wav(f.name)
            finally:
                os.unlink(f.name)

        # 元のタイミングに合わせて無音を挿入
        if match_timing:
            gap = seg["start"] - last_end
            if gap > 0.05:
                output += AudioSegment.silent(duration=int(gap * 1000))

        output += seg_audio
        last_end = seg["end"]

        speaker_name = "A" if seg["speaker"] == 0 else "B"
        print(f"  [{speaker_name}] {seg['text'][:40]}")

    return output


def save_transcript(segments: list[dict], output_path: str):
    """中間データ（文字起こし+話者）をJSONで保存"""
    json_path = Path(output_path).with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    print(f"トランスクリプト保存: {json_path}")


def main():
    parser = argparse.ArgumentParser(
        description="録音データ → 話者分離 → VOICEVOX音声変換"
    )
    parser.add_argument("input", help="入力ファイル（動画/音声）")
    parser.add_argument("--output", "-o", default="output.wav", help="出力WAVファイル")
    parser.add_argument("--model", default="base", help="Whisperモデル (tiny/base/small/medium/large)")
    parser.add_argument("--speaker-a", type=int, default=DEFAULT_SPEAKER_A, help="話者AのVOICEVOX ID (default: 3=ずんだもん)")
    parser.add_argument("--speaker-b", type=int, default=DEFAULT_SPEAKER_B, help="話者BのVOICEVOX ID (default: 2=四国めたん)")
    parser.add_argument("--no-timing", action="store_true", help="タイミング調整を無効化")
    parser.add_argument("--transcript-only", action="store_true", help="文字起こし+話者分離のみ（VOICEVOX不要）")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"エラー: ファイルが見つかりません: {args.input}")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")
        extract_audio(args.input, wav_path)

        segments = transcribe(wav_path, args.model)
        if not segments:
            print("文字起こし結果が空です")
            sys.exit(1)

        diarization = diarize(wav_path, num_speakers=2)
        segments = assign_speakers(segments, diarization)

        save_transcript(segments, args.output)

        if args.transcript_only:
            print("完了（transcript-onlyモード）")
            return

        result = synthesize_conversation(
            segments,
            args.speaker_a,
            args.speaker_b,
            match_timing=not args.no_timing,
        )

        result.export(args.output, format="wav")
        print(f"\n完了: {args.output}")


if __name__ == "__main__":
    main()
