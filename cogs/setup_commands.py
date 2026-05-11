import discord
from discord.ext import commands
from discord import app_commands
import sys
import database as db
import utils

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
        embed.add_field(name="`/setup role`", value="Reward role assign karo with Test Buttons.", inline=False)
        embed.add_field(name="`/setup days` & `/setup top_count`", value="Timer (days) aur kitne logo ko role dena hai (Top N) configure karo.", inline=False)
        embed.add_field(name="`/setup reset` & `/setup hard_reset`", value="Current messages reset karne ya pura data udane ke liye.", inline=False)
        embed.add_field(name="`/setup ping` & `/setup restart`", value="Uptime check karne aur bot restart karne ke liye.", inline=False)
        
        embed.set_footer(text="an app by deep", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
        
        # Action Row / Button
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Follow owner", url="https://instagram.com/deepdey.official", style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Developer Site", url="https://deepdey.vercel.app/", style=discord.ButtonStyle.link))
        
        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(SetupCommands(bot))