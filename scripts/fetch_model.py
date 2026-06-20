"""Materialize the embedded Whisper model for the distribution build.

Downloads (or reuses the Hugging Face cache for) the default base.en CTranslate2
model into models/faster-whisper-base.en/ as real files, so PyInstaller can
embed it (see LocalFlow.spec) and the shipped app transcribes offline with no
first-run download. Run before the distribution build. Idempotent.

Force cache-only (no network) with: HF_HUB_OFFLINE=1 python scripts/fetch_model.py
"""

from pathlib import Path

from huggingface_hub import snapshot_download

REPO_ID = "Systran/faster-whisper-base.en"
DEST = Path(__file__).resolve().parent.parent / "models" / "faster-whisper-base.en"


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=REPO_ID, local_dir=str(DEST))
    print(f"Model ready at {DEST}")


if __name__ == "__main__":
    main()
