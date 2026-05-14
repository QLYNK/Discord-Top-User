import discord
from discord.ext import commands, tasks
from discord import app_commands
import sys
import database as db
import utils
from datetime import datetime, timezone, timedelta

# Interactive Button UI for Role Setup
class RoleSetupView(discord.ui.View):
    def __init__(self, role: discord.Role):
        super().__init__(timeout=None) # Timeout None taaki buttons hamesha kaam karein
        self.role = role

    @discord.ui.button(label="Add Role", style=discord.ButtonStyle.success, custom_id="add_reward_role")
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.user.add_roles(self.role)
            await interaction.response.send_message(f"✅ Tujhe {self.role.mention} role mil gaya!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Mera role is role se niche hai, thik kar usko permissions me!", ephemeral=True)

    @discord.ui.button(label="Remove Role", style=discord.ButtonStyle.danger, custom_id="remove_reward_role")
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.user.remove_roles(self.role)
            await interaction.response.send_message(f"🗑️ Tera {self.role.mention} role hata diya gaya!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Permission issue! Bot ka role upar kar.", ephemeral=True)


class SetupCheckView(discord.ui.View):
    def __init__(self, cog: "SetupCommands"):
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(discord.ui.Button(label="Deep Dey", url="https://deepdey.vercel.app/", style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label="Instagram", url="https://deepdey.vercel.app/insta", style=discord.ButtonStyle.link))

    @discord.ui.button(label="Send Existing Data Backup to Logs", style=discord.ButtonStyle.secondary, emoji="📦")
    async def send_backup_to_logs(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await db.get_guild_settings(interaction.guild_id)
        sent = await self.cog.send_backup_logs(interaction.guild, settings, "Manual backup requested from /setup check")
        if sent:
            await interaction.followup.send("✅ Existing activity data backup (JSON + HTML) sent to configured logs channel.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Logs channel is not configured or unavailable. Use `/setup logs` first.", ephemeral=True)


class SetupCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_activity_logs.start()

    def cog_unload(self):
        self.daily_activity_logs.cancel()

    @staticmethod
    def _branding_view() -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Deep Dey", url="https://deepdey.vercel.app/", style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Instagram", url="https://deepdey.vercel.app/insta", style=discord.ButtonStyle.link))
        return view

    # Create the /setup slash command group
    setup_group = app_commands.Group(name="setup", description="Leaderboard bot setup and configurations", default_permissions=discord.Permissions(administrator=True))

    @staticmethod
    def _to_utc_naive(dt):
        if not isinstance(dt, datetime):
            return None
        if dt.tzinfo:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _parse_option_date(option_value: str):
        try:
            return datetime.strptime(option_value.split(" ")[0], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _next_7_day_options() -> list[str]:
        today = datetime.now(timezone.utc).date()
        return [
            f"{(today + timedelta(days=offset)).isoformat()} ({(today + timedelta(days=offset)).strftime('%A')})"
            for offset in range(8)
        ]

    @staticmethod
    def _format_timestamp(dt_value: datetime | None) -> str:
        if not dt_value:
            return "`Not set`"
        dt_aware = dt_value.replace(tzinfo=timezone.utc)
        unix_ts = int(dt_aware.timestamp())
        return f"<t:{unix_ts}:F>\n(<t:{unix_ts}:R>)"

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_remaining(delta: timedelta) -> str:
        if delta.total_seconds() <= 0:
            return "Due now"
        total_seconds = int(delta.total_seconds())
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        return f"{days}d {hours}h {minutes}m"

    async def _send_current_result_snapshot(self, guild: discord.Guild, settings: dict, reason: str) -> bool:
        announcement_channel = guild.get_channel(settings.get("announcement_channel_id"))
        if not announcement_channel:
            return False

        top_count = max(1, min(self._safe_int(settings.get("top_count", 3), 3), 25))
        top_users = await db.get_top_users(guild.id, top_count)
        all_users = await db.get_all_users(guild.id)
        total_messages = sum(int(user.get("message_count", 0)) for user in all_users)

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, user_data in enumerate(top_users, start=1):
            member = guild.get_member(user_data.get("user_id"))
            rank_label = medals[i - 1] if i <= 3 else f"#{i}"
            display = member.mention if member else f"`{user_data.get('user_id')}`"
            lines.append(f"{rank_label} {display} — {user_data.get('message_count', 0)} messages")
        if not lines:
            lines = ["No tracked activity yet in this cycle."]

        embed = discord.Embed(
            title="📣 Current Cycle Result Snapshot",
            description=f"Reason: **{reason}**",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name=f"Top {top_count} Active Users", value="\n".join(lines), inline=False)
        embed.add_field(name="Total Messages (Current Cycle)", value=str(total_messages), inline=False)
        embed.set_footer(text="an app by deep")
        await announcement_channel.send(embed=embed, view=self._branding_view())
        return True

    async def send_backup_logs(self, guild: discord.Guild, settings: dict, action: str) -> bool:
        """Action hone se pehle Logs channel me JSON aur HTML format me data backup bhejta hai."""
        logs_channel_id = settings.get("logs_channel_id")
        if not logs_channel_id:
            return False
        
        logs_channel = guild.get_channel(logs_channel_id)
        if not logs_channel:
            return False

        users_data = await db.get_all_users(guild.id)
        if not users_data:
            await logs_channel.send(f"⚠️ **Log Action: {action}**\nKoi data nahi mila is cycle ke liye.")
            return True

        # Generate files using utils.py
        json_file = utils.generate_json_file(users_data)
        guild_icon = guild.icon.url if guild.icon else ""
        html_file = utils.generate_html_file(users_data, guild.name, guild_icon)

        await logs_channel.send(f"📄 **Log Action: {action}**\nData backup done before changes:", files=[json_file, html_file])
        return True

    @setup_group.command(name="channel", description="Set the channel for weekly leaderboard announcements")
    async def setup_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_guild_settings(interaction.guild_id, {"announcement_channel_id": channel.id})
        await interaction.response.send_message(f"✅ Announcement channel set to {channel.mention}")

    @setup_group.command(name="logs", description="Set the channel for HTML and JSON data logs")
    async def setup_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_guild_settings(interaction.guild_id, {"logs_channel_id": channel.id})
        await interaction.response.send_message(f"✅ Logs channel set to {channel.mention}")

    @setup_group.command(name="game_logs", description="Set the local channel for server game result summaries")
    async def setup_game_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_guild_settings(interaction.guild_id, {"game_logs_channel_id": channel.id})
        await interaction.response.send_message(
            f"✅ Local game logs channel set to {channel.mention}.\n"
            "Game summaries will now be posted here in addition to centralized telemetry.",
            view=self._branding_view(),
        )

    @setup_group.command(name="autogame", description="Configure automated game drops")
    @app_commands.describe(
        channel="Target channel for auto-games",
        ping_role="Role to ping for auto-games (leave empty for silent drop)",
        interval_in_minutes="Event interval in minutes",
    )
    async def setup_autogame(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        interval_in_minutes: app_commands.Range[int, 1, 1440],
        ping_role: discord.Role | None = None,
    ):
        await interaction.response.defer(thinking=True)
        await db.update_guild_settings(
            interaction.guild_id,
            {
                "autogame_channel_id": channel.id,
                "autogame_role_id": ping_role.id if ping_role else None,
                "autogame_interval_minutes": int(interval_in_minutes),
            },
        )
        role_line = f"Role: {ping_role.mention}\n" if ping_role else "Role: *(no ping — silent drop)*\n"
        await interaction.followup.send(
            (
                "✅ Auto-game scheduler updated.\n"
                f"Channel: {channel.mention}\n"
                f"{role_line}"
                f"Interval: **{interval_in_minutes} minute(s)**"
            ),
            view=self._branding_view(),
        )

    @setup_group.command(name="role", description="Set the reward role and test it with buttons")
    async def setup_role(self, interaction: discord.Interaction, role: discord.Role):
        await db.update_guild_settings(interaction.guild_id, {"reward_role_id": role.id})
        view = RoleSetupView(role)
        await interaction.response.send_message(
            f"✅ Reward role set to {role.mention}.\nNiche diye gaye buttons se members khud role add/remove test kar sakte hain:", 
            view=view
        )

    @setup_group.command(name="days", description="Set custom interval for the leaderboard (Default: 7)")
    async def setup_days(self, interaction: discord.Interaction, days: app_commands.Range[int, 1, 365]):
        await interaction.response.defer(thinking=True)
        settings = await db.get_guild_settings(interaction.guild_id)
        await self.send_backup_logs(interaction.guild, settings, f"Interval days changed to {days}")
        now = datetime.utcnow()
        counting_start = self._to_utc_naive(settings.get("counting_start_time")) or self._to_utc_naive(settings.get("last_reset_time")) or now
        result_hour = max(0, min(self._safe_int(settings.get("result_hour", counting_start.hour), counting_start.hour), 23))
        result_minute = max(0, min(self._safe_int(settings.get("result_minute", counting_start.minute), counting_start.minute), 59))
        next_result = (counting_start + timedelta(days=int(days))).replace(hour=result_hour, minute=result_minute, second=0, microsecond=0)
        if next_result <= counting_start:
            next_result = counting_start + timedelta(days=int(days))
        await db.update_guild_settings(
            interaction.guild_id,
            {
                "interval_days": int(days),
                "next_result_time": next_result,
            },
        )
        await interaction.followup.send(f"✅ Leaderboard timer updated to **{days} days**.")

    @setup_group.command(name="top_count", description="Set how many top members get the role (Default: 3)")
    async def setup_top_count(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 25]):
        await db.update_guild_settings(interaction.guild_id, {"top_count": int(count)})
        await interaction.response.send_message(f"✅ Leaderboard will now reward Top **{count}** active members.")

    @setup_group.command(name="schedule", description="Set counting start + result announce date/time (UTC)")
    @app_commands.describe(
        counting_start_date="Select counting start date (today to next 7 days)",
        result_date="Select result announce date (today to next 7 days)",
        result_hour="Result hour in 24-hour format (UTC)",
        result_minute="Result minute in 24-hour format (UTC)",
    )
    async def setup_schedule(
        self,
        interaction: discord.Interaction,
        counting_start_date: str,
        result_date: str,
        result_hour: app_commands.Range[int, 0, 23],
        result_minute: app_commands.Range[int, 0, 59],
    ):
        allowed_options = set(self._next_7_day_options())
        if counting_start_date not in allowed_options or result_date not in allowed_options:
            await interaction.response.send_message(
                "❌ Invalid date selection. Please pick dates from autocomplete options (today to next 7 days).",
                ephemeral=True,
            )
            return

        start_date = self._parse_option_date(counting_start_date)
        announce_date = self._parse_option_date(result_date)
        if not start_date or not announce_date:
            await interaction.response.send_message("❌ Date parsing failed. Please re-select options.", ephemeral=True)
            return

        counting_start_dt = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, 0)
        next_result_dt = datetime(
            announce_date.year,
            announce_date.month,
            announce_date.day,
            int(result_hour),
            int(result_minute),
            0,
            0,
        )
        if next_result_dt < counting_start_dt:
            await interaction.response.send_message(
                "❌ Result date/time cannot be earlier than counting start date.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        settings = await db.get_guild_settings(interaction.guild_id)
        await self._send_current_result_snapshot(interaction.guild, settings, "Schedule updated from /setup schedule")
        await self.send_backup_logs(interaction.guild, settings, "Schedule updated from /setup schedule")
        await db.reset_activity(interaction.guild_id)
        interval_days = max(1, (next_result_dt.date() - counting_start_dt.date()).days)

        await db.update_guild_settings(
            interaction.guild_id,
            {
                "counting_start_time": counting_start_dt,
                "next_result_time": next_result_dt,
                "result_hour": int(result_hour),
                "result_minute": int(result_minute),
                "interval_days": interval_days,
                "last_reset_time": counting_start_dt,
            },
        )
        await interaction.followup.send(
            (
                "✅ Schedule updated.\n"
                f"Counting Start: **{counting_start_date} (UTC 00:00)**\n"
                f"Next Result: **{result_date} {int(result_hour):02d}:{int(result_minute):02d} UTC**\n"
                "Previous cycle snapshot sent and current server activity data reset."
            ),
            view=self._branding_view(),
        )

    @setup_schedule.autocomplete("counting_start_date")
    async def setup_schedule_counting_start_date_autocomplete(
        self,
        _: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        options = self._next_7_day_options()
        filtered = [opt for opt in options if current.lower() in opt.lower()]
        return [app_commands.Choice(name=opt, value=opt) for opt in filtered[:8]]

    @setup_schedule.autocomplete("result_date")
    async def setup_schedule_result_date_autocomplete(
        self,
        _: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        options = self._next_7_day_options()
        filtered = [opt for opt in options if current.lower() in opt.lower()]
        return [app_commands.Choice(name=opt, value=opt) for opt in filtered[:8]]

    @setup_group.command(name="ping", description="Check bot latency and exact uptime")
    async def setup_ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        uptime = utils.format_uptime(self.bot.start_time)
        embed = discord.Embed(title="🏓 Pong!", color=discord.Color.green())
        embed.add_field(name="Latency", value=f"{latency}ms", inline=True)
        embed.add_field(name="Uptime", value=uptime, inline=True)
        await interaction.response.send_message(embed=embed)

    @setup_group.command(name="check", description="Show current leaderboard cycle overview and configuration")
    async def setup_check(self, interaction: discord.Interaction):
        settings = await db.get_guild_settings(interaction.guild_id)
        top_count = max(1, min(self._safe_int(settings.get("top_count", 3), 3), 25))
        top_users = await db.get_top_users(interaction.guild_id, top_count)
        all_users = await db.get_all_users(interaction.guild_id)
        total_messages = sum(int(user.get("message_count", 0)) for user in all_users)

        top_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, user_data in enumerate(top_users, start=1):
            member = interaction.guild.get_member(user_data.get("user_id"))
            display = member.mention if member else f"`{user_data.get('user_id')}`"
            rank_label = medals[i - 1] if i <= 3 else f"#{i}"
            top_lines.append(f"{rank_label} {display} — {user_data.get('message_count', 0)} messages")
        if not top_lines:
            top_lines = ["No tracked activity yet in this cycle."]

        announcement_channel = interaction.guild.get_channel(settings.get("announcement_channel_id"))
        logs_channel = interaction.guild.get_channel(settings.get("logs_channel_id"))
        reward_role = interaction.guild.get_role(settings.get("reward_role_id"))
        interval_days = max(1, self._safe_int(settings.get("interval_days", 7), 7))
        now_utc = datetime.utcnow()
        counting_start_time = self._to_utc_naive(settings.get("counting_start_time")) or self._to_utc_naive(settings.get("last_reset_time")) or now_utc
        next_result_time = self._to_utc_naive(settings.get("next_result_time")) or (counting_start_time + timedelta(days=interval_days))
        last_result_sent_time = self._to_utc_naive(settings.get("last_result_sent_time"))
        remaining = self._format_remaining(next_result_time - now_utc)

        embed = discord.Embed(
            title="🧪 Setup Check",
            description="Current cycle leaderboard status and configuration snapshot.",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name=f"Top {top_count} Most Active Users", value="\n".join(top_lines), inline=False)
        embed.add_field(name="Total Messages (Current Cycle)", value=str(total_messages), inline=False)
        embed.add_field(name="Announcement Channel", value=announcement_channel.mention if announcement_channel else "`Not set`", inline=True)
        embed.add_field(name="Logs Channel", value=logs_channel.mention if logs_channel else "`Not set`", inline=True)
        embed.add_field(name="Reward Role", value=reward_role.mention if reward_role else "`Not set`", inline=True)
        embed.add_field(name="Interval Days", value=str(interval_days), inline=True)
        embed.add_field(name="Top Winners Count", value=str(top_count), inline=True)
        embed.add_field(name="Remaining Until Next Result", value=remaining, inline=True)
        embed.add_field(name="Counting Start Date", value=self._format_timestamp(counting_start_time), inline=False)
        embed.add_field(name="Last Result Sent", value=self._format_timestamp(last_result_sent_time), inline=False)
        embed.add_field(name="Next Result Date", value=self._format_timestamp(next_result_time), inline=False)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=SetupCheckView(self))

    @setup_group.command(name="reset", description="Soft Reset: Send logs and delete current cycle data")
    async def setup_reset(self, interaction: discord.Interaction):
        await interaction.response.defer()
        settings = await db.get_guild_settings(interaction.guild_id)
        await self.send_backup_logs(interaction.guild, settings, "Manual Soft Reset triggered")
        await db.reset_activity(interaction.guild_id)
        await interaction.followup.send("✅ Data cleared for this cycle. Logs have been sent if configured.")

    @setup_group.command(name="hard_reset", description="Hard Reset: Send logs and wipe entire server settings + data")
    async def setup_hard_reset(self, interaction: discord.Interaction):
        await interaction.response.defer()
        settings = await db.get_guild_settings(interaction.guild_id)
        await self.send_backup_logs(interaction.guild, settings, "Manual HARD Reset triggered")
        await db.hard_reset_guild(interaction.guild_id)
        await interaction.followup.send("🚨 COMPLETE WIPE DONE. All settings and activity data erased.")

    @setup_group.command(name="restart", description="Restart the bot instance (Only if hosted with auto-restart like PM2)")
    async def setup_restart(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Restarting bot... (Agar server pe auto-restart on hai toh 5 seconds me wapas aayega).")
        sys.exit(0)

    @setup_group.command(name="help", description="Show all setup commands and info")
    async def setup_help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="⚙️ Setup Guide & Commands", description="Yahan saare configuration commands list hain. Is bot se tum apna custom leaderboard system automate kar sakte ho.", color=0x5865F2)
        
        embed.add_field(name="`/setup channel`", value="Leaderboard list kahan bhejni hai wo set karo.", inline=False)
        embed.add_field(name="`/setup logs`", value="Backup (JSON & HTML) kaha bhejna hai wo set karo.", inline=False)
        embed.add_field(name="`/setup game_logs`", value="Har game result ka local server summary channel set karo.", inline=False)
        embed.add_field(name="`/setup autogame`", value="Auto-game channel + ping role + interval configure karo.", inline=False)
        embed.add_field(name="`/setup role`", value="Reward role assign karo with Test Buttons.", inline=False)
        embed.add_field(name="`/setup days` & `/setup top_count`", value="Timer (days) aur kitne logo ko role dena hai (Top N) configure karo.", inline=False)
        embed.add_field(name="`/setup schedule`", value="Counting start date + next result date/time (UTC) set karo; old cycle snapshot bhejkar current server cycle reset karega.", inline=False)
        embed.add_field(name="`/setup reset` & `/setup hard_reset`", value="Current messages reset karne ya pura data udane ke liye.", inline=False)
        embed.add_field(name="`/setup ping` & `/setup restart`", value="Uptime check karne aur bot restart karne ke liye.", inline=False)
        
        embed.set_footer(text="an app by deep", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
        await interaction.response.send_message(embed=embed, view=self._branding_view())

    @tasks.loop(hours=24)
    async def daily_activity_logs(self):
        async for settings in db.settings_col.find({}):
            guild_id = settings.get("guild_id")
            logs_channel_id = settings.get("logs_channel_id")
            if not guild_id or not logs_channel_id:
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            logs_channel = guild.get_channel(logs_channel_id)
            if not logs_channel:
                continue

            all_users = await db.get_all_users(guild_id)
            total_messages = sum(int(user.get("message_count", 0)) for user in all_users)
            top_users = all_users[:3]
            top_lines = []
            medals = ["🥇", "🥈", "🥉"]
            for i, user_data in enumerate(top_users):
                member = guild.get_member(user_data.get("user_id"))
                display = member.mention if member else f"`{user_data.get('user_id')}`"
                top_lines.append(f"{medals[i]} {display} — {user_data.get('message_count', 0)}")
            if not top_lines:
                top_lines = ["No activity tracked in the last 24h window."]

            embed = discord.Embed(
                title="📊 Daily Activity Summary",
                description=f"Server: **{guild.name}**",
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Total Messages Tracked", value=str(total_messages), inline=False)
            embed.add_field(name="Quick Active User Stats", value="\n".join(top_lines), inline=False)
            embed.set_footer(text="an app by deep")
            await logs_channel.send(embed=embed, view=self._branding_view())

    @daily_activity_logs.before_loop
    async def before_daily_activity_logs(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(SetupCommands(bot))
