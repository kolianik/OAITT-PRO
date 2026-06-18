import glob
import os
import sys
import ssl


def _wire_corp_ca() -> None:
    """Trust a staged corporate CA so host downloads succeed with verification still ON (S3).

    Prefers an explicit REQUESTS_CA_BUNDLE / SSL_CERT_FILE if the operator set one; otherwise,
    when CORP_CA_AUTO_TRUST is opted in and a cert is staged at certs/extra-ca/*.crt, it ADDS
    that root on top of the system trust store. The vendor gigaam downloader uses
    ``urllib.request.urlopen`` with the default SSL context, which honors SSL_CERT_FILE but not
    REQUESTS_CA_BUNDLE — so we set both env vars and also override the default HTTPS context.
    """
    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not bundle and os.getenv("CORP_CA_AUTO_TRUST", "").strip() in {"1", "true", "yes"}:
        staged = sorted(
            glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "extra-ca", "*.crt"))
        )
        if staged:
            bundle = staged[0]
    if not bundle:
        return
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    os.environ.setdefault("SSL_CERT_FILE", bundle)  # gigaam __init__ urllib reads this

    def _ctx_with_corp_ca() -> ssl.SSLContext:
        ctx = ssl.create_default_context()  # keep public roots
        try:
            ctx.load_verify_locations(cafile=bundle)  # add the corporate root
        except OSError:
            pass
        return ctx

    ssl._create_default_https_context = _ctx_with_corp_ca
    print(f"Using corporate CA bundle for host downloads: {bundle}")


if os.getenv("PREPARE_INSECURE_SSL", "").strip() in {"1", "true", "yes"}:
    print("WARNING: SSL certificate verification is disabled (PREPARE_INSECURE_SSL). Use only in development.")
    ssl._create_default_https_context = ssl._create_unverified_context
else:
    _wire_corp_ca()

def download_gigaam_weights():
    """Optional: prefetch GigaAM PyTorch weights on host (for offline seed / faster bootstrap)."""
    print("OAITT-PRO GigaAM Weights Prefetch (optional)")
    print("===========================================")
    print("Note: gigaam-service bootstraps models into Docker volume on first start.")
    print("      Use this script only for air-gapped seed or to speed up bootstrap.\n")
    
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


def sync_build_secrets() -> None:
    script = os.path.join(os.path.dirname(__file__), "scripts", "sync_build_secrets.py")
    if not os.path.isfile(script):
        return
    import subprocess
    print("\nSyncing HF_TOKEN for docker compose build secrets...")
    subprocess.check_call([sys.executable, script])


if __name__ == "__main__":
    download_gigaam_weights()
    # sync_build_secrets no longer required for gigaam image build (runtime HF_TOKEN only)
