import os
import asyncio
import logging
from psycopg.pq import TransactionStatus
from typing import Callable, Awaitable, TypeVar
from psycopg import AsyncConnection, OperationalError
from psycopg_pool import AsyncConnectionPool, PoolClosed
from datetime import datetime, timedelta, timezone
# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================================
# POOL CONFIGURATION & GLOBALS
# =====================================
# Connection pool sized for medium concurrency (~80 concurrent DB operations):
# - POOL_MIN: keeps a minimum number of connections ready for bursts
# - POOL_MAX: caps total Postgres connections to avoid overloading the DB
# - POOL_MAX_WAITING: maximum number of coroutines that can wait for a connection
# - POOL_TIMEOUT: seconds to wait for a connection before raising an error
# - POOL_MAX_LIFETIME: recycles connections to avoid stale server-side state
# - POOL_RECONNECT_TIMEOUT: lets the pool self-heal if a server connection is lost
POOL_MIN = 20
POOL_MAX = 80
POOL_TIMEOUT = 15         # seconds to wait for a connection before raising
POOL_MAX_WAITING = 300     # max coroutines queued for a connection
POOL_MAX_LIFETIME = 3600    # seconds before a connection is recycled
POOL_RECONNECT_TIMEOUT = 300 # seconds the pool tries to reconnect a broken server

MAX_WORK_HOURS = timedelta(hours=6, minutes=30)


# =====================================
# POOL STATE
# =====================================
_pool: AsyncConnectionPool | None = None

# Async lock to serialize pool creation and replacement
# Ensures that only one coroutine at a time can create or reset the pool,
# prevetind multiple pools from being spawned concurrently
_pool_lock = asyncio.Lock()


