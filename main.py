import asyncio
import discord
from discord.ext import commands, tasks
import os
import sys
import json # Imports me add kar lena
import secrets
from datetime import datetime, timedelta, timezone
import aiohttp
from dotenv import load_dotenv

# Helpers & Database import
import database as db
import utils
import keep_alive
from telemetry import log_exception, send_activity_log, send_master_log
from utils.branding_view import create_branding_view, install_global_branding_enforcer

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True


async def _dynamic_prefix(bot_instance: commands.Bot, message: discord.Message):
    user_id = getattr(getattr(message, "author", None), "id", 0)
    guild_id = getattr(getattr(message, "guild", None), "id", None)
    prefixes = await db.get_effective_prefixes(user_id=user_id, guild_id=guild_id)
    return commands.when_mentioned_or(*prefixes)(bot_instance, message)


bot = commands.Bot(command_prefix=_dynamic_prefix, intents=intents, help_command=None)
install_global_branding_enforcer()
keep_alive.register_bot(bot)

# Store start time for uptime tracking
# Store start time for uptime tracking
bot.start_time = datetime.now()

# RAM Buffer (6k members ke liye memory store)
message_buffer = {}


def _dashboard_telemetry_bridge(payload: dict):
    async def _send():
        await send_activity_log(
            bot,
            activity_type=payload.get("activity_type", "Dashboard Activity"),
            details=payload.get("details", "Dashboard event recorded."),
            module=payload.get("module", "Web Dashboard"),
            guild=None,
            user=None,
            jump_url=payload.get("path"),
            fields=[
                ("Endpoint", str(payload.get("path", "Unknown")), True),
                ("Method", str(payload.get("method", "Unknown")), True),
                ("Source IP", str(payload.get("ip", "Unknown")), True),
                *list(payload.get("fields", [])),
            ],
        )

    try:
        if bot.loop and bot.loop.is_running():
            asyncio.run_coroutine_threadsafe(_send(), bot.loop)
    except Exception:
        pass


keep_alive.register_telemetry_handler(_dashboard_telemetry_bridge)
KEEPALIVE_URL = "https://deepdey.onrender.com/"

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} | Ready to track!")
    
    # Load Cogs (Setup Commands + Music Engine + Game Engine)
    for extension in (
        "cogs.setup_commands",
        "cogs.music_commands",
        "cogs.game_commands",
        "cogs.utility_commands",
        "cogs.productivity_commands",
        "cogs.proxy",
    ):
        try:
            await bot.load_extension(extension)
            print(f"✅ Loaded extension: {extension}")
        except Exception as e:
            print(f"❌ Error loading {extension}: {e}")
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced!")
    except Exception as e:
        print(f"❌ Error syncing slash commands: {e}")

    # Saare background tasks ek hi baar start karo
    if not leaderboard_loop.is_running():
        leaderboard_loop.start()
    if not update_api_stats.is_running():
        update_api_stats.start()
    if not flush_buffer.is_running():
        flush_buffer.start()
    if not crypto_keepalive.is_running():
        crypto_keepalive.start()

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command):
    qualified_name = command.qualified_name.lower()
    await send_activity_log(
        bot,
        activity_type="Command Usage",
        details=f"Slash command `/{qualified_name}` executed.",
        module="Commands",
        guild=interaction.guild,
        user=interaction.user,
        jump_url=interaction.channel.jump_url if isinstance(interaction.channel, discord.TextChannel) else None,
        fields=[("Command", f"/{qualified_name}", True)],
    )


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    if before.channel is None and after.channel is not None:
        await send_activity_log(
            bot,
            activity_type="Voice Channel Join",
            details=f"User joined voice channel {after.channel.name}.",
            module="Voice",
            guild=member.guild,
            user=member,
            fields=[("Voice Channel", after.channel.name, True)],
        )

