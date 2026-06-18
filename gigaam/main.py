import os
import sys
import gc
import asyncio
import tempfile
import logging
from contextlib import asynccontextmanager
from typing import Optional

import torch
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, status, Header, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared.security import (
    INTERNAL_TOKEN_HEADER,
    validate_shared_path,
    verify_internal_service_token,
)
from pipeline.orchestrator import run_pipeline
from pipeline.diarization import reset_diarization_pipeline
from bootstrap_state import STATE
from bootstrap_models import CacheIncompleteError, ensure_models

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("gigaam-service")

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

GIGAAM_MODEL_NAME = os.getenv("GIGAAM_MODEL", "v3_e2e_rnnt")
GIGAAM_ALIGN_MODEL = os.getenv(
    "GIGAAM_ALIGN_MODEL", "jonatasgrosman/wav2vec2-xls-r-1b-russian"
)
GIGAAM_ONNX_DIR = os.getenv("GIGAAM_ONNX_DIR", "/app/data/gigaam_onnx")
GIGAAM_BATCH_SIZE = max(1, int(os.getenv("GIGAAM_BATCH_SIZE", "4")))
GIGAAM_DIARIZE_BATCH_SIZE = max(1, int(os.getenv("GIGAAM_DIARIZE_BATCH_SIZE", "8")))
GIGAAM_DENOISE = os.getenv("GIGAAM_DENOISE", "true").lower() in ("1", "true", "yes")
GIGAAM_DENOISE_MODEL = os.getenv("GIGAAM_DENOISE_MODEL", "DeepFilterNet3")
GIGAAM_DENOISE_DEVICE = os.getenv("GIGAAM_DENOISE_DEVICE", "cuda")
GIGAAM_DENOISE_CHUNK_SEC = float(os.getenv("GIGAAM_DENOISE_CHUNK_SEC", "30"))
GIGAAM_DENOISE_OVERLAP_SEC = float(os.getenv("GIGAAM_DENOISE_OVERLAP_SEC", "2"))
HF_TOKEN = os.getenv("HF_TOKEN", "")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


async def _run_bootstrap() -> None:
    try:
        await asyncio.to_thread(ensure_models, STATE)
    except CacheIncompleteError:
        logger.error("Model bootstrap failed: %s", STATE.message)
    except Exception as exc:
        logger.exception("Unexpected bootstrap error: %s", exc)
        STATE.update(
            status="failed",
            ready=False,
            message=f"Ошибка инициализации кэша моделей: {exc}",
            error=str(exc),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_run_bootstrap())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="GigaAM Inference & Diarization Service",
    version="1.2.1",
    lifespan=lifespan,
)

# Single-flight GPU guard: only one transcription runs at a time. Combined with
# asyncio.to_thread in the handlers, this keeps the event loop (and GET /health)
# responsive while a job occupies the GPU, and prevents concurrent use of the
# shared per-stage model singletons in pipeline/*. See agents.md §2.5.
PIPELINE_LOCK = asyncio.Lock()


async def require_internal_service(
    x_internal_service_token: Optional[str] = Header(None, alias=INTERNAL_TOKEN_HEADER),
) -> None:
    try:
        verify_internal_service_token(x_internal_service_token)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc


async def require_models_ready() -> None:
    if STATE.ready:
        return
    body = STATE.to_health_dict(
        device=str(DEVICE),
        onnx_dir=GIGAAM_ONNX_DIR,
        denoise_enabled=GIGAAM_DENOISE,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=body,
    )


def _ensure_diarization_available(diarize: str) -> None:
    """Reject diarize=true early when this deployment cannot diarize (agents.md §2.5).

    A ready service has Pyannote cached iff HF_TOKEN was set at bootstrap, so the
    runtime check is HF_TOKEN present AND STATE.pyannote_cached.
    """
    if diarize.strip().lower() != "true":
        return
    if not HF_TOKEN or not STATE.pyannote_cached:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Diarization requested but unavailable on this GigaAM deployment "
                "(requires HF_TOKEN and a prefetched Pyannote model)."
            ),
        )


def process_transcription_from_file(
    file_path: str,
    language: Optional[str],
    response_format: str,
    diarize: str,
) -> dict:
    should_diarize = diarize.lower() == "true"
    return run_pipeline(
        file_path,
        diarize=should_diarize,
        language=language,
        onnx_dir=GIGAAM_ONNX_DIR,
        model_version=GIGAAM_MODEL_NAME,
        align_model=GIGAAM_ALIGN_MODEL,
        cache_dir="/app/data",
        hf_token=HF_TOKEN,
        batch_size=GIGAAM_BATCH_SIZE,
        diarize_batch_size=GIGAAM_DIARIZE_BATCH_SIZE,
        denoise_enabled=GIGAAM_DENOISE,
        denoise_model=GIGAAM_DENOISE_MODEL,
        denoise_device=GIGAAM_DENOISE_DEVICE,
        denoise_chunk_sec=GIGAAM_DENOISE_CHUNK_SEC,
        denoise_overlap_sec=GIGAAM_DENOISE_OVERLAP_SEC,
    )


class LocalTranscribeRequest(BaseModel):
    file_path: str
    language: Optional[str] = None
    response_format: str = "json"
    diarize: str = "false"


@app.post("/v1/audio/transcriptions/local")
async def transcribe_local(
    req: LocalTranscribeRequest,
    _auth: None = Depends(require_internal_service),
    _ready: None = Depends(require_models_ready),
):
    logger.info("Received local transcription request, diarize: %s", req.diarize)
    _ensure_diarization_available(req.diarize)
    try:
        safe_path = validate_shared_path(req.file_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="File not found on shared storage")
    try:
        async with PIPELINE_LOCK:
            result = await asyncio.to_thread(
                process_transcription_from_file,
                safe_path,
                req.language,
                req.response_format,
                req.diarize,
            )
        return JSONResponse(content=result)
    except Exception as e:
        logger.error("Error during local transcription: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transcription failed",
        )


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
    diarize: str = Form("false"),
    _ready: None = Depends(require_models_ready),
):
    logger.info("Processing transcription for %s, diarize: %s", file.filename, diarize)
    _ensure_diarization_available(diarize)
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name

    try:
        async with PIPELINE_LOCK:
            result = await asyncio.to_thread(
                process_transcription_from_file,
                tmp_path,
                language,
                response_format,
                diarize,
            )
        return JSONResponse(content=result)
    except Exception as e:
        logger.error("Error during transcription: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transcription failed",
        )
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


@app.post("/unload")
async def unload_models(_auth: None = Depends(require_internal_service)):
    logger.info("UNLOAD: clearing pipeline model caches...")
    reset_diarization_pipeline()
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
    return {"status": "unloaded"}


@app.get("/health")
async def health_check():
    body = STATE.to_health_dict(
        device=str(DEVICE),
        onnx_dir=GIGAAM_ONNX_DIR,
        denoise_enabled=GIGAAM_DENOISE,
    )
    if STATE.ready and STATE.status == "healthy":
        return JSONResponse(status_code=status.HTTP_200_OK, content=body)
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)
