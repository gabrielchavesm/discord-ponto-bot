"""
Comprehensive test suite for database.py

Covers:
- UTC utilities
- Connection pool management
- Error detection
- _run and retry logic
- Internal helpers (_fetch_daily_record, _persist_record, channel lock helpers)
- Record actions (ON/BREAK/FINISH) with all branches
- Status and reporting functions
- Permissions (admin, super_admin)
- User locking and transfer
- Status panel functions
- Graceful shutdown
- Database initialization
- Exception handlers for all public functions
"""

import pytest
import os
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock, call
from psycopg import OperationalError
from psycopg_pool import PoolClosed
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import database

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def mock_env_vars():
    env_vars = {
        'POSTGRES_HOST': 'localhost',
        'POSTGRES_PORT': '5432',
        'POSTGRES_DB': 'test_db',
        'POSTGRES_USER': 'test_user',
        'POSTGRES_PASSWORD': 'test_pass',
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars

@pytest.fixture
def mock_conn():
    """
    Mock AsyncConnection.

    Both conn.cursor() and pool.connection() must return their context manager
    synchronously (not as a coroutine).  We achieve this by assigning plain
    MagicMock instances to those two callables, while keeping the returned
    context manager itself as an AsyncMock so __aenter__/__aexit__ are awaitable.
    """
    conn = AsyncMock()

    # cursor context manager
    cursor = AsyncMock()
    cursor.__aenter__.return_value = cursor
    cursor.__aexit__.return_value = None
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.rowcount = 0

    # conn.cursor must be a plain MagicMock so conn.cursor() returns
    # the context manager object synchronously, not a coroutine.
    conn.cursor = MagicMock(return_value=cursor)

    # transaction context manager
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=None)
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_cm)
    return conn

@pytest.fixture
def mock_pool(mock_conn):
    """Mock AsyncConnectionPool.

    pool.connection() must return an async context manager synchronously
    (same MagicMock trick used for conn.cursor above).
    """
    pool = AsyncMock()
    conn_cm = AsyncMock()
    conn_cm.__aenter__.return_value = mock_conn
    conn_cm.__aexit__.return_value = None
    pool.connection = MagicMock(return_value=conn_cm)
    return pool

def _cursor(mock_conn):
    """Helper to grab the cursor mock from a mock_conn."""
    return mock_conn.cursor.return_value.__aenter__.return_value


# ----------------------------------------------------------------------
# UTC Utilities
# ----------------------------------------------------------------------
class TestUTCUtilities:
    def test_utcnow_returns_utc(self):
        assert database.utcnow().tzinfo == timezone.utc

    def test_ensure_utc_with_other_timezone(self):
        dt = datetime(2025, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))
        assert database.ensure_utc(dt) == dt

    def test_ensure_utc_none_returns_none(self):
        """ensure_utc(None) must return None."""
        assert database.ensure_utc(None) is None

    def test_ensure_utc_naive_adds_utc(self):
        """ensure_utc with naive datetime adds UTC tzinfo."""
        naive = datetime(2025, 1, 1, 12, 0)
        result = database.ensure_utc(naive)
        assert result.tzinfo == timezone.utc
        assert result == datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

    def test_format_timedelta_negative(self):
        assert database.format_timedelta(timedelta(hours=-1)) == "-1:00:00"

    def test_format_timedelta_none(self):
        """format_timedelta(None) returns '00:00:00'."""
        assert database.format_timedelta(None) == "00:00:00"

    def test_format_timedelta_zero(self):
        """format_timedelta(timedelta(0)) also takes the falsy branch."""
        assert database.format_timedelta(timedelta(0)) == "00:00:00"


