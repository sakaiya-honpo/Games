@echo off
chcp 65001 > nul
echo ============================================
echo  AAR Tool - Windows ビルドスクリプト
echo ============================================
echo.

:: Python バージョン確認
python --version 2>nul
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。Python 3.10 以降をインストールしてください。
    pause
    exit /b 1
)

:: 依存パッケージインストール
echo [1/3] 依存パッケージをインストールしています...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] requirements.txt のインストールに失敗しました。
    pause
    exit /b 1
)

:: PyInstaller インストール
pip install pyinstaller>=6.0
if errorlevel 1 (
    echo [ERROR] PyInstaller のインストールに失敗しました。
    pause
    exit /b 1
)

:: ビルド前のクリーンアップ
echo.
echo [2/3] ビルドディレクトリをクリーンアップしています...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

:: PyInstaller でビルド
echo.
echo [3/3] アプリケーションをビルドしています（数分かかります）...
pyinstaller aar_tool.spec
if errorlevel 1 (
    echo [ERROR] ビルドに失敗しました。上記のエラーメッセージを確認してください。
    pause
    exit /b 1
)

echo.
echo ============================================
echo  ビルド完了！
echo  実行ファイル: dist\AARTool\AARTool.exe
echo ============================================
echo.
echo 注意: 初回起動時に PaddleOCR の日本語モデルを
echo       ダウンロードします（約400MB、要インターネット接続）。
echo       2回目以降はオフラインで動作します。
echo.
pause
