import os
import logging
import asyncio
import asyncpg
import json
from datetime import date as date_type
from typing import Optional, Dict, Any, List, Tuple

class ClientNotFoundError(Exception):
    """Raised when client_id does not exist in clients table."""


class PricingConflictError(Exception):
    """Raised on duplicate (client_id, currency, valid_from)."""

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

        # Create transcription_jobs table for async queue
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transcription_jobs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                filename VARCHAR(255) NOT NULL,
                file_path VARCHAR(512) NOT NULL,
                engine VARCHAR(50) NOT NULL,
                model_name VARCHAR(100) NOT NULL,
                diarization_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                language VARCHAR(10),
                response_format VARCHAR(50) DEFAULT 'json',
                min_avg_logprob NUMERIC(5, 2),
                max_chars_per_second NUMERIC(5, 2),
                webhook_url TEXT,
                status VARCHAR(50) NOT NULL DEFAULT 'pending',
                result JSONB,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON transcription_jobs(status);
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS client_pricing (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                audio_price_per_minute NUMERIC(10, 4),
                speech_price_per_minute NUMERIC(10, 4),
                currency VARCHAR(3) NOT NULL DEFAULT 'RUB',
                valid_from DATE NOT NULL DEFAULT CURRENT_DATE,
                valid_to DATE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (client_id, currency, valid_from)
            );
            CREATE INDEX IF NOT EXISTS idx_pricing_client_dates
                ON client_pricing (client_id, valid_from, valid_to);
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

_COST_LATERAL_JOIN = """
LEFT JOIN LATERAL (
    SELECT audio_price_per_minute, speech_price_per_minute
    FROM client_pricing cp
    WHERE cp.client_id = tl.client_id
      AND cp.currency = 'RUB'
      AND cp.valid_from <= tl.created_at::date
      AND (cp.valid_to IS NULL OR cp.valid_to >= tl.created_at::date)
    ORDER BY cp.valid_from DESC
    LIMIT 1
) cp ON true
"""


def _analytics_where(
    client_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    table_alias: str = "",
) -> Tuple[str, List[Any]]:
    prefix = f"{table_alias}." if table_alias else ""
    conditions: List[str] = []
    params: List[Any] = []
    idx = 1
    if client_id:
        conditions.append(f"{prefix}client_id = ${idx}")
        params.append(client_id)
        idx += 1
    if date_from:
        conditions.append(f"{prefix}created_at >= ${idx}::date")
        params.append(
            date_from if isinstance(date_from, date_type) else date_type.fromisoformat(date_from)
        )
        idx += 1
    if date_to:
        conditions.append(f"{prefix}created_at < (${idx}::date + INTERVAL '1 day')")
        params.append(
            date_to if isinstance(date_to, date_type) else date_type.fromisoformat(date_to)
        )
        idx += 1
    if not conditions:
        return "", []
    return " WHERE " + " AND ".join(conditions), params


