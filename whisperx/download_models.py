import os
import sys

# Pre-download WhisperX model weights during docker build
WHISPER_MODEL_NAME = os.getenv("WHISPERX_MODEL", "bzikst/faster-whisper-large-v3-russian")
ALIGN_MODEL_NAME = os.getenv(
    "WHISPERX_ALIGN_MODEL",
    "jonatasgrosman/wav2vec2-xls-r-1b-russian",
)

print(f"Pre-downloading WhisperX model: {WHISPER_MODEL_NAME}...")

try:
    import whisperx

    # Download the main ASR model
    # We load on CPU since GPU is not available during docker build
    model = whisperx.load_model(
        WHISPER_MODEL_NAME,
        device="cpu",
        compute_type="float32",
        download_root="/app/data"
    )
    print("ASR model downloaded successfully!")

    # Pre-download the alignment model for Russian
    print(f"Pre-downloading Russian wav2vec2 alignment model: {ALIGN_MODEL_NAME}...")
    align_model, align_metadata = whisperx.load_align_model(
        language_code="ru",
        device="cpu",
        model_name=ALIGN_MODEL_NAME,
        model_dir="/app/data",
    )
    print("Russian alignment model downloaded successfully!")

except Exception as e:
    print(f"Failed to download models: {e}")
    sys.exit(1)
