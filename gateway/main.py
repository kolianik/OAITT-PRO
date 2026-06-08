import os
import sys
import time
import logging
import asyncio
import httpx
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared.security import (
    internal_service_headers,
    normalize_audio_extension,
    resolve_shared_data_path,
    safe_remove_shared_path,
    validate_shared_path,
    validate_webhook_url,
)
from fastapi import FastAPI, Depends, File, Form, Query, UploadFile, HTTPException, Security, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, model_validator
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from db import (
    init_db, close_db, authenticate_client, log_transcription, get_analytics_summary,
    get_analytics_by_client, create_client_pricing, get_active_client_pricing,
    get_client_pricing_history, ClientNotFoundError, PricingConflictError,
    create_transcription_job, get_transcription_job, get_next_pending_job_atomic,
    update_job_success, update_job_failure, delete_expired_jobs_from_db, is_file_active_in_db
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("gateway")

# Backends
WHISPERX_URL = os.getenv("WHISPERX_URL", "http://whisperx-service:9007")
GIGAAM_URL = os.getenv("GIGAAM_URL", "http://gigaam-service:9007")

# VRAM state
current_gpu_engine = None  # None | 'whisperx' | 'gigaam'
gpu_lock = asyncio.Lock()

security = HTTPBearer()

async def get_current_client(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
    token = credentials.credentials
    client = await authenticate_client(token)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return client


async def require_admin(client: dict = Depends(get_current_client)) -> dict:
    if client["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return client


def _parse_date_param(value: Optional[str], param_name: str) -> Optional[str]:
    if value is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format for '{param_name}': expected YYYY-MM-DD",
        ) from exc
    return value


def _validate_date_range(date_from: Optional[str], date_to: Optional[str]) -> None:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="'from' must not be after 'to'")


def _validate_uuid_param(value: str, param_name: str = "client_id") -> str:
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid UUID for '{param_name}'",
        ) from exc
    return value


class PricingCreateRequest(BaseModel):
    client_id: str
    audio_price_per_minute: Optional[float] = None
    speech_price_per_minute: Optional[float] = None
    valid_from: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_rate(self):
        if self.audio_price_per_minute is None and self.speech_price_per_minute is None:
            raise ValueError("at least one of audio_price_per_minute or speech_price_per_minute is required")
        return self

