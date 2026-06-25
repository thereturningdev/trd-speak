# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the TRD Speak DISTRIBUTION build (arm64, self-contained).
# See RELEASING.md. Build with:  .venv/bin/pyinstaller --noconfirm TRDSpeak.spec
# Output: dist/TRDSpeak.app  (no repo or .venv required at runtime).

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# The release workflow passes the dispatched version in TRDSPEAK_VERSION so the
# tag is the single source of truth; local builds fall back to the literal.
_VERSION = os.environ.get("TRDSPEAK_VERSION") or "0.1.0"

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

# Common-words guardrail list: loaded at runtime by flow/common_words.py via
# Path(__file__).resolve().parent / "data" / "common_words.txt".
datas += [('flow/data/common_words.txt', 'flow/data')]

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

# PyObjC frameworks the app touches. AppKit/Foundation/Quartz/objc are covered
# by PyInstaller's bundled hooks, so a hidden import suffices. The AV/audio
# frameworks have NO hooks: a bare hidden import bundles the .so but not the
# package's pure-Python _metadata, so `import AVFoundation` half-loads and
# mic_status() falls to "unknown" — and the Microphone prompt then never fires.
# collect_all pulls the full packages (incl. AVFoundation's own deps: AVFAudio,
# CoreMedia, CoreAudio, Foundation).
for pkg in ("AVFoundation", "AVFAudio", "CoreMedia", "CoreAudio"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += [
    "objc", "AppKit", "Foundation", "Quartz",
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
    name="TRDSpeak",
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
    name="TRDSpeak",
)

app = BUNDLE(
    coll,
    name="TRDSpeak.app",
    icon="assets/AppIcon.icns",
    bundle_identifier="com.thereturningdev.speak",
    version=_VERSION,
    info_plist={
        "CFBundleName": "TRD Speak",
        "CFBundleDisplayName": "TRD Speak",
        "CFBundleShortVersionString": _VERSION,  # marketing version (from the release tag)
        "CFBundleVersion": _VERSION,             # build number (Developer ID: need not increase)
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        # Dock app, NOT a menu-bar agent: run() sets NSApplicationActivationPolicyRegular
        # (flow/menubar.py). LSUIElement must stay false or it fights that policy.
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription":
            "TRD Speak records your voice while the hotkey is held, to "
            "transcribe it locally on this machine.",
    },
)