# ----------------------------------------------------------------------
# Connection Pool Management
# ----------------------------------------------------------------------
class TestConnectionPool:
    def test_conninfo_with_all_vars(self, mock_env_vars):
        result = database._conninfo()
        for key in mock_env_vars:
            assert key.split('_', 1)[1].lower() in result

    def test_conninfo_missing_host(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="Missing required DB environment variables"):
                database._conninfo()

    def test_get_pool_fast_path(self):
        mp = AsyncMock()
        mp.closed = False
        database._pool = mp
        result = asyncio.run(database.get_pool())
        assert result is mp

    def test_get_pool_slow_path_creates_new(self, mock_env_vars):
        database._pool = None
        with patch('database._create_pool', new_callable=AsyncMock) as mc:
            mc.return_value = AsyncMock()
            result = asyncio.run(database.get_pool())
            mc.assert_called_once()
            assert database._pool is result

    def test_replace_pool(self, mock_env_vars):
        old = AsyncMock()
        old.closed = False
        database._pool = old
        with patch('database._create_pool', new_callable=AsyncMock) as mc:
            new = AsyncMock()
            mc.return_value = new
            asyncio.run(database._replace_pool())
            old.close.assert_awaited_once()
            assert database._pool is new

    def test_replace_pool_close_timeout(self, mock_env_vars):
        old = AsyncMock()
        old.closed = False
        old.close.side_effect = asyncio.TimeoutError
        database._pool = old
        with patch('database._create_pool', new_callable=AsyncMock) as mc:
            new = AsyncMock()
            mc.return_value = new
            asyncio.run(database._replace_pool())
            assert database._pool is new

    @pytest.mark.asyncio
    async def test_create_pool_instantiates_and_opens(self, mock_env_vars):
        """_create_pool must construct AsyncConnectionPool and call open(wait=True)."""
        mock_instance = AsyncMock()
        mock_instance.open = AsyncMock()

        with patch('database.AsyncConnectionPool', return_value=mock_instance) as MockCls:
            result = await database._create_pool()

        MockCls.assert_called_once()
        mock_instance.open.assert_awaited_once_with(wait=True)
        assert result is mock_instance


# ----------------------------------------------------------------------
# Database Initialization
# ----------------------------------------------------------------------
class TestInitDatabase:
    @pytest.mark.asyncio
    async def test_init_database_executes_schema(self, mock_conn, mock_pool):
        """
        When _run succeeds, _run_init is called with the real conn.
        Verify 7 CREATE TABLE/INDEX statements are executed and commit is called.
        """
        with patch('database.get_pool', return_value=mock_pool):
            await database.init_database()

        cur = _cursor(mock_conn)
        # 4 CREATE TABLE + 3 CREATE INDEX = 7 execute calls
        assert cur.execute.call_count == 7
        mock_conn.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_init_database_propagates_exception(self):
        """If _run raises, init_database logs and re-raises."""
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db init failed")):
            with pytest.raises(Exception, match="db init failed"):
                await database.init_database()


# ----------------------------------------------------------------------
# Error Detection
# ----------------------------------------------------------------------
class TestErrorDetection:
    def test_is_transient_error_connection_reset(self):
        assert database._is_transient_error(OperationalError("connection reset by peer")) is True

    def test_is_transient_error_could_not_connect(self):
        assert database._is_transient_error(OperationalError("could not connect to server")) is True

    def test_is_transient_error_server_closed(self):
        assert database._is_transient_error(OperationalError("server closed the connection unexpectedly")) is True

    def test_is_transient_error_terminating_connection(self):
        assert database._is_transient_error(OperationalError("terminating connection due to administrator command")) is True

    def test_is_transient_error_timeout(self):
        assert database._is_transient_error(OperationalError("timeout expired")) is True

    def test_is_not_transient_error_syntax(self):
        assert database._is_transient_error(OperationalError("syntax error at or near")) is False

    def test_is_not_transient_error_other(self):
        assert database._is_transient_error(OperationalError("duplicate key value violates unique constraint")) is False


