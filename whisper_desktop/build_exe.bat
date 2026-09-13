@echo off
echo === Whisper Desktop exe ビルド ===
echo.
echo 依存パッケージをインストール中...
python -m pip install faster-whisper customtkinter sounddevice numpy pyinstaller
echo.
echo exe をビルド中（数分かかります）...
python -m PyInstaller --noconfirm --onedir --windowed ^
    --name "WhisperDesktop" ^
    --add-data "requirements.txt;." ^
    --add-data "hotwords;hotwords" ^
    --hidden-import faster_whisper ^
    --hidden-import sounddevice ^
    --hidden-import numpy ^
    --hidden-import customtkinter ^
    --collect-all faster_whisper ^
    --collect-all ctranslate2 ^
    --collect-all customtkinter ^
    whisper_desktop.py
echo.
echo === デスクトップにコピー中 ===
set DESKTOP=%USERPROFILE%\Desktop\WhisperDesktop
if exist "%DESKTOP%" rmdir /s /q "%DESKTOP%"
xcopy /e /i /q "dist\WhisperDesktop" "%DESKTOP%"
echo.
echo === 完了 ===
echo デスクトップの WhisperDesktop\WhisperDesktop.exe を実行してください
echo.
pause