def build_cost_from_aggregates(
    audio_minutes: float,
    speech_minutes: float,
    by_audio_total: float,
    by_speech_total: float,
    audio_rate: Optional[float] = None,
    speech_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Build the cost JSON object for analytics responses."""
    pricing_options: Dict[str, Any] = {}
    if audio_rate is not None:
        pricing_options["by_audio_duration"] = {
            "price_per_minute": round(float(audio_rate), 4),
            "total": round(float(by_audio_total), 2),
        }
    elif by_audio_total > 0:
        pricing_options["by_audio_duration"] = {"total": round(float(by_audio_total), 2)}
    if speech_rate is not None:
        pricing_options["by_speech_duration"] = {
            "price_per_minute": round(float(speech_rate), 4),
            "total": round(float(by_speech_total), 2),
        }
    elif by_speech_total > 0:
        pricing_options["by_speech_duration"] = {"total": round(float(by_speech_total), 2)}
    return {
        "currency": "RUB",
        "audio_minutes": round(float(audio_minutes), 2),
        "speech_minutes": round(float(speech_minutes), 2),
        "pricing_options": pricing_options,
    }


def _display_rate(distinct_count: int, min_rate) -> Optional[float]:
    if distinct_count == 1 and min_rate is not None:
        return float(min_rate)
    return None


def _serialize_pricing_row(row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "client_id": str(row["client_id"]),
        "audio_price_per_minute": (
            float(row["audio_price_per_minute"])
            if row["audio_price_per_minute"] is not None
            else None
        ),
        "speech_price_per_minute": (
            float(row["speech_price_per_minute"])
            if row["speech_price_per_minute"] is not None
            else None
        ),
        "currency": row["currency"],
        "valid_from": row["valid_from"].isoformat() if row["valid_from"] else None,
        "valid_to": row["valid_to"].isoformat() if row["valid_to"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


async def _fetch_cost_aggregates(
    conn,
    where_sql: str,
    params: List[Any],
) -> Dict[str, Any]:
    row = await conn.fetchrow(
        f"""
        SELECT
            COALESCE(SUM(tl.audio_duration_seconds), 0) / 60.0 AS audio_minutes,
            COALESCE(SUM(tl.speech_duration_seconds), 0) / 60.0 AS speech_minutes,
            COALESCE(SUM((tl.audio_duration_seconds / 60.0) * cp.audio_price_per_minute), 0)
                AS by_audio_total,
            COALESCE(SUM((tl.speech_duration_seconds / 60.0) * cp.speech_price_per_minute), 0)
                AS by_speech_total,
            COUNT(DISTINCT cp.audio_price_per_minute)
                FILTER (WHERE cp.audio_price_per_minute IS NOT NULL) AS audio_rate_cnt,
            COUNT(DISTINCT cp.speech_price_per_minute)
                FILTER (WHERE cp.speech_price_per_minute IS NOT NULL) AS speech_rate_cnt,
            MIN(cp.audio_price_per_minute)
                FILTER (WHERE cp.audio_price_per_minute IS NOT NULL) AS min_audio_rate,
            MIN(cp.speech_price_per_minute)
                FILTER (WHERE cp.speech_price_per_minute IS NOT NULL) AS min_speech_rate
        FROM transcription_logs tl
        {_COST_LATERAL_JOIN}
        {where_sql}
        """,
        *params,
    )
    if row is None:
        return build_cost_from_aggregates(0, 0, 0, 0)
    return build_cost_from_aggregates(
        float(row["audio_minutes"]),
        float(row["speech_minutes"]),
        float(row["by_audio_total"]),
        float(row["by_speech_total"]),
        _display_rate(int(row["audio_rate_cnt"] or 0), row["min_audio_rate"]),
        _display_rate(int(row["speech_rate_cnt"] or 0), row["min_speech_rate"]),
    )


def _summary_metrics_from_row(row, by_engine: Dict[str, int]) -> Dict[str, Any]:
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
        "by_engine": by_engine,
    }


async def get_analytics_summary(
    client_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retrieve aggregated analytical logs with cost.
    If client_id is provided, filters for that specific client.
    """
    if DB_POOL is None:
        return {}

    where_sql, params = _analytics_where(client_id, date_from, date_to)

    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*) as total_transcriptions,
                COALESCE(SUM(audio_duration_seconds), 0) as total_audio_duration_seconds,
                COALESCE(SUM(speech_duration_seconds), 0) as total_speech_duration_seconds,
                COALESCE(SUM(processing_time_seconds), 0) as total_processing_seconds,
                SUM(CASE WHEN status IN ('success', 'completed') THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0) as success_rate
            FROM transcription_logs
            {where_sql}
            """,
            *params,
        )

        engines_rows = await conn.fetch(
            f"""
            SELECT engine, COUNT(*) as count
            FROM transcription_logs
            {where_sql}
            GROUP BY engine
            """,
            *params,
        )
        by_engine = {r["engine"]: r["count"] for r in engines_rows}
        tl_where, tl_params = _analytics_where(client_id, date_from, date_to, table_alias="tl")
        cost = await _fetch_cost_aggregates(conn, tl_where, tl_params)

        result = _summary_metrics_from_row(row, by_engine)
        result["cost"] = cost
        return result


async def get_analytics_by_client(
    date_from: str,
    date_to: str,
) -> List[Dict[str, Any]]:
    """Per-client analytics for a date range (admin report)."""
    if DB_POOL is None:
        return []

    tl_where, params = _analytics_where(None, date_from, date_to, table_alias="tl")

    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                c.id AS client_id,
                c.name AS client_name,
                COUNT(*) AS total_transcriptions,
                COALESCE(SUM(tl.audio_duration_seconds), 0) AS total_audio_duration_seconds,
                COALESCE(SUM(tl.speech_duration_seconds), 0) AS total_speech_duration_seconds,
                COALESCE(SUM(tl.processing_time_seconds), 0) AS total_processing_seconds,
                SUM(CASE WHEN tl.status IN ('success', 'completed') THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0) AS success_rate,
                COALESCE(SUM(tl.audio_duration_seconds), 0) / 60.0 AS audio_minutes,
                COALESCE(SUM(tl.speech_duration_seconds), 0) / 60.0 AS speech_minutes,
                COALESCE(SUM((tl.audio_duration_seconds / 60.0) * cp.audio_price_per_minute), 0)
                    AS by_audio_total,
                COALESCE(SUM((tl.speech_duration_seconds / 60.0) * cp.speech_price_per_minute), 0)
                    AS by_speech_total,
                COUNT(DISTINCT cp.audio_price_per_minute)
                    FILTER (WHERE cp.audio_price_per_minute IS NOT NULL) AS audio_rate_cnt,
                COUNT(DISTINCT cp.speech_price_per_minute)
                    FILTER (WHERE cp.speech_price_per_minute IS NOT NULL) AS speech_rate_cnt,
                MIN(cp.audio_price_per_minute)
                    FILTER (WHERE cp.audio_price_per_minute IS NOT NULL) AS min_audio_rate,
                MIN(cp.speech_price_per_minute)
                    FILTER (WHERE cp.speech_price_per_minute IS NOT NULL) AS min_speech_rate
            FROM transcription_logs tl
            JOIN clients c ON c.id = tl.client_id
            {_COST_LATERAL_JOIN}
            {tl_where}
            GROUP BY c.id, c.name
            ORDER BY c.name
            """,
            *params,
        )

        engines_by_client: Dict[str, Dict[str, int]] = {}
        engine_rows = await conn.fetch(
            f"""
            SELECT tl.client_id, tl.engine, COUNT(*) AS count
            FROM transcription_logs tl
            {tl_where}
            GROUP BY tl.client_id, tl.engine
            """,
            *params,
        )
        for er in engine_rows:
            cid = str(er["client_id"])
            engines_by_client.setdefault(cid, {})[er["engine"]] = er["count"]

        results: List[Dict[str, Any]] = []
        for row in rows:
            cid = str(row["client_id"])
            by_engine = engines_by_client.get(cid, {})
            metrics = _summary_metrics_from_row(row, by_engine)
            metrics["client_id"] = cid
            metrics["client_name"] = row["client_name"]
            metrics["cost"] = build_cost_from_aggregates(
                float(row["audio_minutes"]),
                float(row["speech_minutes"]),
                float(row["by_audio_total"]),
                float(row["by_speech_total"]),
                _display_rate(int(row["audio_rate_cnt"] or 0), row["min_audio_rate"]),
                _display_rate(int(row["speech_rate_cnt"] or 0), row["min_speech_rate"]),
            )
            results.append(metrics)
        return results


