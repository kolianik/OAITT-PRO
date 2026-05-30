import os
import time
import logging
import asyncio
import httpx
from fastapi import FastAPI, Depends, File, Form, Query, UploadFile, HTTPException, Security, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse, PlainTextResponse
from typing import Optional, List
from contextlib import asynccontextmanager

from gateway.db import init_db, close_db, authenticate_client, log_transcription, get_analytics_summary

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Gateway service is starting...")
    await init_db()
    yield
    # Shutdown
    logger.info("Gateway service is shutting down...")
    await close_db()

app = FastAPI(
    title="OAITT-PRO Gateway Orchestrator",
    description="Gateway Orchestrator with security, analytics logging, and VRAM management.",
    version="1.0.0",
    lifespan=lifespan
)

async def unload_engine(engine_name: str, base_url: str):
    logger.info(f"Commanding {engine_name} at {base_url} to UNLOAD weights from VRAM...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{base_url}/unload")
            if resp.status_code == 200:
                logger.info(f"Successfully unloaded {engine_name}")
            else:
                logger.error(f"Failed to unload {engine_name}: Status {resp.status_code}")
    except Exception as e:
        logger.error(f"Exception while unloading {engine_name}: {e}")

@app.post("/v1/audio/transcriptions")
async def openai_transcribe(
    request: Request,
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: str = Form("whisper-1", description="Model name: whisper-1, whisperx, or gigaam"),
    language: Optional[str] = Form(None, description="Language code (e.g. ru, en)"),
    response_format: str = Form("json", description="Response format: json, verbose_json"),
    diarize: bool = Form(False, description="Enable speaker diarization"),
    min_avg_logprob: Optional[float] = Form(None, description="Anti-hallucination logprob threshold"),
    max_chars_per_second: Optional[float] = Form(None, description="Anti-hallucination speech rate limit"),
    client: dict = Depends(get_current_client)
):
    global current_gpu_engine
    
    # Determine engine based on model parameter
    requested_engine = "gigaam" if model.lower() == "gigaam" else "whisperx"
    target_url = GIGAAM_URL if requested_engine == "gigaam" else WHISPERX_URL
    model_name = "v3_e2e_rnnt" if requested_engine == "gigaam" else "bzikst/faster-whisper-large-v3-russian"
    
    logger.info(f"Client {client['name']} ({client['id']}) requested transcription with model: {model} (Engine: {requested_engine}, diarize={diarize})")
    
    # Read the file content
    file_bytes = await file.read()
    filename = file.filename or "audio.wav"
    
    start_time = time.perf_counter()
    status_str = "success"
    error_msg = None
    audio_duration = 0.0
    speech_duration = 0.0
    char_count = 0
    transcription_text = ""
    
    # Secure GPU access exclusively
    async with gpu_lock:
        if current_gpu_engine is not None and current_gpu_engine != requested_engine:
            # We must unload the other engine to prevent VRAM overflow
            prev_url = WHISPERX_URL if current_gpu_engine == "whisperx" else GIGAAM_URL
            await unload_engine(current_gpu_engine, prev_url)
            
        current_gpu_engine = requested_engine
        
        # Call the active transcription service
        try:
            async with httpx.AsyncClient(timeout=300.0) as httpx_client:
                files = {"file": (filename, file_bytes, file.content_type)}
                data = {
                    "language": language or "",
                    "response_format": response_format,
                    "diarize": "true" if diarize else "false"
                }
                
                logger.info(f"Forwarding request to {requested_engine} at {target_url}...")
                resp = await httpx_client.post(f"{target_url}/v1/audio/transcriptions", files=files, data=data)
                
                if resp.status_code != 200:
                    status_str = "failed"
                    error_msg = f"Inference engine returned status {resp.status_code}: {resp.text}"
                    raise HTTPException(status_code=resp.status_code, detail=error_msg)
                
                resp_json = resp.json()
        except httpx.RequestError as exc:
            status_str = "failed"
            error_msg = f"HTTP request to inference engine failed: {exc}"
            logger.error(error_msg)
            raise HTTPException(status_code=502, detail=error_msg)
            
    processing_time = time.perf_counter() - start_time
    
    # Extract metadata for logging
    if status_str == "success":
        transcription_text = resp_json.get("text", "")
        char_count = len(transcription_text)
        audio_duration = float(resp_json.get("duration", 0.0))
        
        # Compute speech duration from segments if available (VAD output)
        segments = resp_json.get("segments", [])
        if segments:
            speech_duration = sum(float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)) for seg in segments)
        else:
            speech_duration = audio_duration
            
        # 4. Anti-Hallucination Filter Logic
        filtered_segments = []
        is_filtered = False
        
        for seg in segments:
            seg_logprob = seg.get("avg_logprob")
            seg_text = seg.get("text", "")
            seg_start = float(seg.get("start", 0.0))
            seg_end = float(seg.get("end", 0.0))
            seg_duration = seg_end - seg_start
            seg_cps = len(seg_text) / seg_duration if seg_duration > 0 else 0.0
            
            # Apply filters
            if min_avg_logprob is not None and seg_logprob is not None and seg_logprob < min_avg_logprob:
                is_filtered = True
                logger.info(f"Filtering segment '{seg_text}' due to logprob {seg_logprob} < {min_avg_logprob}")
                continue
                
            if max_chars_per_second is not None and seg_cps > max_chars_per_second:
                is_filtered = True
                logger.info(f"Filtering segment '{seg_text}' due to CPS {seg_cps:.1f} > {max_chars_per_second}")
                continue
                
            filtered_segments.append(seg)
            
        if is_filtered:
            status_str = "hallucination_filtered"
            # Reconstruct text from remaining segments
            transcription_text = " ".join(seg.get("text", "").strip() for seg in filtered_segments).strip()
            char_count = len(transcription_text)
            resp_json["text"] = transcription_text
            resp_json["segments"] = filtered_segments
            
    # Log to PostgreSQL
    await log_transcription(
        client_id=client["id"],
        filename=filename,
        audio_duration=audio_duration,
        speech_duration=speech_duration,
        processing_time=processing_time,
        engine=requested_engine,
        model_name=model_name,
        diarization_enabled=diarize,
        char_count=char_count,
        status=status_str,
        error_message=error_msg
    )
    
    return JSONResponse(content=resp_json)

