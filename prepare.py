import os
import sys
import ssl

if os.getenv("PREPARE_INSECURE_SSL", "").strip() in {"1", "true", "yes"}:
    print("WARNING: SSL certificate verification is disabled (PREPARE_INSECURE_SSL). Use only in development.")
    ssl._create_default_https_context = ssl._create_unverified_context

def download_gigaam_weights():
    print("OAITT-PRO Model Weights Downloader")
    print("==================================")
    
    # Ensure dependencies are available for GigaAM imports
    try:
        import omegaconf
        import hydra
        import torchaudio
        import soundfile
    except ImportError:
        print("Installing required dependencies (omegaconf, hydra-core, tqdm, sentencepiece, torch, torchaudio, soundfile)...")
        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", 
            "install", "omegaconf", "hydra-core", "tqdm", "sentencepiece", "torch", "torchaudio", "soundfile"
        ])

    # Insert vendor/gigaam to Python Path
    sys.path.insert(0, os.path.abspath("vendor/gigaam"))

    try:
        import gigaam
    except ImportError as e:
        print(f"Error: Cannot import gigaam from vendor/gigaam: {e}")
        sys.exit(1)

    models = ["v3_e2e_rnnt", "v3_e2e_ctc"]
    local_dir = os.path.abspath("data/gigaam")

    os.makedirs(local_dir, exist_ok=True)

    for model_name in models:
        print(f"\nDownloading and caching GigaAM model: {model_name}...")
        try:
            # Let GigaAM load_model download and verify checksum using its Sber CDN URLs
            model = gigaam.load_model(
                model_name=model_name,
                fp16_encoder=False,
                use_flash=False,
                device="cpu",
                download_root=local_dir
            )
            print(f"[OK] Successfully cached model {model_name}")
            del model
        except Exception as e:
            print(f"[ERROR] Failed to download/load GigaAM model {model_name}: {e}")
            sys.exit(1)

    print("\n[OK] Preparation of GigaAM model weights complete!")

if __name__ == "__main__":
    download_gigaam_weights()
