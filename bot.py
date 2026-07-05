import discord
import os
import io
import csv
import asyncio
import logging
from discord.ext import commands
from discord import app_commands
from collections import defaultdict
from discord.ui import Button, View
from datetime import datetime, timedelta, timezone

import database

# ---------------- #
# CONFIG
# ---------------- #
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

if not TOKEN or not GUILD_ID:
    raise RuntimeError("Missing DISCORD_TOKEN or GUILD_ID.")

_status_update_locks: dict[int, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


# ---------------- #
# BOT SETUP
# ---------------- #
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

class MyBot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def setup_hook(self):
        """Persistence of views after restart"""
        self.add_view(PointView())

bot = MyBot(command_prefix="!", intents=intents)


# ---------------- #
# AUX FUNCTIONS & UTILS
# ---------------- #
def detect_device(interaction: discord.Interaction) -> str:
    """Detects the user's device"""
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        return "unknown"

    if member.desktop_status != discord.Status.offline:
        return "desktop"
    elif member.mobile_status != discord.Status.offline:
        return "mobile"
    elif member.web_status != discord.Status.offline:
        return "web"
    
    return "unknown"

async def update_status(channel_id: int):
    """Fetch the message ID associated with the status panel for the given channel"""
    lock = _status_update_locks[channel_id]

    async with lock:
        message_id = await database.get_status_message(str(channel_id))
        if not message_id:
            return

        # Fetch guild
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning("Guild not found in cache")
            return

        # Fetch channel from guild (works even if not cached)
        try:
            channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
        except Exception as e:
            logger.warning(f"Failed to fetch channel {channel_id}: {e}")
            return

        if not channel:
            logger.warning(f"Channel not found: {channel_id}")
            return

        try:
            message = await channel.fetch_message(int(message_id))
        except Exception as e:
            logger.warning(f"Failed to fetch message {message_id} in channel {channel_id}: {e}")
            return

        status = await database.status_atual_users(str(channel_id))
        if not status:
            texto = f"📡 **Realtime status - {channel.name}**\n\nNo user with records."
        else:
            texto = f"📡 **Realtime status - {channel.name}**\n\n"
            for user_id, info in status.items():
                member = channel.guild.get_member(int(user_id))
                if member:
                    nome = member.display_name
                else:
                    try:
                        user = await bot.fetch_user(int(user_id))
                        nome = user.name
                    except Exception as e:
                        logger.warning(f"Failed to fetch user {user_id}: {e}")
                        nome = "Unknown user"

                unix_time = info.get('hora')
                estado = info.get("estado")
                if estado == "TRABALHANDO":
                    texto += f"🟢 **{nome}** — Working since <t:{unix_time}:T> PT\n" if unix_time else f"🟢 **{nome}** — Working\n"
                elif estado == "PAUSA":
                    texto += f"🟡 **{nome}** — On break since <t:{unix_time}:T> PT\n" if unix_time else f"🟡 **{nome}** — On break\n"
                elif estado == "AUSENTE":
                    texto += f"🔴 **{nome}** — Absent since <t:{unix_time}:f> PT\n" if unix_time else f"🔴 **{nome}** — Absent\n"
                else:
                    texto += f"⚪ **{nome}** — The workday has not yet begun.\n"

            agora_unix = int(datetime.now(timezone.utc).timestamp())
            texto += f"\n*Updated <t:{agora_unix}:R>* PT"

        try:
            await message.edit(content=texto)
        except Exception as e:
            logger.warning(f"Failed to edit message {message_id} in channel {channel_id}: {e}")


async def restaurar_status_paineis():
    """Fetch all channels with status panels and update them. Continue of failure."""
    canais = await database.list_channel_with_status()

    for channel_id, message_id in canais:
        try:
            await update_status(int(channel_id))
        except Exception as e:
            logger.warning(f"Failed to restore status panel for channel {channel_id}: {e}")

# PERMISSION CHECKS
async def check_admin(interaction: discord.Interaction) -> bool:
    # Super Admin always has access
    if await check_super_admin(interaction):
        return True

    return await database.is_admin(
        str(interaction.channel_id),
        str(interaction.user.id)
    )


async def check_super_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False

    # Server owner
    if interaction.user.id == interaction.guild.owner_id:
        return True

    # Discord Administrator permission
    member = interaction.guild.get_member(interaction.user.id)
    if member and member.guild_permissions.administrator:
        return True

    # Explicit SUPER ADMIN (database)
    if await database.is_super_admin(str(interaction.user.id)):  
        return True

    return False


# ---------------- #
# VIEWS
# ---------------- #
class PointView(View):
    """Point buttons - ON/BREAK/FINISH system"""
    def __init__(self):
        super().__init__(timeout=None)

    async def handle_action(self, interaction: discord.Interaction, action: str):
        channel_id = str(interaction.channel_id)
        user_id = str(interaction.user.id)
        device = detect_device(interaction)

        try:
            # Imediate response to avoid "This interaction failed" message. The real response will be sent later.
            await interaction.response.defer(ephemeral=True)

            success, msg = await database.record_action(
                channel_id, user_id, action, device
            )

            await interaction.followup.send(msg, ephemeral=True)

            if success:
                # runs out of the current flow to avoid blocking the button interaction response
                asyncio.create_task(update_status(interaction.channel_id))

        except Exception as e:
            logger.exception("Button interaction failure")

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Internal error while processing action.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Internal error while processing action.",
                    ephemeral=True
                )


    @discord.ui.button(label="ON", style=discord.ButtonStyle.success, custom_id="on_point")
    async def on(self, interaction, button):
        await self.handle_action(interaction, "ON")

    @discord.ui.button(label="BREAK", style=discord.ButtonStyle.secondary, custom_id="break_point")
    async def pause(self, interaction, button):
        await self.handle_action(interaction, "BREAK")

    @discord.ui.button(label="FINISH", style=discord.ButtonStyle.danger, custom_id="finish_point")
    async def finish(self, interaction, button):
        channel_id = str(interaction.channel_id)
        user_id = str(interaction.user.id)
        device = detect_device(interaction)

        view = ConfirmFinishView(channel_id, user_id, device)

        await interaction.response.send_message(
            "⚠️ **Are you sure you want to FINISH your workday?**\n\n"
            "This action cannot be undone today.",
            view=view,
            ephemeral=True
        )

