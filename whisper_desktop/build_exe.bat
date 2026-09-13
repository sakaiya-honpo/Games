@echo off
echo === Whisper Desktop exe ビルド ===
echo.
echo 依存パッケージをインストール中...
pip install faster-whisper customtkinter sounddevice numpy pyinstaller
echo.
echo exe をビルド中（数分かかります）...
pyinstaller --noconfirm --onedir --windowed ^
    --name "WhisperDesktop" ^
    --add-data "requirements.txt;." ^
    --hidden-import faster_whisper ^
    --hidden-import sounddevice ^
    --hidden-import numpy ^
    --hidden-import customtkinter ^
    --collect-all faster_whisper ^
    --collect-all ctranslate2 ^
    --collect-all customtkinter ^
    whisper_desktop.py
echo.
echo === ビルド完了 ===
echo dist\WhisperDesktop フォルダ内の WhisperDesktop.exe を実行してください
echo.
pause