# Webhook delivery function
async def fire_webhook(url: str, job_id: str, status_str: str, result: Optional[dict], error_message: Optional[str]):
    try:
        validated_url = validate_webhook_url(url)
    except ValueError as exc:
        logger.error("Invalid webhook URL for job %s: %s", job_id, exc)
        return

    webhook_host = urlparse(validated_url).hostname or "unknown"
    logger.info("Sending webhook for job %s to host %s", job_id, webhook_host)
    payload = {
        "job_id": job_id,
        "status": status_str,
        "result": result,
        "error_message": error_message
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.post(validated_url, json=payload)
            logger.info("Webhook delivered for job %s, status code: %s", job_id, resp.status_code)
    except Exception as e:
        logger.error("Failed to deliver webhook for job %s to host %s: %s", job_id, webhook_host, e)

# Async Background Worker Job Processor
async def process_job(job: dict):
    job_id = job["id"]
    try:
        file_path = validate_shared_path(job["file_path"])
    except ValueError:
        logger.error("Invalid file path for job %s", job_id)
        await update_job_failure(job_id, "Invalid file path")
        return
    engine = job["engine"]
    target_url = GIGAAM_URL if engine == "gigaam" else WHISPERX_URL
    start_time = time.perf_counter()
    
    # Secure GPU access exclusively
    async with gpu_lock:
        global current_gpu_engine
        
        if current_gpu_engine is not None and current_gpu_engine != engine:
            prev_url = WHISPERX_URL if current_gpu_engine == "whisperx" else GIGAAM_URL
            await unload_engine(current_gpu_engine, prev_url)
            
        current_gpu_engine = engine
        
        # Initialize job variables
        status_str = "success"
        error_msg = None
        audio_duration = 0.0
        speech_duration = 0.0
        char_count = 0
        resp_json = {}
        
        try:
            # Call inference endpoint /v1/audio/transcriptions/local
            # We set a long timeout (1800s = 30 minutes) to allow 2+ hour files to process
            async with httpx.AsyncClient(timeout=1800.0) as httpx_client:
                data = {
                    "file_path": file_path,
                    "language": job["language"] or "",
                    "response_format": job["response_format"] or "json",
                    "diarize": "true" if job["diarization_enabled"] else "false"
                }
                
                logger.info("Commanding %s backend to transcribe job %s", engine, job_id)
                resp = await httpx_client.post(
                    f"{target_url}/v1/audio/transcriptions/local",
                    json=data,
                    headers=internal_service_headers(),
                )
                
                if resp.status_code != 200:
                    status_str = "failed"
                    error_msg = f"Inference engine returned status {resp.status_code}"
                    logger.error("%s for job %s: %s", error_msg, job_id, resp.text)
                else:
                    resp_json = resp.json()
                    
        except Exception as exc:
            status_str = "failed"
            error_msg = "Request to inference engine failed"
            logger.error("%s for job %s: %s", error_msg, job_id, exc)
            
    # Process transcription outcome (outside gpu_lock to release GPU immediately for other jobs!)
    processing_time = time.perf_counter() - start_time
    
    try:
        if status_str == "success":
            transcription_text = resp_json.get("text", "")
            char_count = len(transcription_text)
            audio_duration = float(resp_json.get("duration", 0.0))
            
            # Compute speech duration from segments if available
            segments = resp_json.get("segments", [])
            if segments:
                speech_duration = sum(float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)) for seg in segments)
            else:
                speech_duration = audio_duration
                
            # Anti-Hallucination Filter Logic
            filtered_segments = []
            is_filtered = False
            min_avg_logprob = job["min_avg_logprob"]
            max_cps = job["max_chars_per_second"]
            
            for seg in segments:
                seg_logprob = seg.get("avg_logprob")
                seg_text = seg.get("text", "")
                seg_start = float(seg.get("start", 0.0))
                seg_end = float(seg.get("end", 0.0))
                seg_duration = seg_end - seg_start
                seg_cps = len(seg_text) / seg_duration if seg_duration > 0 else 0.0
                
                # Apply filters
                if min_avg_logprob is not None and seg_logprob is not None and float(seg_logprob) < float(min_avg_logprob):
                    is_filtered = True
                    logger.info(f"Filtering segment '{seg_text}' due to logprob {seg_logprob} < {min_avg_logprob}")
                    continue
                    
                if max_cps is not None and seg_cps > float(max_cps):
                    is_filtered = True
                    logger.info(f"Filtering segment '{seg_text}' due to CPS {seg_cps:.1f} > {max_cps}")
                    continue
                    
                filtered_segments.append(seg)
                
            if is_filtered:
                status_str = "hallucination_filtered"
                transcription_text = " ".join(seg.get("text", "").strip() for seg in filtered_segments).strip()
                char_count = len(transcription_text)
                resp_json["text"] = transcription_text
                resp_json["segments"] = filtered_segments
                
            # Update job record to 'completed' / 'hallucination_filtered' with results
            await update_job_success(job_id, resp_json, status_str)
            
            # Log to transcription_logs for analytics and dashboard
            await log_transcription(
                client_id=str(job["client_id"]),
                filename=job["filename"],
                audio_duration=audio_duration,
                speech_duration=speech_duration,
                processing_time=processing_time,
                engine=engine,
                model_name=job["model_name"],
                diarization_enabled=job["diarization_enabled"],
                char_count=char_count,
                status=status_str,
                error_message=None
            )
        else:
            # Update job record to 'failed'
            await update_job_failure(job_id, error_msg)
            
            # Log failure to transcription_logs for analytics
            await log_transcription(
                client_id=str(job["client_id"]),
                filename=job["filename"],
                audio_duration=0.0,
                speech_duration=0.0,
                processing_time=processing_time,
                engine=engine,
                model_name=job["model_name"],
                diarization_enabled=job["diarization_enabled"],
                char_count=0,
                status="failed",
                error_message=error_msg
            )
            
    except Exception as e:
        logger.error("Error post-processing job %s: %s", job_id, e, exc_info=True)
        await update_job_failure(job_id, "Post-processing error")
        
    finally:
        # Guarantee removal of local media file from shared storage to avoid leakage!
        try:
            safe_remove_shared_path(file_path)
            logger.info("Cleaned up local file for job %s", job_id)
        except (ValueError, FileNotFoundError):
            pass
        except OSError as e:
            logger.error("Failed to delete local file for job %s: %s", job_id, e)
                
        # Fire webhook if provided
        if job["webhook_url"]:
            await fire_webhook(job["webhook_url"], job_id, status_str, resp_json if status_str in ("success", "hallucination_filtered") else None, error_msg)