@app.post("/asr")
async def native_transcribe(
    audio_file: UploadFile = File(..., description="Audio file to transcribe"),
    output: str = Query("json", description="Output format: json, text, srt, vtt, tsv"),
    diarize: bool = Query(False, description="Enable speaker diarization"),
    min_avg_logprob: Optional[float] = Query(None, description="Anti-hallucination logprob threshold"),
    max_chars_per_second: Optional[float] = Query(None, description="Anti-hallucination speech rate limit"),
    client: dict = Depends(get_current_client)
):
    # Call OpenAI endpoint internally to leverage the engine loading/unloading, lock, and analytics logging,
    # and then format the response accordingly!
    # This is a very clean way to keep both endpoints in full parity!
    model_name = "whisperx" # Native ASR uses WhisperX by default
    
    # We simulate calling the openai_transcribe internally
    response = await openai_transcribe(
        request=None,
        file=audio_file,
        model=model_name,
        language=None,
        response_format="verbose_json" if output in ("srt", "vtt", "tsv", "json") else "json",
        diarize=diarize,
        min_avg_logprob=min_avg_logprob,
        max_chars_per_second=max_chars_per_second,
        client=client
    )
    
    if response.status_code != 200:
        return response
        
    resp_json = response.body
    # Because response is a JSONResponse, we parse its body
    import json
    data = json.loads(resp_json.decode("utf-8"))
    
    if output == "text":
        return PlainTextResponse(content=data.get("text", ""))
    elif output == "json":
        return JSONResponse(content=data)
        
    # Formats (SRT, VTT, TSV) from segments
    segments = data.get("segments", [])
    if output == "srt":
        # Format SRT
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
        # Format VTT
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
        # Format TSV
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
async def analytics_summary(client: dict = Depends(get_current_client)):
    # Admin gets full dashboard; standard clients only get their own data!
    if client["role"] == "admin":
        logger.info("Admin requested global analytical summary")
        summary = await get_analytics_summary()
    else:
        logger.info(f"Client {client['name']} requested personal analytical summary")
        summary = await get_analytics_summary(client_id=client["id"])
    return JSONResponse(content=summary)

@app.get("/health")
async def health_check():
    # Verify DB connection
    from gateway.db import DB_POOL
    db_ok = False
    if DB_POOL:
        try:
            async with DB_POOL.acquire() as conn:
                await conn.execute("SELECT 1")
                db_ok = True
        except Exception:
            pass
            
    # Check backends
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
