"""
cogs/music_commands.py — Full Music & Audio Engine Cog
Features: CDN integration, smart downloader, 96 kbps LRU cache,
queue with cryptographic randomness, 24/7 mode, and live dashboard.
"""

import asyncio
import secrets
import time
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from audio_manager import (
    MAX_CACHE_BYTES,
    TMP_DIR,
    build_slug,
    download_and_convert,
    get_dir_size,
    is_bypass_domain,
    purge_lru_cache,
    smart_download,
    upload_to_cdn,
)
from database import client as _mongo_client

# ── Database collection for saved tracks ──────────────────────────────────────
_music_db = _mongo_client["LeaderboardBotDB"]
music_col = _music_db["MusicTracks"]

# ── Constants ─────────────────────────────────────────────────────────────────
APP_LINK = "https://deepdey.vercel.app/"
DEFAULT_ARTWORK = "https://deydeep-static-files.hf.space/f/ncs.gif"


# ── Shared UI helpers ─────────────────────────────────────────────────────────

def _app_button() -> discord.ui.Button:
    """Returns the standard 'an app by deep' link button."""
    return discord.ui.Button(
        label="an app by deep",
        url=APP_LINK,
        style=discord.ButtonStyle.link,
    )


def _base_view() -> discord.ui.View:
    """Returns a View that contains only the watermark link button."""
    v = discord.ui.View()
    v.add_item(_app_button())
    return v


# ── Per-guild state ────────────────────────────────────────────────────────────

class GuildMusicState:
    """Holds all music-related runtime state for a single guild."""

    __slots__ = (
        "queue",
        "current",
        "voice_client",
        "is_247",
        "paused",
        "start_time",
        "_playback_task",
    )

    def __init__(self) -> None:
        self.queue: list[dict] = []          # {"name": str, "url": str}
        self.current: dict | None = None     # currently playing track
        self.voice_client: discord.VoiceClient | None = None
        self.is_247: bool = False
        self.paused: bool = False
        self.start_time: float | None = None  # epoch when current track started
        self._playback_task: asyncio.Task | None = None


# ── Modal: secure CDN password collection ─────────────────────────────────────

class _CDNPasswordModal(discord.ui.Modal, title="CDN Upload — Enter Password"):
    password = discord.ui.TextInput(
        label="Space Password",
        placeholder="Enter the CDN space password…",
        style=discord.TextStyle.short,
        required=True,
        min_length=1,
        max_length=256,
    )

    def __init__(self, link: str, cog: "MusicCommands") -> None:
        super().__init__()
        self.link = link
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.cog._process_music_add(interaction, self.link, self.password.value)


# ── 24/7 toggle view ──────────────────────────────────────────────────────────

