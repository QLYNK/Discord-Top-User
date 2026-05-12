import discord
from discord.ext import commands, tasks
from discord import app_commands
import sys
import database as db
import utils
from datetime import datetime, timezone

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
    @app_commands.describe(channel="Target channel for auto-games", role="Role to ping for auto-games", interval_in_minutes="Event interval in minutes")
    async def setup_autogame(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        interval_in_minutes: app_commands.Range[int, 1, 1440],
    ):
        await db.update_guild_settings(
            interaction.guild_id,
            {
                "autogame_channel_id": channel.id,
                "autogame_role_id": role.id,
                "autogame_interval_minutes": int(interval_in_minutes),
            },
        )
        await interaction.response.send_message(
            (
                "✅ Auto-game scheduler updated.\n"
                f"Channel: {channel.mention}\n"
                f"Role: {role.mention}\n"
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
    async def setup_days(self, interaction: discord.Interaction, days: int):
        settings = await db.get_guild_settings(interaction.guild_id)
        await self.send_backup_logs(interaction.guild, settings, f"Interval days changed to {days}")
        await db.update_guild_settings(interaction.guild_id, {"interval_days": days})
        await interaction.response.send_message(f"✅ Leaderboard timer updated to **{days} days**.")

    @setup_group.command(name="top_count", description="Set how many top members get the role (Default: 3)")
    async def setup_top_count(self, interaction: discord.Interaction, count: int):
        await db.update_guild_settings(interaction.guild_id, {"top_count": count})
        await interaction.response.send_message(f"✅ Leaderboard will now reward Top **{count}** active members.")

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
        top_users = await db.get_top_users(interaction.guild_id, 3)
        all_users = await db.get_all_users(interaction.guild_id)
        total_messages = sum(int(user.get("message_count", 0)) for user in all_users)

        top_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, user_data in enumerate(top_users):
            member = interaction.guild.get_member(user_data.get("user_id"))
            display = member.mention if member else f"`{user_data.get('user_id')}`"
            top_lines.append(f"{medals[i]} {display} — {user_data.get('message_count', 0)} messages")
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
        embed.add_field(name="Top 3 Most Active Users", value="\n".join(top_lines), inline=False)
        embed.add_field(name="Total Messages (Current Cycle)", value=str(total_messages), inline=False)
        embed.add_field(name="Announcement Channel", value=announcement_channel.mention if announcement_channel else "`Not set`", inline=True)
        embed.add_field(name="Logs Channel", value=logs_channel.mention if logs_channel else "`Not set`", inline=True)
        embed.add_field(name="Reward Role", value=reward_role.mention if reward_role else "`Not set`", inline=True)
        embed.add_field(name="Interval Days", value=str(settings.get("interval_days", 7)), inline=True)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=self._branding_view())

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