@bot.event
async def on_message(message):
    # Bot ke apne messages aur DMs ignore karo
    if message.author.bot or not message.guild:
        return

    g_id = message.guild.id
    u_id = message.author.id

    # RAM mein store karo (Memory Dictionary)
    if g_id not in message_buffer:
        message_buffer[g_id] = {}
        
    # Count badhao
    message_buffer[g_id][u_id] = message_buffer[g_id].get(u_id, 0) + 1
    
    await bot.process_commands(message)


async def _tree_on_error(interaction: discord.Interaction, error: Exception):
    user_message = "An unexpected error occurred while running this command."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(user_message, ephemeral=True)
        else:
            await interaction.response.send_message(user_message, ephemeral=True)
    except Exception:
        pass

    cmd_name = interaction.command.qualified_name if interaction.command else "unknown"
    context = (
        f"Command: /{cmd_name} | User: {interaction.user} ({interaction.user.id}) | "
        f"Guild: {interaction.guild_id} | Channel: {interaction.channel_id}"
    )
    await log_exception(
        bot,
        title="Slash Command Error",
        error=error,
        context=context,
    )


bot.tree.on_error = _tree_on_error

@tasks.loop(minutes=1)
async def update_api_stats():
    uptime_seconds = 0
    if getattr(bot, "start_time", None):
        try:
            uptime_seconds = max(0, int((datetime.now() - bot.start_time).total_seconds()))
        except Exception:
            uptime_seconds = 0
    stats_data = {
        "servers": len(bot.guilds),
        "users": sum(int(g.member_count or 0) for g in bot.guilds),
        "ping": round(bot.latency * 1000),
        "uptime_seconds": uptime_seconds,
    }
    with open('stats.json', 'w') as f:
        json.dump(stats_data, f)


@tasks.loop(seconds=300)
async def crypto_keepalive():
    interval = secrets.choice(range(300, 601))
    crypto_keepalive.change_interval(seconds=interval)

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(KEEPALIVE_URL) as response:
                if 200 <= response.status < 400:
                    await send_master_log(
                        bot,
                        title="Crypto Keep-Alive Ping",
                        description="Successful self-ping to keep Render host awake.",
                        fields=[
                            ("Target", KEEPALIVE_URL, False),
                            ("HTTP Status", str(response.status), True),
                            ("Sleep Interval", f"{interval}s", True),
                        ],
                    )
    except Exception:
        pass


@crypto_keepalive.before_loop
async def before_crypto_keepalive():
    await bot.wait_until_ready()
    crypto_keepalive.change_interval(seconds=secrets.choice(range(300, 601)))

@tasks.loop(minutes=2) # Har 2 minute me RAM se DB me bhejega
async def flush_buffer():
    global message_buffer
    if not message_buffer: 
        return

    # Buffer ki copy banao aur main buffer clear karo
    buffer_snapshot = message_buffer.copy()
    message_buffer.clear()

    # Bulk update DB
    try:
        await db.bulk_update_activity(buffer_snapshot)
        print(f"✅ Flushed {len(buffer_snapshot)} guilds' data to MongoDB.")
    except Exception as e:
        print(f"❌ Error in bulk DB update: {e}")
        # Agar error aaye, toh data wapas ram me bacha lo
        for g_id, users in buffer_snapshot.items():
            if g_id not in message_buffer:
                message_buffer[g_id] = {}
            for u_id, count in users.items():
                message_buffer[g_id][u_id] = message_buffer[g_id].get(u_id, 0) + count

