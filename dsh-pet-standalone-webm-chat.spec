# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets/characters', 'assets/characters'), ('assets/big_blue_fat_fish', 'assets/big_blue_fat_fish'), ('pet/persona_presets', 'pet/persona_presets'), ('pet/menu_templates', 'pet/menu_templates'), ('pet/chat/legacy_styles.qss', 'pet/chat'), ('pet/chat/modern_styles.qss', 'pet/chat'), ('assets/sounds', 'assets/sounds'), ('assets/chat', 'assets/chat'), ('assets/icon.ico', 'assets'), ('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.'), ('integrations', 'integrations'), ('resources/presentmon', 'resources/presentmon'), ('resources/rapidocr_models', 'resources/rapidocr_models'), ('G:/Anaconda3/envs/dafeiyu-ai/Lib/site-packages/rapidocr/config.yaml', 'rapidocr'), ('G:/Anaconda3/envs/dafeiyu-ai/Lib/site-packages/rapidocr/default_models.yaml', 'rapidocr')]
binaries = [('G:/Anaconda3/envs/dafeiyu-ai/Library/bin/ffi.dll', '.'), ('G:/Anaconda3/envs/dafeiyu-ai/Library/bin/libcrypto-3-x64.dll', '.'), ('G:/Anaconda3/envs/dafeiyu-ai/Library/bin/libssl-3-x64.dll', '.')]
hiddenimports = ['pet.screen_capture.ocr', 'pet.screen_capture.ocr_worker']
tmp_ret = collect_all('imageio_ffmpeg')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('certifi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PySide6.QtMultimedia')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('edge_tts')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiofiles')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tzdata')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('psutil')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('keyring')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('win32ctypes')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['packaging/pet_entry.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['OpenGL', 'torch', 'transformers', 'datasets', 'langchain', 'langchain_core', 'langchain_openai', 'langsmith', 'langgraph', 'wandb', 'sentry_sdk', 'pandas', 'numba', 'llvmlite', 'pyarrow', 'polars', '_polars_runtime_32', 'spacy', 'playwright', 'narwhals', 'sympy', 'fsspec', 'PyQt5', 'PyQt6', 'PySide2', 'matplotlib', 'matplotlib_inline', 'seaborn', 'IPython', 'ipykernel', 'jupyter_client', 'jupyter_core', 'nbformat', 'zmq', 'django', 'hypothesis'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='dsh-pet-standalone-webm-chat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='dsh-pet-standalone-webm-chat',
)
