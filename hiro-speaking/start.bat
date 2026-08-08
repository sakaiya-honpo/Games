@echo off
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
    echo Node.js が見つかりません。
    echo https://nodejs.org/ からインストールしてください。
    pause
    exit /b 1
)

if not exist "node_modules" (
    echo 依存パッケージをインストール中...
    call npm install
)

echo.
echo   Hiro 英会話練習アプリ
echo   ブラウザで開く: http://localhost:3000
echo   停止する: Ctrl+C
echo.

node server.js
pause
