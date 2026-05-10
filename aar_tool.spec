# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for aar_tool
#
# Build:
#   pip install pyinstaller
#   pyinstaller aar_tool.spec
#
# 出力先: dist/AARTool/AARTool.exe  (onedir モード)
#
# 注意: 初回起動時に PaddleOCR が日本語モデルをダウンロードします（約400MB）。
#       モデルは %USERPROFILE%\.paddleocr\ にキャッシュされます。

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# --------------------------------------------------------------------------
# paddlepaddle / paddleocr は内部モジュールが多いため collect_all で一括収集
# --------------------------------------------------------------------------
paddle_datas, paddle_binaries, paddle_hiddenimports = collect_all('paddle')
paddleocr_datas, paddleocr_binaries, paddleocr_hiddenimports = collect_all('paddleocr')

# pyclipper は paddleocr の依存で動的ロードされる
pyclipper_datas, pyclipper_binaries, pyclipper_hiddenimports = collect_all('pyclipper')
pygetwindow_datas, pygetwindow_binaries, pygetwindow_hiddenimports = collect_all('pygetwindow')

# Cython は paddle の依存として引き込まれるが .cpp 等のデータファイルが必要
cython_datas = collect_data_files('Cython')

all_datas    = paddle_datas + paddleocr_datas + pyclipper_datas + pygetwindow_datas + cython_datas
all_binaries = paddle_binaries + paddleocr_binaries + pyclipper_binaries + pygetwindow_binaries
all_hidden   = (
    paddle_hiddenimports
    + paddleocr_hiddenimports
    + pyclipper_hiddenimports
    + pygetwindow_hiddenimports
    + collect_submodules('skimage')
    + collect_submodules('scipy')
    + collect_submodules('cv2')
    + [
        'PIL._tkinter_finder',
        'lmdb',
        'rapidfuzz',
        'shapely',
        'shapely.geometry',
        'imghdr',
        'requests',
        'tqdm',
        'yaml',
        'six',
        'cachetools',
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
    excludes=[
        'matplotlib',
        'jupyter',
        'IPython',
    ],
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
    upx=True,
    console=False,          # コンソールウィンドウを非表示
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # アイコンを用意する場合は 'aar_tool.ico' を指定
)

# onedir モード（単一フォルダに展開）: paddle の大量DLLを扱いやすくするため
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AARTool',
)
