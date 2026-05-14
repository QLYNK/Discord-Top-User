import discord
from discord.ext import commands, tasks
from discord import app_commands
import sys
import database as db
import utils
from datetime import datetime, timedelta, timezone

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


class BackupLogsView(discord.ui.View):
    def __init__(self, cog: "SetupCommands"):
        super().__init__(timeout=600)
        self.cog = cog
        self.add_item(discord.ui.Button(label="Deep Dey", url="https://deepdey.vercel.app/", style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label="Instagram", url="https://deepdey.vercel.app/insta", style=discord.ButtonStyle.link))

    @discord.ui.button(
        label="Send Existing Data Backup to Logs",
        style=discord.ButtonStyle.secondary,
        emoji="📁",
    )
    async def backup_to_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ This button only works in a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Sirf admins hi backup logs trigger kar sakte hain.", ephemeral=True)
            return
        settings = await db.get_guild_settings(interaction.guild_id)
        await self.cog.send_backup_logs(interaction.guild, settings, f"Manual backup by {interaction.user} via setup check")
        await interaction.response.send_message("✅ Existing activity backup logs channel me bhej diya gaya (if configured).", ephemeral=True)


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

    @staticmethod
    def _format_discord_time(dt: datetime | None) -> str:
        if not dt:
            return "`Not available`"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = int(dt.timestamp())
        return f"<t:{ts}:F>\n(<t:{ts}:R>)"

    @staticmethod
    def _format_remaining(now: datetime, target: datetime | None) -> str:
        if not target:
            return "`Not available`"
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        delta = target - now
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "Due now"
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        return f"{days} day(s), {hours} hour(s), {minutes} minute(s)"

    @staticmethod
    def _normalize_utc(dt: datetime | None) -> datetime | None:
        if not dt:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _next_result_time(self, settings: dict, now: datetime | None = None) -> datetime | None:
        now = now or datetime.now(timezone.utc)
        last_reset = self._normalize_utc(settings.get("last_reset_time"))
        if not last_reset:
            return None
        interval_days = max(1, int(settings.get("interval_days", 7) or 7))
        next_time = last_reset + timedelta(days=interval_days)
        if now < next_time:
            return next_time
        cycle_seconds = interval_days * 86400
        cycles_passed = int((now - last_reset).total_seconds() // cycle_seconds) + 1
        return last_reset + timedelta(days=cycles_passed * interval_days)

    async def _announce_current_result(self, guild: discord.Guild, settings: dict, reason: str):
        announcement_channel = guild.get_channel(settings.get("announcement_channel_id"))
        role = guild.get_role(settings.get("reward_role_id"))
        top_count = max(1, int(settings.get("top_count", 3) or 3))

        if not announcement_channel:
            return

        top_users = await db.get_top_users(guild.id, top_count)
        if not top_users:
            await announcement_channel.send(f"ℹ️ {reason}\nNo tracked activity found for current cycle.")
            return

        if role:
            for member in role.members:
                try:
                    await member.remove_roles(role)
                except discord.Forbidden:
                    pass

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank, user_data in enumerate(top_users, start=1):
            medal = medals[rank - 1] if rank <= 3 else f"#{rank}"
            member = guild.get_member(user_data["user_id"])
            if member:
                lines.append(f"{medal} **{member.mention}** — {user_data.get('message_count', 0)} messages")
                if role:
                    try:
                        await member.add_roles(role)
                    except discord.Forbidden:
                        pass
            else:
                lines.append(f"{medal} **Left User ({user_data['user_id']})** — {user_data.get('message_count', 0)} messages")

        embed = discord.Embed(
            title="🏆 Server Activity Leaderboard",
            description="\n".join(lines),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="an app by deep")

        role_mention = role.mention if role else "Top Members"
        content = f"{role_mention} Existing cycle result ({reason}) — Top {top_count} members."
        await announcement_channel.send(content=content, embed=embed, view=self._branding_view())

    # Create the /setup slash command group
    setup_group = app_commands.Group(name="setup", description="Leaderboard bot setup and configurations", default_permissions=discord.Permissions(administrator=True))

    async def send_backup_logs(self, guild: discord.Guild, settings: dict, action: str):
        """Action hone se pehle Logs channel me JSON aur HTML format me data backup bhejta hai."""
        logs_channel_id = settings.get("logs_channel_id")
        if not logs_channel_id: return
        
        logs_channel = guild.get_channel(logs_channel_id)
        if not logs_channel: return

        users_data = await db.get_all_users(guild.id)
        if not users_data:
            await logs_channel.send(f"⚠️ **Log Action: {action}**\nKoi data nahi mila is cycle ke liye.")
            return

        # Generate files using utils.py
        json_file = utils.generate_json_file(users_data)
        guild_icon = guild.icon.url if guild.icon else ""
        html_file = utils.generate_html_file(users_data, guild.name, guild_icon)

        await logs_channel.send(f"📄 **Log Action: {action}**\nData backup done before changes:", files=[json_file, html_file])

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
        await interaction.response.defer()
        settings = await db.get_guild_settings(interaction.guild_id)
        await self.send_backup_logs(interaction.guild, settings, f"Interval days changed to {days}")
        await db.update_guild_settings(interaction.guild_id, {"interval_days": days})
        updated_settings = {**settings, "interval_days": int(days)}
        next_result_time = self._next_result_time(updated_settings, datetime.now(timezone.utc))
        await interaction.followup.send(
            "✅ Leaderboard timer updated.\n"
            f"Interval: **{days} day(s)**\n"
            f"Next result: {self._format_discord_time(next_result_time)}"
        )

    @setup_group.command(name="top_count", description="Set how many top members get the role (Default: 3)")
    async def setup_top_count(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 25]):
        await db.update_guild_settings(interaction.guild_id, {"top_count": count})
        await interaction.response.send_message(f"✅ Leaderboard will now reward Top **{count}** active members.")

    @setup_group.command(name="schedule", description="Set counting start date + result time (UTC)")
    @app_commands.describe(
        result_date="Select counting start date (today to next 7 days)",
        hour="Hour in 24h format (UTC)",
        minute="Minute in 24h format (UTC)",
    )
    async def setup_schedule(
        self,
        interaction: discord.Interaction,
        result_date: str,
        hour: app_commands.Range[int, 0, 23],
        minute: app_commands.Range[int, 0, 59],
    ):
        await interaction.response.defer(thinking=True)
        now = datetime.now(timezone.utc)
        allowed_dates = [(now + timedelta(days=i)).date() for i in range(8)]
        allowed_map = {d.isoformat(): d for d in allowed_dates}
        chosen_date = allowed_map.get(result_date)
        if not chosen_date:
            await interaction.followup.send(
                "❌ Invalid date. Use the `result_date` autocomplete list (today to next 7 days) and select one of those options."
            )
            return

        selected_start = datetime(
            year=chosen_date.year,
            month=chosen_date.month,
            day=chosen_date.day,
            hour=int(hour),
            minute=int(minute),
            tzinfo=timezone.utc,
        )
        if selected_start < now:
            await interaction.followup.send("❌ Selected date/time is in the past. Please choose a current or future UTC time.")
            return

        settings = await db.get_guild_settings(interaction.guild_id)
        await self.send_backup_logs(interaction.guild, settings, "Schedule change (before reset)")
        await self._announce_current_result(interaction.guild, settings, "before new schedule")
        await db.reset_activity(interaction.guild_id)

        interval_days = max(1, int(settings.get("interval_days", 7) or 7))
        next_result = selected_start + timedelta(days=interval_days)
        await db.update_guild_settings(
            interaction.guild_id,
            {
                "last_reset_time": selected_start,
                "pending_cycle_start": selected_start > now,
                "last_result_time": now,
            },
        )

        await interaction.followup.send(
            "✅ New schedule configured (UTC).\n"
            f"Counting Start: {self._format_discord_time(selected_start)}\n"
            f"Configured Time: **{int(hour):02d}:{int(minute):02d} UTC**\n"
            f"Next Result: {self._format_discord_time(next_result)}\n"
            "Existing result was announced (if channel configured), and this server's activity data was reset."
        )

    @setup_schedule.autocomplete("result_date")
    async def setup_schedule_date_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        now = datetime.now(timezone.utc)
        choices = []
        for day_offset in range(8):
            d = (now + timedelta(days=day_offset)).date()
            value = d.isoformat()
            day_name = d.strftime("%A")
            label = f"{day_name} - {value}"
            if not current or current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label, value=value))
        return choices[:25]

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
        top_count = max(1, int(settings.get("top_count", 3) or 3))
        top_users = await db.get_top_users(interaction.guild_id, top_count)
        all_users = await db.get_all_users(interaction.guild_id)
        total_messages = sum(int(user.get("message_count", 0)) for user in all_users)
        now = datetime.now(timezone.utc)
        last_reset_time = self._normalize_utc(settings.get("last_reset_time"))
        next_result_time = self._next_result_time(settings, now)
        last_result_time = self._normalize_utc(settings.get("last_result_time"))
        configured_time = f"{last_reset_time.hour:02d}:{last_reset_time.minute:02d} UTC" if last_reset_time else "`Not set`"

        top_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, user_data in enumerate(top_users):
            member = interaction.guild.get_member(user_data.get("user_id"))
            display = member.mention if member else f"`{user_data.get('user_id')}`"
            medal = medals[i] if i < 3 else f"#{i + 1}"
            top_lines.append(f"{medal} {display} — {user_data.get('message_count', 0)} messages")
        if not top_lines:
            top_lines = ["No tracked activity yet in this cycle."]

        announcement_channel = interaction.guild.get_channel(settings.get("announcement_channel_id"))
        logs_channel = interaction.guild.get_channel(settings.get("logs_channel_id"))
        reward_role = interaction.guild.get_role(settings.get("reward_role_id"))

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
        embed.add_field(name="Interval Days", value=str(settings.get("interval_days", 7)), inline=True)
        embed.add_field(name="Top Count", value=str(top_count), inline=True)
        embed.add_field(name="Configured Result Time (UTC)", value=configured_time, inline=True)
        embed.add_field(name="Last Result Sent", value=self._format_discord_time(last_result_time), inline=False)
        embed.add_field(name="Upcoming Result", value=self._format_discord_time(next_result_time), inline=False)
        embed.add_field(name="Remaining Until Next Result", value=self._format_remaining(now, next_result_time), inline=False)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=BackupLogsView(self))

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
        embed.add_field(name="`/setup schedule`", value="Start date + 24h UTC time set karo (today se next 7 days options).", inline=False)
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
