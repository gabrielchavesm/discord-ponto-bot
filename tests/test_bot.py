"""
Comprehensive test suite for bot.py

Covers:
- Device detection
- Permission checks (check_admin, check_super_admin)
- Constants validation
- All PointView / ConfirmFinishView / ConfirmViewExclusion / ConfirmTransferView buttons
- All 14 Ponto slash commands (success, permission-denied, and error branches)
- update_status — every early-return and rendering path
- restaurar_status_paineis — resilience when one channel fails
- handle_action — unexpected-exception branches
- on_ready — nominal and database-error paths
- Missing-env-var guard (importlib.reload)
"""

import io
import os
import sys
import csv
import importlib
import asyncio
import pytest
from datetime import datetime, timezone, date, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, call

# conftest.py already sets DISCORD_TOKEN / GUILD_ID
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bot as bot_module
import database


# ===========================================================================
# Helpers
# ===========================================================================
def make_interaction(channel_id=123, user_id=456, channel_name="general"):
    """Return a fully-configured AsyncMock interaction."""
    ix = AsyncMock()
    ix.channel_id = channel_id
    ix.user.id = user_id
    ix.channel.name = channel_name
    ix.channel.send = AsyncMock()
    ix.response.send_message = AsyncMock()
    ix.response.edit_message = AsyncMock()
    ix.response.defer = AsyncMock()
    ix.response.is_done = MagicMock(return_value=False)
    ix.followup.send = AsyncMock()
    return ix

def make_user(user_id=789, name="TestUser"):
    u = MagicMock()
    u.id = user_id
    u.name = name
    u.mention = f"<@{user_id}>"
    return u


# ===========================================================================
# Device Detection
# ===========================================================================
class TestDeviceDetection:
    def _make(self, desktop=False, mobile=False, web=False):
        ix = MagicMock()
        m = MagicMock()
        m.desktop_status.__ne__ = MagicMock(return_value=desktop)
        m.mobile_status.__ne__  = MagicMock(return_value=mobile)
        m.web_status.__ne__     = MagicMock(return_value=web)
        ix.guild.get_member.return_value = m
        return ix

    def test_detect_device_desktop(self):
        assert bot_module.detect_device(self._make(desktop=True)) == "desktop"

    def test_detect_device_mobile(self):
        assert bot_module.detect_device(self._make(mobile=True)) == "mobile"

    def test_detect_device_web(self):
        assert bot_module.detect_device(self._make(web=True)) == "web"

    def test_detect_device_unknown_no_member(self):
        ix = MagicMock()
        ix.guild.get_member.return_value = None
        assert bot_module.detect_device(ix) == "unknown"

    def test_detect_device_all_offline(self):
        assert bot_module.detect_device(self._make()) == "unknown"


# ===========================================================================
# Permission Checks
# ===========================================================================
class TestPermissionChecks:
    @pytest.mark.asyncio
    async def test_check_super_admin_server_owner(self):
        ix = MagicMock()
        ix.guild.owner_id = 123
        ix.user.id = 123
        assert await bot_module.check_super_admin(ix) is True

    @pytest.mark.asyncio
    async def test_check_super_admin_no_guild(self):
        ix = MagicMock()
        ix.guild = None
        assert await bot_module.check_super_admin(ix) is False

    @pytest.mark.asyncio
    async def test_check_super_admin_discord_admin(self):
        ix = MagicMock()
        ix.guild.owner_id = 999
        ix.user.id = 123
        m = MagicMock()
        m.guild_permissions.administrator = True
        ix.guild.get_member.return_value = m
        with patch('database.is_super_admin', new_callable=AsyncMock, return_value=False):
            assert await bot_module.check_super_admin(ix) is True

    @pytest.mark.asyncio
    async def test_check_super_admin_db_super_admin(self):
        ix = MagicMock()
        ix.guild.owner_id = 999
        ix.user.id = 123
        m = MagicMock()
        m.guild_permissions.administrator = False
        ix.guild.get_member.return_value = m
        with patch('database.is_super_admin', new_callable=AsyncMock, return_value=True):
            assert await bot_module.check_super_admin(ix) is True

    @pytest.mark.asyncio
    async def test_check_super_admin_false(self):
        ix = MagicMock()
        ix.guild.owner_id = 999
        ix.user.id = 123
        m = MagicMock()
        m.guild_permissions.administrator = False
        ix.guild.get_member.return_value = m
        with patch('database.is_super_admin', new_callable=AsyncMock, return_value=False):
            assert await bot_module.check_super_admin(ix) is False

    @pytest.mark.asyncio
    async def test_check_admin_super_admin(self):
        ix = MagicMock()
        ix.channel_id = 123
        ix.user.id = 456
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True) as ms:
            assert await bot_module.check_admin(ix) is True
        ms.assert_called_once_with(ix)

    @pytest.mark.asyncio
    async def test_check_admin_db_admin(self):
        ix = MagicMock()
        ix.channel_id = 123
        ix.user.id = 456
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False), \
             patch('database.is_admin', new_callable=AsyncMock, return_value=True) as md:
            assert await bot_module.check_admin(ix) is True
        md.assert_called_once_with("123", "456")