async def background_worker_loop():
    logger.info("Starting background worker loop...")
    while True:
        try:
            job = await get_next_pending_job_atomic()
            if job:
                logger.info(f"Picked up job {job['id']} for client {job['client_id']}")
                await process_job(job)
            else:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            logger.info("Background worker loop cancelled")
            break
        except Exception as e:
            logger.error(f"Error in background worker loop: {e}", exc_info=True)
            await asyncio.sleep(5)

async def cleanup_loop():
    logger.info("Starting periodic 24-hour retention cleanup worker...")
    while True:
        try:
            await asyncio.sleep(3600)  # Run every hour
            
            # 1. Clean up jobs older than 24 hours from database
            deleted_count = await delete_expired_jobs_from_db()
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired transcription jobs from DB")
                
            # 2. Check and delete orphaned files in /shared_data older than 24 hours
            if os.path.exists("/shared_data"):
                now = time.time()
                for filename in os.listdir("/shared_data"):
                    if ".." in filename or "/" in filename or "\\" in filename:
                        continue
                    file_path = os.path.join("/shared_data", filename)
                    if not os.path.isfile(file_path):
                        continue
                    try:
                        validated_path = validate_shared_path(file_path)
                    except ValueError:
                        continue
                    mtime = os.path.getmtime(validated_path)
                    # If file is older than 24 hours, delete it
                    if now - mtime > 86400:
                        is_active = await is_file_active_in_db(validated_path)
                        if not is_active:
                            try:
                                safe_remove_shared_path(validated_path)
                                logger.info("Auto-cleaned orphaned file from shared storage")
                            except (ValueError, OSError) as e:
                                logger.warning("Failed to auto-clean orphaned file: %s", e)
        except asyncio.CancelledError:
            logger.info("Cleanup loop cancelled")
            break
        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}", exc_info=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Gateway service is starting...")
    await init_db()
    # Create /shared_data directory if it doesn't exist
    os.makedirs("/shared_data", exist_ok=True)
    
    # Start background tasks
    app.state.worker_task = asyncio.create_task(background_worker_loop())
    app.state.cleanup_task = asyncio.create_task(cleanup_loop())
    yield
    # Shutdown
    logger.info("Gateway service is shutting down...")
    if hasattr(app.state, "worker_task"):
        app.state.worker_task.cancel()
    if hasattr(app.state, "cleanup_task"):
        app.state.cleanup_task.cancel()
    await close_db()

app = FastAPI(
    title="OAITT-PRO Gateway Orchestrator",
    description="Gateway Orchestrator with security, analytics logging, and VRAM management.",
    version="1.1.1",
    lifespan=lifespan
)

async def unload_engine(engine_name: str, base_url: str):
    logger.info("Commanding %s to UNLOAD weights from VRAM", engine_name)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{base_url}/unload", headers=internal_service_headers())
            if resp.status_code == 200:
                logger.info(f"Successfully unloaded {engine_name}")
            else:
                logger.error(f"Failed to unload {engine_name}: Status {resp.status_code}")
    except Exception as e:
        logger.error(f"Exception while unloading {engine_name}: {e}")