# ----------------------------------------------------------------------
# _run and Retry Logic
# ----------------------------------------------------------------------
class TestRun:
    @pytest.mark.asyncio
    async def test_run_success_simple(self, mock_pool):
        """_run successfully executes operation"""
        with patch('database.get_pool', return_value=mock_pool):
            async def op(conn):
                return "ok"
            assert await database._run(op) == "ok"

    @pytest.mark.asyncio
    async def test_run_retry_transient_error(self, mock_pool):
        with patch('database.get_pool', return_value=mock_pool) as mgp:
            async def op(conn):
                if not hasattr(op, "called"):
                    op.called = True
                    raise OperationalError("connection reset")
                return "ok"
            with patch('database._is_transient_error', return_value=True), \
                 patch('database._replace_pool', new_callable=AsyncMock) as mr, \
                 patch('asyncio.sleep', new_callable=AsyncMock):
                result = await database._run(op, retries=2)
                assert result == "ok"
                assert mr.call_count == 1
                assert mgp.call_count == 2

    @pytest.mark.asyncio
    async def test_run_non_transient_error(self, mock_pool):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._is_transient_error', return_value=False):
            async def op(conn):
                raise OperationalError("syntax error")
            with pytest.raises(OperationalError):
                await database._run(op)

    @pytest.mark.asyncio
    async def test_run_pool_closed(self, mock_pool):
        """PoolClosed is a subclass of OperationalError in psycopg_pool, so it is
        caught by the OperationalError handler.  Since 'pool closed' is not a
        transient keyword, _is_transient_error returns False and the exception is
        re-raised immediately as OperationalError (not RuntimeError).
        The test verifies the exception propagates without infinite retries."""
        mock_pool.connection.side_effect = PoolClosed("pool closed")
        with patch('database.get_pool', return_value=mock_pool):
            async def op(conn):
                return "ok"
            with pytest.raises((OperationalError, RuntimeError)):
                await database._run(op)

    @pytest.mark.asyncio
    async def test_run_cancelled(self, mock_pool):
        with patch('database.get_pool', return_value=mock_pool):
            async def op(conn):
                raise asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await database._run(op)

    @pytest.mark.asyncio
    async def test_run_all_retries_exhausted_raises_last_exc(self, mock_pool):
        """
        When every attempt raises a transient OperationalError and retries run out,
        _run breaks out of the loop and raises last_exc.
        """
        attempts = []

        async def always_fail(conn):
            attempts.append(1)
            raise OperationalError("connection reset")

        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._is_transient_error', return_value=True), \
             patch('database._replace_pool', new_callable=AsyncMock), \
             patch('asyncio.sleep', new_callable=AsyncMock):
            with pytest.raises(OperationalError, match="connection reset"):
                await database._run(always_fail, retries=2)

        assert len(attempts) == 2  # tried exactly retries times