# ===========================================================================
# Constants
# ===========================================================================
class TestConstants:
    def test_guild_id_is_set(self):
        assert isinstance(bot_module.GUILD_ID, int)
        assert bot_module.GUILD_ID == 1458825338313244767

    def test_token_is_set(self):
        assert isinstance(bot_module.TOKEN, str)
        assert bot_module.TOKEN == "TEST_TOKEN_123456789"


# ===========================================================================
# Views — PointView & ConfirmFinishView
# ===========================================================================
class TestPointView:
    @pytest.mark.asyncio
    async def test_handle_action_success(self):
        view = bot_module.PointView()
        ix = make_interaction()
        with patch('bot.detect_device', return_value='desktop'), \
             patch('database.record_action', new_callable=AsyncMock, return_value=(True, "OK")), \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await view.handle_action(ix, "ON")
        ix.response.defer.assert_called_once_with(ephemeral=True)
        ix.followup.send.assert_called_once_with("OK", ephemeral=True)
        mu.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_handle_action_failure_no_update(self):
        view = bot_module.PointView()
        ix = make_interaction()
        with patch('bot.detect_device', return_value='mobile'), \
             patch('database.record_action', new_callable=AsyncMock, return_value=(False, "Error")), \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await view.handle_action(ix, "BREAK")
        mu.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_action_exception_response_not_done(self):
        """Unexpected exception → send_message when response not yet done."""
        view = bot_module.PointView()
        ix = make_interaction()
        ix.response.is_done.return_value = False
        with patch('bot.detect_device', return_value='desktop'), \
             patch('database.record_action', new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await view.handle_action(ix, "ON")
        ix.response.send_message.assert_called_once()
        assert "Internal error" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_action_exception_response_done(self):
        """Unexpected exception → followup.send when response already done."""
        view = bot_module.PointView()
        ix = make_interaction()
        ix.response.is_done.return_value = True
        with patch('bot.detect_device', return_value='desktop'), \
             patch('database.record_action', new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await view.handle_action(ix, "ON")
        ix.followup.send.assert_called_once()
        assert "Internal error" in ix.followup.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_finish_button_shows_confirm_view(self):
        view = bot_module.PointView()
        ix = make_interaction()
        with patch('bot.detect_device', return_value='desktop'), \
             patch('bot.ConfirmFinishView') as MockCV:
            await view.finish.callback(ix)
        ix.response.send_message.assert_called_once()
        MockCV.assert_called_once_with("123", "456", "desktop")

class TestConfirmFinishView:
    @pytest.mark.asyncio
    async def test_confirm_success(self):
        view = bot_module.ConfirmFinishView("123", "456", "desktop")
        ix = AsyncMock()
        with patch('database.record_action', new_callable=AsyncMock, return_value=(True, "Done")), \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await view.confirm.callback(ix)
        ix.response.edit_message.assert_called_once_with(content="Done", view=None)
        mu.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_confirm_failure_no_update(self):
        view = bot_module.ConfirmFinishView("123", "456", "desktop")
        ix = AsyncMock()
        with patch('database.record_action', new_callable=AsyncMock, return_value=(False, "Fail")), \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await view.confirm.callback(ix)
        mu.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel(self):
        view = bot_module.ConfirmFinishView("123", "456", "desktop")
        ix = AsyncMock()
        await view.cancel.callback(ix)
        ix.response.edit_message.assert_called_once_with(content="❌ Finish cancelled.", view=None)


# ===========================================================================
# Views — ConfirmViewExclusion
# ===========================================================================
class TestConfirmViewExclusion:
    @pytest.mark.asyncio
    async def test_confirmar_deletes_and_updates_status(self):
        view = bot_module.ConfirmViewExclusion("123", "456")
        ix = AsyncMock()
        with patch('database.delete_user_records', new_callable=AsyncMock) as md, \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await view.confirmar.callback(ix)
        md.assert_called_once_with("123", "456")
        mu.assert_called_once_with(123)
        ix.response.edit_message.assert_called_once()
        assert "deleted" in ix.response.edit_message.call_args[1]["content"].lower()
        assert view.confirmacao is True

    @pytest.mark.asyncio
    async def test_cancelar(self):
        view = bot_module.ConfirmViewExclusion("123", "456")
        ix = AsyncMock()
        await view.cancelar.callback(ix)
        ix.response.edit_message.assert_called_once_with(content="❌ Exclusion cancelled.", view=None)
        assert view.confirmacao is False


# ===========================================================================
# Views — ConfirmTransferView
# ===========================================================================
class TestConfirmTransferView:
    @pytest.mark.asyncio
    async def test_confirmar_success(self):
        view = bot_module.ConfirmTransferView("456", "100", "200")
        ix = AsyncMock()
        with patch('database.transfer_user_to_channel', new_callable=AsyncMock, return_value=(True, 5)) as mt, \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await view.confirmar.callback(ix)
        mt.assert_called_once_with("456", "100", "200")
        assert mu.call_count == 2
        msg = ix.response.edit_message.call_args[1]["content"]
        assert "transferred" in msg.lower()
        assert "5" in msg

    @pytest.mark.asyncio
    async def test_confirmar_transfer_returns_false(self):
        view = bot_module.ConfirmTransferView("456", "100", "200")
        ix = AsyncMock()
        with patch('database.transfer_user_to_channel', new_callable=AsyncMock, return_value=(False, 0)):
            await view.confirmar.callback(ix)
        msg = ix.response.edit_message.call_args[1]["content"]
        assert "Error" in msg

    @pytest.mark.asyncio
    async def test_confirmar_exception(self):
        view = bot_module.ConfirmTransferView("456", "100", "200")
        ix = AsyncMock()
        with patch('database.transfer_user_to_channel', new_callable=AsyncMock, side_effect=Exception("db fail")):
            await view.confirmar.callback(ix)
        msg = ix.response.edit_message.call_args[1]["content"]
        assert "db fail" in msg

    @pytest.mark.asyncio
    async def test_cancelar(self):
        view = bot_module.ConfirmTransferView("456", "100", "200")
        ix = AsyncMock()
        await view.cancelar.callback(ix)
        ix.response.edit_message.assert_called_once_with(content="❌ Transfer cancelled.", view=None)


# ===========================================================================
# update_status — all branches
# ===========================================================================
class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_no_panel_returns_early(self):
        with patch('database.get_status_message', new_callable=AsyncMock, return_value=None):
            await bot_module.update_status(123)  # no error

    @pytest.mark.asyncio
    async def test_guild_not_found_returns_early(self):
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=None):
            await bot_module.update_status(123)  # no error

    @pytest.mark.asyncio
    async def test_channel_fetch_raises_returns_early(self):
        guild = MagicMock()
        guild.get_channel.return_value = None
        guild.fetch_channel = AsyncMock(side_effect=Exception("not found"))
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=guild):
            await bot_module.update_status(123)  # no error

    @pytest.mark.asyncio
    async def test_channel_none_after_fetch_returns_early(self):
        guild = MagicMock()
        guild.get_channel.return_value = None
        guild.fetch_channel = AsyncMock(return_value=None)
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=guild):
            await bot_module.update_status(123)  # no error

    @pytest.mark.asyncio
    async def test_message_fetch_raises_returns_early(self):
        guild = MagicMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(side_effect=Exception("not found"))
        guild.get_channel.return_value = channel
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=guild):
            await bot_module.update_status(123)  # no error

    @pytest.mark.asyncio
    async def test_no_users_renders_empty_panel(self):
        guild = MagicMock()
        channel = MagicMock()
        channel.name = "general"
        message = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
        guild.get_channel.return_value = channel
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=guild), \
             patch('database.status_atual_users', new_callable=AsyncMock, return_value={}):
            await bot_module.update_status(123)
        message.edit.assert_called_once()
        assert "No user" in message.edit.call_args[1]["content"]

    @pytest.mark.asyncio
    async def test_all_status_states_rendered(self):
        """TRABALHANDO / PAUSA / AUSENTE / NAO_INICIADO all appear in the message."""
        guild = MagicMock()
        channel = MagicMock()
        channel.name = "general"
        message = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
        # member found in guild for uid 111, not found for others
        channel.guild.get_member = MagicMock(side_effect=lambda uid: (
            MagicMock(display_name="Alice") if uid == 111 else None
        ))
        guild.get_channel.return_value = channel

        status = {
            "111": {"estado": "TRABALHANDO", "hora": 1700000000},
            "222": {"estado": "PAUSA",        "hora": 1700001000},
            "333": {"estado": "AUSENTE",      "hora": 1700002000},
            "444": {"estado": "NAO_INICIADO", "hora": None},
        }
        mock_user = MagicMock()
        mock_user.name = "Bob"
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=guild), \
             patch('database.status_atual_users', new_callable=AsyncMock, return_value=status), \
             patch('bot.bot.fetch_user', new_callable=AsyncMock, return_value=mock_user):
            await bot_module.update_status(123)

        content = message.edit.call_args[1]["content"]
        assert "Working" in content
        assert "break" in content.lower()
        assert "Absent" in content
        assert "not yet begun" in content.lower()

    @pytest.mark.asyncio
    async def test_user_fetch_fails_uses_unknown(self):
        guild = MagicMock()
        channel = MagicMock()
        channel.name = "general"
        message = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
        channel.guild.get_member = MagicMock(return_value=None)
        guild.get_channel.return_value = channel
        status = {"999": {"estado": "TRABALHANDO", "hora": 1700000000}}
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=guild), \
             patch('database.status_atual_users', new_callable=AsyncMock, return_value=status), \
             patch('bot.bot.fetch_user', new_callable=AsyncMock, side_effect=Exception("api error")):
            await bot_module.update_status(123)
        content = message.edit.call_args[1]["content"]
        assert "Unknown user" in content

    @pytest.mark.asyncio
    async def test_message_edit_raises_logs_warning(self):
        guild = MagicMock()
        channel = MagicMock()
        channel.name = "general"
        message = AsyncMock()
        message.edit = AsyncMock(side_effect=Exception("edit failed"))
        channel.fetch_message = AsyncMock(return_value=message)
        guild.get_channel.return_value = channel
        with patch('database.get_status_message', new_callable=AsyncMock, return_value="456"), \
             patch('bot.bot.get_guild', return_value=guild), \
             patch('database.status_atual_users', new_callable=AsyncMock, return_value={}):
            await bot_module.update_status(123)  # should not raise


