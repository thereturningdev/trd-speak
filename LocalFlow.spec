# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the LocalFlow DISTRIBUTION build (arm64, self-contained).
# See RELEASING.md. Build with:  .venv/bin/pyinstaller --noconfirm LocalFlow.spec
# Output: dist/LocalFlow.app  (no repo or .venv required at runtime).

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Embedded default model (base.en) so the app transcribes offline, no download.
# Populate with: .venv/bin/python scripts/fetch_model.py  (before building).
_MODEL_DIR = "models/faster-whisper-base.en"
if os.path.isdir(_MODEL_DIR):
    datas += [(_MODEL_DIR, _MODEL_DIR)]
else:
    raise SystemExit(
        f"Missing {_MODEL_DIR}. Run: .venv/bin/python scripts/fetch_model.py"
    )

# Heavy / native-dependency packages — pull their dylibs, data files, and
# submodules so the frozen app needs nothing from the build machine.
for pkg in (
    "faster_whisper",
    "ctranslate2",
    "av",            # PyAV -> bundled ffmpeg dylibs
    "onnxruntime",
    "tokenizers",
    "sounddevice",   # -> _sounddevice_data/portaudio
    "numpy",
    "huggingface_hub",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# hf_xet: native accelerator huggingface_hub loads at runtime (optional).
try:
    d, b, h = collect_all("hf_xet")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    pass

# PyObjC frameworks the app touches, plus the whole flow package.
hiddenimports += [
    "objc", "AppKit", "Foundation", "Quartz",
    "AVFoundation", "CoreMedia", "CoreAudio",
]
hiddenimports += collect_submodules("flow")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LocalFlow",
)

app = BUNDLE(
    coll,
    name="LocalFlow.app",
    icon="assets/AppIcon.icns",
    bundle_identifier="dev.local-flow.app",
    version="0.1.0",
    info_plist={
        "CFBundleName": "LocalFlow",
        "CFBundleDisplayName": "LocalFlow",
        "CFBundleShortVersionString": "0.1.0",  # marketing version
        "CFBundleVersion": "1",                 # build number — bump every build, must increase
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        # Dock app, NOT a menu-bar agent: run() sets NSApplicationActivationPolicyRegular
        # (flow/menubar.py). LSUIElement must stay false or it fights that policy.
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription":
            "LocalFlow records your voice while the hotkey is held, to "
            "transcribe it locally on this machine.",
    },
)