# ----------------------------------------------------------------------
# Internal Helpers (_fetch_daily_record, _persist_record, channel lock helpers)
# ----------------------------------------------------------------------
class TestInternalHelpers:
    @pytest.mark.asyncio
    async def test_fetch_daily_record_raises_when_idle(self):
        """Calling _fetch_daily_record outside a transaction raises RuntimeError."""
        from psycopg.pq import TransactionStatus
        conn = MagicMock()
        conn.info.transaction_status = TransactionStatus.IDLE
        with pytest.raises(RuntimeError, match="must be called inside a transaction"):
            await database._fetch_daily_record(conn, "123", "456", datetime.now().date())

    @pytest.mark.asyncio
    async def test_fetch_daily_record_returns_dict(self):
        """Normal execution returns a correctly shaped dict."""
        from psycopg.pq import TransactionStatus
        from datetime import date as _date

        today = _date(2025, 1, 6)
        first_on_dt = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        row = (
            42,       # id
            "123",    # channel_id
            "456",    # user_id
            today,    # data
            first_on_dt,  # first_on
            None,     # finish
            None,     # work_sum (None → timedelta(0))
            timedelta(minutes=15),  # break_sum
            True,     # is_on
            False,    # is_break
            False,    # is_finished
            True,     # mobile_first_on
            first_on_dt,  # last_transition
            "ON",     # last_action
        )

        cursor = AsyncMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=row)

        conn = MagicMock()
        conn.info.transaction_status = TransactionStatus.INTRANS
        conn.cursor = MagicMock(return_value=cursor)

        result = await database._fetch_daily_record(conn, "123", "456", today)

        assert result["id"] == 42
        assert result["channel_id"] == "123"
        assert result["user_id"] == "456"
        assert result["first_on"] == first_on_dt
        assert result["work_sum"] == timedelta(0) # None coerced to timedelta(0)
        assert result["break_sum"] == timedelta(minutes=15)
        assert result["is_on"] is True
        assert result["mobile_first_on"] is True
        assert result["last_action"] == "ON"
        cursor.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persist_record_calls_execute(self):
        """_persist_record runs the UPDATE statement."""
        cursor = AsyncMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock()

        conn = MagicMock()
        conn.cursor = MagicMock(return_value=cursor)

        r = {
            "first_on": None, "finish": None,
            "work_sum": timedelta(hours=2), "break_sum": timedelta(minutes=10),
            "is_on": False, "is_break": False, "is_finished": True,
            "mobile_first_on": False, "last_transition": None, "last_action": "FINISH",
            "id": 99,
        }
        await database._persist_record(conn, r)
        cursor.execute.assert_awaited_once()
        # verify the id is passed as last positional arg
        args = cursor.execute.call_args[0]
        assert args[1][-1] == 99

    @pytest.mark.asyncio
    async def test_fetch_locked_channel_returns_channel_id(self, mock_conn):
        """Returns the channel_id when a lock row is found."""
        _cursor(mock_conn).fetchone.return_value = ("999",)
        result = await database._fetch_locked_channel(mock_conn, "456")
        assert result == "999"
        _cursor(mock_conn).execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_locked_channel_returns_none_when_not_found(self, mock_conn):
        """Returns None when no lock row exists."""
        _cursor(mock_conn).fetchone.return_value = None
        result = await database._fetch_locked_channel(mock_conn, "456")
        assert result is None

    @pytest.mark.asyncio
    async def test_write_channel_lock_executes_insert(self, mock_conn):
        """Executes the INSERT ... ON CONFLICT DO NOTHING."""
        await database._write_channel_lock(mock_conn, "456", "123")
        _cursor(mock_conn).execute.assert_awaited_once()
        sql = _cursor(mock_conn).execute.call_args[0][0]
        assert "INSERT" in sql
        assert "ON CONFLICT" in sql


