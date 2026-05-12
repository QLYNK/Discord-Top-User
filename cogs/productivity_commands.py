import asyncio
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from database import client as mongo_client
from telemetry import send_activity_log, send_guild_module_log

_prod_db = mongo_client["LeaderboardBotDB"]
afks_col = _prod_db["AFKStates"]
pomodoro_profiles_col = _prod_db["PomodoroProfiles"]
pomodoro_sessions_col = _prod_db["PomodoroSessions"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _message_link(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _excerpt(content: str, max_len: int = 140) -> str:
    clean = (content or "").strip()
    if not clean:
        return "[No text content]"
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


def _format_hms(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours} Hours, {minutes} Minutes, {seconds} Seconds"


def _afk_applies(afk_doc: dict, guild_id: int | None) -> bool:
    if afk_doc.get("scope") == "global":
        return True
    return guild_id is not None and afk_doc.get("guild_id") == guild_id


class AFKSetupView(discord.ui.View):
    def __init__(self, cog: "ProductivityCommands", user_id: int, reason: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.reason = reason
        self.scope_value = "server"
        self.strictness_value = "soft"

        self.scope_select = discord.ui.Select(
            placeholder="Select AFK scope",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Server Only", value="server"),
                discord.SelectOption(label="Global", value="global"),
            ],
        )
        self.scope_select.callback = self._on_scope_select

        self.strictness_select = discord.ui.Select(
            placeholder="Select AFK strictness",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Soft AFK", value="soft"),
                discord.SelectOption(label="Hard AFK", value="hard"),
            ],
        )
        self.strictness_select.callback = self._on_strictness_select

        self.add_item(self.scope_select)
        self.add_item(self.strictness_select)

    async def _on_scope_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the command author can configure this AFK session.", ephemeral=True)
            return
        self.scope_value = self.scope_select.values[0]
        await interaction.response.defer()

    async def _on_strictness_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the command author can configure this AFK session.", ephemeral=True)
            return
        self.strictness_value = self.strictness_select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="Activate AFK", style=discord.ButtonStyle.success)
    async def activate(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the command author can activate this AFK session.", ephemeral=True)
            return

        now = _utc_now()
        payload = {
            "user_id": self.user_id,
            "guild_id": interaction.guild_id,
            "reason": self.reason,
            "scope": self.scope_value,
            "strictness": self.strictness_value,
            "started_at": now,
            "missed": [],
            "updated_at": now,
        }
        await afks_col.update_one({"user_id": self.user_id}, {"$set": payload}, upsert=True)

        scope_text = "Global" if self.scope_value == "global" else "Server Only"
        mode_text = "Hard AFK" if self.strictness_value == "hard" else "Soft AFK"

        await self.cog._log_afk_event(
            interaction,
            activity_type="AFK Activated",
            details=f"AFK activated with {scope_text} scope and {mode_text} mode.",
            fields=[("Reason", self.reason, False)],
        )

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=(
                f"AFK activated successfully.\n"
                f"**Reason:** {self.reason}\n"
                f"**Scope:** {scope_text}\n"
                f"**Strictness:** {mode_text}"
            ),
            view=self,
        )