class _TwoFourSevenView(discord.ui.View):
    def __init__(self, cog: "MusicCommands") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(_app_button())

    @discord.ui.button(label="🟢 24/7 ON", style=discord.ButtonStyle.success, custom_id="music_247_on")
    async def enable_247(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.cog.get_state(interaction.guild_id).is_247 = True
        await interaction.response.send_message(
            "✅ 24/7 mode **enabled** — bot will stay in VC even when the channel is empty.",
            ephemeral=True,
        )

    @discord.ui.button(label="🔴 24/7 OFF", style=discord.ButtonStyle.danger, custom_id="music_247_off")
    async def disable_247(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.cog.get_state(interaction.guild_id).is_247 = False
        await interaction.response.send_message("✅ 24/7 mode **disabled**.", ephemeral=True)


# ── Live dashboard control buttons ────────────────────────────────────────────

class _LiveDashboardView(discord.ui.View):
    def __init__(self, cog: "MusicCommands", guild_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.add_item(_app_button())

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="live_prev")
    async def prev_track(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(self.guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()
        await interaction.response.defer()

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
            await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            state.paused = False
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="live_skip")
    async def skip_track(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(self.guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="live_stop")
    async def stop_music(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        state = self.cog.get_state(self.guild_id)
        if state.voice_client:
            state.voice_client.stop()
            state.queue.clear()
            await interaction.response.send_message("⏹️ Stopped and queue cleared.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Bot is not in a voice channel.", ephemeral=True)


# ── Main Cog ──────────────────────────────────────────────────────────────────

class MusicCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}
        # guild_id → {"message": discord.Message, "task": asyncio.Task}
        self._live_dashboards: dict[int, dict] = {}
        self._cache_monitor.start()

    def cog_unload(self) -> None:
        self._cache_monitor.cancel()
        for entry in self._live_dashboards.values():
            t = entry.get("task")
            if t and not t.done():
                t.cancel()

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    # ── Background: LRU cache monitor ─────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def _cache_monitor(self) -> None:
        if get_dir_size() > MAX_CACHE_BYTES:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, purge_lru_cache)

    # ── Embed builders ─────────────────────────────────────────────────────────

    @staticmethod
    def _seek_bar(elapsed_secs: int) -> str:
        bar_len = 20
        ticks = min(elapsed_secs // 10, bar_len)
        bar = "▓" * ticks + "░" * (bar_len - ticks)
        m, s = divmod(elapsed_secs, 60)
        return f"`{m}:{s:02d}` [{bar}]"

    def _nowplaying_embed(self, state: GuildMusicState) -> discord.Embed:
        track = state.current or {}
        name = track.get("name", "Nothing playing")
        url = track.get("url", "")
        elapsed = int(time.time() - state.start_time) if state.start_time else 0

        embed = discord.Embed(title="🎵 Now Playing", description=f"**{name}**", color=0x1DB954)
        embed.set_thumbnail(url=DEFAULT_ARTWORK)
        embed.add_field(name="Progress", value=self._seek_bar(elapsed), inline=False)
        embed.add_field(
            name="Status",
            value="⏸️ Paused" if state.paused else "▶️ Playing",
            inline=True,
        )
        if url:
            embed.add_field(name="Source", value=f"[Link]({url})", inline=True)
        embed.set_footer(text="an app by deep")
        return embed

    # ── Slash command group ────────────────────────────────────────────────────

    music_group = app_commands.Group(name="music", description="Music & Audio Engine 🎵")

    # /music help ──────────────────────────────────────────────────────────────

    @music_group.command(name="help", description="Show all music commands")
    async def music_help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🎵 Music Engine — Command Guide",
            description=(
                "Full-featured music engine with CDN uploads, "
                "24/7 mode, smart caching, and live dashboards."
            ),
            color=0x1DB954,
        )
        fields = [
            ("`/music join`", "Join your voice channel."),
            ("`/music leave`", "Leave VC and clear the queue."),
            ("`/music add <link>`", "Download, upload to CDN and save track (password via secure popup)."),
            ("`/music start`", "Start playing the saved queue on loop."),
            ("`/music pause`", "Pause playback."),
            ("`/music resume`", "Resume playback."),
            ("`/music temp <link>`", "Play a one-off track (auto-deleted after playback)."),
            ("`/music 247`", "Toggle 24/7 mode with interactive buttons."),
            ("`/music live`", "Open the live real-time playback dashboard."),
            ("`/music nowplaying`", "Show a static snapshot of the current track."),
        ]
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    # /music join ──────────────────────────────────────────────────────────────

    @music_group.command(name="join", description="Join your voice channel")
    async def music_join(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be in a voice channel first.", ephemeral=True
            )
            return
        channel = interaction.user.voice.channel
        state = self.get_state(interaction.guild_id)
        if state.voice_client and state.voice_client.is_connected():
            await state.voice_client.move_to(channel)
        else:
            state.voice_client = await channel.connect()

        embed = discord.Embed(
            title="✅ Joined Voice Channel",
            description=f"Connected to **{channel.name}**.",
            color=0x1DB954,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    # /music leave ─────────────────────────────────────────────────────────────

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

        embed = discord.Embed(
            title="👋 Left Voice Channel",
            description="Disconnected and queue cleared.",
            color=0xFF4444,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

    # /music pause ─────────────────────────────────────────────────────────────

    @music_group.command(name="pause", description="Pause playback")
    async def music_pause(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        vc = state.voice_client
        if vc and vc.is_playing():
            vc.pause()
            state.paused = True
            embed = discord.Embed(title="⏸️ Paused", color=0xFFA500)
            embed.set_footer(text="an app by deep")
            await interaction.response.send_message(embed=embed, view=_base_view())
        else:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

    # /music resume ────────────────────────────────────────────────────────────

    @music_group.command(name="resume", description="Resume playback")
    async def music_resume(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        vc = state.voice_client
        if vc and vc.is_paused():
            vc.resume()
            state.paused = False
            embed = discord.Embed(title="▶️ Resumed", color=0x1DB954)
            embed.set_footer(text="an app by deep")
            await interaction.response.send_message(embed=embed, view=_base_view())
        else:
            await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)

    # /music add ───────────────────────────────────────────────────────────────

    @music_group.command(name="add", description="Add a track to the CDN database")
    async def music_add(self, interaction: discord.Interaction, link: str) -> None:
        # Bypass domains → no download/upload needed; add directly as permanent
        if is_bypass_domain(link):
            track_name = link.rstrip("/").split("/")[-1].split("?")[0] or link
            await music_col.update_one(
                {"guild_id": interaction.guild_id, "url": link},
                {
                    "$set": {
                        "guild_id": interaction.guild_id,
                        "url": link,
                        "name": track_name,
                        "permanent": True,
                        "added_at": datetime.utcnow(),
                    }
                },
                upsert=True,
            )
            embed = discord.Embed(
                title="✅ Track Added (CDN Direct)",
                description=f"Trusted CDN link saved directly to the database:\n`{link}`",
                color=0x1DB954,
            )
            embed.set_footer(text="an app by deep")
            await interaction.response.send_message(embed=embed, view=_base_view())
            return

        # All other links → collect CDN password securely via modal
        await interaction.response.send_modal(_CDNPasswordModal(link=link, cog=self))

    async def _process_music_add(
        self, interaction: discord.Interaction, link: str, password: str
    ) -> None:
        """Called after the CDN password modal is submitted."""
        await interaction.followup.send(
            "⏳ Downloading and converting to 96 kbps MP3…", ephemeral=True
        )
        mp3_path = await download_and_convert(link)
        if not mp3_path:
            await interaction.followup.send("❌ Failed to download the track.", ephemeral=True)
            return

        await interaction.followup.send("⬆️ Uploading to CDN…", ephemeral=True)
        original_name = mp3_path.stem
        cdn_url = await upload_to_cdn(mp3_path, password, original_name)
        if not cdn_url:
            await interaction.followup.send(
                "❌ CDN upload failed. Check your password and try again.", ephemeral=True
            )
            return

        await music_col.update_one(
            {"guild_id": interaction.guild_id, "url": cdn_url},
            {
                "$set": {
                    "guild_id": interaction.guild_id,
                    "url": cdn_url,
                    "name": original_name,
                    "permanent": False,
                    "added_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )

        embed = discord.Embed(
            title="✅ Track Uploaded & Saved",
            description=(
                f"**{original_name}** has been converted, uploaded, "
                f"and added to the database.\n\n🔗 `{cdn_url}`"
            ),
            color=0x1DB954,
        )
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_base_view())

    # /music start ─────────────────────────────────────────────────────────────

    @music_group.command(name="start", description="Start playing the saved queue")
    async def music_start(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ Use `/music join` first to put the bot in a voice channel.", ephemeral=True
            )
            return

        tracks = await music_col.find({"guild_id": interaction.guild_id}).to_list(length=None)
        if not tracks:
            await interaction.response.send_message(
                "❌ No tracks saved. Use `/music add <link>` to add some.", ephemeral=True
            )
            return

        state.queue = [{"name": t["name"], "url": t["url"]} for t in tracks]

        embed = discord.Embed(
            title="▶️ Starting Queue",
            description=f"Loaded **{len(state.queue)}** tracks. Starting playback on continuous loop…",
            color=0x1DB954,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_base_view())

        # Cancel any existing playback loop before starting a new one
        if state._playback_task and not state._playback_task.done():
            state._playback_task.cancel()
        state._playback_task = asyncio.create_task(self._playback_loop(interaction.guild_id))

    # ── Internal playback loop ─────────────────────────────────────────────────

    async def _playback_loop(self, guild_id: int) -> None:
        """
        Continuously loops through the queue picking tracks with cryptographic
        randomness (secrets.choice).  Handles 24/7 mode and inactivity disconnect.
        """
        state = self.get_state(guild_id)
        try:
            while state.voice_client and state.voice_client.is_connected() and state.queue:
                track = secrets.choice(state.queue)
                state.current = track
                state.start_time = time.time()
                state.paused = False

                mp3_path = await smart_download(track["url"])
                if not mp3_path:
                    # Skip undownloadable tracks rather than crashing
                    await asyncio.sleep(2)
                    continue

                try:
                    source = discord.FFmpegPCMAudio(str(mp3_path))
                    state.voice_client.play(source)
                except Exception as exc:
                    print(f"[MusicCog] Playback start error: {exc}")
                    await asyncio.sleep(2)
                    continue

                # Wait for playback to finish
                while state.voice_client and (
                    state.voice_client.is_playing() or state.voice_client.is_paused()
                ):
                    await asyncio.sleep(1)

                    # Inactivity disconnect unless 24/7 mode is on
                    if not state.is_247 and state.voice_client.channel:
                        non_bot_members = [
                            m for m in state.voice_client.channel.members if not m.bot
                        ]
                        if not non_bot_members:
                            state.voice_client.stop()
                            await state.voice_client.disconnect()
                            state.voice_client = None
                            state.current = None
                            return

                if not state.voice_client or not state.voice_client.is_connected():
                    break
        except asyncio.CancelledError:
            pass
        finally:
            state.current = None

    # /music temp ──────────────────────────────────────────────────────────────

    @music_group.command(
        name="temp",
        description="Play a temporary track — auto-deleted from server after playback",
    )
    async def music_temp(self, interaction: discord.Interaction, link: str) -> None:
        state = self.get_state(interaction.guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ Use `/music join` first.", ephemeral=True
            )
            return

        await interaction.response.defer()
        mp3_path = await download_and_convert(link)
        if not mp3_path:
            await interaction.followup.send("❌ Failed to download the track.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎵 Playing Temp Track",
            description=f"Now playing (auto-deleted after):\n`{link[:120]}`",
            color=0x1DB954,
        )
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_base_view())

        asyncio.create_task(self._play_temp(interaction.guild_id, mp3_path))

    async def _play_temp(self, guild_id: int, mp3_path: Path) -> None:
        state = self.get_state(guild_id)
        # Stop any existing playback politely
        if state.voice_client and (
            state.voice_client.is_playing() or state.voice_client.is_paused()
        ):
            state.voice_client.stop()

        if not state.voice_client:
            return

        try:
            source = discord.FFmpegPCMAudio(str(mp3_path))
            state.voice_client.play(source)
            while state.voice_client and (
                state.voice_client.is_playing() or state.voice_client.is_paused()
            ):
                await asyncio.sleep(1)
        except Exception as exc:
            print(f"[MusicCog] Temp playback error: {exc}")
        finally:
            try:
                if mp3_path.exists():
                    mp3_path.unlink()
            except Exception as exc:
                print(f"[MusicCog] Temp file deletion error: {exc}")

    # /music 247 ───────────────────────────────────────────────────────────────

    @music_group.command(name="247", description="Toggle 24/7 mode with interactive buttons")
    async def music_247(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        status_label = "🟢 ON" if state.is_247 else "🔴 OFF"
        embed = discord.Embed(
            title="⚙️ 24/7 Mode",
            description=(
                f"Current status: **{status_label}**\n"
                "Use the buttons below to enable or disable 24/7 mode."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_TwoFourSevenView(self))

    # /music nowplaying ────────────────────────────────────────────────────────

    @music_group.command(name="nowplaying", description="Show current track info (static)")
    async def music_nowplaying(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        if not state.current:
            await interaction.response.send_message(
                "❌ Nothing is playing right now.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=self._nowplaying_embed(state), view=_base_view()
        )

    # /music live ──────────────────────────────────────────────────────────────

    @music_group.command(name="live", description="Open the live real-time playback dashboard")
    async def music_live(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)

        # Stop any existing live dashboard updater for this guild
        existing = self._live_dashboards.get(guild_id)
        if existing:
            old_task = existing.get("task")
            if old_task and not old_task.done():
                old_task.cancel()

        live_view = _LiveDashboardView(self, guild_id)
        await interaction.response.send_message(
            embed=self._nowplaying_embed(state), view=live_view
        )
        message = await interaction.original_response()

        task = asyncio.create_task(self._live_updater(guild_id, message, live_view))
        self._live_dashboards[guild_id] = {"message": message, "task": task}

    async def _live_updater(
        self,
        guild_id: int,
        message: discord.Message,
        view: discord.ui.View,
    ) -> None:
        """
        Anti-rate-limit background loop: updates the live dashboard at a
        cryptographically random interval between 5 and 20 seconds.
        """
        try:
            while True:
                # secrets.randbelow(16) → [0, 15], so total → [5, 20]
                interval = secrets.randbelow(16) + 5
                await asyncio.sleep(interval)

                state = self.get_state(guild_id)
                try:
                    await message.edit(embed=self._nowplaying_embed(state), view=view)
                except discord.NotFound:
                    break  # Message deleted
                except discord.HTTPException as exc:
                    if exc.status == 429:
                        await asyncio.sleep(20)  # Back off on rate-limit
                    else:
                        break
        except asyncio.CancelledError:
            pass
        finally:
            self._live_dashboards.pop(guild_id, None)


# ── Cog setup entry-point ─────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCommands(bot))
