import asyncio
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import discord
from bson import ObjectId
from discord import app_commands
from discord.ext import commands, tasks

from audio_manager import (
    MAX_CACHE_BYTES,
    build_slug,
    download_and_convert,
    get_dir_size,
    purge_lru_cache,
    smart_download,
    upload_to_cdn,
)
from database import client as _mongo_client
from telemetry import log_exception, send_master_log
from utils.audio_manager import cleanup_path, convert_to_96k_mp3, extract_from_url

_music_db = _mongo_client["LeaderboardBotDB"]
music_col = _music_db["MusicTracks"]
music_session_col = _music_db["MusicSessions"]

APP_LINK = "https://deepdey.vercel.app/"
INSTA_LINK = "https://instagram.com/deepdey.official"
DEFAULT_ARTWORK = "https://deydeep-static-files.hf.space/f/ncs"
PASSWORD = os.getenv("PASSWORD")
SPACE_PASSWORD = os.getenv("SPACE_PASSWORD")


def _coerce_track_query(raw_id: str) -> dict:
    try:
        return {"$or": [{"_id": ObjectId(raw_id)}, {"_id": raw_id}]}
    except Exception:
        return {"_id": raw_id}


def _base_view() -> discord.ui.View:
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label="an app by deep", url=APP_LINK, style=discord.ButtonStyle.link))
    v.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))
    return v


class GuildMusicState:
    __slots__ = (
        "queue",
        "current",
        "voice_client",
        "channel_id",
        "is_247",
        "paused",
        "start_time",
        "resume_offset",
        "next_track",
        "_playback_task",
    )

    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.channel_id: int | None = None
        self.is_247: bool = False
        self.paused: bool = False
        self.start_time: float | None = None
        self.resume_offset: int = 0
        self.next_track: dict | None = None
        self._playback_task: asyncio.Task | None = None