# ----------------------------------------------------------------------
# Record Action Tests
# ----------------------------------------------------------------------
class TestRecordAction:
    @pytest.fixture
    def record(self):
        """A bare daily record with no state (no first_on yet)."""
        return {
            "id": 1, "channel_id": "123", "user_id": "456",
            "data": database.utcnow().date(),
            "first_on": None, "finish": None,
            "work_sum": timedelta(0), "break_sum": timedelta(0),
            "is_on": False, "is_break": False, "is_finished": False,
            "mobile_first_on": False, "last_transition": None, "last_action": None,
        }

    @pytest.fixture
    def record_working(self, record):
        """A record where the user already clocked in and is actively working."""
        record.update(
            first_on=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
            is_on=True,
            last_transition=datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
            last_action="ON",
        )
        return record

    @pytest.fixture
    def record_on_break(self, record):
        """A record where the user clocked in and is currently on break."""
        record.update(
            first_on=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
            is_on=False, is_break=True,
            last_transition=datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
            last_action="BREAK",
        )
        return record

    def _base(self):
        """Return a base record for testing missing branches."""
        return {
            "id": 1, "channel_id": "123", "user_id": "456",
            "data": datetime(2025, 1, 1, tzinfo=timezone.utc).date(),
            "first_on": datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
            "finish": None,
            "work_sum": timedelta(0), "break_sum": timedelta(0),
            "is_on": False, "is_break": False, "is_finished": False,
            "mobile_first_on": False,
            "last_transition": datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
            "last_action": None,
        }

    @pytest.mark.asyncio
    async def test_record_action_on_first(self, mock_conn, mock_pool, record):
        """ON with no first_on -> registers first ON"""
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record), \
             patch('database._persist_record', new_callable=AsyncMock) as mp, \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):

            success, msg = await database.record_action("123", "456", "ON", "desktop")

            assert success is True
            assert "First ON" in msg
            updated = mp.call_args[0][1]
            assert updated["is_on"] is True
            assert updated["last_action"] == "ON"
            assert updated["mobile_first_on"] is False

    @pytest.mark.asyncio
    async def test_record_action_on_from_break(self, mock_conn, mock_pool, record_on_break):
        """ON when is_break=True, first_on set -> return from break"""
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record_on_break), \
             patch('database._persist_record', new_callable=AsyncMock) as mp, \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):

            success, msg = await database.record_action("123", "456", "ON", "mobile")

            assert success is True
            assert "Break return" in msg
            updated = mp.call_args[0][1]
            assert updated["break_sum"] == timedelta(hours=1)
            assert updated["is_on"] is True
            assert updated["is_break"] is False

    @pytest.mark.asyncio
    async def test_record_action_on_already_working(self, mock_conn, mock_pool, record_working):
        """ON when already working -> failure"""
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record_working), \
             patch('database._persist_record', new_callable=AsyncMock) as mp:

            success, msg = await database.record_action("123", "456", "ON")

            assert success is False
            assert "already working" in msg
            mp.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_action_on_finished(self, mock_conn, mock_pool, record):
        record["is_finished"] = True
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record):

            success, msg = await database.record_action("123", "456", "ON")
            assert success is False
            assert "Day already finished" in msg

    @pytest.mark.asyncio
    async def test_record_action_break_success(self, mock_conn, mock_pool, record_working):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record_working), \
             patch('database._persist_record', new_callable=AsyncMock) as mp, \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):

            success, msg = await database.record_action("123", "456", "BREAK")

            assert success is True
            assert "Break started" in msg
            updated = mp.call_args[0][1]
            assert updated["work_sum"] == timedelta(hours=1)
            assert updated["is_on"] is False
            assert updated["is_break"] is True

    @pytest.mark.asyncio
    async def test_record_action_break_not_working(self, mock_conn, mock_pool, record):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record):

            success, msg = await database.record_action("123", "456", "BREAK")
            assert success is False
            assert "need to be working" in msg

    @pytest.mark.asyncio
    async def test_record_action_break_auto_finish(self, mock_conn, mock_pool, record_working):
        record_working["work_sum"] = timedelta(hours=6)
        original_max = database.MAX_WORK_HOURS
        database.MAX_WORK_HOURS = timedelta(hours=6, minutes=30)

        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record_working), \
             patch('database._persist_record', new_callable=AsyncMock) as mp, \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):

            success, msg = await database.record_action("123", "456", "BREAK")
            assert success is True
            assert "automatically finished" in msg
            updated = mp.call_args[0][1]
            assert updated["is_finished"] is True
            assert updated["work_sum"] == database.MAX_WORK_HOURS

        database.MAX_WORK_HOURS = original_max

    @pytest.mark.asyncio
    async def test_record_action_finish_success(self, mock_conn, mock_pool, record_working):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record_working), \
             patch('database._persist_record', new_callable=AsyncMock) as mp, \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):

            success, msg = await database.record_action("123", "456", "FINISH")
            assert success is True
            assert "Day finished" in msg
            updated = mp.call_args[0][1]
            assert updated["work_sum"] == timedelta(hours=1)
            assert updated["is_finished"] is True

    @pytest.mark.asyncio
    async def test_record_action_finish_not_working(self, mock_conn, mock_pool, record):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record):

            success, msg = await database.record_action("123", "456", "FINISH")
            assert success is False
            assert "need to be working" in msg

    @pytest.mark.asyncio
    async def test_record_action_channel_lock_mismatch(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value="999"):

            success, msg = await database.record_action("123", "456", "ON")
            assert success is False
            assert "already registered at <#999>" in msg

    @pytest.mark.asyncio
    async def test_record_action_database_error(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', side_effect=Exception("DB down")):

            success, msg = await database.record_action("123", "456", "ON")
            assert success is False
            assert "Database error: DB down" in msg

    @pytest.mark.asyncio
    async def test_record_action_on_invalid_state(self, mock_conn, mock_pool):
        """
        ON when first_on is set, is_finished=False, is_break=False, is_on=False.
        """
        record = self._base()  # is_on=False, is_break=False, is_finished=False, first_on SET

        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record), \
             patch('database._persist_record', new_callable=AsyncMock), \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):
            success, msg = await database.record_action("123", "456", "ON")

        assert success is False
        assert "Invalid state" in msg

    @pytest.mark.asyncio
    async def test_record_action_break_when_already_finished(self, mock_conn, mock_pool):
        """
        BREAK when is_on=True, is_break=False, but is_finished=True.
        Passes the 'not is_on or is_break' guard, hits is_finished
        """
        record = self._base()
        record.update(is_on=True, is_break=False, is_finished=True)

        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record), \
             patch('database._persist_record', new_callable=AsyncMock), \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):
            success, msg = await database.record_action("123", "456", "BREAK")

        assert success is False
        assert "Day already finished" in msg

    @pytest.mark.asyncio
    async def test_record_action_finish_when_already_finished(self, mock_conn, mock_pool):
        """
        FINISH when is_on=True, is_break=False, but is_finished=True.
        Passes the 'not is_on or is_break' guard, hits is_finished
        """
        record = self._base()
        record.update(is_on=True, is_break=False, is_finished=True)

        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record), \
             patch('database._persist_record', new_callable=AsyncMock), \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):
            success, msg = await database.record_action("123", "456", "FINISH")

        assert success is False
        assert "Day already finished" in msg

    @pytest.mark.asyncio
    async def test_record_action_invalid_action_string(self, mock_conn, mock_pool):
        """
        Action string other than ON/BREAK/FINISH falls to: 'Invalid action.'
        """
        record = self._base()
        record.update(is_on=True)

        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', new_callable=AsyncMock, return_value=None), \
             patch('database._write_channel_lock', new_callable=AsyncMock), \
             patch('database._fetch_daily_record', new_callable=AsyncMock, return_value=record), \
             patch('database._persist_record', new_callable=AsyncMock), \
             patch('database.utcnow', return_value=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)):
            success, msg = await database.record_action("123", "456", "LUNCH")

        assert success is False
        assert "Invalid action" in msg