class ConfirmFinishView(View):
    """Double confirmation view for FINISH action"""
    def __init__(self, channel_id, user_id, device):
        super().__init__(timeout=30)
        self.channel_id = channel_id
        self.user_id = user_id
        self.device = device

    @discord.ui.button(label="Confirm FINISH", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        success, msg = await database.record_action(  
            self.channel_id,
            self.user_id,
            "FINISH",
            self.device
        )
        await interaction.response.edit_message(content=msg, view=None)

        if success:
            await update_status(int(self.channel_id))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(
            content="❌ Finish cancelled.",
            view=None
        )

class ConfirmViewExclusion(View):
    """Confirmation view for data deletion"""
    def __init__(self, channel_id, user_id):
        super().__init__(timeout=60)
        self.channel_id = channel_id
        self.user_id = user_id
        self.confirmacao = False

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, button: Button):
        await database.delete_user_records(self.channel_id, self.user_id)  
        await update_status(int(self.channel_id))
        await interaction.response.edit_message(
            content=f"✅ User records successfully deleted in this department..",
            view=None
        )
        self.confirmacao = True

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="❌ Exclusion cancelled.", view=None)
        self.confirmacao = False


class ConfirmTransferView(View):
    """Confirmation view for user transfer"""
    def __init__(self, user_id, origem_channel_id, destino_channel_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.origem_channel_id = origem_channel_id
        self.destino_channel_id = destino_channel_id

    @discord.ui.button(label="Confirm Transfer", style=discord.ButtonStyle.primary)
    async def confirmar(self, interaction: discord.Interaction, button: Button):
        try:
            success, registros = await database.transfer_user_to_channel(  
                self.user_id, 
                self.origem_channel_id, 
                self.destino_channel_id
            )
            
            if success:
                await update_status(int(self.origem_channel_id))
                await update_status(int(self.destino_channel_id))
                
                await interaction.response.edit_message(
                    content=f"✅ User transferred successfully.!\n"
                            f"📊 {registros} record(s) moved\n"
                            f"📤 From: <#{self.origem_channel_id}>\n"
                            f"📥 To: <#{self.destino_channel_id}>",
                    view=None
                )
            else:
                await interaction.response.edit_message(
                    content="❌ Error transferring user.",
                    view=None
                )
        except Exception as e:
            await interaction.response.edit_message(
                content=f"❌ Error: {str(e)}",
                view=None
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="❌ Transfer cancelled.", view=None)


# ---------------- #
# COG - PONTO
# ---------------- #
class Ponto(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="config_department_setup",
        description="Creates record panel in this channel/department"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def config_department_setup(self, interaction: discord.Interaction):
        if not await check_admin(interaction):  
            await interaction.response.send_message("❌ You do not have permission to use commands in this department.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Time clock panel created for **{interaction.channel.name}**.",
            ephemeral=True
        )

        await interaction.channel.send(
            f"🕒 **Time Clock - {interaction.channel.name}**\n\n"
            "**System of States:**\n"
            "• **ON**: Starts/resumes work\n"
            "• **BREAK**: Break time\n"
            "• **FINISH**: Finish the workday\n\n",
            view=PointView()
        )

    @app_commands.command(
        name="config_status_panel",
        description="Creates real-time status panel in this channel/department"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def config_status_panel(self, interaction: discord.Interaction):
        if not await check_admin(interaction):  
            await interaction.response.send_message("❌ No permission in this department..", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Status panel created for **{interaction.channel.name}**.",
            ephemeral=True
        )

        msg = await interaction.channel.send(
            f"📡 **Realtime status - {interaction.channel.name}**\n\nLoading..."
        )

        await database.set_status_message(str(interaction.channel_id), str(msg.id))  
        await update_status(interaction.channel_id)


    @app_commands.command(
        name="report_user_detailed",
        description="Shows averages and total hours of a user in a given period"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.describe(
        user="User",
        start_date="Start Date DD/MM/YYYY (optional)",
        end_date="End Date DD/MM/YYYY (optional)"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def report_user_detailed(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        start_date: str = None,
        end_date: str = None
    ):
        if not await check_admin(interaction):  
            await interaction.response.send_message(
                "❌ No permission in this department.",
                ephemeral=True
            )
            return

        channel_id = str(interaction.channel_id)
        user_id = str(user.id)

        # ============================
        # DATES PARSE
        # ============================
        try:
            di = datetime.strptime(start_date, "%d/%m/%Y").date() if start_date else None
            df = datetime.strptime(end_date, "%d/%m/%Y").date() if end_date else None
        except:
            await interaction.response.send_message(
                "❌ Invalid date format. Use DD/MM/YYYY.",
                ephemeral=True
            )
            return

        # ============================
        # SET DEFAULT PERIOD IF NO DATES ARE SPECIFIED
        # ============================
        if not di or not df:
            primeiro, ultimo = await database.first_last_record(channel_id, user_id)  
            if not primeiro or not ultimo:
                await interaction.response.send_message(
                    f"📊 **{user.name}** no records.",
                    ephemeral=True
                )
                return

            if not di:
                di = primeiro
            if not df:
                df = ultimo

        # ============================
        # SEARCH RECORDS
        # ============================
        registros = await database.period_report(channel_id, user_id, di, df)  

        if not registros:
            await interaction.response.send_message(
                f"📊 **{user.name}** no records available for the period.",
                ephemeral=True
            )
            return

        entradas = []
        saidas = []
        breaks = []
        total_trabalho = timedelta()
        dias_uteis = 0

        for r in registros:
            data, first_on, finish, work_sum, break_sum, is_finished = r

            # Ignore weekends
            if data.weekday() >= 5:
                continue

            # Only full valid workdays
            if first_on and work_sum and is_finished:
                dias_uteis += 1
                entradas.append(first_on)
                if finish:
                    saidas.append(finish)
                total_trabalho += work_sum
                breaks.append(break_sum or timedelta(0))

        if dias_uteis == 0:
            await interaction.response.send_message(
                "⚠️ No full working days found in the period.",
                ephemeral=True
            )
            return

        # ============================
        # CALCULATIONS
        # ============================
        # Average start and finish
        def media_hora(lista):
            if not lista:
                return None
            total_secods = sum(h.hour * 3600 + h.minute * 60 + h.second for h in lista)
            return total_secods / len(lista)

        media_entrada = media_hora(entradas)
        media_saida = media_hora(saidas)
        media_trabalho = total_trabalho / dias_uteis

        # Breaks
        total_break = sum((b.total_seconds() for b in breaks))
        media_break = total_break / dias_uteis if dias_uteis else 0

        def segundos_para_hora(seg):
            h = int(seg // 3600)
            m = int((seg % 3600) // 60)
            s = int(seg % 60)
            return f"{h:02d}:{m:02d}:{s:02d}"

        texto = (
            f"📊 **Detailed Records - {user.name}**\n"
            f"📅 **Period:** {di.strftime('%d/%m/%Y')} → {df.strftime('%d/%m/%Y')}\n\n"
            f"🟢 **Average start time:** `{segundos_para_hora(media_entrada) if media_entrada else '--'}`\n"
            f"🔴 **Average finish time:** `{segundos_para_hora(media_saida) if media_saida else '--'}`\n\n"
            f"⏱️ **Average daily hours worked:** `{database.format_timedelta(media_trabalho)}`\n"
            f"🧮 **Total hours worked:** `{database.format_timedelta(total_trabalho)}`\n"
            f"☕ **Average daily breaks:** `{segundos_para_hora(media_break)}`\n"
            f"📌 **Total break hours:** `{database.format_timedelta(timedelta(seconds=total_break))}`\n"
            f"📆 **Working days considered:** {dias_uteis}"
        )


        await interaction.response.send_message(texto, ephemeral=True)

    @app_commands.command(
        name="report_user_mobile",
        description="Lists days with first ON via mobile device"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.describe(
        user="User",
        start_date="Start Date DD/MM/YYYY (optional)",
        end_date="End Date DD/MM/YYYY (optional)"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def report_user_mobile_cmd(
        self, 
        interaction: discord.Interaction, 
        user: discord.User,
        start_date: str = None,
        end_date: str = None
    ):
        if not await check_admin(interaction):  
            await interaction.response.send_message("❌ No permission in this department..", ephemeral=True)
            return
        
        channel_id = str(interaction.channel_id)
        user_id = str(user.id)

        # Dates parse
        try:
            di = datetime.strptime(start_date, "%d/%m/%Y").date() if start_date else None
            df = datetime.strptime(end_date, "%d/%m/%Y").date() if end_date else None
        except:
            await interaction.response.send_message("❌ Invalid date format. Use DD/MM/YYYY", ephemeral=True)
            return
        
        # Default period
        if not di or not df:
            primeiro, ultimo = await database.first_last_record(channel_id, user_id)  
            if not primeiro or not ultimo:
                await interaction.response.send_message(
                    f"📱 **{user.name}** has no mobile records.",
                    ephemeral = True
                )
                return
            if not di:
                di = primeiro
            if not df:
                df = ultimo

        # Fetch mobile records
        registros = await database.mobile_records(channel_id, user_id, di, df)  
        
        if not registros:
            await interaction.response.send_message(
                f"📱 **{user.name}** There are no records available via mobile phone during this period.",
                ephemeral=True
            )
            return
        
        texto = f"📱 **Mobile Records - {user.name}**\n"
        texto += f"📅 Period: {di.strftime('%d/%m/%Y')} → {df.strftime('%d/%m/%Y')}\n\n"
        
        for data, first_on in registros:
            texto += f"• {data.strftime('%d/%m/%Y')} - First ON: `{first_on.strftime('%H:%M:%S')}`\n"
        
        texto += f"\n**Total:** {len(registros)} day(s)"
        
        await interaction.response.send_message(texto, ephemeral=True)

    @app_commands.command(
        name="report_export_csv",
        description="Exports records from this department to CSV"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def report_export_csv(self, interaction: discord.Interaction):
        if not await check_admin(interaction):  
            await interaction.response.send_message("❌ No permission in this department..", ephemeral=True)
            return

        channel_id = str(interaction.channel_id)
        dados = await database.export_csv_data(channel_id)  
        
        if not dados:
            await interaction.response.send_message("❌ There is no data to export in this department.", ephemeral=True)
            return

        with io.StringIO() as csv_buffer:
            writer = csv.writer(csv_buffer)
            writer.writerow([
                "channel_id", "user_id", "data", "first_on", "finish", 
                "work_sum", "break_sum", "is_finished", "mobile_first_on"
            ])
            
            for row in dados:
                writer.writerow([
                    row[0], row[1], row[2],
                    row[3].strftime("%Y-%m-%d %H:%M:%S") if row[3] else "",
                    row[4].strftime("%Y-%m-%d %H:%M:%S") if row[4] else "",
                    database.format_timedelta(row[5]),
                    database.format_timedelta(row[6]),
                    row[7], row[8]
                ])
            
            csv_buffer.seek(0)
            file = discord.File(
                io.BytesIO(csv_buffer.getvalue().encode("utf-8")),
                filename=f"registros_{interaction.channel.name}_{datetime.now().strftime('%Y%m%d')}.csv"
            )

        await interaction.response.send_message(
            f"📄 CSV generated for **{interaction.channel.name}**!",
            file=file,
            ephemeral=True
        )

    @app_commands.command(
        name="admin_delete_data",
        description="Deletes records of a user IN THIS department"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def admin_delete_data(self, interaction: discord.Interaction, user: discord.User):
        if not await check_admin(interaction):  
            await interaction.response.send_message("❌ No permission in this department..", ephemeral=True)
            return

        channel_id = str(interaction.channel_id)
        view = ConfirmViewExclusion(channel_id, str(user.id))
        await interaction.response.send_message(
            f"⚠️ Are you sure you want to delete all records from **{user.name}** "
            f"in the department **{interaction.channel.name}**?\nThis action is irreversible.",
            view=view,
            ephemeral=True
        )

    @app_commands.command(
        name="config_admins_add",
        description="[SUPER ADMIN] Adds administrator to this department"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.describe(user="User to be promoted to admin")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def config_admins_add(self, interaction: discord.Interaction, user: discord.User):
        # Only Super Admins can add administrators.
        if not await check_super_admin(interaction):  
            await interaction.response.send_message(
                "❌ Only Super Admins can add administrators..", 
                ephemeral=True
            )
            return

        channel_id = str(interaction.channel_id)
        await database.config_admins_adds(channel_id, str(user.id))  
        await interaction.response.send_message(
            f"✅ **{user.name}** now is the administrator of **{interaction.channel.name}**.",
            ephemeral=True
        )

    @app_commands.command(
        name="config_admins_remove",
        description="[SUPER ADMIN] Removes administrator from this department"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.describe(user="Utilizador a ser removido")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def config_admins_remove(self, interaction: discord.Interaction, user: discord.User):
        # Only Super Admins can remove administrators.
        if not await check_super_admin(interaction):  
            await interaction.response.send_message(
                "❌ Only Super Admins can remove administrators.", 
                ephemeral=True
            )
            return

        channel_id = str(interaction.channel_id)
        success = await database.config_admins_removes(channel_id, str(user.id))  
        if success:
            await interaction.response.send_message(
                f"✅ **{user.name}** removed as administrator of **{interaction.channel.name}**.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ **{user.name}** was not administrator of this department.",
                ephemeral=True
            )

    @app_commands.command(
        name="config_admins_list",
        description="Lists administrators of this department"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def config_admins_list_cmd(self, interaction: discord.Interaction):
        if not await check_admin(interaction):  
            await interaction.response.send_message("❌ No permission in this department..", ephemeral=True)
            return

        channel_id = str(interaction.channel_id)
        admins = await database.config_admins_list(channel_id)  
        if not admins:
            await interaction.response.send_message(
                f"📋 **{interaction.channel.name}** has no registered administrators.",
                ephemeral=True
            )
            return

        texto = f"📋 **Administrators of {interaction.channel.name}:**\n\n"
        for admin_id in admins:
            try:
                user = await bot.fetch_user(int(admin_id))
                texto += f"• {user.name} ({user.mention})\n"
            except:
                texto += f"• ID: {admin_id}\n"

        await interaction.response.send_message(texto, ephemeral=True)


    # ============================================
    # SUPER ADMIN COMMANDS
    # ============================================
    @app_commands.command(
        name="admin_transfer_user",
        description="[SUPER ADMIN] Transfers user between departments"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.describe(
        user="User to be transferred",
        canal_destino="Destination channel"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def admin_transfer_user(
        self, 
        interaction: discord.Interaction, 
        user: discord.User,
        canal_destino: discord.TextChannel
    ):
        if not await check_super_admin(interaction):  
            await interaction.response.send_message(
                "❌ Only Super Admins can transfer users.", 
                ephemeral=True
            )
            return

        user_id = str(user.id)
        destino_id = str(canal_destino.id)
        origem_id = await database.get_user_locked_channel(user_id)  
        
        if not origem_id:
            await interaction.response.send_message(
                f"❌ **{user.name}** is not registered in any department.",
                ephemeral=True
            )
            return
        
        if origem_id == destino_id:
            await interaction.response.send_message(
                f"❌ **{user.name}** is already registered in {canal_destino.mention}.",
                ephemeral=True
            )
            return

        canal_origem = interaction.guild.get_channel(int(origem_id))
        origem_nome = canal_origem.mention if canal_origem else f"Channel ID: {origem_id}"

        view = ConfirmTransferView(user_id, origem_id, destino_id)
        await interaction.response.send_message(
            f"⚠️ **Transfer Confirmation**\n\n"
            f"👤 **User:** {user.mention}\n"
            f"📤 **From:** {origem_nome}\n"
            f"📥 **To:** {canal_destino.mention}\n\n"
            f"All records will be moved. Do you wish to continue?",
            view=view,
            ephemeral=True
        )

    @app_commands.command(
        name="admin_view_locks",
        description="[SUPER ADMIN] Lists all users and their departments"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def admin_view_locks(self, interaction: discord.Interaction):
        if not await check_super_admin(interaction):  
            await interaction.response.send_message(
                "❌ Only Super Admins can see locks.", 
                ephemeral=True
            )
            return

        bloqueios = await database.list_blocked_users()  
        
        if not bloqueios:
            await interaction.response.send_message(
                "📋 No users are registered in any departments.",
                ephemeral=True
            )
            return

        texto = "📋 **Registered Users by Department**\n\n"
        
        for user_id, channel_id, locked_at in bloqueios:
            try:
                user = await bot.fetch_user(int(user_id))
                nome = user.name
            except:
                nome = f"ID: {user_id}"
            
            canal = interaction.guild.get_channel(int(channel_id))
            canal_nome = canal.mention if canal else f"ID: {channel_id}"
            
            texto += f"• **{nome}** → {canal_nome}\n"

        await interaction.response.send_message(texto, ephemeral=True)

    @app_commands.command(
        name="super_admin_add",
        description="[SUPER ADMIN] Grants SUPER ADMIN privileges to a user"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.describe(user="User to be promoted to SUPER ADMIN")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def super_admin_add(self, interaction: discord.Interaction, user: discord.User):
        if not await check_super_admin(interaction):  
            await interaction.response.send_message(
                "❌ Only Super Admins can grant SUPER ADMIN privileges.",
                ephemeral=True
            )
            return

        if await database.is_super_admin(str(user.id)):  
            await interaction.response.send_message(
                f"⚠️ **{user.name}** is already a SUPER ADMIN.",
                ephemeral=True
            )
            return

        await database.add_super_admin(str(user.id))  

        await interaction.response.send_message(
            f"🛡️ **{user.name}** is now a **SUPER ADMIN**.",
            ephemeral=True
        )
            

    @app_commands.command(
        name="super_admin_list",
        description="[SUPER ADMIN] Lists all SUPER ADMINS"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def super_admin_list(self, interaction: discord.Interaction):
        if not await check_super_admin(interaction):  
            await interaction.response.send_message(
                "❌ Only Super Admins can view this list.",
                ephemeral=True
            )
            return

        admins = await database.list_super_admins()  
        if not admins:
            await interaction.response.send_message(
                "📋 No SUPER ADMINS registered.",
                ephemeral=True
            )
            return

        texto = "🛡️ **SUPER ADMINS**\n\n"
        for admin_id in admins:
            try:
                user = await self.bot.fetch_user(int(admin_id))
                texto += f"• {user.name} ({user.mention})\n"
            except:
                texto += f"• ID: {admin_id}\n"

        await interaction.response.send_message(texto, ephemeral=True)


    @app_commands.command(
        name="super_admin_remove",
        description="[SUPER ADMIN] Removes SUPER ADMIN privileges from a user"
    )
    @app_commands.default_permissions(administrator=True)

    @app_commands.describe(user="User to be removed from SUPER ADMIN")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def super_admin_remove(self, interaction: discord.Interaction, user: discord.User):
        if not await check_super_admin(interaction):  
            await interaction.response.send_message(
                "❌ Only Super Admins can remove SUPER ADMIN privileges.",
                ephemeral=True
            )
            return

        # Prevent removing server owner
        if interaction.guild and user.id == interaction.guild.owner_id:
            await interaction.response.send_message(
                "❌ The server owner cannot be removed from SUPER ADMIN.",
                ephemeral=True
            )
            return

        # Prevent self-removal
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ You cannot remove your own SUPER ADMIN privileges.",
                ephemeral=True
            )
            return

        if not await database.is_super_admin(str(user.id)):  
            await interaction.response.send_message(
                f"⚠️ **{user.name}** is not a SUPER ADMIN.",
                ephemeral=True
            )
            return

        success = await database.remove_super_admin(str(user.id))  

        if success:
            await interaction.response.send_message(
                f"🗑️ **{user.name}** is no longer a **SUPER ADMIN**.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Failed to remove SUPER ADMIN privileges.",
                ephemeral=True
            )


# --------------------------
# EVENTS
# --------------------------
@bot.event
async def on_ready():
    print(f" Bot connected as {bot.user}")
    print(f" Multi-department system activated")
    print(f" Channel locking system activated")
    print(f" ON/BREAK/FINISH status system implemented")
    print(f" UTC + Discord Timestamps enabled")
    
    # Initialize database first
    await database.init_database()
    print(" Database initialized")
    
    await bot.add_cog(Ponto(bot))
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print(" Synchronized commands")

    await restaurar_status_paineis()


# --------------------------
# Run
# --------------------------
if __name__ == "__main__":
    bot.run(TOKEN)