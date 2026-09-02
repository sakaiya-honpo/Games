# premiere-lipsync

2人トーク動画で、**各話者が喋っている区間だけ口パク mp4 を自動配置**するための最小ツール。
字幕は Premiere 標準機能（文字起こし → キャプション）で付ける想定なので、ここには含まない。
口パクは「音が鳴っている＝喋っている」だけで決まるため、文字起こし（Whisper）は使わない。

対象: Premiere Pro 25.6.4 / Windows

## 構成

```
録音（Craig で話者別トラック録音）
  → Premiere でカット確定 → カット後の各トラック音声を WAV 書き出し
    → detect_speech.py  … 発話区間を検出して *_mouth.json を出力
      → place_mouth.jsx … その区間に口パク mp4 を敷き詰め配置
```

立ち絵は「下のトラックに閉じ口の静止画を常時表示」「その上のトラックに喋っている区間だけ口パク mp4」を重ねる。
口パク mp4 は短いループでよい。区間の長さに合わせて自動で繰り返し配置する。

## 1. 収録

1. Discord に全員入り、Craig で `/join` → 収録 → `/leave`。
2. ダウンロードは **Multi-track / FLAC**（話者ごとに別ファイル）。
3. 冒頭に全員で合図音（せーの・手拍子）を入れておくと、後でトラック同期が楽。

## 2. Premiere でカット

1. 映像＋各トラック音声を読み込み、合図音で頭を合わせる。
2. **文字起こしベース編集（標準機能）** で不要部分をカット。
3. 字幕が必要なら、この段階で文字起こし → キャプション生成（日本語対応）。
4. カット確定後、**各話者トラックの音声を WAV で書き出す**
   （16bit PCM 推奨。ステレオ可）。この WAV が口パク検出の入力になる。

> カットは必ず detect_speech.py の前に確定する。後でカットすると時刻がずれる。

## 3. 発話区間を検出（detect_speech.py）

```bash
pip install -r requirements.txt      # 初回のみ（numpy）
python detect_speech.py A.wav B.wav
# -> A_mouth.json / B_mouth.json が出力される
```

主なオプション（既定で概ね良好。口パクが細切れ／もたつくとき調整）:

| オプション | 既定 | 説明 |
|---|---|---|
| `--threshold` | 8.0 | ノイズフロアからのしきい値 dB。小さいほど敏感（拾いすぎるなら上げる） |
| `--min-speech` | 120 | この長さ未満の区間は捨てる (ms) |
| `--min-silence` | 300 | この長さ未満の無音は前後をつなぐ (ms)。口のチカチカ防止 |
| `--pad` | 80 | 区間前後の余白 (ms)。口の追従を自然にする |

出力 JSON:

```json
{
  "source": "A.wav",
  "sample_rate": 48000,
  "duration": 612.34,
  "count": 210,
  "segments": [ { "start": 1.23, "end": 3.40 }, ... ]
}
```

## 4. Premiere に口パクを自動配置（place_mouth.jsx）

### 準備
1. 口パク mp4 を Premiere の**プロジェクトパネルに読み込む**。
2. 口パクを乗せる**空のビデオトラックを追加**（例: V3）。
3. `place_mouth.jsx` の先頭 `CONFIG` を自分の環境に合わせる:
   - `flapClipName` … プロジェクトパネルでの口パク mp4 の表示名
   - `loopSeconds` … 口パク mp4 の尺（秒）
   - `videoTrackNumber` … 配置先トラック番号（V3 なら 3）

キャラ A/B で口パク素材やトラックが違うので、**話者ごとに CONFIG を変えて 2 回実行**する
（A を V3、B を V4 など）。

### 実行方法（ExtendScript Debugger / 無料）
1. VS Code に拡張「**ExtendScript Debugger**」(Adobe 公式) を入れる。
2. Premiere を起動し、対象シーケンスをアクティブにする。
3. VS Code で `place_mouth.jsx` を開き、`F5`（または実行）→ ターゲットに **Adobe Premiere Pro** を選ぶ。
4. ファイル選択ダイアログで、その話者の `*_mouth.json` を選ぶ。
5. 完了ダイアログが出れば配置完了。もう一方の話者は CONFIG を変えて再実行。

## 5. 仕上げ

- 口パクのタイミングや字幕を微調整して書き出し。

## 調整メモ / トラブルシュート

- **口パクが尺いっぱいに伸びず端で途切れる / 逆に長すぎる**
  `place_mouth.jsx` の `CONFIG.MEDIA_TYPE` を `4` → `1` に変えて再実行
  （Premiere のバージョン差で in/out トリムの mediaType が異なるため）。
- **口パクが細かく点滅する** … `--min-silence` を大きく（例 500）。
- **喋りだしで口が遅れる** … `--pad` を大きく（例 120）。
- **静かな相槌を拾わない** … `--threshold` を小さく（例 5）。
- **24bit WAV でエラー** … Premiere の書き出しを 16bit PCM にする。
- FLAC しか無い場合は Premiere か Audacity で WAV に変換してから detect_speech.py へ。

## 注意

- `place_mouth.jsx` は Premiere 実機での動作確認が未実施（この環境では Premiere を実行できないため）。
  最初の 1 回はテスト用シーケンスで挙動を確認し、必要なら CONFIG / MEDIA_TYPE を調整すること。