# =====================================
# UTC UTILITY FUCTIONS
# =====================================
def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def format_timedelta(td: timedelta | None) -> str:
    if not td:
        return "00:00:00"
    total = int(td.total_seconds())
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# =====================================
# CONNECTION POOL MANAGEMENT
# =====================================
def _conninfo() -> str:
    """
    Construct PostgreSQL connection string from environment variables.
    Raises RuntimeError if any required DB environment variable is missing.
    """
    mapping = {
        "POSTGRES_HOST": "host",
        "POSTGRES_PORT": "port",
        "POSTGRES_DB": "dbname",
        "POSTGRES_USER": "user",
        "POSTGRES_PASSWORD": "password",
    }

    missing = [var for var in mapping if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required DB environment variables: {missing}")

    # Format for psycopg
    return " ".join(f"{mapping[var]}={os.getenv(var)}" for var in mapping)

async def _create_pool() -> AsyncConnectionPool:
    """Create and open a new pool. Called only under _pool_lock."""
    logger.info("Creating async PostgreSQL connection pool")
    pool = AsyncConnectionPool(
        conninfo=_conninfo(),
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        timeout=POOL_TIMEOUT,
        max_waiting=POOL_MAX_WAITING,
        max_lifetime=POOL_MAX_LIFETIME,
        reconnect_timeout=POOL_RECONNECT_TIMEOUT,
        open=False, # do not automatically open the pool; we will await open() to ensure min_size connections are ready
    )
    await pool.open(wait=True) # block until POOL_MIN connections are ready

    return pool

async def get_pool() -> AsyncConnectionPool:
    """Return the live pool, creating it if necessary. Safe for concurrent coroutines."""
    global _pool

    # Fast path: pool exists and is open
    pool = _pool
    if pool is not None and not pool.closed:
        return pool

    # Slow path: acquire lock to create or refresh pool
    async with _pool_lock:
        # Double-check under lock in case another coroutine already created it
        if _pool is None or _pool.closed:
            _pool = await _create_pool()
        return _pool

async def _replace_pool() -> None:
    # Attempt to close old pool with a 30-second drain window.
    # Connections not closed within this time are abandoned; the DB will terminate them server-side.
    global _pool
    async with _pool_lock:
        old = _pool
        _pool = None # mark unavailable while we rebuild

        if old is not None and not old.closed:
            logger.warning("Closing broken pool (30 s drain window)")
            try:
                await asyncio.wait_for(old.close(), timeout=30.0)
            except (asyncio.TimeoutError, Exception):
                pass # connections that didn't drain are abandoned 
                     # the DB will time them out server-side

        logger.info("Opening replacement pool")
        _pool = await _create_pool()


# =====================================
# CORE RETRY | DB EXECUTOR
# =====================================
# All public database functions are expressed as:
#
#   return await _run(lambda conn: <body using conn>)
#
# _run owns the full connection lifecycle via a complete `async with` block.
# Python's context manager protocol guarantees __aexit__ runs under every
# exit path: normal return, exception, asyncio.CancelledError, KeyboardInterrupt.
# We NEVER call __aenter__ / __aexit__ manually.
async def init_database() -> None:
    """Create schema tables. Called once from setup_hook before bot starts."""

    async def _run_init(conn: AsyncConnection) -> None:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS registros_diarios (
                    id               SERIAL PRIMARY KEY,
                    channel_id       TEXT NOT NULL,
                    user_id          TEXT NOT NULL,
                    data             DATE NOT NULL,
                    first_on         TIMESTAMPTZ,
                    finish           TIMESTAMPTZ,
                    work_sum         INTERVAL DEFAULT '0',
                    break_sum        INTERVAL DEFAULT '0',
                    is_on            BOOLEAN DEFAULT FALSE,
                    is_break         BOOLEAN DEFAULT FALSE,
                    is_finished      BOOLEAN DEFAULT FALSE,
                    mobile_first_on  BOOLEAN DEFAULT FALSE,
                    last_transition  TIMESTAMPTZ,
                    last_action      TEXT,
                    UNIQUE(channel_id, user_id, data)
                )
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS permissao_canal (
                    channel_id  TEXT NOT NULL,
                    user_id     TEXT NOT NULL,
                    tipo        TEXT NOT NULL CHECK (tipo IN ('admin', 'locked_user')),
                    locked_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, user_id, tipo)
                )
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_status_config (
                    channel_id        TEXT PRIMARY KEY,
                    status_message_id TEXT NOT NULL,
                    updated_at        TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS super_admins (
                    user_id   TEXT PRIMARY KEY,
                    added_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Index for the hot path: every button click hits this lookup
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_registros_lookup
                ON registros_diarios (channel_id, user_id, data)
            """)
            # Index for the channel lock lookup on every record_action
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_permissao_locked
                ON permissao_canal (user_id, tipo)
                WHERE tipo = 'locked_user'
            """)
            # Index to enforce one locked channel per user
            await cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uniq_locked_user
                ON permissao_canal(user_id)
                WHERE tipo = 'locked_user'
            """)
        await conn.commit()

    try:
        await _run(_run_init)
        logger.info("Database schema ready")
    except Exception:
        logger.exception("Database initialisation failed")
        raise

def _is_transient_error(exc: OperationalError) -> bool:
    """
    Return True if this OperationalError is likely transient
    (network issues, server restart, connection reset, etc)
    """
    msg = str(exc).lower()
    transient_keywords = (
        "connection reset",
        "could not connect",
        "server closed the connection",
        "terminating connection",
        "timeout"
    )
    return any(keyword in msg for keyword in transient_keywords)

_T = TypeVar("_T")
async def _run(
    operation: Callable[[AsyncConnection], Awaitable[_T]],
    retries: int = 3,
    retry_delay: float = 1.5,
) -> _T:
    """
    Execute 'operation(conn)' with automatic retry on transient failures.

    Guarantees:
    - No connection leaks (connections are always returned to the pool);
    - Proper propagation of asyncio.CancelledError for clean shutdown;
    - Exponential backoff on transient failures to avoid overloading the DB;
    - Pool replacement only occurs for transient OperationalErrors.
    """
    last_exc: Exception | None = None

    for attempt in range(retries):
        try:
            pool = await get_pool()
            async with pool.connection() as conn:
                # Run the operation inside a connection context.
                # __aexit__ is guaranteed to execute even if CancelledError occurs.
                return await operation(conn)
                # __aexit__ fires here: conn returned to pool

        except asyncio.CancelledError:
            # Never swallow cancellation, required for clean shutdown
            raise

        except OperationalError as exc:
            last_exc = exc
            logger.warning(
                "OperationalError on attempt %d/%d: %s",
                attempt + 1, retries, exc,
            )
            
            if not _is_transient_error(exc):
                # Non-transient DB errors fail immediately; only transient errors are retried.
                raise

            if attempt >= retries - 1:
                break

            # Exponential backoff: 1.5s, 3s, 6s...
            delay = retry_delay * (2 ** attempt)

            logger.info(
                "Transient error detected. Replacing pool and retrying in %.2fs...",
                delay
            )

            await _replace_pool()
            await asyncio.sleep(delay)

        except PoolClosed as exc:
            # PoolClosed: the pool was shut down (e.g., during bot shutdown); propagate error immediately.
            raise RuntimeError("Database pool is closed") from exc

    # Defensive: should never be None here, but avoid raising None
    if last_exc:
        raise last_exc
    
    raise RuntimeError("_run failed without capturing an exception (unexpected state)")


# =====================================
# INTERNAL TRANSACTION HELPERS
# =====================================
# These accept an already-open conn so they participate in the caller's transaction.
async def _fetch_daily_record(
    conn: AsyncConnection, channel_id: str, user_id: str, data
) -> dict:
    """
    Return the daily record for (channel, user, date), creating it if absent.

    Concurrency model:
    - Single atomic UPSERT
    - Always returns a locked row
    - Prevents lost updates under concurrent requests
    """

    if conn.info.transaction_status == TransactionStatus.IDLE:
        raise RuntimeError(
            "_fetch_daily_record must be called inside a transaction "
            "(use: async with conn.transaction():)"
        )

    async with conn.cursor() as cur:
        # UPSERT: insert new row or return existing one with row-level lock
        await cur.execute("""
            INSERT INTO registros_diarios (channel_id, user_id, data)
            VALUES (%s, %s, %s)
            ON CONFLICT (channel_id, user_id, data)
            DO UPDATE SET channel_id = EXCLUDED.channel_id
            RETURNING 
                    id, channel_id, user_id, data,
                    first_on, finish, work_sum, break_sum,
                    is_on, is_break, is_finished, mobile_first_on,
                    last_transition, last_action
        """, (channel_id, user_id, data))

        row = await cur.fetchone()

    return {
        "id":              row[0],
        "channel_id":      row[1],
        "user_id":         row[2],
        "data":            row[3],
        "first_on":        row[4],
        "finish":          row[5],
        "work_sum":        row[6] or timedelta(0),
        "break_sum":       row[7] or timedelta(0),
        "is_on":           row[8],
        "is_break":        row[9],
        "is_finished":     row[10],
        "mobile_first_on": row[11],
        "last_transition": row[12],
        "last_action":     row[13],
    }

async def _persist_record(conn: AsyncConnection, r: dict) -> None:
    """Write an in-memory record back to the database. Must be within a transaction."""
    async with conn.cursor() as cur:
        await cur.execute("""
            UPDATE registros_diarios
            SET first_on         = %s,
                finish           = %s,
                work_sum         = %s,
                break_sum        = %s,
                is_on            = %s,
                is_break         = %s,
                is_finished      = %s,
                mobile_first_on  = %s,
                last_transition  = %s,
                last_action      = %s
            WHERE id = %s
        """, (
            r["first_on"], r["finish"], r["work_sum"], r["break_sum"],
            r["is_on"], r["is_break"], r["is_finished"], r["mobile_first_on"],
            r["last_transition"], r["last_action"], r["id"],
        ))


# =====================================
# RECORD ACTIONS — hot path
# =====================================
async def record_action(
    channel_id: str,
    user_id: str,
    acao: str,
    dispositivo: str | None = None,
) -> tuple[bool, str]:
    """
    Register ON / BREAK / FINISH for a user. Returns (success, message).

    Concurrency model:
    - One _run() call -> one pooled connection -> one transaction.
    - _fetch_daily_record() uses atomic UPSERT (INSERT ... ON CONFLICT DO UPDATE)
      which acquires a row-level lock on conflict, preventing lost updates.
    - 200 concurrent calls for 200 different users -> 200 different row locks,
      all proceeding in parallel with zero contention.
    - 2 concurrent calls for the SAME user -> one waits at FOR UPDATE until
      the first commits; it then sees the updated state and responds correctly.
    """
    async def _body(conn: AsyncConnection) -> tuple[bool, str]:
        async with conn.transaction():
            # Channel-lock check uses its own connection (no nesting needed)
            locked_ch = await _fetch_locked_channel(conn, user_id)
            if locked_ch and locked_ch != channel_id:
                return False, f"You are already registered at <#{locked_ch}>. Contact an admin."
            
            await _write_channel_lock(conn, user_id, channel_id)

            hoje = utcnow().date()
            agora = utcnow()
            r = await _fetch_daily_record(conn, channel_id, user_id, hoje)

            # -- ON --
            if acao == "ON":
                if r["is_finished"]:
                    return False, "❌ Day already finished. No further actions can be registered."

                if not r["first_on"]:
                    r.update(first_on=agora, mobile_first_on=(dispositivo == "mobile"),
                             is_on=True, last_transition=agora, last_action="ON")
                    await _persist_record(conn, r)
                    return True, "🟢 First ON of the day registered! Good work!"

                if r["is_break"]:
                    pause = agora - ensure_utc(r["last_transition"])
                    r.update(break_sum=(r["break_sum"] or timedelta(0)) + pause,
                             is_break=False, is_on=True,
                             last_transition=agora, last_action="ON")
                    await _persist_record(conn, r)
                    return True, f"🟢 Break return registered! Break duration: {format_timedelta(pause)}"

                if r["is_on"]:
                    return False, "⚠️ You are already working!"
                return False, "❌ Invalid state for ON."

            # -- BREAK --
            elif acao == "BREAK":
                if not r["is_on"] or r["is_break"]:
                    return False, "❌ You need to be working to start a break."
                if r["is_finished"]:
                    return False, "❌ Day already finished."

                worked = agora - ensure_utc(r["last_transition"])
                new_sum = (r["work_sum"] or timedelta(0)) + worked

                if new_sum >= MAX_WORK_HOURS:
                    r.update(work_sum=MAX_WORK_HOURS, finish=agora,
                             is_on=False, is_break=False, is_finished=True,
                             last_transition=agora, last_action="FINISH")
                    await _persist_record(conn, r)
                    return True, "🔴 Day automatically finished (6h30min reached)!\n"

                r.update(work_sum=new_sum, is_on=False, is_break=True,
                         last_transition=agora, last_action="BREAK")
                await _persist_record(conn, r)
                return True, f"🟡 Break started! Work session: {format_timedelta(worked)}"

            # -- FINISH --
            elif acao == "FINISH":
                if not r["is_on"] or r["is_break"]:
                    return False, "❌ You need to be working to finish the day."
                if r["is_finished"]:
                    return False, "❌ Day already finished."

                worked = agora - ensure_utc(r["last_transition"])
                r.update(
                    work_sum=min((r["work_sum"] or timedelta(0)) + worked, MAX_WORK_HOURS),
                    finish=agora, is_on=False, is_finished=True,
                    last_transition=agora, last_action="FINISH",
                )
                await _persist_record(conn, r)
                return True, "🔴 Day finished!\n"

            return False, "❌ Invalid action."

    try:
        return await _run(_body)
    except Exception as e:
        logger.exception("record_action failed for user %s", user_id)
        return False, f"❌ Database error: {e}"


# =====================================
# CHANNEL LOCK — internal helpers
# =====================================
# These accept 'conn' so they run inside record_action's transaction.
# Callers in the public API that need standalone access use _run() themselves.
async def _fetch_locked_channel(conn: AsyncConnection, user_id: str) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT channel_id FROM permissao_canal
            WHERE user_id = %s AND tipo = 'locked_user'
        """, (user_id,))
        row = await cur.fetchone()
    return row[0] if row else None

