import os
import gc
import tempfile
import logging
import torch
import time
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, status
from fastapi.responses import JSONResponse
from typing import Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("whisperx-service")

# Enable PyTorch weights_only patching for compatibility with newer PyTorch
try:
    import collections
    import typing
    import omegaconf
    import pyannote.audio
    
    # Add safe globals for loading older pyannote checkpoints safely
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
    logger.info("Configured safe torch globals for Pyannote")
except Exception as e:
    logger.warning(f"Could not configure safe torch globals: {e}")

# Configuration
WHISPER_MODEL_NAME = os.getenv("WHISPERX_MODEL", "bzikst/faster-whisper-large-v3-russian")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", "float16")
if DEVICE == "cpu" and COMPUTE_TYPE == "float16":
    logger.info("CPU detected. Falling back from float16 to float32 for CTranslate2 compatibility.")
    COMPUTE_TYPE = "float32"
HF_TOKEN = os.getenv("HF_TOKEN", "")

app = FastAPI(title="WhisperX Inference Service", version="1.0.0")

# Loaded Models (Lazy)
whisper_model = None
align_model = None
align_metadata = None
diarize_pipeline = None

def load_models_if_needed():
    global whisper_model, align_model, align_metadata
    import whisperx
    
    if whisper_model is None:
        logger.info(f"Loading WhisperX model '{WHISPER_MODEL_NAME}' on {DEVICE} with {COMPUTE_TYPE}...")
        whisper_model = whisperx.load_model(
            WHISPER_MODEL_NAME,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root="/app/data"
        )
        logger.info("WhisperX model loaded successfully")
        
    if align_model is None:
        logger.info("Loading alignment model for Russian ('ru')...")
        align_model, align_metadata = whisperx.load_align_model(
            language_code="ru",
            device=DEVICE
        )
        logger.info("Alignment model loaded successfully")

def load_diarize_pipeline_if_needed():
    global diarize_pipeline
    import whisperx
    
    if diarize_pipeline is None:
        if not HF_TOKEN:
            raise ValueError("HF_TOKEN is required for Pyannote Diarization")
        logger.info("Loading Pyannote Diarization Pipeline (pyannote/speaker-diarization-community-1)...")
        diarize_pipeline = whisperx.DiarizationPipeline(
            use_auth_token=HF_TOKEN,
            device=DEVICE
        )
        logger.info("Diarization pipeline loaded successfully")

@app.post("/unload")
async def unload_models():
    global whisper_model, align_model, align_metadata, diarize_pipeline
    
    logger.info("Received request to UNLOAD WhisperX models from memory...")
    
    # Release model objects
    whisper_model = None
    align_model = None
    align_metadata = None
    diarize_pipeline = None
    
    # Run Garbage Collection and clear CUDA memory
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
    
    logger.info("Models successfully unloaded and GPU memory freed")
    return {"status": "unloaded"}

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
    diarize: str = Form("false")
):
    import whisperx
    
    logger.info(f"Processing transcription request for file: {file.filename}, diarize: {diarize}")
    
    # Check if we should enable diarization
    should_diarize = diarize.lower() == "true"
    
    # 1. Save upload file bytes to a temporary WAV file
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name
        
    try:
        # Load models
        load_models_if_needed()
        
        # 2. Load audio and transcribe
        logger.info("Loading audio file into WhisperX...")
        audio = whisperx.load_audio(tmp_path)
        duration_seconds = len(audio) / 16000.0  # WhisperX audio is loaded at 16000Hz
        
        logger.info(f"Running WhisperX transcription (duration: {duration_seconds:.2f}s)...")
        # Run WhisperX transcription
        transcribe_args = {}
        if language and language != "None":
            transcribe_args["language"] = language
            
        result = whisper_model.transcribe(audio, batch_size=16, **transcribe_args)
        detected_lang = result.get("language", "ru")
        
        # 3. Align transcription segments
        logger.info("Running word-level alignment...")
        aligned_result = whisperx.align(
            result["segments"],
            align_model,
            align_metadata,
            audio,
            device=DEVICE,
            return_char_alignments=False
        )
        
        # 4. Optional Diarization
        if should_diarize:
            load_diarize_pipeline_if_needed()
            logger.info("Running Pyannote speaker diarization...")
            diarize_segments = diarize_pipeline(audio)
            
            logger.info("Assigning speakers to segments and words...")
            # Assign speaker labels to alignment segments
            aligned_result = whisperx.assign_word_speakers(diarize_segments, aligned_result)
            
        # 5. Format to consistent return structure
        output_segments = []
        for idx, seg in enumerate(aligned_result.get("segments", [])):
            # Fallback values
            start_time = float(seg.get("start", 0.0))
            end_time = float(seg.get("end", start_time))
            text = seg.get("text", "").strip()
            speaker = seg.get("speaker", None)
            
            output_segments.append({
                "id": idx,
                "start": start_time,
                "end": end_time,
                "text": text,
                "speaker": speaker,
                "avg_logprob": seg.get("avg_logprob", 0.0),
                "words": [
                    {
                        "word": w.get("word", ""),
                        "start": w.get("start", 0.0),
                        "end": w.get("end", 0.0),
                        "probability": w.get("score", 0.0)
                    } for w in seg.get("words", []) if "word" in w
                ]
            })
            
        response_data = {
            "text": " ".join(seg.get("text", "").strip() for seg in output_segments).strip(),
            "duration": duration_seconds,
            "language": detected_lang,
            "segments": output_segments
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
        "device": DEVICE,
        "whisper_model_loaded": whisper_model is not None,
        "align_model_loaded": align_model is not None,
        "diarize_pipeline_loaded": diarize_pipeline is not None
    }