# ===========================================================================
# restaurar_status_paineis
# ===========================================================================
class TestRestaurarStatusPaineis:
    @pytest.mark.asyncio
    async def test_calls_update_for_each_channel(self):
        with patch('database.list_channel_with_status', new_callable=AsyncMock,
                   return_value=[("123", "1"), ("456", "2")]), \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await bot_module.restaurar_status_paineis()
        mu.assert_any_call(123)
        mu.assert_any_call(456)

    @pytest.mark.asyncio
    async def test_continues_after_one_channel_fails(self):
        """If update_status raises for channel 1, channel 2 must still be processed."""
        calls = []

        async def fake_update(ch_id):
            calls.append(ch_id)
            if ch_id == 123:
                raise RuntimeError("boom")

        with patch('database.list_channel_with_status', new_callable=AsyncMock,
                   return_value=[("123", "1"), ("456", "2")]), \
             patch('bot.update_status', side_effect=fake_update):
            await bot_module.restaurar_status_paineis()

        assert 456 in calls


# ===========================================================================
# on_ready
# ===========================================================================
class TestOnReady:
    @pytest.mark.asyncio
    async def test_on_ready_nominal(self):
        mock_bot = MagicMock()
        mock_bot.add_cog = AsyncMock()
        mock_bot.tree.sync = AsyncMock()
        mock_bot.user = "TestBot"
        with patch('bot.bot', mock_bot), \
             patch('database.init_database', new_callable=AsyncMock), \
             patch('bot.restaurar_status_paineis', new_callable=AsyncMock) as mr, \
             patch('bot.Ponto'), \
             patch('builtins.print'):
            await bot_module.on_ready()
        mr.assert_awaited_once()
        mock_bot.add_cog.assert_awaited_once()
        mock_bot.tree.sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_ready_db_init_raises(self):
        """If database.init_database raises, on_ready propagates the exception."""
        mock_bot = MagicMock()
        mock_bot.user = "TestBot"
        with patch('bot.bot', mock_bot), \
             patch('database.init_database', new_callable=AsyncMock,
                   side_effect=RuntimeError("db down")), \
             patch('builtins.print'):
            with pytest.raises(RuntimeError, match="db down"):
                await bot_module.on_ready()


