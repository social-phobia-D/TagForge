# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
ROOT = os.path.abspath(SPECPATH)

# qfluentwidgets 运行时需要包内字体/图标/样式资源
from PyInstaller.utils.hooks import collect_all
qfw_datas, qfw_bins, qfw_hidden = collect_all('qfluentwidgets')

a = Analysis(
    ['main.py'],
    pathex=[ROOT],
    binaries=qfw_bins,
    datas=[
        ('app', 'app'),                      # 引擎源码，供 sidecar worker 使用
        ('icon.ico', '.'),                   # 运行时窗口图标
    ] + qfw_datas,
    hiddenimports=qfw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 重引擎依赖（torch 系），sidecar 运行时按需安装
        'torch', 'torchvision', 'transformers', 'ultralytics',
        'bitsandbytes', 'accelerate', 'tkinter', 'matplotlib', 'scipy',
        # 构建环境污染带入、主程序完全不用的包（实测约占 70MB）
        'boto3', 'botocore', 'pandas', 'lxml', 'cryptography', 'bcrypt',
        'paramiko', 'invoke', 'fabric', 'jsonschema', 'hf_xet',
        # 未使用的 Qt 模块（QML/多媒体/PDF/图表等，界面只用 Widgets）
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtCharts',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
        'PySide6.QtLocation', 'PySide6.QtSensors', 'PySide6.QtSerialPort',
        'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.Qt3DCore',
    ],
    # PySide6 在冻结包中需要真实的包路径来注册 Qt DLL 搜索目录。
    noarchive=True,
)

# excludes 只能拦 Python 模块，PySide6 hook 捆绑的 Qt DLL 需在这里过滤。
# 保留 opengl32sw.dll 作为无 GL 驱动环境的软渲染兜底。
_DROP_DLLS = ('Qt6Pdf', 'Qt6Quick', 'Qt6Qml', 'Qt6QuickWidgets', 'Qt6Charts')
a.binaries = [b for b in a.binaries
              if not any(d in b[0] for d in _DROP_DLLS)]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TagForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='TagForge',
)