async def _write_channel_lock(
    conn: AsyncConnection, user_id: str, channel_id: str
    ) -> None:
    async with conn.cursor() as cur:
        await cur.execute("""
            INSERT INTO permissao_canal (channel_id, user_id, tipo)
            VALUES (%s, %s, 'locked_user')
            ON CONFLICT (user_id) WHERE tipo = 'locked_user'
            DO NOTHING
        """, (channel_id, user_id))


# =====================================
# PUBLIC API - Status / Reports
# =====================================
async def status_atual_users(channel_id: str) -> dict:
    async def _body(conn):
        hoje = utcnow().date()
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT user_id, first_on, finish, is_on, is_break,
                       is_finished, last_transition, last_action
                FROM registros_diarios
                WHERE channel_id = %s AND data = %s
            """, (channel_id, hoje))
            rows = await cur.fetchall()

        result = {}
        for uid, first_on, finish, is_on, is_break, is_finished, last_tr, _ in rows:
            if is_finished:
                estado, hora = "AUSENTE", int(finish.timestamp()) if finish else None
            elif is_break:
                estado, hora = "PAUSA", int(last_tr.timestamp()) if last_tr else None
            elif is_on:
                estado, hora = "TRABALHANDO", int(last_tr.timestamp()) if last_tr else None
            else:
                estado, hora = "NAO_INICIADO", None
            result[uid] = {"estado": estado, "data": hoje.isoformat(), "hora": hora}
        return result

    try:
        return await _run(_body)
    except Exception:
        logger.exception("status_atual_users failed")
        return {}

async def period_report(channel_id: str, user_id: str, data_inicio, data_fim) -> list:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT data, first_on, finish, work_sum, break_sum, is_finished
                FROM registros_diarios
                WHERE channel_id = %s AND user_id = %s
                  AND data BETWEEN %s AND %s
                ORDER BY data
            """, (channel_id, user_id, data_inicio, data_fim))
            return await cur.fetchall()
    try:
        return await _run(_body)
    except Exception:
        logger.exception("period_report failed")
        return []

