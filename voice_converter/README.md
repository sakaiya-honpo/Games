# Voice Converter

録音データ（Discord通話等）を文字起こし → 話者分離 → VOICEVOX音声に変換するツール。

## セットアップ

```bash
cd voice_converter
pip install -r requirements.txt
```

VOICEVOXエンジンを起動しておく（ https://voicevox.hiroshiba.jp/ ）

## 使い方

### GUI（おすすめ）

```bash
python gui.py
```

- ファイルをドラッグ&ドロップ（tkinterdnd2インストール時）またはクリックで選択
- 話者キャラ・Whisperモデル等をGUIで設定
- ログがリアルタイム表示される

### CLI

```bash
# 基本（ずんだもん + 四国めたん）
python convert.py recording.mp4 -o output.wav

# 文字起こし+話者分離のみ（VOICEVOX不要）
python convert.py recording.wav --transcript-only

# 話者のキャラを変更
python convert.py recording.mp3 --speaker-a 3 --speaker-b 8

# タイミング調整なし（VOICEVOXデフォルト速度）
python convert.py recording.wav --no-timing

# 高精度モデル（遅いがより正確）
python convert.py recording.mp4 --model medium
```

## VOICEVOX 話者ID（抜粋）

| ID | キャラクター |
|----|-------------|
| 0  | 四国めたん（あまあま） |
| 2  | 四国めたん（ノーマル） |
| 3  | ずんだもん（ノーマル） |
| 1  | ずんだもん（あまあま） |
| 8  | 春日部つむぎ |
| 10 | 雨晴はう |
| 14 | 冥鳴ひまり |

全話者一覧: `curl http://localhost:50021/speakers | python -m json.tool`

## 出力

- `output.wav` — VOICEVOX合成音声
- `output.json` — 文字起こし+話者ラベル（中間データ）
