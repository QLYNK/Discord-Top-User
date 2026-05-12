import asyncio
import discord
from discord.ext import commands, tasks
import os
import sys
import json # Imports me add kar lena
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Helpers & Database import
import database as db
import utils
import keep_alive
from telemetry import log_exception, send_activity_log

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

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

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} | Ready to track!")
    
    # Load Cogs (Setup Commands + Music Engine + Game Engine)
    for extension in ("cogs.setup_commands", "cogs.music_commands", "cogs.game_commands", "cogs.utility_commands", "cogs.productivity_commands"):
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
    stats_data = {
        "servers": len(bot.guilds),
        "ping": round(bot.latency * 1000)
    }
    with open('stats.json', 'w') as f:
        json.dump(stats_data, f)

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

@tasks.loop(hours=1) # Missing timer fixed
async def leaderboard_loop():


    now = datetime.utcnow()
    
    # Saare guilds ki settings fetch karo
    async for settings in db.settings_col.find({}):
        guild_id = settings.get("guild_id")
        guild = bot.get_guild(guild_id)
        if not guild: continue

        interval_days = settings.get("interval_days", 7)
        last_reset = settings.get("last_reset_time")

        # Agar last_reset None hai, toh abhi ka time set kardo (first run)
        if not last_reset:
            await db.settings_col.update_one({"guild_id": guild_id}, {"$set": {"last_reset_time": now}})
            continue

        # Check agar interval khatam ho gaya
        if now >= last_reset + timedelta(days=interval_days):
            await process_leaderboard(guild, settings)
            # Update last reset time
            await db.settings_col.update_one({"guild_id": guild_id}, {"$set": {"last_reset_time": now}})

async def process_leaderboard(guild: discord.Guild, settings: dict):
    """Automatically logs bhejta hai, role deta hai aur list post karta hai."""
    announcement_channel = guild.get_channel(settings.get("announcement_channel_id"))
    logs_channel = guild.get_channel(settings.get("logs_channel_id"))
    role = guild.get_role(settings.get("reward_role_id"))
    top_count = settings.get("top_count", 3)

    if not announcement_channel: return

    # 1. Pehle Logs Bhejo (Agar configured hai)
    all_users_data = await db.get_all_users(guild.id)
    if logs_channel and all_users_data:
        json_file = utils.generate_json_file(all_users_data)
        guild_icon = guild.icon.url if guild.icon else ""
        html_file = utils.generate_html_file(all_users_data, guild.name, guild_icon)
        await logs_channel.send(f"📄 **Automatic Cycle Reset Logs**\nData before leaderboard wipe:", files=[json_file, html_file])

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
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Follow owner", url="https://instagram.com/deepdey.official", style=discord.ButtonStyle.link))
    view.add_item(discord.ui.Button(label="Developer Site", url="https://deepdey.vercel.app/", style=discord.ButtonStyle.link))

    # 6. Final Message bhejna
    role_mention = role.mention if role else "Top Members"
    content = f"{role_mention} Here are the top {top_count} most active members for this period!\n\n@everyone"
    
    await announcement_channel.send(content=content, embed=embed, view=view)

    # 7. Data Wipe for the new cycle
    await db.reset_activity(guild.id)

# Start bot + Keep Alive
if __name__ == "__main__":
    keep_alive.keep_alive()  # Starts the Flask server
    bot.run(TOKEN)