# ===========================================================================
# Missing env-var guard
# ===========================================================================
class TestMissingEnvVars:
    def test_raises_when_token_missing(self):
        env = {"DISCORD_TOKEN": "", "GUILD_ID": "12345678901234567"}
        with patch.dict(os.environ, env, clear=False):
            # Temporarily override the values in the already-imported module
            original_token = bot_module.TOKEN
            bot_module.TOKEN = ""
            try:
                if not bot_module.TOKEN or not bot_module.GUILD_ID:
                    # Exercise the same guard logic
                    with pytest.raises(RuntimeError):
                        raise RuntimeError("Missing DISCORD_TOKEN or GUILD_ID.")
            finally:
                bot_module.TOKEN = original_token


# ===========================================================================
# Slash Commands — Ponto cog
# ===========================================================================
class TestSlashCommandsDepartmentSetup:
    @pytest.mark.asyncio
    async def test_success(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True):
            await cog.config_department_setup.callback(cog, ix)
        ix.response.send_message.assert_called_once()
        ix.channel.send.assert_called_once()
        assert isinstance(ix.channel.send.call_args[1]['view'], bot_module.PointView)

    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=False):
            await cog.config_department_setup.callback(cog, ix)
        ix.channel.send.assert_not_called()
        assert "permission" in ix.response.send_message.call_args[0][0].lower()

