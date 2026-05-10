# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for aar_tool (Windows OCR 版)
#
# Build:
#   pip install pyinstaller
#   pyinstaller aar_tool.spec
#
# 出力先: dist/AARTool/AARTool.exe  (onedir モード)
#
# 注意: Windows の日本語言語パックが必要です（通常インストール済み）。
#       Ollama を別途起動しておく必要があります。

from PyInstaller.utils.hooks import collect_all, collect_data_files

# winocr / winrt: Windows Runtime OCR バインディング
winocr_datas,      winocr_binaries,      winocr_hidden      = collect_all('winocr')
pygetwindow_datas, pygetwindow_binaries, pygetwindow_hidden = collect_all('pygetwindow')
pyautogui_datas,   pyautogui_binaries,   pyautogui_hidden   = collect_all('pyautogui')
ollama_datas,      ollama_binaries,      ollama_hidden      = collect_all('ollama')

# winrt-* パッケージを収集（winocr の依存）
try:
    winrt_datas, winrt_binaries, winrt_hidden = collect_all('winrt')
except Exception:
    winrt_datas, winrt_binaries, winrt_hidden = [], [], []

all_datas    = winocr_datas    + pygetwindow_datas    + pyautogui_datas    + ollama_datas    + winrt_datas
all_binaries = winocr_binaries + pygetwindow_binaries + pyautogui_binaries + ollama_binaries + winrt_binaries
all_hidden   = (
    winocr_hidden
    + pygetwindow_hidden
    + pyautogui_hidden
    + ollama_hidden
    + winrt_hidden
    + [
        'PIL._tkinter_finder',
        'requests',
        'tqdm',
    ]
)

block_cipher = None

a = Analysis(
    ['aar_tool.py'],
    pathex=[],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'jupyter', 'IPython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AARTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AARTool',
)
