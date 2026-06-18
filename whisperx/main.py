import os
import sys
import gc
import tempfile
import logging
import torch
import time
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, status, Header, Depends
from fastapi.responses import JSONResponse
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared.security import (
    INTERNAL_TOKEN_HEADER,
    validate_shared_path,
    verify_internal_service_token,
)

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
ALIGN_MODEL_NAME = os.getenv(
    "WHISPERX_ALIGN_MODEL",
    "jonatasgrosman/wav2vec2-xls-r-1b-russian",
)
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", os.getenv("WHISPERX_COMPUTE_TYPE", "float16"))
if DEVICE == "cuda" and COMPUTE_TYPE != "float16":
    raise RuntimeError(
        f"Unsupported WHISPERX_COMPUTE_TYPE={COMPUTE_TYPE!r} on CUDA. "
        "Only float16 is allowed (int8 and other quantizations are disabled)."
    )
if DEVICE == "cpu" and COMPUTE_TYPE == "float16":
    logger.info("CPU detected. Falling back from float16 to float32 for CTranslate2 compatibility.")
    COMPUTE_TYPE = "float32"
try:
    WHISPERX_BATCH_SIZE = max(1, int(os.getenv("WHISPERX_BATCH_SIZE", "4")))
except ValueError as e:
    raise RuntimeError("WHISPERX_BATCH_SIZE must be a positive integer") from e
HF_TOKEN = os.getenv("HF_TOKEN", "")

app = FastAPI(title="WhisperX Inference Service", version="1.0.0")


async def require_internal_service(
    x_internal_service_token: Optional[str] = Header(None, alias=INTERNAL_TOKEN_HEADER),
) -> None:
    try:
        verify_internal_service_token(x_internal_service_token)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc


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
            download_root="/app/data",
            local_files_only=True
        )
        logger.info("WhisperX model loaded successfully")
        
    if align_model is None:
        logger.info(f"Loading alignment model '{ALIGN_MODEL_NAME}' for Russian ('ru')...")
        align_model, align_metadata = whisperx.load_align_model(
            language_code="ru",
            device=DEVICE,
            model_name=ALIGN_MODEL_NAME,
            model_dir="/app/data",
            model_cache_only=True,
        )
        logger.info("Alignment model loaded successfully")

def load_diarize_pipeline_if_needed():
    global diarize_pipeline
    
    if diarize_pipeline is None:
        if not HF_TOKEN:
            raise ValueError("HF_TOKEN is required for Pyannote Diarization")
        logger.info("Loading Pyannote Diarization Pipeline (pyannote/speaker-diarization-community-1)...")
        from whisperx.diarize import DiarizationPipeline
        diarize_pipeline = DiarizationPipeline(
            token=HF_TOKEN,
            device=DEVICE
        )
        logger.info("Diarization pipeline loaded successfully")

@app.post("/unload")
async def unload_models(_auth: None = Depends(require_internal_service)):
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

def process_transcription_from_file(
    file_path: str,
    language: Optional[str],
    response_format: str,
    diarize: str
) -> dict:
    import whisperx
    should_diarize = diarize.lower() == "true"
    
    # Load models
    load_models_if_needed()
    
    # 2. Load audio and transcribe
    logger.info("Loading audio file into WhisperX...")
    audio = whisperx.load_audio(file_path)
    duration_seconds = len(audio) / 16000.0  # WhisperX audio is loaded at 16000Hz
    
    logger.info(f"Running WhisperX transcription (duration: {duration_seconds:.2f}s)...")
    
    # Run WhisperX transcription
    transcribe_args = {}
    if language and language != "None" and language != "":
        transcribe_args["language"] = language
        
    result = whisper_model.transcribe(audio, batch_size=WHISPERX_BATCH_SIZE, **transcribe_args)
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
    return response_data

from pydantic import BaseModel

class LocalTranscribeRequest(BaseModel):
    file_path: str
    language: Optional[str] = None
    response_format: str = "json"
    diarize: str = "false"

@app.post("/v1/audio/transcriptions/local")
async def transcribe_local(
    req: LocalTranscribeRequest,
    _auth: None = Depends(require_internal_service),
):
    logger.info("Received local transcription request, diarize: %s", req.diarize)
    try:
        safe_path = validate_shared_path(req.file_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="File not found on shared storage")
    try:
         result = process_transcription_from_file(
             safe_path,
             req.language,
             req.response_format,
             req.diarize
         )
         return JSONResponse(content=result)
    except Exception as e:
         logger.error("Error during local transcription: %s", e, exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Transcription failed")

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
    diarize: str = Form("false")
):
    logger.info(f"Processing transcription request for file: {file.filename}, diarize: {diarize}")
    
    # 1. Save upload file bytes to a temporary WAV file
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name
        
    try:
        result = process_transcription_from_file(
            tmp_path,
            language,
            response_format,
            diarize
        )
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error("Error during transcription: %s", e, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Transcription failed")
        
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
        "whisper_model": WHISPER_MODEL_NAME,
        "align_model": ALIGN_MODEL_NAME,
        "whisper_model_loaded": whisper_model is not None,
        "align_model_loaded": align_model is not None,
        "diarize_pipeline_loaded": diarize_pipeline is not None
    }