@app.post("/v1/audio/transcriptions/async", status_code=status.HTTP_202_ACCEPTED)
async def openai_transcribe_async(
    request: Request,
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: str = Form("whisper-1", description="Model name: whisper-1, whisperx, or gigaam"),
    language: Optional[str] = Form(None, description="Language code (e.g. ru, en)"),
    response_format: str = Form("json", description="Response format: json, verbose_json"),
    diarize: bool = Form(False, description="Enable speaker diarization"),
    min_avg_logprob: Optional[float] = Form(None, description="Anti-hallucination logprob threshold"),
    max_chars_per_second: Optional[float] = Form(None, description="Anti-hallucination speech rate limit"),
    webhook_url: Optional[str] = Form(None, description="Webhook URL to receive results"),
    client: dict = Depends(get_current_client)
):
    requested_engine = "gigaam" if model.lower() == "gigaam" else "whisperx"
    model_name = "v3_e2e_rnnt" if requested_engine == "gigaam" else "bzikst/faster-whisper-large-v3-russian"
    
    logger.info(f"Client {client['name']} ({client['id']}) requested ASYNC transcription with model: {model} (Engine: {requested_engine}, diarize={diarize})")
    
    if webhook_url:
        try:
            webhook_url = validate_webhook_url(webhook_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Generate UUID for job
    job_id = str(uuid.uuid4())
    
    # Stream file upload chunk-by-chunk to the shared volume to avoid loading it entirely in memory
    try:
        file_ext = normalize_audio_extension(file.filename)
        file_path = resolve_shared_data_path(job_id, file_ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunk
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        logger.error("Error saving uploaded file for job %s: %s", job_id, e)
        try:
            safe_remove_shared_path(file_path)
        except (ValueError, FileNotFoundError, OSError):
            pass
        raise HTTPException(status_code=500, detail="Failed to save uploaded file")
        
    # Create database entry
    try:
        actual_job_id = await create_transcription_job(
            client_id=str(client["id"]),
            filename=file.filename or "audio.wav",
            file_path=file_path,
            engine=requested_engine,
            model_name=model_name,
            diarization_enabled=diarize,
            language=language,
            response_format=response_format,
            min_avg_logprob=min_avg_logprob,
            max_chars_per_second=max_chars_per_second,
            webhook_url=webhook_url
        )
        
        created_at_str = datetime.now(timezone.utc).isoformat()
        
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": actual_job_id,
                "status": "pending",
                "created_at": created_at_str
            }
        )
    except Exception as e:
        logger.error("Failed to create transcription job in database: %s", e)
        try:
            safe_remove_shared_path(file_path)
        except (ValueError, FileNotFoundError, OSError):
            pass
        raise HTTPException(status_code=500, detail="Failed to queue transcription job")

@app.get("/v1/audio/transcriptions/status/{job_id}")
async def get_job_status(
    job_id: str,
    output: str = Query("json", description="Output format: json, text, srt, vtt, tsv"),
    client: dict = Depends(get_current_client)
):
    job = await get_transcription_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Transcription job {job_id} not found")
        
    # Enforce security/role boundaries
    if client["role"] != "admin" and str(job["client_id"]) != str(client["id"]):
        raise HTTPException(status_code=403, detail="You do not have permission to view this transcription job")
        
    created_at_str = job["created_at"].isoformat() if job["created_at"] else None
    updated_at_str = job["updated_at"].isoformat() if job["updated_at"] else None
    
    base_response = {
        "job_id": str(job["id"]),
        "status": job["status"],
        "created_at": created_at_str,
        "updated_at": updated_at_str
    }
    
    if job["status"] == "failed":
        base_response["error_message"] = job["error_message"]
        return JSONResponse(content=base_response)
        
    if job["status"] in ("pending", "processing"):
        return JSONResponse(content=base_response)
        
    # Job is completed or hallucination_filtered
    import json
    if isinstance(job["result"], str):
        result_data = json.loads(job["result"])
    else:
        result_data = job["result"]
        
    if output == "json":
        base_response["result"] = result_data
        return JSONResponse(content=base_response)
        
    elif output == "text":
        return PlainTextResponse(content=result_data.get("text", ""))
        
    # Format SRT, VTT, TSV
    segments = result_data.get("segments", [])
    
    if output == "srt":
        lines = []
        for idx, seg in enumerate(segments):
            start = format_timestamp_srt(float(seg.get("start", 0.0)))
            end = format_timestamp_srt(float(seg.get("end", 0.0)))
            spk_prefix = f"[{seg['speaker']}] " if "speaker" in seg and seg["speaker"] else ""
            lines.append(str(idx + 1))
            lines.append(f"{start} --> {end}")
            lines.append(f"{spk_prefix}{seg.get('text', '').strip()}")
            lines.append("")
        return PlainTextResponse(content="\n".join(lines), media_type="text/plain")
        
    elif output == "vtt":
        lines = ["WEBVTT", ""]
        for seg in segments:
            start = format_timestamp_vtt(float(seg.get("start", 0.0)))
            end = format_timestamp_vtt(float(seg.get("end", 0.0)))
            spk_prefix = f"[{seg['speaker']}] " if "speaker" in seg and seg["speaker"] else ""
            lines.append(f"{start} --> {end}")
            lines.append(f"{spk_prefix}{seg.get('text', '').strip()}")
            lines.append("")
        return PlainTextResponse(content="\n".join(lines), media_type="text/vtt")
        
    elif output == "tsv":
        lines = ["start\tend\tspeaker\ttext"]
        for seg in segments:
            start = int(float(seg.get("start", 0.0)) * 1000)
            end = int(float(seg.get("end", 0.0)) * 1000)
            spk = seg.get("speaker", "") or "UNKNOWN"
            text = seg.get("text", "").strip().replace("\t", " ")
            lines.append(f"{start}\t{end}\t{spk}\t{text}")
        return PlainTextResponse(content="\n".join(lines), media_type="text/tab-separated-values")
        
    raise HTTPException(status_code=400, detail=f"Unsupported format: {output}")