# ----------------------------------------------------------------------
# Status and Reporting Functions
# ----------------------------------------------------------------------
class TestStatusReports:
    @pytest.mark.asyncio
    async def test_status_atual_users(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [
            ("111", None, None, False, False, False, None, None),
            ("222", None, None, True,  False, False, datetime(2025,1,1,10,0,tzinfo=timezone.utc), None),
            ("333", None, None, False, True,  False, datetime(2025,1,1,11,0,tzinfo=timezone.utc), None),
            ("444", None, datetime(2025,1,1,12,0,tzinfo=timezone.utc), False, False, True, None, None),
        ]
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database.utcnow', return_value=datetime(2025,1,1,12,0,tzinfo=timezone.utc)):
            result = await database.status_atual_users("123")

        assert result == {
            "111": {"estado": "NAO_INICIADO", "data": "2025-01-01", "hora": None},
            "222": {"estado": "TRABALHANDO",  "data": "2025-01-01", "hora": 1735725600},
            "333": {"estado": "PAUSA",        "data": "2025-01-01", "hora": 1735729200},
            "444": {"estado": "AUSENTE",      "data": "2025-01-01", "hora": 1735732800},
        }

    @pytest.mark.asyncio
    async def test_period_report(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [
            (datetime(2025,1,1).date(), datetime(2025,1,1,9,0), datetime(2025,1,1,18,0), timedelta(9), timedelta(1), True),
        ]
        with patch('database.get_pool', return_value=mock_pool):
            result = await database.period_report("123", "456", datetime(2025,1,1).date(), datetime(2025,1,5).date())
        assert len(result) == 1
        assert result[0][0] == datetime(2025,1,1).date()

    @pytest.mark.asyncio
    async def test_mobile_records(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [(datetime(2025,1,1).date(), datetime(2025,1,1,9,0))]
        with patch('database.get_pool', return_value=mock_pool):
            result = await database.mobile_records("123", "456", datetime(2025,1,1).date(), datetime(2025,1,5).date())
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_first_last_record(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchone.return_value = (datetime(2025,1,1).date(), datetime(2025,1,5).date())
        with patch('database.get_pool', return_value=mock_pool):
            first, last = await database.first_last_record("123", "456")
        assert first == datetime(2025,1,1).date()
        assert last == datetime(2025,1,5).date()

    @pytest.mark.asyncio
    async def test_export_csv_data(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [
            ("123", "456", datetime(2025,1,1).date(), datetime(2025,1,1,9,0), datetime(2025,1,1,18,0),
             timedelta(9), timedelta(1), True, False)
        ]
        with patch('database.get_pool', return_value=mock_pool):
            result = await database.export_csv_data("123")
        assert len(result) == 1


# ----------------------------------------------------------------------
# Permission Functions (Admin)
# ----------------------------------------------------------------------
class TestAdminPermissions:
    @pytest.mark.asyncio
    async def test_is_admin_true(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchone.return_value = (1,)
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.is_admin("123", "456") is True

    @pytest.mark.asyncio
    async def test_is_admin_false(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchone.return_value = None
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.is_admin("123", "456") is False

    @pytest.mark.asyncio
    async def test_config_admins_adds(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.config_admins_adds("123", "456") is True
        _cursor(mock_conn).execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_admins_removes_success(self, mock_conn, mock_pool):
        _cursor(mock_conn).rowcount = 1
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.config_admins_removes("123", "456") is True

    @pytest.mark.asyncio
    async def test_config_admins_removes_not_found(self, mock_conn, mock_pool):
        _cursor(mock_conn).rowcount = 0
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.config_admins_removes("123", "456") is False

    @pytest.mark.asyncio
    async def test_config_admins_list(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [("456",), ("789",)]
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.config_admins_list("123") == ["456", "789"]


# ----------------------------------------------------------------------
# User Locking and Transfer
# ----------------------------------------------------------------------
class TestUserLocks:
    @pytest.mark.asyncio
    async def test_lock_user_to_channel(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._write_channel_lock', new_callable=AsyncMock) as mw:
            assert await database.lock_user_to_channel("456", "123") is True
        mw.assert_called_once_with(mock_conn, "456", "123")

    @pytest.mark.asyncio
    async def test_unlock_user_success(self, mock_conn, mock_pool):
        _cursor(mock_conn).rowcount = 1
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.unlock_user("456") is True

    @pytest.mark.asyncio
    async def test_unlock_user_not_found(self, mock_conn, mock_pool):
        _cursor(mock_conn).rowcount = 0
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.unlock_user("456") is False

    @pytest.mark.asyncio
    async def test_get_user_locked_channel(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool), \
             patch('database._fetch_locked_channel', return_value="123") as mf:
            assert await database.get_user_locked_channel("456") == "123"
        mf.assert_called_once_with(mock_conn, "456")

    @pytest.mark.asyncio
    async def test_transfer_user_to_channel(self, mock_conn, mock_pool):
        _cursor(mock_conn).rowcount = 5
        with patch('database.get_pool', return_value=mock_pool):
            success, count = await database.transfer_user_to_channel("456", "123", "789")
        assert success is True
        assert count == 5
        assert _cursor(mock_conn).execute.call_count == 2

    @pytest.mark.asyncio
    async def test_delete_user_records(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.delete_user_records("123", "456") is True
        _cursor(mock_conn).execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_blocked_users(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [("456", "123", datetime.now())]
        with patch('database.get_pool', return_value=mock_pool):
            result = await database.list_blocked_users()
        assert len(result) == 1
        assert result[0][0] == "456"


# ----------------------------------------------------------------------
# Super Admin Functions
# ----------------------------------------------------------------------
class TestSuperAdmin:
    @pytest.mark.asyncio
    async def test_is_super_admin_true(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchone.return_value = (1,)
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.is_super_admin("456") is True

    @pytest.mark.asyncio
    async def test_is_super_admin_false(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchone.return_value = None
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.is_super_admin("456") is False

    @pytest.mark.asyncio
    async def test_add_super_admin(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.add_super_admin("456") is True

    @pytest.mark.asyncio
    async def test_remove_super_admin_success(self, mock_conn, mock_pool):
        _cursor(mock_conn).rowcount = 1
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.remove_super_admin("456") is True

    @pytest.mark.asyncio
    async def test_remove_super_admin_not_found(self, mock_conn, mock_pool):
        _cursor(mock_conn).rowcount = 0
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.remove_super_admin("456") is False

    @pytest.mark.asyncio
    async def test_list_super_admins(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [("456",), ("789",)]
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.list_super_admins() == ["456", "789"]


# ----------------------------------------------------------------------
# Status Panel Functions
# ----------------------------------------------------------------------
class TestStatusPanel:
    @pytest.mark.asyncio
    async def test_set_status_message(self, mock_conn, mock_pool):
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.set_status_message("123", "456") is True
        _cursor(mock_conn).execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_status_message(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchone.return_value = ("456",)
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.get_status_message("123") == "456"

    @pytest.mark.asyncio
    async def test_get_status_message_none(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchone.return_value = None
        with patch('database.get_pool', return_value=mock_pool):
            assert await database.get_status_message("123") is None

    @pytest.mark.asyncio
    async def test_list_channel_with_status(self, mock_conn, mock_pool):
        _cursor(mock_conn).fetchall.return_value = [("123", "456"), ("789", "012")]
        with patch('database.get_pool', return_value=mock_pool):
            result = await database.list_channel_with_status()
        assert result == [("123", "456"), ("789", "012")]


# ----------------------------------------------------------------------
# Exception Handlers for Public Functions
# ----------------------------------------------------------------------
class TestExceptionHandlers:
    """
    Every public function that wraps _run in try/except Exception should:
    - log the error
    - return an appropriate safe default value
    """

    @pytest.mark.asyncio
    async def test_status_atual_users_exception_returns_empty_dict(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.status_atual_users("123")
        assert result == {}

    @pytest.mark.asyncio
    async def test_period_report_exception_returns_empty_list(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.period_report("123", "456", None, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_mobile_records_exception_returns_empty_list(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.mobile_records("123", "456", None, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_first_last_record_exception_returns_none_tuple(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            first, last = await database.first_last_record("123", "456")
        assert first is None
        assert last is None

    @pytest.mark.asyncio
    async def test_export_csv_data_exception_returns_empty_list(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.export_csv_data("123")
        assert result == []

    @pytest.mark.asyncio
    async def test_is_admin_exception_returns_false(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.is_admin("123", "456")
        assert result is False

    @pytest.mark.asyncio
    async def test_config_admins_list_exception_returns_empty_list(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.config_admins_list("123")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_user_locked_channel_exception_returns_none(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.get_user_locked_channel("456")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_blocked_users_exception_returns_empty_list(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.list_blocked_users()
        assert result == []

    @pytest.mark.asyncio
    async def test_is_super_admin_exception_returns_false(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.is_super_admin("456")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_super_admins_exception_returns_empty_list(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.list_super_admins()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_status_message_exception_returns_none(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.get_status_message("123")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_channel_with_status_exception_returns_empty_list(self):
        with patch('database._run', new_callable=AsyncMock, side_effect=Exception("db error")):
            result = await database.list_channel_with_status()
        assert result == []


# ----------------------------------------------------------------------
# Graceful Shutdown
# ----------------------------------------------------------------------
class TestShutdown:
    @pytest.mark.asyncio
    async def test_close_pool(self):
        mp = AsyncMock()
        mp.closed = False
        database._pool = mp
        database._pool_lock = asyncio.Lock()
        await database.close_pool()
        mp.close.assert_awaited_once()
        assert database._pool is None

    @pytest.mark.asyncio
    async def test_close_pool_already_closed(self):
        """Pool is already closed -> close() not called, _pool is reset to None."""
        mp = AsyncMock()
        mp.closed = True
        database._pool = mp
        database._pool_lock = asyncio.Lock()
        await database.close_pool()
        mp.close.assert_not_called()
        assert database._pool is None

    @pytest.mark.asyncio
    async def test_close_pool_no_pool(self):
        database._pool = None
        database._pool_lock = asyncio.Lock()
        await database.close_pool()  # no error