async def mobile_records(channel_id: str, user_id: str, data_inicio, data_fim) -> list:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT data, first_on
                FROM registros_diarios
                WHERE channel_id = %s AND user_id = %s
                  AND data BETWEEN %s AND %s
                  AND mobile_first_on = TRUE
                ORDER BY data
            """, (channel_id, user_id, data_inicio, data_fim))
            return await cur.fetchall()
    try:
        return await _run(_body)
    except Exception:
        logger.exception("mobile_records failed")
        return []

async def first_last_record(channel_id: str, user_id: str) -> tuple:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT MIN(data), MAX(data) FROM registros_diarios
                WHERE channel_id = %s AND user_id = %s
            """, (channel_id, user_id))
            row = await cur.fetchone()
        return (row[0], row[1]) if row else (None, None)
    try:
        return await _run(_body)
    except Exception:
        logger.exception("first_last_record failed")
        return None, None

async def export_csv_data(channel_id: str) -> list:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT channel_id, user_id, data,
                       first_on, finish, work_sum, break_sum,
                       is_finished, mobile_first_on
                FROM registros_diarios
                WHERE channel_id = %s
                ORDER BY data DESC, user_id
            """, (channel_id,))
            return await cur.fetchall()
    try:
        return await _run(_body)
    except Exception:
        logger.exception("export_csv_data failed")
        return []


# =====================================
# PUBLIC API — Permissions / Admins
# =====================================
async def is_admin(channel_id: str, user_id: str) -> bool:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 1 FROM permissao_canal
                WHERE channel_id = %s AND user_id = %s AND tipo = 'admin'
            """, (channel_id, user_id))
            return await cur.fetchone() is not None
    try:
        return await _run(_body)
    except Exception:
        logger.exception("is_admin failed")
        return False

