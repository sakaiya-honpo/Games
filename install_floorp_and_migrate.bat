@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo   Floorp インストール ^& Vivaldi データ移行
echo ============================================================
echo.

:: 管理者権限チェック
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [情報] 管理者権限なしで実行中。winget が使えない場合は
    echo        管理者として再実行してください。
)

:: Python チェック
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [エラー] Python が見つかりません。
    echo Python 3.8以上をインストールしてください。
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 必要ライブラリのインストール
echo [1/4] 必要なPythonライブラリをインストール中...
pip install pycryptodomex pywin32 >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] ライブラリのインストールに問題がありました。
    echo        pip install pycryptodomex pywin32 を手動実行してください。
)

:: Floorp インストール確認
echo [2/4] Floorp のインストール状況を確認中...
set FLOORP_INSTALLED=0

:: レジストリで確認
reg query "HKLM\SOFTWARE\Mozilla\Floorp" >nul 2>&1 && set FLOORP_INSTALLED=1
reg query "HKCU\SOFTWARE\Mozilla\Floorp" >nul 2>&1 && set FLOORP_INSTALLED=1

:: パスで確認
if exist "%ProgramFiles%\Floorp\floorp.exe" set FLOORP_INSTALLED=1
if exist "%ProgramFiles(x86)%\Floorp\floorp.exe" set FLOORP_INSTALLED=1
if exist "%LOCALAPPDATA%\Floorp\floorp.exe" set FLOORP_INSTALLED=1

if %FLOORP_INSTALLED%==1 (
    echo   Floorp は既にインストールされています。
) else (
    echo   Floorp が見つかりません。インストールを試みます...
    echo.

    :: winget で Floorp をインストール
    winget install Ablaze.Floorp --accept-package-agreements --accept-source-agreements 2>nul
    if !errorlevel! equ 0 (
        echo   Floorp のインストールが完了しました。
    ) else (
        echo.
        echo   [警告] winget でのインストールに失敗しました。
        echo   以下から手動でインストールしてください:
        echo     https://floorp.app/ja/download
        echo.
        echo   インストール後、このスクリプトを再実行するか、
        echo   直接 python vivaldi_to_floorp.py を実行してください。
        echo.
        choice /c YN /m "手動インストール済みですか？続行しますか？"
        if !errorlevel! equ 2 (
            pause
            exit /b 1
        )
    )
)

:: Vivaldi が起動中か確認
echo [3/4] Vivaldi の起動状態を確認中...
tasklist /fi "imagename eq vivaldi.exe" 2>nul | find "vivaldi.exe" >nul
if %errorlevel% equ 0 (
    echo.
    echo   [警告] Vivaldi が起動中です！
    echo   パスワードのエクスポートにはVivaldiを閉じる必要があります。
    echo.
    choice /c YN /m "  Vivaldi を自動的に閉じますか？"
    if !errorlevel! equ 1 (
        taskkill /im vivaldi.exe /f >nul 2>&1
        timeout /t 2 /nobreak >nul
        echo   Vivaldi を閉じました。
    ) else (
        echo   Vivaldi を手動で閉じてから再実行してください。
        pause
        exit /b 1
    )
)

:: データ移行実行
echo [4/4] Vivaldi データのエクスポートを実行中...
echo.
python "%~dp0vivaldi_to_floorp.py" -o "%USERPROFILE%\Desktop\vivaldi_export"

echo.
echo ============================================================
echo   完了！
echo   デスクトップの vivaldi_export フォルダにデータが出力されました。
echo   上記の手順に従って Floorp にインポートしてください。
echo ============================================================
echo.
echo ⚠ インポート完了後、passwords.csv は必ず削除してください。
echo.
pause
