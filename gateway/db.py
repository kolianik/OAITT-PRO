import os
import logging
import asyncio
import asyncpg
from typing import Optional, Dict, Any

logger = logging.getLogger("gateway.db")

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "transcribe_db")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "secure_pass")
ADMIN_KEY = os.getenv("ADMIN_KEY", "admin-secret-key")

DB_POOL: Optional[asyncpg.Pool] = None

async def init_db():
    global DB_POOL
    dsn = f"postgres://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    
    # Retry connecting to DB since Postgres might start slightly slower
    for i in range(10):
        try:
            DB_POOL = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
            logger.info("Successfully connected to PostgreSQL")
            break
        except Exception as e:
            logger.warning(f"Failed to connect to DB, retrying ({i+1}/10)... Error: {e}")
            await asyncio.sleep(3)
    
    if DB_POOL is None:
        raise RuntimeError("Could not connect to PostgreSQL after 10 attempts.")

    # Create tables
    async with DB_POOL.acquire() as conn:
        # Create clients table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                api_key VARCHAR(255) UNIQUE NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_clients_api_key ON clients(api_key);
        """)
        
        # Create transcription_logs table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transcription_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                filename VARCHAR(255) NOT NULL,
                audio_duration_seconds NUMERIC(10, 2) NOT NULL,
                speech_duration_seconds NUMERIC(10, 2) NOT NULL,
                processing_time_seconds NUMERIC(10, 2) NOT NULL,
                engine VARCHAR(50) NOT NULL,
                model_name VARCHAR(100) NOT NULL,
                diarization_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                char_count INTEGER NOT NULL,
                status VARCHAR(50) NOT NULL,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_logs_client_id ON transcription_logs(client_id);
            CREATE INDEX IF NOT EXISTS idx_logs_created_at ON transcription_logs(created_at);
        """)
        
        # Seed an initial client for testing/general usage if table is empty and ADMIN_KEY is set
        row_count = await conn.fetchval("SELECT COUNT(*) FROM clients")
        if row_count == 0:
            # Seed a default client key
            await conn.execute("""
                INSERT INTO clients (id, name, api_key, is_active)
                VALUES ('00000000-0000-0000-0000-000000000000', 'Default Client', 'default-client-key', true)
                ON CONFLICT DO NOTHING;
            """)
            logger.info("Database seeded with Default Client key ('default-client-key')")

async def close_db():
    global DB_POOL
    if DB_POOL:
        await DB_POOL.close()
        logger.info("Closed PostgreSQL pool")

async def authenticate_client(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Checks if API key belongs to a valid active client or the admin.
    Returns a dict with client details: { 'id': UUID, 'name': str, 'role': 'client'|'admin' }
    """
    if api_key == ADMIN_KEY:
        return {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Global Admin",
            "role": "admin"
        }
    
    if DB_POOL is None:
        return None
        
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name FROM clients WHERE api_key = $1 AND is_active = true",
            api_key
        )
        if row:
            return {
                "id": str(row["id"]),
                "name": row["name"],
                "role": "client"
            }
    return None

async def log_transcription(
    client_id: str,
    filename: str,
    audio_duration: float,
    speech_duration: float,
    processing_time: float,
    engine: str,
    model_name: str,
    diarization_enabled: bool,
    char_count: int,
    status: str,
    error_message: Optional[str] = None
):
    if DB_POOL is None:
        logger.error("DB Pool not initialized, cannot log transcription")
        return
        
    async with DB_POOL.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO transcription_logs (
                    client_id, filename, audio_duration_seconds, speech_duration_seconds,
                    processing_time_seconds, engine, model_name, diarization_enabled,
                    char_count, status, error_message
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """, 
            client_id, filename, audio_duration, speech_duration,
            processing_time, engine, model_name, diarization_enabled,
            char_count, status, error_message
            )
            logger.info(f"Logged transcription task for client {client_id} with status {status}")
        except Exception as e:
            logger.error(f"Failed to write transcription log: {e}")

async def get_analytics_summary(client_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieve aggregated analytical logs.
    If client_id is provided, filters for that specific client.
    """
    if DB_POOL is None:
        return {}
        
    async with DB_POOL.acquire() as conn:
        query_suffix = ""
        params = []
        if client_id:
            query_suffix = " WHERE client_id = $1"
            params.append(client_id)
            
        row = await conn.fetchrow(f"""
            SELECT 
                COUNT(*) as total_transcriptions,
                COALESCE(SUM(audio_duration_seconds), 0) as total_audio_duration_seconds,
                COALESCE(SUM(speech_duration_seconds), 0) as total_speech_duration_seconds,
                COALESCE(SUM(processing_time_seconds), 0) as total_processing_seconds,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) as success_rate
            FROM transcription_logs{query_suffix}
        """, *params)
        
        engines_rows = await conn.fetch(f"""
            SELECT engine, COUNT(*) as count 
            FROM transcription_logs{query_suffix} 
            GROUP BY engine
        """, *params)
        
        by_engine = {r["engine"]: r["count"] for r in engines_rows}
        
        total_audio = float(row["total_audio_duration_seconds"])
        total_proc = float(row["total_processing_seconds"])
        avg_rtf = round(total_proc / total_audio, 4) if total_audio > 0 else 0.0
        
        return {
            "total_transcriptions": int(row["total_transcriptions"]),
            "total_audio_duration_seconds": round(total_audio, 2),
            "total_speech_duration_seconds": round(float(row["total_speech_duration_seconds"]), 2),
            "total_processing_seconds": round(total_proc, 2),
            "average_rtf": avg_rtf,
            "success_rate": round(row["success_rate"] or 0.0, 4),
            "by_engine": by_engine
        }