@tasks.loop(minutes=1)
async def leaderboard_loop():
    now = datetime.now(timezone.utc)
    
    # Saare guilds ki settings fetch karo
    async for settings in db.settings_col.find({}):
        guild_id = settings.get("guild_id")
        guild = bot.get_guild(guild_id)
        if not guild: continue

        interval_days = max(1, int(settings.get("interval_days", 7) or 7))
        last_reset = settings.get("last_reset_time")
        pending_cycle_start = bool(settings.get("pending_cycle_start", False))

        if last_reset and last_reset.tzinfo is None:
            last_reset = last_reset.replace(tzinfo=timezone.utc)

        # Agar last_reset None hai, toh abhi ka time set kardo (first run)
        if not last_reset:
            await db.settings_col.update_one({"guild_id": guild_id}, {"$set": {"last_reset_time": now, "pending_cycle_start": False}})
            continue

        # Future start ke liye pending flag set ho toh exact start pe cycle reset karo
        if pending_cycle_start:
            if now >= last_reset:
                await db.reset_activity(guild_id)
                await db.settings_col.update_one(
                    {"guild_id": guild_id},
                    {"$set": {"pending_cycle_start": False, "last_reset_time": last_reset}},
                )
            else:
                continue
        elif now < last_reset:
            continue

        # Check agar interval khatam ho gaya
        due_time = last_reset + timedelta(days=interval_days)
        if now >= due_time:
            await process_leaderboard(guild, settings)
            # Update last reset time
            await db.settings_col.update_one(
                {"guild_id": guild_id},
                {"$set": {"last_reset_time": due_time, "last_result_time": now, "pending_cycle_start": False}},
            )

async def process_leaderboard(guild: discord.Guild, settings: dict):
    """Automatically logs bhejta hai, role deta hai aur list post karta hai."""
    announcement_channel = guild.get_channel(settings.get("announcement_channel_id"))
    logs_channel = guild.get_channel(settings.get("logs_channel_id"))
    role = guild.get_role(settings.get("reward_role_id"))
    top_count = settings.get("top_count", 3)

    if not announcement_channel: return

    # 1. Pehle Logs Bhejo (Agar configured hai)
    all_users_data = await db.get_all_users(guild.id)
    if logs_channel:
        if all_users_data:
            json_file = utils.generate_json_file(all_users_data)
            guild_icon = guild.icon.url if guild.icon else ""
            html_file = utils.generate_html_file(all_users_data, guild.name, guild_icon)
            await logs_channel.send(f"📄 **Automatic Cycle Reset Logs**\nData before leaderboard wipe:", files=[json_file, html_file])
        else:
            await logs_channel.send("📄 **Automatic Cycle Reset Logs**\nNo activity data found for this cycle.")

    # 2. Top N Users Fetch Karo
    top_users = await db.get_top_users(guild.id, top_count)
    
    if not top_users:
        await announcement_channel.send("Is period mein kisi ne chat nahi ki! Data reset kar raha hu.")
        await db.reset_activity(guild.id)
        return

    # 3. Purane role members se role hatao
    if role:
        for member in role.members:
            try:
                await member.remove_roles(role)
            except discord.Forbidden:
                pass

    # 4. Embed Banaiye
    embed = discord.Embed(title="🏆 Server Activity Leaderboard", color=0x5865F2)
    description = ""
    
    medals = ["🥇", "🥈", "🥉"]
    
    for rank, user_data in enumerate(top_users, start=1):
        member = guild.get_member(user_data["user_id"])
        medal = medals[rank-1] if rank <= 3 else f"#{rank}"
        
        if member:
            description += f"{medal} **{member.mention}** — {user_data['message_count']} messages\n"
            # Naye winners ko role do
            if role:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    pass
        else:
            description += f"{medal} **Left User ({user_data['user_id']})** — {user_data['message_count']} messages\n"

    embed.description = description + "\n\nThank you to everyone who participated! If your name isn't here, don't be sad—keep chatting and try again next time! ❤️"
    embed.set_footer(text="an app by deep", icon_url=bot.user.avatar.url if bot.user.avatar else None)

    # 5. Buttons (Action Row)
    view = create_branding_view()

    # 6. Final Message bhejna
    role_mention = role.mention if role else "Top Members"
    content = f"{role_mention} Here are the top {top_count} most active members for this period!"
    
    await announcement_channel.send(content=content, embed=embed, view=view)

    # 7. Data Wipe for the new cycle
    await db.reset_activity(guild.id)

# Start bot + Keep Alive
if __name__ == "__main__":
    if os.getenv("SEPARATE_WEBSITE_PROCESS", "0") != "1":
        keep_alive.keep_alive()  # Starts the Flask server
    bot.run(TOKEN)
