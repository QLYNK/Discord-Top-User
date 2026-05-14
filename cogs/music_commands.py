import asyncio
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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
import database as db
from telemetry import log_exception, send_activity_log, send_guild_module_log, send_master_log
from utils.audio_manager import cleanup_path, convert_to_96k_mp3, extract_from_url

_music_db = _mongo_client["LeaderboardBotDB"]
music_col = _music_db["MusicTracks"]
music_states_col = _music_db["music_states"]

APP_LINK = "https://deepdey.vercel.app/"
INSTA_LINK = "https://deepdey.vercel.app/insta"
DEFAULT_ARTWORK = "https://deydeep-static-files.hf.space/f/ncs"
PASSWORD = os.getenv("PASSWORD")
SPACE_PASSWORD = os.getenv("SPACE_PASSWORD")
PLAYBACK_VALIDATION_DELAY_SECONDS = 0.2
PLAYBACK_VALIDATION_MAX_WAIT_SECONDS = 2.0


def _coerce_track_query(raw_id: str) -> dict:
    try:
        return {"$or": [{"_id": ObjectId(raw_id)}, {"_id": raw_id}]}
    except Exception:
        return {"_id": raw_id}


def _base_view() -> discord.ui.View:
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label="Deep Dey", url=APP_LINK, style=discord.ButtonStyle.link))
    v.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))
    return v


