import os
import gc
import tempfile
import logging
import torch
import soundfile as sf
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, status
from fastapi.responses import JSONResponse
from typing import Optional, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("gigaam-service")

# PyTorch weights_only patching for Pyannote model loading
try:
    import collections
    import typing
    import omegaconf
    import pyannote.audio
    
    safe_globals = [
        omegaconf.listconfig.ListConfig,
        omegaconf.base.ContainerMetadata,
        omegaconf.nodes.AnyNode,
        omegaconf.base.Metadata,
        typing.Any,
        list,
        dict,
        int,
        collections.defaultdict,
        torch.torch_version.TorchVersion,
        pyannote.audio.core.model.Introspection,
        pyannote.audio.core.task.Specifications,
        pyannote.audio.core.task.Problem,
        pyannote.audio.core.task.Resolution,
    ]
    for cls in safe_globals:
        torch.serialization.add_safe_globals([cls])
    logger.info("Configured safe torch globals for GigaAM")
except Exception as e:
    logger.warning(f"Could not configure safe torch globals: {e}")

# Configuration
GIGAAM_MODEL_NAME = os.getenv("GIGAAM_MODEL", "v3_e2e_rnnt")
HF_TOKEN = os.getenv("HF_TOKEN", "")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

app = FastAPI(title="GigaAM Inference & Diarization Service", version="1.0.0")

# Loaded Models (Lazy)
gigaam_model = None
diarize_pipeline = None

def load_gigaam_model_if_needed():
    global gigaam_model
    if gigaam_model is None:
        try:
            import gigaam
            logger.info(f"Loading GigaAM model '{GIGAAM_MODEL_NAME}' on {DEVICE}...")
            gigaam_model = gigaam.load_model(
                model_name=GIGAAM_MODEL_NAME,
                fp16_encoder=True if DEVICE.type == "cuda" else False,
                use_flash=False,
                device=DEVICE,
                download_root="/app/data/gigaam"
            )
            logger.info("GigaAM model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load GigaAM model: {e}", exc_info=True)
            raise RuntimeError(f"Failed to load GigaAM model: {e}")

def load_diarize_pipeline_if_needed():
    global diarize_pipeline
    if diarize_pipeline is None:
        if not HF_TOKEN:
            raise ValueError("HF_TOKEN is required for Pyannote Diarization in GigaAM Service")
        from pyannote.audio import Pipeline
        logger.info("Loading Pyannote Diarization Pipeline (pyannote/speaker-diarization-community-1)...")
        diarize_pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-community-1",
            use_auth_token=HF_TOKEN
        )
        diarize_pipeline.to(DEVICE)
        logger.info("Diarization pipeline loaded successfully")

@app.post("/unload")
async def unload_models():
    global gigaam_model, diarize_pipeline
    
    logger.info("Received request to UNLOAD GigaAM and Pyannote models from memory...")
    
    gigaam_model = None
    diarize_pipeline = None
    
    # Force Garbarge Collection and CUDA cleanups
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        
    logger.info("Models successfully unloaded and GPU memory freed")
    return {"status": "unloaded"}

