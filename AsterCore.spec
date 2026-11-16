# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# sentencepiece / ctranslate2 都是**在函数里延迟导入**的，PyInstaller 静态分析
# 扫不到，必须在这里显式列出。少了它们，打包版会报
#   DLL load failed while importing _sentencepiece
# 然后悄悄降级到词库兜底 —— 离线模型等于没装。
hiddenimports = [
    'winocr',
    'websockets.asyncio.server',
    'websockets.sync.server',
    'sentencepiece',
    'sentencepiece.sentencepiece_model_pb2',
    'ctranslate2',
]
hiddenimports += collect_submodules('winrt')
hiddenimports += collect_submodules('winsdk')

# ctranslate2 自带 ctranslate2.dll / libiomp5md.dll 等，需要随包带上
binaries = collect_dynamic_libs('ctranslate2')


a = Analysis(
    ['core_main.py'],
    pathex=[],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AsterCore',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AsterCore',
)