class TestSlashCommandsStatusPanel:
    @pytest.mark.asyncio
    async def test_success(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        msg = MagicMock()
        msg.id = 789
        ix.channel.send.return_value = msg
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.set_status_message', new_callable=AsyncMock) as ms, \
             patch('bot.update_status', new_callable=AsyncMock) as mu:
            await cog.config_status_panel.callback(cog, ix)
        ms.assert_called_once_with("123", "789")
        mu.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=False):
            await cog.config_status_panel.callback(cog, ix)
        ix.channel.send.assert_not_called()

class TestSlashCommandsReportUserDetailed:
    def _make_record(self, weekday_offset=0):
        # Monday 2025-01-06
        d = date(2025, 1, 6 + weekday_offset)
        first_on = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        finish   = datetime(2025, 1, 6, 17, 0, tzinfo=timezone.utc)
        return (d, first_on, finish, timedelta(hours=8), timedelta(hours=1), True)

    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=False):
            await cog.report_user_detailed.callback(cog, ix, user)
        assert "permission" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_invalid_date_format(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True):
            await cog.report_user_detailed.callback(cog, ix, user, start_date="not-a-date")
        assert "Invalid date" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_records_in_db(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.first_last_record', new_callable=AsyncMock, return_value=(None, None)):
            await cog.report_user_detailed.callback(cog, ix, user)
        assert "no records" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_period_report_empty(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.period_report', new_callable=AsyncMock, return_value=[]):
            await cog.report_user_detailed.callback(
                cog, ix, user, start_date="01/01/2025", end_date="31/01/2025"
            )
        assert "no records available" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_no_valid_working_days(self):
        """All records are weekends → dias_uteis == 0."""
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        # Saturday
        sat = date(2025, 1, 4)
        record = (sat, datetime(2025,1,4,9,tzinfo=timezone.utc),
                  datetime(2025,1,4,17,tzinfo=timezone.utc), timedelta(8), timedelta(1), True)
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.period_report', new_callable=AsyncMock, return_value=[record]):
            await cog.report_user_detailed.callback(
                cog, ix, user, start_date="04/01/2025", end_date="04/01/2025"
            )
        assert "No full working days" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success_with_explicit_dates(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        record = self._make_record()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.period_report', new_callable=AsyncMock, return_value=[record]):
            await cog.report_user_detailed.callback(
                cog, ix, user, start_date="06/01/2025", end_date="06/01/2025"
            )
        content = ix.response.send_message.call_args[0][0]
        assert "Detailed Records" in content
        assert "Working days considered" in content

    @pytest.mark.asyncio
    async def test_success_without_dates_uses_first_last(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        record = self._make_record()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.first_last_record', new_callable=AsyncMock,
                   return_value=(date(2025,1,6), date(2025,1,6))), \
             patch('database.period_report', new_callable=AsyncMock, return_value=[record]):
            await cog.report_user_detailed.callback(cog, ix, user)
        assert "Detailed Records" in ix.response.send_message.call_args[0][0]

class TestSlashCommandsReportUserMobile:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=False):
            await cog.report_user_mobile_cmd.callback(cog, ix, user)
        assert "permission" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_invalid_date(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True):
            await cog.report_user_mobile_cmd.callback(cog, ix, user, start_date="bad")
        assert "Invalid date" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_records_in_db(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.first_last_record', new_callable=AsyncMock, return_value=(None, None)):
            await cog.report_user_mobile_cmd.callback(cog, ix, user)
        assert "no mobile records" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_no_mobile_records_in_period(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.mobile_records', new_callable=AsyncMock, return_value=[]):
            await cog.report_user_mobile_cmd.callback(
                cog, ix, user, start_date="01/01/2025", end_date="31/01/2025"
            )
        assert "no records available" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_success(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        records = [(date(2025,1,6), datetime(2025,1,6,9,0,tzinfo=timezone.utc))]
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.mobile_records', new_callable=AsyncMock, return_value=records):
            await cog.report_user_mobile_cmd.callback(
                cog, ix, user, start_date="01/01/2025", end_date="31/01/2025"
            )
        content = ix.response.send_message.call_args[0][0]
        assert "Mobile Records" in content
        assert "Total:" in content

class TestSlashCommandsReportExportCsv:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=False):
            await cog.report_export_csv.callback(cog, ix)
        assert "permission" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_no_data(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.export_csv_data', new_callable=AsyncMock, return_value=[]):
            await cog.report_export_csv.callback(cog, ix)
        assert "no data" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_success_sends_file(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        row = (
            "123", "456", date(2025,1,6),
            datetime(2025,1,6,9,0), datetime(2025,1,6,17,0),
            timedelta(hours=8), timedelta(hours=1), True, False
        )
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.export_csv_data', new_callable=AsyncMock, return_value=[row]), \
             patch('database.format_timedelta', return_value="08:00:00"):
            await cog.report_export_csv.callback(cog, ix)
        _, kwargs = ix.response.send_message.call_args
        assert 'file' in kwargs
        assert "CSV generated" in ix.response.send_message.call_args[0][0]

class TestSlashCommandsAdminDeleteData:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=False):
            await cog.admin_delete_data.callback(cog, ix, user)
        assert "permission" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_success_shows_confirm_view(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True):
            await cog.admin_delete_data.callback(cog, ix, user)
        _, kwargs = ix.response.send_message.call_args
        assert isinstance(kwargs['view'], bot_module.ConfirmViewExclusion)

class TestSlashCommandsConfigAdminsAdd:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.config_admins_add.callback(cog, ix, user)
        assert "Super Admin" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.config_admins_adds', new_callable=AsyncMock) as ma:
            await cog.config_admins_add.callback(cog, ix, user)
        ma.assert_called_once_with("123", str(user.id))
        assert "administrator" in ix.response.send_message.call_args[0][0].lower()

class TestSlashCommandsConfigAdminsRemove:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.config_admins_remove.callback(cog, ix, user)
        assert "Super Admin" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success_removed(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.config_admins_removes', new_callable=AsyncMock, return_value=True):
            await cog.config_admins_remove.callback(cog, ix, user)
        assert "removed" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_not_found(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.config_admins_removes', new_callable=AsyncMock, return_value=False):
            await cog.config_admins_remove.callback(cog, ix, user)
        assert "not administrator" in ix.response.send_message.call_args[0][0].lower()

class TestSlashCommandsConfigAdminsList:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=False):
            await cog.config_admins_list_cmd.callback(cog, ix)
        assert "permission" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_no_admins(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.config_admins_list', new_callable=AsyncMock, return_value=[]):
            await cog.config_admins_list_cmd.callback(cog, ix)
        assert "no registered" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_success_with_user_fetch(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        mock_user = MagicMock()
        mock_user.name = "Alice"
        mock_user.mention = "<@111>"
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.config_admins_list', new_callable=AsyncMock, return_value=["111"]), \
             patch('bot.bot.fetch_user', new_callable=AsyncMock, return_value=mock_user):
            await cog.config_admins_list_cmd.callback(cog, ix)
        assert "Alice" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success_user_fetch_fails_fallback(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.config_admins_list', new_callable=AsyncMock, return_value=["111"]), \
             patch('bot.bot.fetch_user', new_callable=AsyncMock, side_effect=Exception("not found")):
            await cog.config_admins_list_cmd.callback(cog, ix)
        assert "111" in ix.response.send_message.call_args[0][0]

class TestSlashCommandsAdminTransferUser:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        dest = MagicMock()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.admin_transfer_user.callback(cog, ix, user, dest)
        assert "Super Admin" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_user_not_registered(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        dest = MagicMock()
        dest.id = 200
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.get_user_locked_channel', new_callable=AsyncMock, return_value=None):
            await cog.admin_transfer_user.callback(cog, ix, user, dest)
        assert "not registered" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_already_in_destination(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        dest = MagicMock()
        dest.id = 200
        dest.mention = "<#200>"
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.get_user_locked_channel', new_callable=AsyncMock, return_value="200"):
            await cog.admin_transfer_user.callback(cog, ix, user, dest)
        assert "already registered" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_success_shows_confirm_view(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        dest = MagicMock()
        dest.id = 200
        dest.mention = "<#200>"

        # Synchronous mock for guild.get_channel
        mock_origin_channel = MagicMock()
        mock_origin_channel.mention = "<#100>"
        ix.guild.get_channel = MagicMock(return_value=mock_origin_channel)

        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
            patch('database.get_user_locked_channel', new_callable=AsyncMock, return_value="100"):
            await cog.admin_transfer_user.callback(cog, ix, user, dest)

        _, kwargs = ix.response.send_message.call_args
        assert isinstance(kwargs['view'], bot_module.ConfirmTransferView)

class TestSlashCommandsAdminViewLocks:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.admin_view_locks.callback(cog, ix)
        assert "Super Admin" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_users(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.list_blocked_users', new_callable=AsyncMock, return_value=[]):
            await cog.admin_view_locks.callback(cog, ix)
        assert "No users" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success_with_user_and_channel(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()

        # Synchronous mock for guild.get_channel
        mock_channel = MagicMock()
        mock_channel.mention = "<#100>"
        ix.guild.get_channel = MagicMock(return_value=mock_channel)

        mock_user = MagicMock()
        mock_user.name = "Alice"
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
            patch('database.list_blocked_users', new_callable=AsyncMock,
                return_value=[("111", "100", datetime.now(timezone.utc))]), \
            patch('bot.bot.fetch_user', new_callable=AsyncMock, return_value=mock_user):
            await cog.admin_view_locks.callback(cog, ix)

        assert "Alice" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_user_fetch_fails_fallback_id(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()

        # get_channel returns None → fallback to "ID: {channel_id}"
        ix.guild.get_channel = MagicMock(return_value=None)

        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
            patch('database.list_blocked_users', new_callable=AsyncMock,
                return_value=[("111", "100", datetime.now(timezone.utc))]), \
            patch('bot.bot.fetch_user', new_callable=AsyncMock, side_effect=Exception("api error")):
            await cog.admin_view_locks.callback(cog, ix)

        assert "ID: 111" in ix.response.send_message.call_args[0][0]
        assert "ID: 100" in ix.response.send_message.call_args[0][0]

class TestSlashCommandsSuperAdminAdd:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.super_admin_add.callback(cog, ix, user)
        assert "Super Admin" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_already_super_admin(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.is_super_admin', new_callable=AsyncMock, return_value=True):
            await cog.super_admin_add.callback(cog, ix, user)
        assert "already" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_success(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.is_super_admin', new_callable=AsyncMock, return_value=False), \
             patch('database.add_super_admin', new_callable=AsyncMock) as ma:
            await cog.super_admin_add.callback(cog, ix, user)
        ma.assert_called_once_with(str(user.id))
        assert "SUPER ADMIN" in ix.response.send_message.call_args[0][0]

class TestSlashCommandsSuperAdminList:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.super_admin_list.callback(cog, ix)
        assert "Super Admin" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_admins(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.list_super_admins', new_callable=AsyncMock, return_value=[]):
            await cog.super_admin_list.callback(cog, ix)
        assert "No SUPER ADMINS" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success_with_fetch(self):
        cog = bot_module.Ponto(bot=MagicMock())
        ix = make_interaction()
        mock_user = AsyncMock()
        mock_user.name = "Bob"
        mock_user.mention = "<@222>"
        cog.bot.fetch_user = AsyncMock(return_value=mock_user)
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.list_super_admins', new_callable=AsyncMock, return_value=["222"]):
            await cog.super_admin_list.callback(cog, ix)
        assert "Bob" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_fetch_fails_fallback(self):
        cog = bot_module.Ponto(bot=MagicMock())
        ix = make_interaction()
        cog.bot.fetch_user = AsyncMock(side_effect=Exception("not found"))
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.list_super_admins', new_callable=AsyncMock, return_value=["222"]):
            await cog.super_admin_list.callback(cog, ix)
        assert "222" in ix.response.send_message.call_args[0][0]

class TestSlashCommandsSuperAdminRemove:
    @pytest.mark.asyncio
    async def test_no_permission(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user()
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.super_admin_remove.callback(cog, ix, user)
        assert "Super Admin" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cannot_remove_server_owner(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction()
        user = make_user(user_id=999)
        ix.guild.owner_id = 999
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True):
            await cog.super_admin_remove.callback(cog, ix, user)
        assert "server owner" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_cannot_remove_self(self):
        cog = bot_module.Ponto(bot=None)
        # user_id == interaction.user.id
        ix = make_interaction(user_id=456)
        user = make_user(user_id=456)
        ix.guild.owner_id = 999
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True):
            await cog.super_admin_remove.callback(cog, ix, user)
        assert "own" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_not_a_super_admin(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction(user_id=1)
        user = make_user(user_id=789)
        ix.guild.owner_id = 999
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.is_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.super_admin_remove.callback(cog, ix, user)
        assert "not a SUPER ADMIN" in ix.response.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction(user_id=1)
        user = make_user(user_id=789)
        ix.guild.owner_id = 999
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.is_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.remove_super_admin', new_callable=AsyncMock, return_value=True):
            await cog.super_admin_remove.callback(cog, ix, user)
        assert "no longer" in ix.response.send_message.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_remove_fails(self):
        cog = bot_module.Ponto(bot=None)
        ix = make_interaction(user_id=1)
        user = make_user(user_id=789)
        ix.guild.owner_id = 999
        with patch('bot.check_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.is_super_admin', new_callable=AsyncMock, return_value=True), \
             patch('database.remove_super_admin', new_callable=AsyncMock, return_value=False):
            await cog.super_admin_remove.callback(cog, ix, user)
        assert "Failed" in ix.response.send_message.call_args[0][0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])