class AFKReasonModal(discord.ui.Modal, title="Set AFK Status"):
    reason = discord.ui.TextInput(label="Reason", placeholder="Enter your AFK reason", max_length=200, required=True)

    def __init__(self, cog: "ProductivityCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = AFKSetupView(self.cog, interaction.user.id, self.reason.value.strip())
        await interaction.response.send_message(
            "Select AFK scope and strictness, then activate.",
            view=view,
            ephemeral=True,
        )


class PomodoroProfileModal(discord.ui.Modal):
    def __init__(self, mode: str, defaults: dict | None = None):
        title = "Save Pomodoro Profile" if mode == "save" else "Edit Pomodoro Profile"
        super().__init__(title=title)

        defaults = defaults or {}
        self.work = discord.ui.TextInput(
            label="Work Minutes",
            default=str(defaults.get("work_minutes", 25)),
            required=True,
            max_length=3,
        )
        self.short_break = discord.ui.TextInput(
            label="Short Break Minutes",
            default=str(defaults.get("short_break_minutes", 5)),
            required=True,
            max_length=3,
        )
        self.long_break = discord.ui.TextInput(
            label="Long Break Minutes",
            default=str(defaults.get("long_break_minutes", 15)),
            required=True,
            max_length=3,
        )
        self.cycles = discord.ui.TextInput(
            label="Cycles Before Long Break",
            default=str(defaults.get("cycles_before_long_break", 4)),
            required=True,
            max_length=2,
        )

        self.add_item(self.work)
        self.add_item(self.short_break)
        self.add_item(self.long_break)
        self.add_item(self.cycles)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            work = int(self.work.value)
            short_break = int(self.short_break.value)
            long_break = int(self.long_break.value)
            cycles = int(self.cycles.value)
        except ValueError:
            await interaction.response.send_message("All profile values must be valid integers.", ephemeral=True)
            return

        if min(work, short_break, long_break, cycles) <= 0:
            await interaction.response.send_message("All profile values must be greater than zero.", ephemeral=True)
            return

        await pomodoro_profiles_col.update_one(
            {"user_id": interaction.user.id},
            {
                "$set": {
                    "work_minutes": work,
                    "short_break_minutes": short_break,
                    "long_break_minutes": long_break,
                    "cycles_before_long_break": cycles,
                    "updated_at": _utc_now(),
                },
                "$setOnInsert": {
                    "lifetime_focus_minutes": 0.0,
                },
            },
            upsert=True,
        )
        await interaction.response.send_message("Pomodoro profile saved successfully.", ephemeral=True)


class PomodoroTaskModal(discord.ui.Modal, title="Start Focus Session"):
    task = discord.ui.TextInput(label="Focus Reason/Task", placeholder="What are you focusing on?", max_length=200, required=True)

    def __init__(self, cog: "ProductivityCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.start_pomodoro_session(interaction, self.task.value.strip())


class ProductivityCommands(commands.Cog):
    afk_group = app_commands.Group(name="afk", description="AFK commands")
    pomodoro_group = app_commands.Group(name="pomodoro", description="Deep work tracker")
    pomodoro_profile_group = app_commands.Group(name="profile", description="Manage Pomodoro profile", parent=pomodoro_group)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._pomodoro_tasks: dict[int, asyncio.Task] = {}
        self._notice_cooldowns: dict[tuple[int, str], float] = {}

    def cog_unload(self) -> None:
        for task in self._pomodoro_tasks.values():
            if not task.done():
                task.cancel()

    async def _safe_dm(self, user: discord.abc.User, content: str | None = None, embed: discord.Embed | None = None) -> None:
        try:
            await user.send(content=content, embed=embed)
        except discord.Forbidden:
            pass
        except Exception:
            pass

    async def _notify_focus(
        self,
        *,
        user: discord.abc.User,
        channel: discord.abc.Messageable | None,
        text: str,
        embed: discord.Embed | None = None,
    ) -> None:
        if channel:
            try:
                await channel.send(text, embed=embed)
            except Exception:
                pass
        await self._safe_dm(user, content=text, embed=embed)

    def _should_send_notice(self, user_id: int, key: str, cooldown_seconds: int = 20) -> bool:
        now = time.time()
        ident = (user_id, key)
        last = self._notice_cooldowns.get(ident, 0)
        if now - last < cooldown_seconds:
            return False
        self._notice_cooldowns[ident] = now
        return True

    async def _log_afk_event(
        self,
        interaction_or_message: discord.Interaction | discord.Message,
        *,
        activity_type: str,
        details: str,
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        guild = interaction_or_message.guild
        user = interaction_or_message.user if isinstance(interaction_or_message, discord.Interaction) else interaction_or_message.author
        jump_url = (
            interaction_or_message.jump_url
            if isinstance(interaction_or_message, discord.Message)
            else (interaction_or_message.channel.jump_url if isinstance(interaction_or_message.channel, discord.TextChannel) else None)
        )

        await send_activity_log(
            self.bot,
            activity_type=activity_type,
            details=details,
            module="AFK",
            guild=guild,
            user=user,
            jump_url=jump_url,
            fields=fields,
        )
        await send_guild_module_log(
            self.bot,
            guild=guild,
            module="afk",
            title=f"AFK • {activity_type}",
            description=details,
            fields=fields,
        )

    async def _log_pomodoro_event(
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
            module="Pomodoro",
            guild=guild,
            user=user,
            jump_url=jump_url,
            fields=fields,
        )
        await send_guild_module_log(
            self.bot,
            guild=guild,
            module="pomodoro",
            title=f"Pomodoro • {activity_type}",
            description=details,
            fields=fields,
        )

    async def _build_missed_embed(self, title: str, missed: list[dict]) -> discord.Embed:
        embed = discord.Embed(title=title, color=0x5865F2, timestamp=_utc_now())
        if not missed:
            embed.description = "No missed mentions or replies were recorded."
            return embed

        lines = []
        for idx, item in enumerate(missed[:20], start=1):
            sender = item.get("sender_name", "Unknown")
            excerpt = item.get("excerpt", "[No text content]")
            link = item.get("link")
            when = item.get("created_at")
            when_str = f"<t:{int(when.timestamp())}:R>" if isinstance(when, datetime) else "Unknown time"
            lines.append(f"{idx}. **{sender}** • {when_str}\n{excerpt}\n[Jump to message]({link})")

        embed.description = "\n\n".join(lines)
        if len(missed) > 20:
            embed.set_footer(text=f"Showing 20 of {len(missed)} missed items")
        return embed

    async def _get_or_create_profile(self, user_id: int) -> dict:
        profile = await pomodoro_profiles_col.find_one({"user_id": user_id})
        if profile:
            return profile

        default_profile = {
            "user_id": user_id,
            "work_minutes": 25,
            "short_break_minutes": 5,
            "long_break_minutes": 15,
            "cycles_before_long_break": 4,
            "lifetime_focus_minutes": 0.0,
            "updated_at": _utc_now(),
        }
        await pomodoro_profiles_col.insert_one(default_profile)
        return default_profile

    async def _append_missed_to_afk(self, target_doc: dict, message: discord.Message):
        entry = {
            "sender_id": message.author.id,
            "sender_name": message.author.display_name,
            "link": _message_link(message.guild.id, message.channel.id, message.id),
            "excerpt": _excerpt(message.content),
            "created_at": _utc_now(),
        }
        await afks_col.update_one({"_id": target_doc["_id"]}, {"$push": {"missed": entry}})

    async def _append_missed_to_session(self, target_doc: dict, message: discord.Message):
        entry = {
            "sender_id": message.author.id,
            "sender_name": message.author.display_name,
            "link": _message_link(message.guild.id, message.channel.id, message.id),
            "excerpt": _excerpt(message.content),
            "created_at": _utc_now(),
        }
        await pomodoro_sessions_col.update_one({"_id": target_doc["_id"]}, {"$push": {"missed": entry}})

    async def _get_referenced_author_id(self, message: discord.Message) -> int | None:
        if not message.reference:
            return None
        if message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            return message.reference.resolved.author.id
        if message.reference.message_id and message.channel:
            try:
                referenced = await message.channel.fetch_message(message.reference.message_id)
                return referenced.author.id
            except Exception:
                return None
        return None

    async def _end_afk(self, user: discord.User | discord.Member, channel: discord.abc.Messageable, forced: bool = False):
        afk_doc = await afks_col.find_one({"user_id": user.id})
        if not afk_doc:
            return False

        started = afk_doc.get("started_at")
        missed = afk_doc.get("missed", [])
        await afks_col.delete_one({"user_id": user.id})

        duration = _format_hms(int((_utc_now() - started).total_seconds())) if isinstance(started, datetime) else "Unknown"
        embed = await self._build_missed_embed("Missed Mentions While AFK", missed)

        mode_label = "Hard AFK" if afk_doc.get("strictness") == "hard" else "Soft AFK"
        reason = afk_doc.get("reason", "No reason provided")
        content = (
            f"Welcome back, {user.mention}. Your {mode_label} session has ended.\n"
            f"**Reason:** {reason}\n"
            f"**AFK Duration:** {duration}"
        )
        if forced:
            content = (
                f"Welcome back, {user.mention}. Your Soft AFK session ended because you sent a message.\n"
                f"**Reason:** {reason}\n"
                f"**AFK Duration:** {duration}"
            )

        await channel.send(content, embed=embed)
        await self._safe_dm(
            user,
            content=(
                f"Your AFK session has ended.\n"
                f"Reason: {reason}\n"
                f"Duration: {duration}"
            ),
            embed=embed,
        )
        return True

    async def _cancel_pomodoro_task(self, user_id: int):
        task = self._pomodoro_tasks.get(user_id)
        if task and not task.done():
            task.cancel()
        self._pomodoro_tasks.pop(user_id, None)

    async def _schedule_session_timeout(self, user_id: int, seconds: int):
        async def _runner():
            await asyncio.sleep(max(1, int(seconds)))
            session = await pomodoro_sessions_col.find_one({"user_id": user_id})
            if not session or session.get("status") != "running":
                return
            await self._complete_pomodoro_session(user_id, "Timer Completed", ended_by_timer=True)

        await self._cancel_pomodoro_task(user_id)
        self._pomodoro_tasks[user_id] = asyncio.create_task(_runner())

    async def _complete_pomodoro_session(self, user_id: int, end_reason: str, ended_by_timer: bool = False):
        session = await pomodoro_sessions_col.find_one({"user_id": user_id})
        if not session:
            return

        await self._cancel_pomodoro_task(user_id)

        focused_seconds = int(session.get("focused_seconds_accum", 0))
        if session.get("status") == "running" and session.get("last_resumed_at"):
            focused_seconds += int((_utc_now() - session["last_resumed_at"]).total_seconds())

        focused_minutes = focused_seconds / 60
        profile = await self._get_or_create_profile(user_id)
        break_minutes = int(profile.get("short_break_minutes", 5))
        break_label = "Short Break"

        await pomodoro_profiles_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {
                    "lifetime_focus_minutes": focused_minutes,
                },
                "$set": {"updated_at": _utc_now()},
            },
            upsert=True,
        )

        missed = session.get("missed", [])
        task_name = session.get("task", "Focus session")
        guild = self.bot.get_guild(session.get("guild_id")) if session.get("guild_id") else None
        channel = guild.get_channel(session.get("channel_id")) if guild and session.get("channel_id") else None
        user = self.bot.get_user(user_id)
        if guild and not user:
            user = guild.get_member(user_id)
        if not user:
            await pomodoro_sessions_col.delete_one({"user_id": user_id})
            return

        await pomodoro_sessions_col.delete_one({"user_id": user_id})

        embed = await self._build_missed_embed("Missed Mentions During Focus Session", missed)
        duration = _format_hms(focused_seconds)
        end_text = (
            f"{user.mention} your focus session has ended ({end_reason}).\n"
            f"**Task:** {task_name}\n"
            f"**Focused Time Added:** {duration}\n"
            f"**Break:** {break_label} for {break_minutes} minute(s)."
        )
        if ended_by_timer:
            end_text = (
                f"{user.mention} your scheduled focus block is complete.\n"
                f"**Task:** {task_name}\n"
                f"**Focused Time Added:** {duration}\n"
                f"**Break:** {break_label} for {break_minutes} minute(s)."
            )

        await self._notify_focus(user=user, channel=channel, text=end_text, embed=embed)

        await self._log_pomodoro_event(
            guild=guild,
            user=user,
            activity_type="Focus Session Ended",
            details=f"Session ended. Break announced: {break_label} ({break_minutes} min).",
            fields=[
                ("Task", task_name[:200], False),
                ("Focused Duration", duration, True),
                ("End Reason", end_reason, True),
            ],
        )

    async def start_pomodoro_session(self, interaction: discord.Interaction, task_reason: str):
        existing = await pomodoro_sessions_col.find_one({"user_id": interaction.user.id})
        if existing:
            await interaction.response.send_message("You already have an active or paused focus session.", ephemeral=True)
            return

        profile = await self._get_or_create_profile(interaction.user.id)
        work_minutes = int(profile.get("work_minutes", 25))
        work_seconds = work_minutes * 60

        payload = {
            "user_id": interaction.user.id,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "task": task_reason,
            "status": "running",
            "remaining_seconds": work_seconds,
            "focused_seconds_accum": 0,
            "last_resumed_at": _utc_now(),
            "started_at": _utc_now(),
            "paused_at": None,
            "missed": [],
        }
        await pomodoro_sessions_col.insert_one(payload)
        await self._schedule_session_timeout(interaction.user.id, work_seconds)

        await interaction.response.send_message(
            f"Focus session started for **{work_minutes} minutes**.\n**Task:** {task_reason}"
        )

        await self._safe_dm(
            interaction.user,
            (
                f"Your focus session has started for {work_minutes} minutes.\n"
                f"Task: {task_reason}"
            ),
        )

        await self._log_pomodoro_event(
            guild=interaction.guild,
            user=interaction.user,
            activity_type="Focus Session Started",
            details=f"Focus session started for {work_minutes} minute(s).",
            fields=[("Task", task_reason[:200], False)],
            jump_url=interaction.channel.jump_url if isinstance(interaction.channel, discord.TextChannel) else None,
        )

    # -------------------- Admin log channel config --------------------

    @afk_group.command(name="logs", description="Set the dedicated AFK logs channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def afk_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_guild_settings(interaction.guild_id, {"afk_logs_channel_id": channel.id})
        await interaction.response.send_message(f"AFK logs channel set to {channel.mention}.")

    @pomodoro_group.command(name="logs", description="Set the dedicated Pomodoro logs channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def pomodoro_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_guild_settings(interaction.guild_id, {"pomodoro_logs_channel_id": channel.id})
        await interaction.response.send_message(f"Pomodoro logs channel set to {channel.mention}.")

    # -------------------- AFK commands --------------------

    @afk_group.command(name="set", description="Set your AFK status")
    async def afk_set(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AFKReasonModal(self))

    @afk_group.command(name="end", description="End your Hard AFK session")
    async def afk_end(self, interaction: discord.Interaction):
        afk_doc = await afks_col.find_one({"user_id": interaction.user.id})
        if not afk_doc:
            await interaction.response.send_message("You do not have an active AFK status.", ephemeral=True)
            return
        if afk_doc.get("strictness") != "hard":
            await interaction.response.send_message(
                "Your AFK mode is Soft AFK and ends automatically when you chat.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        await self._end_afk(interaction.user, interaction.channel)
        await self._log_afk_event(
            interaction,
            activity_type="AFK Ended",
            details="Hard AFK ended manually using command.",
        )

    # -------------------- Pomodoro commands --------------------

    @pomodoro_group.command(name="help", description="Show Pomodoro system help")
    async def pomodoro_help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Pomodoro Help", color=0x5865F2)
        embed.description = (
            "Use profile commands to configure your focus settings, then start a deep work session.\n\n"
            "**Profile Commands**\n"
            "`/pomodoro profile save`\n"
            "`/pomodoro profile edit`\n"
            "`/pomodoro profile reset`\n"
            "`/pomodoro profile view`\n\n"
            "**Session Commands**\n"
            "`/pomodoro start`\n"
            "`/pomodoro pause`\n"
            "`/pomodoro resume`\n"
            "`/pomodoro end`"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @pomodoro_group.command(name="start", description="Start a focus session")
    async def pomodoro_start(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PomodoroTaskModal(self))

    @pomodoro_group.command(name="pause", description="Pause your active focus session")
    async def pomodoro_pause(self, interaction: discord.Interaction):
        session = await pomodoro_sessions_col.find_one({"user_id": interaction.user.id})
        if not session or session.get("status") != "running":
            await interaction.response.send_message("You do not have an active running focus session.", ephemeral=True)
            return

        now = _utc_now()
        elapsed = int((now - session.get("last_resumed_at", now)).total_seconds())
        remaining = max(0, int(session.get("remaining_seconds", 0)) - elapsed)
        focused = int(session.get("focused_seconds_accum", 0)) + max(0, elapsed)

        await pomodoro_sessions_col.update_one(
            {"user_id": interaction.user.id},
            {
                "$set": {
                    "status": "paused",
                    "remaining_seconds": remaining,
                    "focused_seconds_accum": focused,
                    "paused_at": now,
                    "last_resumed_at": None,
                }
            },
        )
        await self._cancel_pomodoro_task(interaction.user.id)

        msg = f"Focus session paused with **{remaining // 60}m {remaining % 60}s** remaining."
        await interaction.response.send_message(msg)
        await self._safe_dm(interaction.user, msg)

        await self._log_pomodoro_event(
            guild=interaction.guild,
            user=interaction.user,
            activity_type="Focus Session Paused",
            details="Focus session paused.",
            fields=[("Remaining", f"{remaining // 60}m {remaining % 60}s", True)],
        )

    @pomodoro_group.command(name="resume", description="Resume your paused focus session")
    async def pomodoro_resume(self, interaction: discord.Interaction):
        session = await pomodoro_sessions_col.find_one({"user_id": interaction.user.id})
        if not session or session.get("status") != "paused":
            await interaction.response.send_message("You do not have a paused focus session.", ephemeral=True)
            return

        remaining = int(session.get("remaining_seconds", 0))
        if remaining <= 0:
            await interaction.response.send_message("No remaining focus time found. Use `/pomodoro end`.", ephemeral=True)
            return

        now = _utc_now()
        await pomodoro_sessions_col.update_one(
            {"user_id": interaction.user.id},
            {"$set": {"status": "running", "last_resumed_at": now, "paused_at": None}},
        )
        await self._schedule_session_timeout(interaction.user.id, remaining)

        msg = "Focus session resumed successfully."
        await interaction.response.send_message(msg)
        await self._safe_dm(interaction.user, msg)

        await self._log_pomodoro_event(
            guild=interaction.guild,
            user=interaction.user,
            activity_type="Focus Session Resumed",
            details="Focus session resumed.",
            fields=[("Remaining", f"{remaining // 60}m {remaining % 60}s", True)],
        )

    @pomodoro_group.command(name="end", description="End your focus session")
    async def pomodoro_end(self, interaction: discord.Interaction):
        session = await pomodoro_sessions_col.find_one({"user_id": interaction.user.id})
        if not session:
            await interaction.response.send_message("You do not have an active focus session.", ephemeral=True)
            return

        await interaction.response.send_message("Ending your focus session and preparing your report...")
        await self._complete_pomodoro_session(interaction.user.id, "Ended by User")

    @pomodoro_profile_group.command(name="save", description="Save your Pomodoro profile")
    async def pomodoro_profile_save(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PomodoroProfileModal("save"))

    @pomodoro_profile_group.command(name="edit", description="Edit your Pomodoro profile")
    async def pomodoro_profile_edit(self, interaction: discord.Interaction):
        current = await self._get_or_create_profile(interaction.user.id)
        await interaction.response.send_modal(PomodoroProfileModal("edit", defaults=current))

    @pomodoro_profile_group.command(name="reset", description="Reset your Pomodoro profile")
    async def pomodoro_profile_reset(self, interaction: discord.Interaction):
        await pomodoro_profiles_col.delete_one({"user_id": interaction.user.id})
        await interaction.response.send_message("Your Pomodoro profile has been reset.", ephemeral=True)

    @pomodoro_profile_group.command(name="view", description="View your Pomodoro profile")
    async def pomodoro_profile_view(self, interaction: discord.Interaction):
        profile = await self._get_or_create_profile(interaction.user.id)
        lifetime_minutes = float(profile.get("lifetime_focus_minutes", 0.0))
        lifetime_seconds = int(lifetime_minutes * 60)

        embed = discord.Embed(title="Pomodoro Profile", color=0x5865F2)
        embed.add_field(name="Work Minutes", value=str(profile.get("work_minutes", 25)), inline=True)
        embed.add_field(name="Short Break Minutes", value=str(profile.get("short_break_minutes", 5)), inline=True)
        embed.add_field(name="Long Break Minutes", value=str(profile.get("long_break_minutes", 15)), inline=True)
        embed.add_field(name="Cycles Before Long Break", value=str(profile.get("cycles_before_long_break", 4)), inline=True)
        embed.add_field(name="Lifetime Focus Time", value=_format_hms(lifetime_seconds), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        afk_doc = await afks_col.find_one({"user_id": message.author.id})
        if afk_doc and _afk_applies(afk_doc, message.guild.id):
            if afk_doc.get("strictness") == "soft":
                await self._end_afk(message.author, message.channel, forced=True)
                await self._log_afk_event(
                    message,
                    activity_type="AFK Auto Ended",
                    details="Soft AFK ended because user sent a message.",
                )
            elif self._should_send_notice(message.author.id, "hard_afk", 25):
                warning = (
                    "You committed to Hard AFK. Why are you chatting right now? "
                    "Stay disciplined and get back to work. Use `/afk end` if you are truly done."
                )
                await self._safe_dm(message.author, warning)

        focus_doc = await pomodoro_sessions_col.find_one({"user_id": message.author.id})
        if focus_doc and focus_doc.get("status") == "running" and focus_doc.get("guild_id") == message.guild.id:
            if self._should_send_notice(message.author.id, "pomodoro_focus", 20):
                warning = (
                    f"You are supposed to be focusing on '{focus_doc.get('task', 'your task')}'. "
                    "Distractions destroy progress. Get back to work."
                )
                try:
                    await message.channel.send(f"{message.author.mention} {warning}")
                except Exception:
                    pass
                await self._safe_dm(message.author, warning)

                await self._log_pomodoro_event(
                    guild=message.guild,
                    user=message.author,
                    activity_type="Focus Warning",
                    details="User sent a message during an active focus block.",
                    fields=[("Task", str(focus_doc.get("task", "Unknown"))[:200], False)],
                    jump_url=message.jump_url,
                )

        targets = {member.id for member in message.mentions if not member.bot}
        ref_author_id = await self._get_referenced_author_id(message)
        if ref_author_id and ref_author_id != message.author.id:
            targets.add(ref_author_id)
        if not targets:
            return

        afk_targets = await afks_col.find({"user_id": {"$in": list(targets)}}).to_list(length=None)
        for target in afk_targets:
            if not _afk_applies(target, message.guild.id):
                continue
            await self._append_missed_to_afk(target, message)

            started = target.get("started_at")
            since = f"<t:{int(started.timestamp())}:R>" if isinstance(started, datetime) else "Unknown"
            user_obj = message.guild.get_member(target["user_id"]) or self.bot.get_user(target["user_id"])
            user_label = user_obj.mention if user_obj else f"User `{target['user_id']}`"
            await message.channel.send(
                f"{user_label} is currently AFK: {target.get('reason', 'No reason provided')} (Since: {since})"
            )

            await self._log_afk_event(
                message,
                activity_type="AFK Triggered",
                details="An AFK user was mentioned or replied to.",
                fields=[("AFK User ID", str(target["user_id"]), True)],
            )

        focus_targets = await pomodoro_sessions_col.find(
            {
                "user_id": {"$in": list(targets)},
                "guild_id": message.guild.id,
                "status": {"$in": ["running", "paused"]},
            }
        ).to_list(length=None)
        for target in focus_targets:
            await self._append_missed_to_session(target, message)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProductivityCommands(bot))