async def create_client_pricing(
    client_id: str,
    audio_price_per_minute: Optional[float] = None,
    speech_price_per_minute: Optional[float] = None,
    valid_from: Optional[str] = None,
) -> Dict[str, Any]:
    if DB_POOL is None:
        raise RuntimeError("DB pool not initialized")

    async with DB_POOL.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM clients WHERE id = $1)",
                client_id,
            )
            if not exists:
                raise ClientNotFoundError()

            valid_from_date: Optional[date_type] = None
            if valid_from:
                valid_from_date = date_type.fromisoformat(valid_from)
                close_params: List[Any] = [client_id, valid_from_date]
                valid_from_expr = "$2::date"
            else:
                valid_from_expr = "CURRENT_DATE"
                close_params = [client_id]

            await conn.execute(
                f"""
                UPDATE client_pricing
                SET valid_to = {valid_from_expr} - INTERVAL '1 day'
                WHERE client_id = $1
                  AND currency = 'RUB'
                  AND valid_to IS NULL
                  AND valid_from < {valid_from_expr}
                """,
                *close_params,
            )

            try:
                if valid_from_date is not None:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO client_pricing (
                            client_id, audio_price_per_minute, speech_price_per_minute, valid_from
                        ) VALUES ($1, $2, $3, $4)
                        RETURNING *
                        """,
                        client_id,
                        audio_price_per_minute,
                        speech_price_per_minute,
                        valid_from_date,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO client_pricing (
                            client_id, audio_price_per_minute, speech_price_per_minute
                        ) VALUES ($1, $2, $3)
                        RETURNING *
                        """,
                        client_id,
                        audio_price_per_minute,
                        speech_price_per_minute,
                    )
            except asyncpg.UniqueViolationError as exc:
                raise PricingConflictError() from exc
            return _serialize_pricing_row(row)