async def config_admins_adds(channel_id: str, user_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO permissao_canal (channel_id, user_id, tipo)
                    VALUES (%s, %s, 'admin')
                    ON CONFLICT DO NOTHING
                """, (channel_id, user_id))
        return True
    return await _run(_body)

async def config_admins_removes(channel_id: str, user_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("""
                    DELETE FROM permissao_canal
                    WHERE channel_id = %s AND user_id = %s AND tipo = 'admin'
                """, (channel_id, user_id))
                return cur.rowcount > 0
    return await _run(_body)

async def config_admins_list(channel_id: str) -> list[str]:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT user_id FROM permissao_canal
                WHERE channel_id = %s AND tipo = 'admin'
            """, (channel_id,))
            return [row[0] for row in await cur.fetchall()]
    try:
        return await _run(_body)
    except Exception:
        logger.exception("config_admins_list failed")
        return []
    
async def lock_user_to_channel(user_id: str, channel_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            await _write_channel_lock(conn, user_id, channel_id)
        return True
    return await _run(_body)

async def unlock_user(user_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("""
                    DELETE FROM permissao_canal
                    WHERE user_id = %s AND tipo = 'locked_user'
                """, (user_id,))
                return cur.rowcount > 0
    return await _run(_body)

async def transfer_user_to_channel(
    user_id: str, origem_channel_id: str, destino_channel_id: str
) -> tuple[bool, int]:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE registros_diarios SET channel_id = %s
                    WHERE channel_id = %s AND user_id = %s
                """, (destino_channel_id, origem_channel_id, user_id))
                transferred = cur.rowcount
                await cur.execute("""
                    UPDATE permissao_canal SET channel_id = %s
                    WHERE user_id = %s AND tipo = 'locked_user'
                """, (destino_channel_id, user_id))
        return True, transferred
    return await _run(_body)

async def get_user_locked_channel(user_id: str) -> str | None:
    async def _body(conn):
        return await _fetch_locked_channel(conn, user_id)
    try:
        return await _run(_body)
    except Exception:
        logger.exception("get_user_locked_channel failed")
        return None
    
async def delete_user_records(channel_id: str, user_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("""
                    DELETE FROM registros_diarios
                    WHERE channel_id = %s AND user_id = %s
                """, (channel_id, user_id))
        return True
    return await _run(_body)    

async def list_blocked_users() -> list:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT user_id, channel_id, locked_at
                FROM permissao_canal
                WHERE tipo = 'locked_user'
                ORDER BY locked_at DESC
            """)
            return await cur.fetchall()
    try:
        return await _run(_body)
    except Exception:
        logger.exception("list_blocked_users failed")
        return []


# =====================================
# PUBLIC API — SUPER ADMINS
# =====================================
async def is_super_admin(user_id: str) -> bool:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM super_admins WHERE user_id = %s", (user_id,))
            return await cur.fetchone() is not None
    try:
        return await _run(_body)
    except Exception:
        logger.exception("is_super_admin failed")
        return False

async def add_super_admin(user_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO super_admins (user_id) VALUES (%s)
                    ON CONFLICT DO NOTHING
                """, (user_id,))
        return True
    return await _run(_body)

async def remove_super_admin(user_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM super_admins WHERE user_id = %s", (user_id,))
                return cur.rowcount > 0
    return await _run(_body)

async def list_super_admins() -> list[str]:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM super_admins ORDER BY added_at")
            return [row[0] for row in await cur.fetchall()]
    try:
        return await _run(_body)
    except Exception:
        logger.exception("list_super_admins failed")
        return []


# =====================================
# PUBLIC API — Status Panel
# ===================================== 
async def set_status_message(channel_id: str, message_id: str) -> bool:
    async def _body(conn):
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO channel_status_config (channel_id, status_message_id)
                    VALUES (%s, %s)
                    ON CONFLICT (channel_id) DO UPDATE
                    SET status_message_id = %s, updated_at = CURRENT_TIMESTAMP
                """, (channel_id, message_id, message_id))
        return True
    return await _run(_body)

async def get_status_message(channel_id: str) -> str | None:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT status_message_id FROM channel_status_config
                WHERE channel_id = %s
            """, (channel_id,))
            row = await cur.fetchone()
        return row[0] if row else None
    try:
        return await _run(_body)
    except Exception:
        logger.exception("get_status_message failed")
        return None

async def list_channel_with_status() -> list:
    async def _body(conn):
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT channel_id, status_message_id
                FROM channel_status_config
                WHERE status_message_id IS NOT NULL
            """)
            return await cur.fetchall()
    try:
        return await _run(_body)
    except Exception:
        logger.exception("list_channel_with_status failed")
        return []


# =====================================
# GRACEFUL SHUTDOWN
# =====================================
async def close_pool() -> None:
    """
    Drain and close the pool. Called from bot's on_close event.

    wait=True (default) allows in-flight operations to finish
    before connections are closed, preventing mid-query disconnects.
    """
    global _pool
    async with _pool_lock:
        if _pool and not _pool.closed:
            logger.info("Draining and closing connection pool…")
            await _pool.close()
            logger.info("Connection pool closed")
        _pool = None