from __future__ import annotations

import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta
from zoneinfo import ZoneInfo
import asyncio

import database as db

DUR_MAP = {
    "24h": 1,
    "7days": 7,
    "1month": 30,
    "6month": 182,
    "1year": 365,
    "lifetime": None,
}

class ServerCommands(commands.Cog):
    """Server-level settings: prefix, timezone, music toggle, NP list, and help/overview."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Slash: set-prefix
    @app_commands.command(name="set-prefix", description="Set server prefix (admin)")
    @app_commands.describe(prefix="New command prefix (single character suggested)")
    async def set_prefix(self, interaction: discord.Interaction, prefix: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        await db.update_ticket_config(interaction.guild.id, {"prefix": prefix})
        await interaction.response.send_message(f"✅ Prefix updated to `{prefix}`.", ephemeral=True)

    # text wrapper
    @commands.command(name="setprefix")
    @commands.has_guild_permissions(administrator=True)
    async def setprefix_text(self, ctx: commands.Context, prefix: str):
        await db.update_ticket_config(ctx.guild.id, {"prefix": prefix})
        await ctx.reply(f"✅ Prefix updated to `{prefix}`")

    @app_commands.command(name="set-timezone", description="Set server timezone (admin)")
    @app_commands.describe(timezone="Timezone name (e.g. Asia/Kolkata)")
    async def set_timezone(self, interaction: discord.Interaction, timezone: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        try:
            ZoneInfo(timezone)
        except Exception:
            await interaction.response.send_message("❌ Invalid timezone name.", ephemeral=True)
            return
        await db.update_ticket_config(interaction.guild.id, {"timezone": timezone})
        await interaction.response.send_message(f"✅ Timezone set to `{timezone}`.", ephemeral=True)

    @commands.command(name="settimezone")
    @commands.has_guild_permissions(administrator=True)
    async def settimezone_text(self, ctx: commands.Context, timezone: str):
        try:
            ZoneInfo(timezone)
        except Exception:
            await ctx.reply("❌ Invalid timezone name.")
            return
        await db.update_ticket_config(ctx.guild.id, {"timezone": timezone})
        await ctx.reply(f"✅ Timezone set to `{timezone}`")

    @app_commands.command(name="toggle-music", description="Enable or disable music features (admin)")
    @app_commands.describe(enable="Set to true to enable music features")
    async def toggle_music(self, interaction: discord.Interaction, enable: bool):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        await db.update_ticket_config(interaction.guild.id, {"music_enabled": bool(enable)})
        state = "enabled" if enable else "disabled"
        await interaction.response.send_message(f"✅ Music features {state}.", ephemeral=True)

    @commands.command(name="togglemusic")
    @commands.has_guild_permissions(administrator=True)
    async def togglemusic_text(self, ctx: commands.Context, enable: bool):
        await db.update_ticket_config(ctx.guild.id, {"music_enabled": bool(enable)})
        await ctx.reply(f"✅ Music features {'enabled' if enable else 'disabled'}")

    @app_commands.command(name="ig-set-count", description="Set manual Instagram follower count for a voice counter (admin)")
    @app_commands.describe(channel_id="Voice channel id for the counter", count="Numeric follower count")
    async def ig_set_count(self, interaction: discord.Interaction, channel_id: int, count: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        cfg = await db.get_ticket_config(interaction.guild.id)
        vcs = list(cfg.get("voice_counters") or [])
        updated = False
        for vc in vcs:
            if vc.get("channel_id") == channel_id:
                vc["instagram_manual"] = int(count)
                updated = True
        if updated:
            await db.update_ticket_config(interaction.guild.id, {"voice_counters": vcs})
            await interaction.response.send_message("✅ Instagram manual count updated.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Voice counter not found.", ephemeral=True)

    @commands.command(name="igsetcount")
    @commands.has_guild_permissions(administrator=True)
    async def igsetcount_text(self, ctx: commands.Context, channel_id: int, count: int):
        cfg = await db.get_ticket_config(ctx.guild.id)
        vcs = list(cfg.get("voice_counters") or [])
        updated = False
        for vc in vcs:
            if vc.get("channel_id") == channel_id:
                vc["instagram_manual"] = int(count)
                updated = True
        if updated:
            await db.update_ticket_config(ctx.guild.id, {"voice_counters": vcs})
            await ctx.reply("✅ Instagram manual count updated.")
        else:
            await ctx.reply("❌ Voice counter not found.")

    # NP add/delete
    @app_commands.command(name="np-add", description="Add a no-prefix allowed user for a duration (admin)")
    @app_commands.describe(user="User to allow without prefix", duration="Duration key (24h,7days,1month,6month,1year,lifetime)")
    async def np_add(self, interaction: discord.Interaction, user: discord.Member, duration: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        days = DUR_MAP.get(duration)
        expires = None
        if days is not None:
            expires = (await asyncio.to_thread(__import__('datetime').datetime.utcnow) + timedelta(days=days)).isoformat()
        cfg = await db.get_ticket_config(interaction.guild.id)
        np_list = list(cfg.get("np_users") or [])
        np_list = [p for p in np_list if p.get("user_id") != user.id]
        np_list.append({"user_id": user.id, "expires_at": expires})
        await db.update_ticket_config(interaction.guild.id, {"np_users": np_list})
        await interaction.response.send_message(f"✅ {user.mention} allowed no-prefix for {duration}.", ephemeral=True)

    @app_commands.command(name="np-delete", description="Remove a no-prefix allowance for a user (admin)")
    @app_commands.describe(user="User to remove")
    async def np_delete(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        cfg = await db.get_ticket_config(interaction.guild.id)
        np_list = [p for p in list(cfg.get("np_users") or []) if p.get("user_id") != user.id]
        await db.update_ticket_config(interaction.guild.id, {"np_users": np_list})
        await interaction.response.send_message(f"✅ Removed no-prefix allowance for {user.mention}.", ephemeral=True)

    @commands.command(name="npadd")
    @commands.has_guild_permissions(administrator=True)
    async def npadd_text(self, ctx: commands.Context, user: discord.Member, duration: str = "24h"):
        await ctx.defer()
        await self.np_add(ctx, user, duration)

    @commands.command(name="npdelete")
    @commands.has_guild_permissions(administrator=True)
    async def npdelete_text(self, ctx: commands.Context, user: discord.Member):
        await ctx.defer()
        await self.np_delete(ctx, user)

    # Simple global help
    @app_commands.command(name="help-all", description="Show global help and command categories")
    async def help_all(self, interaction: discord.Interaction):
        lines = [
            "Commands categories:",
            "• Tickets — /ticket ... and prefix-friendly equivalents",
            "• Voice counters — /voice create, /voice remove",
            "• NP (no-prefix) — /np-add, /np-delete",
            "• Server settings — /set-prefix, /set-timezone, /toggle-music, /ig-set-count",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerCommands(bot))