def transcribe_audio_tensor(audio_numpy: np.ndarray) -> str:
    """Runs GigaAM transcription on a raw numpy array directly in GPU memory."""
    global gigaam_model
    
    # 25s threshold padding/workaround to avoid MPS/CUDA memory leaks and maintain fixed shapes if short
    original_len = len(audio_numpy)
    max_samples = 25 * 16000 # 25 seconds
    
    if len(audio_numpy) < max_samples:
        audio_numpy = np.pad(audio_numpy, (0, max_samples - len(audio_numpy)))
        
    wav_tensor = torch.from_numpy(audio_numpy).float().to(DEVICE)
    wav = wav_tensor.unsqueeze(0)
    length = torch.full([1], original_len, device=DEVICE)
    
    encoded = None
    encoded_len = None
    try:
        # Run forward pass through GigaAM encoder & decoder
        with torch.inference_mode():
            encoded, encoded_len = gigaam_model.forward(wav, length)
            result = gigaam_model.decoding.decode(gigaam_model.head, encoded, encoded_len)[0]
    finally:
        # Explicit cleanups of active tensors to prevent leakage
        del wav, length, wav_tensor
        if encoded is not None:
            del encoded
        if encoded_len is not None:
            del encoded_len
            
    return result

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
    diarize: str = Form("false")
):
    logger.info(f"Processing transcription request for file: {file.filename}, diarize: {diarize}")
    
    should_diarize = diarize.lower() == "true"
    
    # Save upload file bytes to a temporary WAV file
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name
        
    try:
        # 1. Load GigaAM model
        load_gigaam_model_if_needed()
        
        # 2. Read audio
        logger.info("Reading audio file...")
        audio, sample_rate = sf.read(tmp_path)
        
        # Convert to mono if stereo
        if len(audio.shape) > 1:
            audio = np.mean(audio, axis=1)
            
        # Resample to 16000Hz if necessary
        if sample_rate != 16000:
            import librosa
            audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=16000)
            
        duration_seconds = len(audio) / 16000.0
        logger.info(f"Audio loaded successfully. Duration: {duration_seconds:.2f}s")
        
        segments = []
        
        # 3. Handle diarization path (also used for longform splitting)
        if should_diarize:
            load_diarize_pipeline_if_needed()
            logger.info("Running Pyannote speaker diarization...")
            pyannote_result = diarize_pipeline(tmp_path)
            
            # Extract speaker segments
            raw_intervals = []
            for turn, _, speaker in pyannote_result.itertracks(yield_label=True):
                raw_intervals.append({
                    "start": turn.start,
                    "end": turn.end,
                    "speaker": speaker
                })
                
            logger.info(f"Diarization finished. Found {len(raw_intervals)} speaker turns.")
            
            # Split intervals that exceed GigaAM's 25-second limit
            intervals = []
            for interval in raw_intervals:
                start = interval["start"]
                end = interval["end"]
                spk = interval["speaker"]
                
                while end - start > 20.0:
                    intervals.append({"start": start, "end": start + 20.0, "speaker": spk})
                    start += 20.0
                if end - start > 0.1: # Skip negligible segments
                    intervals.append({"start": start, "end": end, "speaker": spk})
                    
            if not intervals:
                # Fallback: if no speech detected, transcribe whole file as one segment (up to 20s chunks)
                logger.warning("No speech segments detected by Pyannote. Falling back to simple chunking.")
                start = 0.0
                while start < duration_seconds:
                    end = min(start + 20.0, duration_seconds)
                    intervals.append({"start": start, "end": end, "speaker": "UNKNOWN"})
                    start += 20.0
                    
            # Transcribe each segment
            for idx, interval in enumerate(intervals):
                start_sec = interval["start"]
                end_sec = interval["end"]
                spk = interval["speaker"]
                
                start_sample = int(start_sec * 16000)
                end_sample = int(end_sec * 16000)
                chunk_audio = audio[start_sample:end_sample]
                
                if len(chunk_audio) < 160: # Skip extremely short segments (<10ms)
                    continue
                    
                logger.info(f"Transcribing segment {idx+1}/{len(intervals)}: {start_sec:.2f}s - {end_sec:.2f}s (Speaker: {spk})...")
                chunk_text = transcribe_audio_tensor(chunk_audio).strip()
                
                if chunk_text:
                    segments.append({
                        "id": idx,
                        "start": start_sec,
                        "end": end_sec,
                        "text": chunk_text,
                        "speaker": spk,
                        "avg_logprob": 0.0 # GigaAM doesn't easily expose token logprobs
                    })
                    
        else:
            # 4. Standard Non-Diarized path (Fixed 20-second chunking for long audio)
            logger.info("Diarization disabled. Performing fixed 20-second chunking...")
            start_sec = 0.0
            idx = 0
            while start_sec < duration_seconds:
                end_sec = min(start_sec + 20.0, duration_seconds)
                start_sample = int(start_sec * 16000)
                end_sample = int(end_sec * 16000)
                chunk_audio = audio[start_sample:end_sample]
                
                if len(chunk_audio) < 160:
                    break
                    
                logger.info(f"Transcribing chunk {idx+1}: {start_sec:.2f}s - {end_sec:.2f}s...")
                chunk_text = transcribe_audio_tensor(chunk_audio).strip()
                
                if chunk_text:
                    segments.append({
                        "id": idx,
                        "start": start_sec,
                        "end": end_sec,
                        "text": chunk_text,
                        "speaker": None,
                        "avg_logprob": 0.0
                    })
                idx += 1
                start_sec += 20.0
                
        response_data = {
            "text": " ".join(seg.get("text", "").strip() for seg in segments).strip(),
            "duration": duration_seconds,
            "language": "ru", # GigaAM is Russian-only
            "segments": segments
        }
        
        return JSONResponse(content=response_data)
        
    except Exception as e:
        logger.error(f"Error during transcription: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
        
    finally:
        # Clean up temporary audio file
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "device": str(DEVICE),
        "gigaam_model_loaded": gigaam_model is not None,
        "diarize_pipeline_loaded": diarize_pipeline is not None
    }