class _TwoFourSevenView(discord.ui.View):
    def __init__(self, cog: "MusicCommands") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(discord.ui.Button(label="an app by deep", url=APP_LINK, style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))

    @discord.ui.button(label="🟢 24/7 ON", style=discord.ButtonStyle.success, custom_id="music_247_on")
    async def enable_247(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(interaction.guild_id)
        state.is_247 = True
        await self.cog.persist_state(interaction.guild_id)
        await interaction.response.send_message("✅ 24/7 mode enabled.", ephemeral=True)

    @discord.ui.button(label="🔴 24/7 OFF", style=discord.ButtonStyle.danger, custom_id="music_247_off")
    async def disable_247(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(interaction.guild_id)
        state.is_247 = False
        await self.cog.persist_state(interaction.guild_id)
        await interaction.response.send_message("✅ 24/7 mode disabled.", ephemeral=True)


class _LiveDashboardView(discord.ui.View):
    def __init__(self, cog: "MusicCommands", guild_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.add_item(discord.ui.Button(label="an app by deep", url=APP_LINK, style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, custom_id="live_playpause")
    async def play_pause(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(self.guild_id)
        vc = state.voice_client
        if not vc:
            await interaction.response.send_message("❌ Bot is not in a voice channel.", ephemeral=True)
            return
        if vc.is_playing():
            vc.pause()
            state.paused = True
            await self.cog.persist_state(self.guild_id)
            await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            state.paused = False
            state.start_time = time.time() - max(0, state.resume_offset)
            state.resume_offset = 0
            await self.cog.persist_state(self.guild_id)
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="live_skip")
    async def skip_track(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(self.guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()
        await interaction.response.defer()


class _MusicAddModal(discord.ui.Modal, title="Add Music Track"):
    link = discord.ui.TextInput(label="Link", placeholder="Enter audio/video URL", required=True, max_length=500)
    track_title = discord.ui.TextInput(label="Title", placeholder="Optional custom title", required=False, max_length=120)
    artwork = discord.ui.TextInput(label="Artwork", placeholder="Optional artwork URL", required=False, max_length=500)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "MusicCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not PASSWORD:
            await interaction.response.send_message("❌ PASSWORD is not configured.", ephemeral=True)
            return
        if not SPACE_PASSWORD:
            await interaction.response.send_message("❌ SPACE_PASSWORD is not configured.", ephemeral=True)
            return
        if not secrets.compare_digest(str(self.password), PASSWORD):
            await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        source_file = None
        final_mp3 = None
        try:
            source_file, extracted_title, extracted_artwork = await extract_from_url(str(self.link).strip())
            title = str(self.track_title).strip() or extracted_title or "Untitled Track"
            artwork_url = str(self.artwork).strip() or extracted_artwork or DEFAULT_ARTWORK
            final_mp3 = await convert_to_96k_mp3(source_file, output_name=secrets.token_hex(8))
            cdn_url = await upload_to_cdn(final_mp3, SPACE_PASSWORD, title)
            if not cdn_url:
                raise RuntimeError("CDN upload failed")

            payload = {
                "title": title,
                "file_url": cdn_url,
                "artwork_url": artwork_url,
                "created_at": datetime.now(timezone.utc),
            }
            inserted = await music_col.insert_one(payload)
            await interaction.followup.send(
                f"✅ Track added successfully.\n**Title:** {title}\n**ID:** `{inserted.inserted_id}`",
                ephemeral=True,
            )
            await send_master_log(
                self.cog.bot,
                "Music Track Added",
                f"{interaction.user.mention} added a new music track.",
                fields=[
                    ("Title", title, False),
                    ("Track ID", str(inserted.inserted_id), True),
                    ("Guild", str(interaction.guild_id), True),
                ],
            )
        except Exception as exc:
            await interaction.followup.send("❌ Failed to process and upload the track.", ephemeral=True)
            await log_exception(
                self.cog.bot,
                title="Music Add Failed",
                error=exc,
                context=f"User {interaction.user.id} in guild {interaction.guild_id}",
            )
        finally:
            if source_file:
                cleanup_path(source_file)
            if final_mp3:
                cleanup_path(final_mp3)


class MusicCommands(commands.Cog):
    music_group = app_commands.Group(name="music", description="Music & Audio Engine 🎵")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}
        self._live_dashboards: dict[int, dict] = {}
        self._restored_once = False
        self._cache_monitor.start()
        self._state_sync.start()

    def cog_unload(self) -> None:
        self._cache_monitor.cancel()
        self._state_sync.cancel()
        for entry in self._live_dashboards.values():
            t = entry.get("task")
            if t and not t.done():
                t.cancel()

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    @tasks.loop(minutes=5)
    async def _cache_monitor(self) -> None:
        if get_dir_size() > MAX_CACHE_BYTES:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, purge_lru_cache)

    @tasks.loop(seconds=15)
    async def _state_sync(self) -> None:
        for guild_id in list(self._states.keys()):
            await self.persist_state(guild_id)

    async def persist_state(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        vc = state.voice_client
        if vc and vc.is_connected() and vc.channel:
            state.channel_id = vc.channel.id

        elapsed = 0
        if state.current and state.start_time:
            elapsed = max(0, int(time.time() - state.start_time))

        payload = {
            "guild_id": guild_id,
            "voice_channel_id": state.channel_id,
            "is_247": state.is_247,
            "queue": state.queue,
            "current": state.current,
            "resume_offset": elapsed,
            "updated_at": datetime.now(timezone.utc),
            "active": bool(state.channel_id and (state.current or state.queue)),
        }
        await music_session_col.update_one({"guild_id": guild_id}, {"$set": payload}, upsert=True)

    async def restore_sessions(self) -> None:
        async for doc in music_session_col.find({"active": True}):
            guild_id = doc.get("guild_id")
            channel_id = doc.get("voice_channel_id")
            if not guild_id or not channel_id:
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.VoiceChannel):
                continue

            state = self.get_state(guild_id)
            state.is_247 = bool(doc.get("is_247", False))
            state.queue = list(doc.get("queue", []))
            state.current = doc.get("current")
            state.next_track = doc.get("current")
            state.resume_offset = int(doc.get("resume_offset", 0))
            state.channel_id = channel_id

            try:
                state.voice_client = await channel.connect(reconnect=True)
            except Exception:
                continue

            if state._playback_task and not state._playback_task.done():
                state._playback_task.cancel()
            state._playback_task = asyncio.create_task(self._playback_loop(guild_id))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._restored_once:
            return
        self._restored_once = True
        await self.restore_sessions()

    @staticmethod
    def _seek_bar(elapsed_secs: int) -> str:
        bar_len = 20
        ticks = min(elapsed_secs // 10, bar_len)
        bar = "▓" * ticks + "░" * (bar_len - ticks)
        m, s = divmod(elapsed_secs, 60)
        return f"`{m}:{s:02d}` [{bar}]"

    def _nowplaying_embed(self, state: GuildMusicState) -> discord.Embed:
        track = state.current or {}
        title = track.get("title", "Nothing playing")
        artwork = track.get("artwork_url") or DEFAULT_ARTWORK
        if state.paused:
            elapsed = state.resume_offset
        else:
            elapsed = int(time.time() - state.start_time) if state.start_time else state.resume_offset

        embed = discord.Embed(title="🎵 Now Playing", description=f"**{title}**", color=0x1DB954)
        embed.set_thumbnail(url=artwork)
        embed.add_field(name="Progress", value=self._seek_bar(elapsed), inline=False)
        embed.add_field(name="Status", value="⏸️ Paused" if state.paused else "▶️ Playing", inline=True)
        embed.set_footer(text="an app by deep")
        return embed

    @music_group.command(name="help", description="Show all music commands")
    async def music_help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="🎵 Music Engine — Command Guide", color=0x1DB954)
        for name, value in [
            ("`/music select <search_query>`", "Select and instantly stream a saved track."),
            ("`/music start`", "Start loop playback from saved tracks."),
            ("`/music temp <link>`", "Play a temporary 96kbps track and auto-delete it."),
            ("`/music pause`", "Pause playback."),
            ("`/music resume`", "Resume playback."),
            ("`/music leave`", "Leave VC and clear queue."),
            ("`/music live`", "Open live dashboard."),
            ("`/music nowplaying`", "Show static current-track embed."),
            ("`/music 247`", "Toggle 24/7 mode."),
        ]:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    @music_group.command(name="select", description="Select a saved track and stream it instantly")
    async def music_select(self, interaction: discord.Interaction, search_query: str) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Join a voice channel first.", ephemeral=True)
            return

        track_doc = await music_col.find_one(_coerce_track_query(search_query))
        if not track_doc:
            await interaction.response.send_message("❌ Track not found.", ephemeral=True)
            return

        track = {
            "title": track_doc.get("title") or track_doc.get("name") or "Untitled Track",
            "file_url": track_doc.get("file_url") or track_doc.get("url") or "",
            "artwork_url": track_doc.get("artwork_url") or DEFAULT_ARTWORK,
        }
        if not track["file_url"]:
            await interaction.response.send_message("❌ Track URL missing in database.", ephemeral=True)
            return

        state = self.get_state(interaction.guild_id)
        channel = interaction.user.voice.channel
        if state.voice_client and state.voice_client.is_connected():
            await state.voice_client.move_to(channel)
        else:
            state.voice_client = await channel.connect()
        state.channel_id = channel.id

        state.queue.append(track)
        state.next_track = track
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            state.voice_client.stop()

        if not state._playback_task or state._playback_task.done():
            state._playback_task = asyncio.create_task(self._playback_loop(interaction.guild_id))

        await self.persist_state(interaction.guild_id)
        embed = discord.Embed(title="▶️ Streaming Selected Track", description=f"Queued: **{track['title']}**", color=0x1DB954)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    @music_group.command(name="add", description="Add a track using secure modal upload flow")
    async def music_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_MusicAddModal(self))

    @music_select.autocomplete("search_query")
    async def music_select_autocomplete(self, interaction: discord.Interaction, current: str):
        query = (current or "").strip()
        if not query:
            cursor = music_col.find({}, {"title": 1, "name": 1}).sort("_id", -1).limit(25)
        else:
            cursor = music_col.find(
                {"$or": [{"title": {"$regex": query, "$options": "i"}}, {"name": {"$regex": query, "$options": "i"}}]},
                {"title": 1, "name": 1},
            ).limit(25)

        docs = await cursor.to_list(length=25)
        return [
            app_commands.Choice(name=(doc.get("title") or doc.get("name") or "Untitled")[:100], value=str(doc["_id"]))
            for doc in docs
        ]

    @music_group.command(name="join", description="Join your voice channel")
    async def music_join(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You must be in a voice channel first.", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        state = self.get_state(interaction.guild_id)
        if state.voice_client and state.voice_client.is_connected():
            await state.voice_client.move_to(channel)
        else:
            state.voice_client = await channel.connect()
        state.channel_id = channel.id
        await self.persist_state(interaction.guild_id)

        embed = discord.Embed(title="✅ Joined Voice Channel", description=f"Connected to **{channel.name}**.", color=0x1DB954)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    @music_group.command(name="leave", description="Leave VC and clear the queue")
    async def music_leave(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        if state._playback_task and not state._playback_task.done():
            state._playback_task.cancel()
        if state.voice_client:
            state.voice_client.stop()
            await state.voice_client.disconnect()
            state.voice_client = None
        state.queue.clear()
        state.current = None
        state.channel_id = None
        await music_session_col.update_one({"guild_id": interaction.guild_id}, {"$set": {"active": False}})

        embed = discord.Embed(title="👋 Left Voice Channel", description="Disconnected and queue cleared.", color=0xFF4444)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    @music_group.command(name="pause", description="Pause playback")
    async def music_pause(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        vc = state.voice_client
        if vc and vc.is_playing():
            vc.pause()
            state.paused = True
            state.resume_offset = int(time.time() - state.start_time) if state.start_time else 0
            await self.persist_state(interaction.guild_id)
            embed = discord.Embed(title="⏸️ Paused", color=0xFFA500)
            embed.set_footer(text="an app by deep")
            await interaction.response.send_message(embed=embed, view=_base_view())
        else:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

    @music_group.command(name="resume", description="Resume playback")
    async def music_resume(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        vc = state.voice_client
        if vc and vc.is_paused():
            vc.resume()
            state.paused = False
            state.start_time = time.time() - max(0, state.resume_offset)
            state.resume_offset = 0
            await self.persist_state(interaction.guild_id)
            embed = discord.Embed(title="▶️ Resumed", color=0x1DB954)
            embed.set_footer(text="an app by deep")
            await interaction.response.send_message(embed=embed, view=_base_view())
        else:
            await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)

    @music_group.command(name="start", description="Start playing the saved queue")
    async def music_start(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            await interaction.response.send_message("❌ Use `/music join` first.", ephemeral=True)
            return

        tracks = await music_col.find({}, {"title": 1, "name": 1, "file_url": 1, "url": 1, "artwork_url": 1}).to_list(length=None)
        queue = []
        for t in tracks:
            file_url = t.get("file_url") or t.get("url")
            if not file_url:
                continue
            queue.append(
                {
                    "title": t.get("title") or t.get("name") or "Untitled Track",
                    "file_url": file_url,
                    "artwork_url": t.get("artwork_url") or DEFAULT_ARTWORK,
                }
            )

        if not queue:
            await interaction.response.send_message("❌ No playable tracks found in DB.", ephemeral=True)
            return

        state.queue = queue
        if state._playback_task and not state._playback_task.done():
            state._playback_task.cancel()
        state._playback_task = asyncio.create_task(self._playback_loop(interaction.guild_id))
        await self.persist_state(interaction.guild_id)

        embed = discord.Embed(title="▶️ Starting Queue", description=f"Loaded **{len(state.queue)}** tracks.", color=0x1DB954)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    async def _playback_loop(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        try:
            while state.voice_client and state.voice_client.is_connected() and (state.queue or state.next_track):
                track = state.next_track
                if not track:
                    if not state.queue:
                        break
                    track = secrets.choice(state.queue)
                state.next_track = None
                state.current = None
                state.paused = False
                await self.persist_state(guild_id)

                mp3_path = await smart_download(track["file_url"])
                if not mp3_path:
                    print(f"[Music] Failed to download/prepare track: {track.get('title', 'unknown')}")
                    await asyncio.sleep(1)
                    continue

                before_parts = [
                    "-reconnect 1",
                    "-reconnect_streamed 1",
                    "-reconnect_delay_max 5",
                ]
                if state.resume_offset > 0:
                    before_parts.insert(0, f"-ss {state.resume_offset}")
                before_options = " ".join(before_parts)

                try:
                    source = discord.FFmpegPCMAudio(
                        str(mp3_path),
                        before_options=before_options,
                        options="-vn",
                    )
                    state.voice_client.play(source)
                    state.current = track
                    state.start_time = time.time()
                    state.resume_offset = 0
                    await self.persist_state(guild_id)
                except Exception as exc:
                    print(f"[Music] Playback error for {track.get('title', 'unknown')}: {type(exc).__name__}")
                    await asyncio.sleep(1)
                    continue

                while state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
                    await asyncio.sleep(1)
                    if not state.is_247 and state.voice_client.channel:
                        non_bot_members = [m for m in state.voice_client.channel.members if not m.bot]
                        if not non_bot_members:
                            state.voice_client.stop()
                            await state.voice_client.disconnect()
                            state.voice_client = None
                            state.current = None
                            await self.persist_state(guild_id)
                            return

                if not state.voice_client or not state.voice_client.is_connected():
                    break
        except asyncio.CancelledError:
            pass
        finally:
            state.current = None
            await self.persist_state(guild_id)

    @music_group.command(name="temp", description="Play temporary link and auto-delete local file")
    async def music_temp(self, interaction: discord.Interaction, link: str) -> None:
        state = self.get_state(interaction.guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            await interaction.response.send_message("❌ Use `/music join` first.", ephemeral=True)
            return

        await interaction.response.defer()
        mp3_path = await download_and_convert(link)
        if not mp3_path:
            await interaction.followup.send("❌ Failed to process the temporary track.", ephemeral=True)
            return

        embed = discord.Embed(title="🎵 Playing Temp Track", description="Temporary track started. File will be deleted after playback.", color=0x1DB954)
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_base_view())

        asyncio.create_task(self._play_temp(interaction.guild_id, mp3_path))

    async def _play_temp(self, guild_id: int, mp3_path: Path) -> None:
        state = self.get_state(guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()
        if not state.voice_client:
            return

        try:
            source = discord.FFmpegPCMAudio(str(mp3_path))
            state.voice_client.play(source)
            while state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
                await asyncio.sleep(1)
        finally:
            try:
                if mp3_path.exists():
                    mp3_path.unlink()
            except Exception:
                pass

    @music_group.command(name="247", description="Toggle 24/7 mode")
    async def music_247(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        status_label = "🟢 ON" if state.is_247 else "🔴 OFF"
        embed = discord.Embed(title="⚙️ 24/7 Mode", description=f"Current status: **{status_label}**", color=0x5865F2)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_TwoFourSevenView(self))

    @music_group.command(name="nowplaying", description="Show current track info (static)")
    async def music_nowplaying(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        if not state.current:
            await interaction.response.send_message("❌ Nothing is playing right now.", ephemeral=True)
            return
        await interaction.response.send_message(embed=self._nowplaying_embed(state), view=_base_view())

    @music_group.command(name="live", description="Open the live playback dashboard")
    async def music_live(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)

        existing = self._live_dashboards.get(guild_id)
        if existing:
            old_task = existing.get("task")
            if old_task and not old_task.done():
                old_task.cancel()

        live_view = _LiveDashboardView(self, guild_id)
        await interaction.response.send_message(embed=self._nowplaying_embed(state), view=live_view)
        message = await interaction.original_response()

        task = asyncio.create_task(self._live_updater(guild_id, message, live_view))
        self._live_dashboards[guild_id] = {"message": message, "task": task}

    async def _live_updater(self, guild_id: int, message: discord.Message, view: discord.ui.View) -> None:
        try:
            while True:
                await asyncio.sleep(secrets.randbelow(16) + 5)
                state = self.get_state(guild_id)
                try:
                    await message.edit(embed=self._nowplaying_embed(state), view=view)
                except discord.NotFound:
                    break
                except discord.HTTPException:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self._live_dashboards.pop(guild_id, None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCommands(bot))