async def get_active_client_pricing(client_id: str) -> Optional[Dict[str, Any]]:
    if DB_POOL is None:
        return None
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM client_pricing
            WHERE client_id = $1
              AND currency = 'RUB'
              AND valid_from <= CURRENT_DATE
              AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)
            ORDER BY valid_from DESC
            LIMIT 1
            """,
            client_id,
        )
        if row:
            return _serialize_pricing_row(row)
    return None


async def get_client_pricing_history(client_id: str) -> List[Dict[str, Any]]:
    if DB_POOL is None:
        return []
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM client_pricing
            WHERE client_id = $1 AND currency = 'RUB'
            ORDER BY valid_from DESC
            """,
            client_id,
        )
        return [_serialize_pricing_row(r) for r in rows]

async def create_transcription_job(
    client_id: str,
    filename: str,
    file_path: str,
    engine: str,
    model_name: str,
    diarization_enabled: bool,
    language: Optional[str],
    response_format: str,
    min_avg_logprob: Optional[float],
    max_chars_per_second: Optional[float],
    webhook_url: Optional[str]
) -> str:
    if DB_POOL is None:
        raise RuntimeError("DB pool not initialized")
    async with DB_POOL.acquire() as conn:
        job_id = await conn.fetchval("""
            INSERT INTO transcription_jobs (
                client_id, filename, file_path, engine, model_name,
                diarization_enabled, language, response_format,
                min_avg_logprob, max_chars_per_second, webhook_url, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'pending')
            RETURNING id
        """, client_id, filename, file_path, engine, model_name,
        diarization_enabled, language, response_format,
        min_avg_logprob, max_chars_per_second, webhook_url)
        return str(job_id)

async def get_transcription_job(job_id: str) -> Optional[Dict[str, Any]]:
    if DB_POOL is None:
        return None
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, client_id, created_at, updated_at, filename, file_path,
                   engine, model_name, diarization_enabled, language,
                   response_format, min_avg_logprob, max_chars_per_second,
                   webhook_url, status, result, error_message
            FROM transcription_jobs WHERE id = $1
        """, job_id)
        if row:
            return dict(row)
    return None

async def get_next_pending_job_atomic() -> Optional[Dict[str, Any]]:
    if DB_POOL is None:
        return None
    async with DB_POOL.acquire() as conn:
        # Atomic selection and status update using safe lock
        row = await conn.fetchrow("""
            UPDATE transcription_jobs
            SET status = 'processing', updated_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id FROM transcription_jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, client_id, filename, file_path, engine, model_name,
                      diarization_enabled, language, response_format,
                      min_avg_logprob, max_chars_per_second, webhook_url
        """)
        if row:
            return dict(row)
    return None

async def update_job_success(job_id: str, result_json: Dict[str, Any], status_str: str):
    if DB_POOL is None:
        return
    async with DB_POOL.acquire() as conn:
        await conn.execute("""
            UPDATE transcription_jobs
            SET status = $2, result = $3, updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
        """, job_id, status_str, json.dumps(result_json))

async def update_job_failure(job_id: str, error_message: str):
    if DB_POOL is None:
        return
    async with DB_POOL.acquire() as conn:
        await conn.execute("""
            UPDATE transcription_jobs
            SET status = 'failed', error_message = $2, updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
        """, job_id, error_message)

async def delete_expired_jobs_from_db() -> int:
    if DB_POOL is None:
        return 0
    async with DB_POOL.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM transcription_jobs
            WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '24 hours'
        """)
        # Parse 'DELETE 5' into count
        count = 0
        if result.startswith("DELETE "):
            try:
                count = int(result.split()[1])
            except (IndexError, ValueError):
                pass
        return count

async def is_file_active_in_db(file_path: str) -> bool:
    if DB_POOL is None:
        return False
    async with DB_POOL.acquire() as conn:
        val = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM transcription_jobs
                WHERE file_path = $1 AND status IN ('pending', 'processing')
            )
        """, file_path)
        return bool(val)