def format_timestamp_srt(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def format_timestamp_vtt(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

@app.get("/api/v1/analytics/summary")
async def analytics_summary(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    filter_client_id: Optional[str] = Query(None, alias="client_id"),
    client: dict = Depends(get_current_client),
):
    date_from = _parse_date_param(date_from, "from")
    date_to = _parse_date_param(date_to, "to")
    _validate_date_range(date_from, date_to)

    if filter_client_id is not None:
        if client["role"] != "admin":
            raise HTTPException(status_code=403, detail="Only admin may use client_id query parameter")
        _validate_uuid_param(filter_client_id)
        target_client_id = filter_client_id
        logger.info(f"Admin requested analytics for client {filter_client_id}")
    elif client["role"] == "admin":
        target_client_id = None
        logger.info("Admin requested global analytical summary")
    else:
        target_client_id = client["id"]
        logger.info(f"Client {client['name']} requested personal analytical summary")

    summary = await get_analytics_summary(
        client_id=target_client_id,
        date_from=date_from,
        date_to=date_to,
    )
    return JSONResponse(content=summary)


@app.get("/api/v1/admin/pricing/history")
async def admin_pricing_history(
    client_id: str = Query(...),
    _admin: dict = Depends(require_admin),
):
    _validate_uuid_param(client_id)
    history = await get_client_pricing_history(client_id)
    return JSONResponse(content=history)


@app.get("/api/v1/admin/pricing")
async def admin_pricing_active(
    client_id: str = Query(...),
    _admin: dict = Depends(require_admin),
):
    _validate_uuid_param(client_id)
    row = await get_active_client_pricing(client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No active pricing found for this client")
    return JSONResponse(content=row)


@app.post("/api/v1/admin/pricing", status_code=status.HTTP_201_CREATED)
async def admin_pricing_create(
    body: PricingCreateRequest,
    _admin: dict = Depends(require_admin),
):
    _validate_uuid_param(body.client_id)
    if body.valid_from is not None:
        _parse_date_param(body.valid_from, "valid_from")
    try:
        row = await create_client_pricing(
            client_id=body.client_id,
            audio_price_per_minute=body.audio_price_per_minute,
            speech_price_per_minute=body.speech_price_per_minute,
            valid_from=body.valid_from,
        )
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=400, detail="Client not found") from exc
    except PricingConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Pricing row already exists for this client, currency, and valid_from",
        ) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=row)


@app.get("/api/v1/admin/analytics/by-client")
async def admin_analytics_by_client(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    _admin: dict = Depends(require_admin),
):
    date_from = _parse_date_param(date_from, "from")
    date_to = _parse_date_param(date_to, "to")
    _validate_date_range(date_from, date_to)
    if date_from is None or date_to is None:
        raise HTTPException(status_code=400, detail="Both 'from' and 'to' query parameters are required")
    rows = await get_analytics_by_client(date_from=date_from, date_to=date_to)
    return JSONResponse(content=rows)


@app.get("/health")
async def health_check():
    from db import DB_POOL
    db_ok = False
    if DB_POOL:
        try:
            async with DB_POOL.acquire() as conn:
                await conn.execute("SELECT 1")
                db_ok = True
        except Exception:
            pass
            
    whisperx_ok = "offline"
    gigaam_ok = "offline"
    
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            r = await client.get(f"{WHISPERX_URL}/health")
            if r.status_code == 200:
                whisperx_ok = "online"
        except Exception:
            pass
            
        try:
            r = await client.get(f"{GIGAAM_URL}/health")
            if r.status_code == 200:
                gigaam_ok = "online"
        except Exception:
            pass
            
    status_str = "healthy" if db_ok and (whisperx_ok == "online" or gigaam_ok == "online") else "unhealthy"
    
    return JSONResponse(content={
        "status": status_str,
        "database_connected": db_ok,
        "currently_hot_model": current_gpu_engine or "none",
        "whisperx_service": whisperx_ok,
        "gigaam_service": gigaam_ok
    })
