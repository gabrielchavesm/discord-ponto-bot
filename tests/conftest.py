"""
Shared pytest configuration and fixtures
"""
import os
import pytest
from contextlib import ExitStack
from unittest.mock import patch, AsyncMock


# =====================================
# CONFIGURE ENVIRONMENT BEFORE IMPORTS
# =====================================
def pytest_configure(config):
    """Configure pytest before any tests run"""
    # Set environment variables BEFORE any imports
    os.environ.update({
        'DISCORD_TOKEN': 'TEST_TOKEN_123456789',
        'GUILD_ID': '1458825338313244767',
        'POSTGRES_HOST': 'localhost',
        'POSTGRES_PORT': '5432',
        'POSTGRES_DB': 'test_db',
        'POSTGRES_USER': 'test_user',
        'POSTGRES_PASSWORD': 'test_pass',
        'DATABASE_URL': 'postgresql://test_user:test_pass@localhost:5432/test_db',
    })


# =====================================
# SHARED FIXTURES
# =====================================

@pytest.fixture
def sample_channel_id():
    """Sample channel ID for tests"""
    return "999888777666"


@pytest.fixture
def sample_user_id():
    """Sample user ID for tests"""
    return "111222333444"


@pytest.fixture
def mock_database():
    """
    Mock commonly used database functions for unit tests.
    Yields a dictionary of mock objects so tests can assert calls and arguments.
    """
    with ExitStack() as stack:
        mock_record = stack.enter_context(
            patch('database.record_action', new_callable=AsyncMock)
        )
        mock_record.return_value = (True, "Success")

        mock_status = stack.enter_context(
            patch('database.get_status_message', new_callable=AsyncMock)
        )
        mock_status.return_value = None

        mock_users = stack.enter_context(
            patch('database.status_atual_users', new_callable=AsyncMock)
        )
        mock_users.return_value = {}

        yield {
            'record_action': mock_record,
            'get_status_message': mock_status,
            'status_atual_users': mock_users
        }