class GuildMusicState:
    __slots__ = (
        "queue",
        "current",
        "voice_client",
        "channel_id",
        "text_channel_id",
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
        self.text_channel_id: int | None = None
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
        self.add_item(discord.ui.Button(label="Deep Dey", url=APP_LINK, style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))

    @discord.ui.button(label="🟢 24/7 ON", style=discord.ButtonStyle.success, custom_id="music_247_on")
    async def enable_247(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(interaction.guild_id)
        state.is_247 = True
        state.text_channel_id = interaction.channel_id
        await self.cog.persist_state(interaction.guild_id)
        await interaction.response.send_message("✅ 24/7 mode enabled.", ephemeral=True)

    @discord.ui.button(label="🔴 24/7 OFF", style=discord.ButtonStyle.danger, custom_id="music_247_off")
    async def disable_247(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(interaction.guild_id)
        state.is_247 = False
        state.text_channel_id = interaction.channel_id
        await self.cog.persist_state(interaction.guild_id)
        await interaction.response.send_message("✅ 24/7 mode disabled.", ephemeral=True)


class _LiveDashboardView(discord.ui.View):
    def __init__(self, cog: "MusicCommands", guild_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.add_item(discord.ui.Button(label="Deep Dey", url=APP_LINK, style=discord.ButtonStyle.link))
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
            await self.cog.persist_state(self.guild_id)
            await interaction.response.send_message("⏭️ Skipped current track.", ephemeral=True)
            return
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)


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
        if not secrets.compare_digest(self.password.value, PASSWORD):
            await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        source_file = None
        final_mp3 = None
        try:
            source_file, extracted_title, extracted_artwork = await extract_from_url(self.link.value.strip())
            title = self.track_title.value.strip() or extracted_title or "Untitled Track"
            artwork_url = self.artwork.value.strip() or extracted_artwork or DEFAULT_ARTWORK
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


class _MusicDeleteModal(discord.ui.Modal, title="Delete Music Track"):
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "MusicCommands", track_id: str, track_title: str):
        super().__init__()
        self.cog = cog
        self.track_id = track_id
        self.track_title = track_title

    async def on_submit(self, interaction: discord.Interaction) -> None:
        key = (interaction.user.id, "music_delete")
        if self.cog._security_failures.get(key, 0) >= 3:
            await interaction.response.send_message("You are locked out after 3 failed password attempts.", ephemeral=True)
            return
        if not PASSWORD or not secrets.compare_digest(self.password.value, PASSWORD):
            failures = self.cog._security_failures.get(key, 0) + 1
            self.cog._security_failures[key] = failures
            await interaction.response.send_message(f"Invalid password. Attempt {failures}/3.", ephemeral=True)
            if failures >= 3:
                await send_activity_log(
                    self.cog.bot,
                    activity_type="Security Lockout",
                    details="User reached 3 failed attempts for /music delete.",
                    module="Security",
                    guild=interaction.guild,
                    user=interaction.user,
                )
            return

        self.cog._security_failures.pop(key, None)
        result = await music_col.delete_one(_coerce_track_query(self.track_id))
        if result.deleted_count:
            await interaction.response.send_message(
                f"Track deleted successfully: **{self.track_title}**",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Track not found or already deleted.", ephemeral=True)


class MusicCommands(commands.Cog):
    music_group = app_commands.Group(name="music", description="Music & Audio Engine 🎵")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}
        self._live_dashboards: dict[int, dict] = {}
        self._restored_once = False
        self._security_failures: dict[tuple[int, str], int] = {}
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

    async def _ensure_voice_state(self, interaction: discord.Interaction) -> GuildMusicState | None:
        voice_state = getattr(interaction.user, "voice", None)
        if not voice_state or not voice_state.channel:
            await interaction.followup.send("❌ Join a voice channel first.", ephemeral=True)
            return None

        state = self.get_state(interaction.guild_id)
        target_channel = voice_state.channel
        try:
            if state.voice_client and state.voice_client.is_connected():
                if state.voice_client.channel and state.voice_client.channel.id != target_channel.id:
                    await state.voice_client.move_to(target_channel)
            else:
                state.voice_client = await target_channel.connect(reconnect=True)
        except Exception:
            try:
                if state.voice_client:
                    await state.voice_client.disconnect(force=True)
            except Exception as disconnect_exc:
                print(f"[Music] Voice disconnect during reconnect failed: {type(disconnect_exc).__name__}")
            state.voice_client = await target_channel.connect(reconnect=True)

        state.channel_id = target_channel.id
        state.text_channel_id = interaction.channel_id
        return state

    async def _emit_music_logs(
        self,
        *,
        guild: discord.Guild | None,
        user: discord.abc.User | None,
        activity_type: str,
        details: str,
        fields: list[tuple[str, str, bool]] | None = None,
        jump_url: str | None = None,
    ) -> None:
        await send_activity_log(
            self.bot,
            activity_type=activity_type,
            details=details,
            module="Music",
            guild=guild,
            user=user,
            jump_url=jump_url,
            fields=fields,
        )
        await send_guild_module_log(
            self.bot,
            guild=guild,
            module="music",
            title=f"Music • {activity_type}",
            description=details,
            fields=fields,
        )

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
            "vc_channel_id": state.channel_id,
            "text_channel_id": state.text_channel_id,
            "is_24_7": state.is_247,
            "queue": state.queue,
            "current_track": state.current,
            "resume_offset": elapsed,
            "updated_at": datetime.now(timezone.utc),
            "active": bool(state.channel_id and (state.current or state.queue or state.is_247)),
        }
        await music_states_col.update_one({"guild_id": guild_id}, {"$set": payload}, upsert=True)

    async def restore_sessions(self) -> None:
        async for doc in music_states_col.find({"$or": [{"active": True}, {"is_24_7": True}]}):
            guild_id = doc.get("guild_id")
            channel_id = doc.get("vc_channel_id")
            if not guild_id or not channel_id:
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.VoiceChannel):
                continue

            state = self.get_state(guild_id)
            state.is_247 = bool(doc.get("is_24_7", False))
            state.queue = list(doc.get("queue", []))
            state.current = doc.get("current_track")
            state.next_track = doc.get("current_track")
            state.resume_offset = int(doc.get("resume_offset", 0))
            state.channel_id = channel_id
            state.text_channel_id = doc.get("text_channel_id")

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

    @music_group.command(name="logs", description="Set the dedicated music logs channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def music_logs(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(thinking=True)
        await db.update_guild_settings(interaction.guild_id, {"music_logs_channel_id": channel.id})
        await interaction.followup.send(f"Music logs channel set to {channel.mention}.")

    @music_group.command(name="select", description="Select a saved track and stream it instantly")
    async def music_select(self, interaction: discord.Interaction, search_query: str) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Join a voice channel first.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        track_doc = await music_col.find_one(_coerce_track_query(search_query))
        if not track_doc:
            await interaction.followup.send("❌ Track not found.", ephemeral=True)
            return

        track = {
            "title": track_doc.get("title") or track_doc.get("name") or "Untitled Track",
            "file_url": track_doc.get("file_url") or track_doc.get("url") or "",
            "artwork_url": track_doc.get("artwork_url") or DEFAULT_ARTWORK,
        }
        if not track["file_url"]:
            await interaction.followup.send("❌ Track URL missing in database.", ephemeral=True)
            return

        state = await self._ensure_voice_state(interaction)
        if not state:
            return

        state.queue.append(track)
        state.next_track = track
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            state.voice_client.stop()

        if not state._playback_task or state._playback_task.done():
            state._playback_task = asyncio.create_task(self._playback_loop(interaction.guild_id))

        await self.persist_state(interaction.guild_id)
        embed = discord.Embed(title="▶️ Streaming Selected Track", description=f"Queued: **{track['title']}**", color=0x1DB954)
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_base_view())

    @music_group.command(name="add", description="Add a track using secure modal upload flow")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def music_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_MusicAddModal(self))

    @music_group.command(name="delete", description="Delete a track securely")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def music_delete(self, interaction: discord.Interaction, search_query: str) -> None:
        key = (interaction.user.id, "music_delete")
        if self._security_failures.get(key, 0) >= 3:
            await interaction.response.send_message("You are locked out after 3 failed password attempts.", ephemeral=True)
            return
        track = await music_col.find_one(_coerce_track_query(search_query), {"title": 1, "name": 1})
        if not track:
            await interaction.response.send_message("No track found for the selected ID.", ephemeral=True)
            return
        track_title = str(track.get("title") or track.get("name") or "Untitled Track")
        await interaction.response.send_modal(_MusicDeleteModal(self, search_query, track_title))

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

    @music_delete.autocomplete("search_query")
    async def music_delete_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.music_select_autocomplete(interaction, current)

    @music_group.command(name="join", description="Join your voice channel")
    async def music_join(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You must be in a voice channel first.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        state = await self._ensure_voice_state(interaction)
        if not state:
            return
        connected_channel = state.voice_client.channel if state.voice_client else None
        channel_name = connected_channel.name if connected_channel else "Unknown Channel"
        await self.persist_state(interaction.guild_id)

        embed = discord.Embed(title="✅ Joined Voice Channel", description=f"Connected to **{channel_name}**.", color=0x1DB954)
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_base_view())
        await self._emit_music_logs(
            guild=interaction.guild,
            user=interaction.user,
            activity_type="Voice Channel Join",
            details=f"Joined voice channel: {channel_name}.",
            fields=[("Voice Channel", channel_name, True)],
            jump_url=interaction.channel.jump_url if isinstance(interaction.channel, discord.TextChannel) else None,
        )

    @music_group.command(name="leave", description="Leave VC and clear the queue")
    async def music_leave(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
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
        state.text_channel_id = interaction.channel_id
        await music_states_col.update_one({"guild_id": interaction.guild_id}, {"$set": {"active": False}}, upsert=True)

        embed = discord.Embed(title="👋 Left Voice Channel", description="Disconnected and queue cleared.", color=0xFF4444)
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_base_view())

    @music_group.command(name="pause", description="Pause playback")
    async def music_pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        state = self.get_state(interaction.guild_id)
        vc = state.voice_client
        if vc and vc.is_playing():
            vc.pause()
            state.paused = True
            state.resume_offset = int(time.time() - state.start_time) if state.start_time else 0
            await self.persist_state(interaction.guild_id)
            embed = discord.Embed(title="⏸️ Paused", color=0xFFA500)
            embed.set_footer(text="an app by deep")
            await interaction.followup.send(embed=embed, view=_base_view())
        else:
            await interaction.followup.send("❌ Nothing is playing.", ephemeral=True)

    @music_group.command(name="resume", description="Resume playback")
    async def music_resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
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
            await interaction.followup.send(embed=embed, view=_base_view())
        else:
            await interaction.followup.send("❌ Nothing is paused.", ephemeral=True)

    @music_group.command(name="start", description="Start playing the saved queue")
    async def music_start(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Join a voice channel first.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        state = await self._ensure_voice_state(interaction)
        if not state:
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
            await interaction.followup.send("❌ No playable tracks found in DB.", ephemeral=True)
            return

        state.queue = queue
        state.text_channel_id = interaction.channel_id
        if state._playback_task and not state._playback_task.done():
            state._playback_task.cancel()
        state._playback_task = asyncio.create_task(self._playback_loop(interaction.guild_id))
        await self.persist_state(interaction.guild_id)

        embed = discord.Embed(title="▶️ Starting Queue", description=f"Loaded **{len(state.queue)}** tracks.", color=0x1DB954)
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_base_view())

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

                if not state.voice_client or not state.voice_client.is_connected():
                    guild = self.bot.get_guild(guild_id)
                    channel = guild.get_channel(state.channel_id) if guild and state.channel_id else None
                    if not channel or not isinstance(channel, discord.VoiceChannel):
                        break
                    try:
                        state.voice_client = await channel.connect(reconnect=True)
                    except Exception as reconnect_exc:
                        print(f"[Music] Voice reconnect failed: {type(reconnect_exc).__name__}")
                        break

                mp3_path = await smart_download(track["file_url"])
                if not mp3_path:
                    print(f"[Music] Failed to download/prepare track: {track.get('title', 'unknown')}")
                    await asyncio.sleep(1)
                    continue

                before_parts = []
                parsed_url = urlparse(str(track.get("file_url") or ""))
                if parsed_url.scheme in {"http", "https"}:
                    before_parts.extend(
                        [
                            "-reconnect 1",
                            "-reconnect_streamed 1",
                            "-reconnect_delay_max 5",
                        ]
                    )
                if state.resume_offset > 0:
                    before_parts.insert(0, f"-ss {state.resume_offset}")
                before_options = " ".join(before_parts) if before_parts else None

                try:
                    source_kwargs = {"options": "-vn"}
                    if before_options:
                        source_kwargs["before_options"] = before_options
                    source = discord.FFmpegPCMAudio(str(mp3_path), **source_kwargs)
                    state.voice_client.play(source)
                    waited = 0.0
                    while waited < PLAYBACK_VALIDATION_MAX_WAIT_SECONDS:
                        if state.voice_client.is_playing() or state.voice_client.is_paused():
                            break
                        await asyncio.sleep(PLAYBACK_VALIDATION_DELAY_SECONDS)
                        waited += PLAYBACK_VALIDATION_DELAY_SECONDS
                    if not (state.voice_client.is_playing() or state.voice_client.is_paused()):
                        raise RuntimeError(f"Voice client did not start playback for track: {track.get('title', 'Untitled Track')}")
                    state.current = track
                    state.start_time = time.time()
                    state.resume_offset = 0
                    await self.persist_state(guild_id)
                    guild = self.bot.get_guild(guild_id)
                    await self._emit_music_logs(
                        guild=guild,
                        user=self.bot.user,
                        activity_type="Track Played",
                        details=f"Track started: {track.get('title', 'Untitled Track')}.",
                        fields=[("Track", track.get("title", "Untitled Track"), False)],
                    )
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
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Join a voice channel first.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        state = await self._ensure_voice_state(interaction)
        if not state:
            return
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
            source = discord.FFmpegPCMAudio(str(mp3_path), options="-vn")
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
