"""Interactive Games & Utilities Engine — Discord Cog."""

import asyncio
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from database import client as _mongo_client
from telemetry import log_exception, send_activity_log, send_game_telemetry, send_master_log

_game_db = _mongo_client["LeaderboardBotDB"]
keywords_col = _game_db["GameKeywords"]
tad_col = _game_db["TruthOrDare"]
quiz_col = _game_db["QuizQuestions"]

APP_LINK = "https://deepdey.vercel.app/"
INSTA_LINK = "https://deepdey.vercel.app/insta"
KEYWORD_CACHE_TTL = 300  # seconds between keyword cache refreshes
RPS_CHOICE_EMOJIS = ("✊", "✋", "✌️")
PASSWORD = os.getenv("PASSWORD")
APP_BUTTON_LABEL = "Deep Dey"
INSTA_BUTTON_LABEL = "Instagram"
DIRECT_GAME_WIN_POINTS = 15
DIRECT_GAME_LOSS_POINTS = -10
AUTO_GAME_WIN_POINTS = 20
AUTO_GAME_LOSS_POINTS = -5
TOSS_WIN_POINTS = 10
TOSS_LOSS_POINTS = -10
HANGMAN_FALLBACK_WORDS = (
    "python",
    "discord",
    "mongodb",
    "telemetry",
    "scheduler",
    "economy",
    "interaction",
    "challenge",
)
EIGHT_BALL_RESPONSES = (
    "It is certain.",
    "Without a doubt.",
    "Most likely.",
    "Yes — definitely.",
    "Ask again later.",
    "Cannot predict now.",
    "Concentrate and ask again.",
    "Don't count on it.",
    "Very doubtful.",
    "My reply is no.",
)

# ── Branding view ────────────────────────────────────────────────────────────

def _branding_view() -> discord.ui.View:
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label=APP_BUTTON_LABEL, url=APP_LINK, style=discord.ButtonStyle.link))
    v.add_item(discord.ui.Button(label=INSTA_BUTTON_LABEL, url=INSTA_LINK, style=discord.ButtonStyle.link))
    return v


def _format_points(delta: int | None) -> str:
    if delta is None:
        return "n/a"
    return f"{delta:+d}" if delta else "0"


def _member_delta(member: discord.abc.User, delta: int) -> str:
    return _format_points(None if getattr(member, "bot", False) else delta)


def _relative_ts(seconds_from_now: int) -> int:
    return int(datetime.now(timezone.utc).timestamp()) + max(1, seconds_from_now)


def _extract_candidate_words(text: str, *, min_len: int = 6) -> list[str]:
    tokens = re.findall(r"[A-Za-z]{%d,}" % min_len, text.lower())
    return [token for token in tokens if token.isalpha()]


def _scramble_text(value: str) -> str:
    chars = list(value)
    if len(chars) < 2:
        return value
    for idx in range(len(chars) - 1, 0, -1):
        swap_idx = secrets.randbelow(idx + 1)
        chars[idx], chars[swap_idx] = chars[swap_idx], chars[idx]
    scrambled = "".join(chars)
    if scrambled.lower() == value.lower():
        rotated = chars[1:] + chars[:1]
        return "".join(rotated)
    return scrambled


# ── RPS View ─────────────────────────────────────────────────────────────────

class RPSView(discord.ui.View):
    CHOICES = {"✊": "Rock", "✋": "Paper", "✌️": "Scissors"}
    WINS = {"Rock": "Scissors", "Paper": "Rock", "Scissors": "Paper"}

    def __init__(self, cog: "GameCommands", challenger: discord.Member, opponent: discord.Member, game_state: dict):
        super().__init__(timeout=120)
        self.cog = cog
        self.challenger = challenger
        self.opponent = opponent
        self.game_state = game_state
        self.first_player_id = secrets.choice([challenger.id, opponent.id])
        self.opening_move_complete = False
        self.resolved = False
        self.add_item(discord.ui.Button(label=APP_BUTTON_LABEL, url=APP_LINK, style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label=INSTA_BUTTON_LABEL, url=INSTA_LINK, style=discord.ButtonStyle.link))

        if opponent.bot and self.first_player_id == opponent.id:
            self.game_state[opponent.id] = secrets.choice(RPS_CHOICE_EMOJIS)
            self.opening_move_complete = True

    async def _handle_pick(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if uid not in (self.challenger.id, self.opponent.id):
            await interaction.response.send_message("⛔ You are not a player in this game.", ephemeral=True)
            return
        if self.resolved:
            await interaction.response.send_message("✅ This match is already resolved.", ephemeral=True)
            return
        if not self.opening_move_complete and uid != self.first_player_id:
            await interaction.response.send_message("⏳ Please wait for the opening move.", ephemeral=True)
            return
        if self.game_state.get(uid):
            await interaction.response.send_message("✅ You have already chosen!", ephemeral=True)
            return
        self.game_state[uid] = choice
        if uid == self.first_player_id:
            self.opening_move_complete = True
        await interaction.response.send_message(f"✅ You chose {choice}. Waiting for the other player…", ephemeral=True)

        if self.opponent.bot and uid == self.challenger.id and not self.game_state.get(self.opponent.id):
            self.game_state[self.opponent.id] = secrets.choice(RPS_CHOICE_EMOJIS)

        if self.game_state.get(self.challenger.id) and self.game_state.get(self.opponent.id):
            await self._resolve(interaction)

    async def _resolve(self, interaction: discord.Interaction):
        if self.resolved:
            return
        self.resolved = True
        c_pick = self.game_state[self.challenger.id]
        o_pick = self.game_state[self.opponent.id]
        c_name = self.CHOICES[c_pick]
        o_name = self.CHOICES[o_pick]

        result_summary: str
        if c_name == o_name:
            result = "🤝 It's a **Draw**!"
            result_summary = "Draw"
            point_changes = {self.challenger.id: 0, self.opponent.id: 0}
        elif self.WINS[c_name] == o_name:
            result = f"🎉 **{self.challenger.display_name}** wins!"
            result_summary = f"{self.challenger.display_name} won"
            point_changes = {
                self.challenger.id: DIRECT_GAME_WIN_POINTS,
                self.opponent.id: DIRECT_GAME_LOSS_POINTS,
            }
        else:
            result = f"🎉 **{self.opponent.display_name}** wins!"
            result_summary = f"{self.opponent.display_name} won"
            point_changes = {
                self.challenger.id: DIRECT_GAME_LOSS_POINTS,
                self.opponent.id: DIRECT_GAME_WIN_POINTS,
            }

        embed = discord.Embed(title="🪨 Rock Paper Scissors — Result", color=0x5865F2)
        embed.add_field(name=self.challenger.display_name, value=c_pick, inline=True)
        embed.add_field(name=self.opponent.display_name, value=o_pick, inline=True)
        embed.add_field(name="Result", value=result, inline=False)
        embed.add_field(
            name="Points",
            value=(
                f"{self.challenger.display_name}: {_format_points(point_changes[self.challenger.id])}\n"
                f"{self.opponent.display_name}: {_member_delta(self.opponent, point_changes[self.opponent.id])}"
            ),
            inline=False,
        )

        profile_updates = [
            {
                "member": self.challenger,
                "points": point_changes[self.challenger.id],
                "wins": 1 if point_changes[self.challenger.id] > 0 else 0,
                "losses": 1 if point_changes[self.challenger.id] < 0 else 0,
                "total_games": 1,
            },
            {
                "member": self.opponent,
                "points": point_changes[self.opponent.id],
                "wins": 1 if point_changes[self.opponent.id] > 0 else 0,
                "losses": 1 if point_changes[self.opponent.id] < 0 else 0,
                "total_games": 1,
            },
        ]

        self.stop()
        for item in self.children:
            if isinstance(item, discord.ui.Button) and not item.url:
                item.disabled = True
        try:
            await interaction.message.edit(embed=embed, view=_branding_view())
        except Exception:
            pass
        asyncio.create_task(
            self.cog._record_game_outcome(
                guild=interaction.guild,
                game_name="Rock Paper Scissors",
                result=result_summary,
                profile_updates=profile_updates,
                players=[
                    (self.challenger.display_name, self.challenger.id, _format_points(point_changes[self.challenger.id])),
                    (self.opponent.display_name, self.opponent.id, _member_delta(self.opponent, point_changes[self.opponent.id])),
                ],
            )
        )

    @discord.ui.button(emoji="✊", label="Rock", style=discord.ButtonStyle.primary)
    async def rock(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._handle_pick(interaction, "✊")

    @discord.ui.button(emoji="✋", label="Paper", style=discord.ButtonStyle.primary)
    async def paper(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._handle_pick(interaction, "✋")

    @discord.ui.button(emoji="✌️", label="Scissors", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._handle_pick(interaction, "✌️")

    async def on_timeout(self):
        self.stop()


# ── Tic-Tac-Toe View ─────────────────────────────────────────────────────────

class TTTButton(discord.ui.Button):
    def __init__(self, row: int, col: int):
        super().__init__(label="\u200b", style=discord.ButtonStyle.secondary, row=row)
        self.row_pos = row
        self.col_pos = col

    async def callback(self, interaction: discord.Interaction):
        view: TTTView = self.view  # type: ignore
        uid = interaction.user.id

        if uid not in (view.players[0].id, view.players[1].id):
            await interaction.response.send_message("⛔ You are not a player in this game.", ephemeral=True)
            return

        current_player = view.players[view.current_turn]
        if uid != current_player.id:
            await interaction.response.send_message("⏳ It's not your turn!", ephemeral=True)
            return

        if view.board[self.row_pos][self.col_pos] != "":
            await interaction.response.send_message("❌ That cell is already taken!", ephemeral=True)
            return

        symbol = "❌" if view.current_turn == 0 else "⭕"
        view.board[self.row_pos][self.col_pos] = symbol
        self.label = symbol
        self.style = discord.ButtonStyle.danger if symbol == "❌" else discord.ButtonStyle.success
        self.disabled = True

        winner = view.check_winner()
        is_draw = winner is None and all(view.board[r][c] != "" for r in range(3) for c in range(3))

        if winner:
            embed = discord.Embed(
                title="❎⭕ Tic-Tac-Toe — Result",
                description=f"🎉 **{current_player.display_name}** ({symbol}) wins!",
                color=0x5865F2,
            )
            loser = view.players[view.current_turn ^ 1]
            profile_updates = [
                {
                    "member": current_player,
                    "points": DIRECT_GAME_WIN_POINTS,
                    "wins": 1,
                    "losses": 0,
                    "total_games": 1,
                },
                {
                    "member": loser,
                    "points": DIRECT_GAME_LOSS_POINTS,
                    "wins": 0,
                    "losses": 1,
                    "total_games": 1,
                },
            ]
            embed.add_field(
                name="Points",
                value=(
                    f"{current_player.display_name}: {_format_points(DIRECT_GAME_WIN_POINTS)}\n"
                    f"{loser.display_name}: {_format_points(DIRECT_GAME_LOSS_POINTS)}"
                ),
                inline=False,
            )
            view.disable_all()
            view.stop()
            await interaction.response.edit_message(embed=embed, view=view)
            asyncio.create_task(
                view.cog._record_game_outcome(
                    guild=interaction.guild,
                    game_name="Tic-Tac-Toe",
                    result=f"{current_player.display_name} won",
                    profile_updates=profile_updates,
                    players=[
                        (current_player.display_name, current_player.id, _format_points(DIRECT_GAME_WIN_POINTS)),
                        (loser.display_name, loser.id, _format_points(DIRECT_GAME_LOSS_POINTS)),
                    ],
                )
            )
        elif is_draw:
            embed = discord.Embed(
                title="❎⭕ Tic-Tac-Toe — Result",
                description="🤝 It's a **Draw**!",
                color=0x5865F2,
            )
            profile_updates = [
                {
                    "member": view.players[0],
                    "points": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_games": 1,
                },
                {
                    "member": view.players[1],
                    "points": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_games": 1,
                },
            ]
            embed.add_field(
                name="Points",
                value=(
                    f"{view.players[0].display_name}: 0\n"
                    f"{view.players[1].display_name}: 0"
                ),
                inline=False,
            )
            view.disable_all()
            view.stop()
            await interaction.response.edit_message(embed=embed, view=view)
            asyncio.create_task(
                view.cog._record_game_outcome(
                    guild=interaction.guild,
                    game_name="Tic-Tac-Toe",
                    result="Draw",
                    profile_updates=profile_updates,
                    players=[
                        (view.players[0].display_name, view.players[0].id, "0"),
                        (view.players[1].display_name, view.players[1].id, "0"),
                    ],
                )
            )
        else:
            view.current_turn ^= 1
            next_player = view.players[view.current_turn]
            embed = discord.Embed(
                title="❎⭕ Tic-Tac-Toe",
                description=f"Turn: **{next_player.mention}** ({'❌' if view.current_turn == 0 else '⭕'})",
                color=0x5865F2,
            )
            await interaction.response.edit_message(embed=embed, view=view)


class TTTView(discord.ui.View):
    def __init__(self, cog: "GameCommands", p1: discord.Member, p2: discord.Member):
        super().__init__(timeout=180)
        self.cog = cog
        first_player = secrets.choice([p1, p2])
        self.players = [first_player, p2 if first_player.id == p1.id else p1]
        self.current_turn = 0
        self.board: list[list[str]] = [["", "", ""], ["", "", ""], ["", "", ""]]

        for r in range(3):
            for c in range(3):
                self.add_item(TTTButton(r, c))

        self.add_item(discord.ui.Button(label=APP_BUTTON_LABEL, url=APP_LINK, style=discord.ButtonStyle.link, row=4))
        self.add_item(discord.ui.Button(label=INSTA_BUTTON_LABEL, url=INSTA_LINK, style=discord.ButtonStyle.link, row=4))

    def check_winner(self) -> Optional[str]:
        b = self.board
        lines = [
            [b[0][0], b[0][1], b[0][2]],
            [b[1][0], b[1][1], b[1][2]],
            [b[2][0], b[2][1], b[2][2]],
            [b[0][0], b[1][0], b[2][0]],
            [b[0][1], b[1][1], b[2][1]],
            [b[0][2], b[1][2], b[2][2]],
            [b[0][0], b[1][1], b[2][2]],
            [b[0][2], b[1][1], b[2][0]],
        ]
        for line in lines:
            if line[0] != "" and len(set(line)) == 1:
                return line[0]
        return None

    def disable_all(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button) and not item.url:
                item.disabled = True

    async def on_timeout(self):
        self.disable_all()
        self.stop()


# ── Memory Game View ──────────────────────────────────────────────────────────

class MemoryButton(discord.ui.Button):
    def __init__(self, index: int, row: int):
        super().__init__(label="❓", style=discord.ButtonStyle.secondary, row=row)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: MemoryView = self.view  # type: ignore
        await view.handle_click(interaction, self)


class MemoryView(discord.ui.View):
    def __init__(self, cog: "GameCommands", player: discord.Member, *, rows: int, cols: int, countdown_ts: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.player = player
        self.rows = rows
        self.cols = cols
        self.countdown_ts = countdown_ts
        self.total_cells = rows * cols
        pair_count = self.total_cells // 2
        emoji_pool = ["🍎", "🍉", "🍇", "🍌", "🍒", "🥝", "🍋", "🍍", "🥭", "🍑", "🌟", "⚡"]
        selected = [emoji_pool[i % len(emoji_pool)] for i in range(pair_count)]
        self.hidden_values = selected + selected
        for idx in range(len(self.hidden_values) - 1, 0, -1):
            swap_idx = secrets.randbelow(idx + 1)
            self.hidden_values[idx], self.hidden_values[swap_idx] = self.hidden_values[swap_idx], self.hidden_values[idx]
        self.matched: set[int] = set()
        self.opened: list[int] = []
        self.locked = False
        self.moves = 0

        for r in range(rows):
            for c in range(cols):
                index = (r * cols) + c
                self.add_item(MemoryButton(index=index, row=r))
        self.add_item(discord.ui.Button(label=APP_BUTTON_LABEL, url=APP_LINK, style=discord.ButtonStyle.link, row=4))
        self.add_item(discord.ui.Button(label=INSTA_BUTTON_LABEL, url=INSTA_LINK, style=discord.ButtonStyle.link, row=4))

    def _button_by_index(self, index: int) -> MemoryButton | None:
        for item in self.children:
            if isinstance(item, MemoryButton) and item.index == index:
                return item
        return None

    async def handle_click(self, interaction: discord.Interaction, button: MemoryButton):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("⛔ Only the command user can play this board.", ephemeral=True)
            return
        if self.locked:
            await interaction.response.send_message("⏳ Pair is being checked, wait a moment.", ephemeral=True)
            return
        if button.index in self.matched or button.index in self.opened:
            await interaction.response.send_message("✅ This tile is already open.", ephemeral=True)
            return

        button.label = self.hidden_values[button.index]
        button.style = discord.ButtonStyle.primary
        self.opened.append(button.index)
        await interaction.response.edit_message(view=self)

        if len(self.opened) < 2:
            return

        self.locked = True
        self.moves += 1
        first, second = self.opened
        if self.hidden_values[first] == self.hidden_values[second]:
            self.matched.update({first, second})
            fb = self._button_by_index(first)
            sb = self._button_by_index(second)
            if fb:
                fb.disabled = True
                fb.style = discord.ButtonStyle.success
            if sb:
                sb.disabled = True
                sb.style = discord.ButtonStyle.success
            self.opened.clear()
            self.locked = False
        else:
            await asyncio.sleep(1.0)
            fb = self._button_by_index(first)
            sb = self._button_by_index(second)
            if fb:
                fb.label = "❓"
                fb.style = discord.ButtonStyle.secondary
            if sb:
                sb.label = "❓"
                sb.style = discord.ButtonStyle.secondary
            self.opened.clear()
            self.locked = False

        if len(self.matched) == self.total_cells:
            for item in self.children:
                if isinstance(item, MemoryButton):
                    item.disabled = True
            self.stop()
            win_embed = discord.Embed(
                title="🧠 Memory — Result",
                description=(
                    f"🎉 {self.player.mention} completed the board!\n"
                    f"Moves: **{self.moves}**\n"
                    f"Finished: <t:{self.countdown_ts}:R>"
                ),
                color=0x5865F2,
            )
            win_embed.add_field(name="Point Change", value=_format_points(DIRECT_GAME_WIN_POINTS), inline=False)
            win_embed.set_footer(text="an app by deep")
            await interaction.message.edit(embed=win_embed, view=_branding_view())
            asyncio.create_task(
                self.cog._record_game_outcome(
                    guild=interaction.guild,
                    game_name="Memory",
                    result=f"{self.player.display_name} completed board",
                    profile_updates=[
                        {
                            "member": self.player,
                            "points": DIRECT_GAME_WIN_POINTS,
                            "wins": 1,
                            "losses": 0,
                            "total_games": 1,
                        }
                    ],
                    players=[(self.player.display_name, self.player.id, _format_points(DIRECT_GAME_WIN_POINTS))],
                )
            )
        else:
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

# ── Quiz View ─────────────────────────────────────────────────────────────────

class QuizView(discord.ui.View):
    def __init__(self, cog: "GameCommands", question: dict, invoker_id: int):
        super().__init__(timeout=30)
        self.cog = cog
        self.invoker_id = invoker_id
        self.correct = question["correct_answer"]
        self.answered = False

        options = question.get("options", [])
        for opt in options:
            btn = discord.ui.Button(label=opt, style=discord.ButtonStyle.primary)
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

        self.add_item(discord.ui.Button(label=APP_BUTTON_LABEL, url=APP_LINK, style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label=INSTA_BUTTON_LABEL, url=INSTA_LINK, style=discord.ButtonStyle.link))

    def _make_callback(self, option: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.invoker_id:
                await interaction.response.send_message("⛔ This quiz is not for you!", ephemeral=True)
                return
            if self.answered:
                await interaction.response.send_message("✅ Already answered!", ephemeral=True)
                return
            self.answered = True
            self.stop()

            for item in self.children:
                if isinstance(item, discord.ui.Button) and not item.url:
                    item.disabled = True
                    if item.label == self.correct:
                        item.style = discord.ButtonStyle.success
                    elif item.label == option and option != self.correct:
                        item.style = discord.ButtonStyle.danger

            if option == self.correct:
                msg = f"✅ Correct! **{self.correct}** is the right answer."
                points = AUTO_GAME_WIN_POINTS
                wins = 1
                losses = 0
                result = "Correct answer"
            else:
                msg = f"❌ Wrong! The correct answer was **{self.correct}**."
                points = AUTO_GAME_LOSS_POINTS
                wins = 0
                losses = 1
                result = "Wrong answer"

            profile_updates = [
                {
                    "member": interaction.user,
                    "points": points,
                    "wins": wins,
                    "losses": losses,
                    "total_games": 1,
                }
            ]
            await interaction.response.edit_message(content=msg, view=self)
            asyncio.create_task(
                self.cog._record_game_outcome(
                    guild=interaction.guild,
                    game_name="Quiz",
                    result=result,
                    profile_updates=profile_updates,
                    players=[(interaction.user.display_name, interaction.user.id, _format_points(points))],
                )
            )

        return callback


def _password_ok(raw_password: str) -> bool:
    return bool(PASSWORD and secrets.compare_digest(raw_password, PASSWORD))


class _AddTADModal(discord.ui.Modal, title="Add Truth or Dare"):
    truth = discord.ui.TextInput(label="Truth", required=False, style=discord.TextStyle.paragraph, max_length=1000)
    dare = discord.ui.TextInput(label="Dare", required=False, style=discord.TextStyle.paragraph, max_length=1000)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not _password_ok(self.password.value):
                await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
                return

            truth_text = self.truth.value.strip()
            dare_text = self.dare.value.strip()
            if not truth_text and not dare_text:
                await interaction.response.send_message("❌ Add at least one Truth or Dare entry.", ephemeral=True)
                return

            entries = []
            if truth_text:
                entries.append({"type": "truth", "text": truth_text})
            if dare_text:
                entries.append({"type": "dare", "text": dare_text})

            await tad_col.insert_many(entries)
            await interaction.response.send_message("✅ Truth or Dare content saved successfully.", ephemeral=True)
            await send_master_log(
                self.cog.bot,
                "Truth or Dare Added",
                f"{interaction.user.mention} added Truth or Dare content.",
                fields=[("Entries Added", str(len(entries)), True), ("Guild", str(interaction.guild_id), True)],
            )
        except Exception as exc:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Failed to save Truth or Dare content.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Failed to save Truth or Dare content.", ephemeral=True)
            await log_exception(
                self.cog.bot,
                title="Truth or Dare Save Failed",
                error=exc,
                context=f"User {interaction.user.id} in guild {interaction.guild_id}",
            )


class _AddQuizModal(discord.ui.Modal, title="Add Quiz Question"):
    question = discord.ui.TextInput(label="Question", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    options = discord.ui.TextInput(label="Options (Comma-separated)", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    correct_answer = discord.ui.TextInput(label="Correct Answer", required=True, style=discord.TextStyle.short, max_length=200)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not _password_ok(self.password.value):
                await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
                return

            options = [part.strip() for part in self.options.value.split(",") if part.strip()]
            correct = self.correct_answer.value.strip()
            question = self.question.value.strip()
            if len(options) < 2:
                await interaction.response.send_message("❌ Please provide at least two options.", ephemeral=True)
                return
            if correct not in options:
                await interaction.response.send_message("❌ The correct answer must match one of the provided options.", ephemeral=True)
                return

            await quiz_col.insert_one({"question": question, "options": options, "correct_answer": correct})
            await interaction.response.send_message("✅ Quiz question saved successfully.", ephemeral=True)
            await send_master_log(
                self.cog.bot,
                "Quiz Question Added",
                f"{interaction.user.mention} added a quiz question.",
                fields=[("Question", question[:1024], False), ("Guild", str(interaction.guild_id), True)],
            )
        except Exception as exc:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Failed to save the quiz question.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Failed to save the quiz question.", ephemeral=True)
            await log_exception(
                self.cog.bot,
                title="Quiz Save Failed",
                error=exc,
                context=f"User {interaction.user.id} in guild {interaction.guild_id}",
            )


class _AddAutoReplyModal(discord.ui.Modal, title="Add Auto Reply"):
    keywords = discord.ui.TextInput(label="Keywords (Comma-separated)", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    reply = discord.ui.TextInput(label="Reply Message", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not _password_ok(self.password.value):
                await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
                return

            parsed_keywords = [kw.strip().lower() for kw in self.keywords.value.split(",") if kw.strip()]
            reply_msg = self.reply.value.strip()
            if not parsed_keywords or not reply_msg:
                await interaction.response.send_message("❌ Keywords and reply are required.", ephemeral=True)
                return

            await keywords_col.insert_many([{"trigger": kw, "reply": reply_msg} for kw in parsed_keywords])
            await self.cog._load_keyword_cache()
            await interaction.response.send_message("✅ Auto-reply keywords saved successfully.", ephemeral=True)
            await send_master_log(
                self.cog.bot,
                "Auto-Reply Added",
                f"{interaction.user.mention} added auto-reply keywords.",
                fields=[
                    ("Keyword Count", str(len(parsed_keywords)), True),
                    ("Guild", str(interaction.guild_id), True),
                ],
            )
        except Exception as exc:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Failed to save auto-reply keywords.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Failed to save auto-reply keywords.", ephemeral=True)
            await log_exception(
                self.cog.bot,
                title="Auto-Reply Save Failed",
                error=exc,
                context=f"User {interaction.user.id} in guild {interaction.guild_id}",
            )


class _SendMessageModal(discord.ui.Modal, title="Send Branded Message"):
    message = discord.ui.TextInput(label="Message Content", required=True, style=discord.TextStyle.paragraph, max_length=2000)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands", target_channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not _password_ok(self.password.value):
                await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
                return

            embed = discord.Embed(description=self.message.value.strip(), color=0x5865F2)
            embed.set_footer(text="Sent by Deep")

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label=APP_BUTTON_LABEL, url=APP_LINK, style=discord.ButtonStyle.link))
            view.add_item(discord.ui.Button(label=INSTA_BUTTON_LABEL, url=INSTA_LINK, style=discord.ButtonStyle.link))

            await self.target_channel.send(embed=embed, view=view)
            await interaction.response.send_message(f"✅ Message sent to {self.target_channel.mention}.", ephemeral=True)
            await send_master_log(
                self.cog.bot,
                "Game Message Sent",
                f"{interaction.user.mention} sent a branded message.",
                fields=[
                    ("Target Channel", self.target_channel.mention, True),
                    ("Guild", str(interaction.guild_id), True),
                ],
            )
        except Exception as exc:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Failed to send the branded message.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Failed to send the branded message.", ephemeral=True)
            await log_exception(
                self.cog.bot,
                title="Branded Message Failed",
                error=exc,
                context=f"User {interaction.user.id} in guild {interaction.guild_id}",
            )


class _DeleteAutoReplyModal(discord.ui.Modal, title="Delete Auto-Reply Keyword"):
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands", trigger: str):
        super().__init__()
        self.cog = cog
        self.trigger = trigger.strip().lower()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        lock_key = (interaction.user.id, "autoreply_delete")
        if self.cog._security_failures.get(lock_key, 0) >= 3:
            await interaction.response.send_message("You are locked out after 3 failed password attempts.", ephemeral=True)
            return

        if not _password_ok(self.password.value):
            failures = self.cog._security_failures.get(lock_key, 0) + 1
            self.cog._security_failures[lock_key] = failures
            await interaction.response.send_message(
                f"Invalid password. Attempt {failures}/3.",
                ephemeral=True,
            )
            if failures >= 3:
                await send_activity_log(
                    self.cog.bot,
                    activity_type="Security Lockout",
                    details="User reached 3 failed attempts for /autoreply delete.",
                    module="Security",
                    guild=interaction.guild,
                    user=interaction.user,
                )
            return

        self.cog._security_failures.pop(lock_key, None)
        result = await keywords_col.delete_one({"trigger": self.trigger})
        if result.deleted_count:
            await interaction.response.send_message(
                f"Auto-reply trigger `{self.trigger}` deleted successfully.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"No auto-reply trigger found for `{self.trigger}`.",
                ephemeral=True,
            )


# ── Game Cog ─────────────────────────────────────────────────────────────────

class GameCommands(commands.Cog):
    """Cog housing all /game subcommands and the auto-responder."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._keyword_cache: dict[str, str] = {}
        self._cache_ts = 0.0
        self._autogame_next_run: dict[int, float] = {}
        self._security_failures: dict[tuple[int, str], int] = {}
        self._refresh_keyword_cache.start()
        self._autogame_scheduler.start()

    def cog_unload(self):
        self._refresh_keyword_cache.cancel()
        self._autogame_scheduler.cancel()

    async def _load_keyword_cache(self):
        docs = await keywords_col.find({}).to_list(length=None)
        cache: dict[str, str] = {}
        for doc in docs:
            reply = str(doc.get("reply") or "").strip()
            if not reply:
                continue
            trigger = str(doc.get("trigger") or "").strip().lower()
            if trigger:
                cache[trigger] = reply
            for kw in doc.get("keywords", []):
                text = str(kw).strip().lower()
                if text:
                    cache[text] = reply
        self._keyword_cache = cache
        self._cache_ts = time.monotonic()

    async def _apply_profile_updates(self, updates: list[dict]):
        payload = []
        for update in updates:
            member = update.get("member")
            if not member or getattr(member, "bot", False):
                continue
            payload.append(
                {
                    "user_id": member.id,
                    "points": int(update.get("points", 0)),
                    "wins": int(update.get("wins", 0)),
                    "losses": int(update.get("losses", 0)),
                    "total_games": int(update.get("total_games", 0)),
                }
            )
        await db.bulk_update_user_profiles(payload)

    async def _log_game_result(
        self,
        *,
        guild: discord.Guild | None,
        game_name: str,
        result: str,
        players: list[tuple[str, int, str]],
    ):
        await send_game_telemetry(
            self.bot,
            guild=guild,
            game_name=game_name,
            result=result,
            players=players,
        )

    async def _log_local_game_result(
        self,
        *,
        guild: discord.Guild | None,
        game_name: str,
        result: str,
        players: list[tuple[str, int, str]],
    ):
        if not guild:
            return
        settings = await db.get_guild_settings(guild.id)
        channel_id = settings.get("game_logs_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        people = "\n".join([f"• {name} ({uid})" for name, uid, _ in players]) or "-"
        points = "\n".join([f"• {name}: {delta}" for name, _, delta in players]) or "-"
        embed = discord.Embed(
            title="🎮 Local Game Summary",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Game Type", value=game_name, inline=True)
        embed.add_field(name="Result", value=result, inline=True)
        embed.add_field(name="Players", value=people[:1024], inline=False)
        embed.add_field(name="Points Changed", value=points[:1024], inline=False)
        embed.set_footer(text="an app by deep")
        try:
            await channel.send(embed=embed, view=_branding_view())
        except Exception:
            pass

    async def _record_game_outcome(
        self,
        *,
        guild: discord.Guild | None,
        game_name: str,
        result: str,
        players: list[tuple[str, int, str]],
        profile_updates: list[dict],
    ):
        try:
            await self._apply_profile_updates(profile_updates)
        except Exception as exc:
            await log_exception(
                self.bot,
                title=f"{game_name} Economy Update Failed",
                error=exc,
                context=f"Guild: {guild.id if guild else 'unknown'} | Result: {result}",
            )
        await self._log_game_result(
            guild=guild,
            game_name=game_name,
            result=result,
            players=players,
        )
        await self._log_local_game_result(
            guild=guild,
            game_name=game_name,
            result=result,
            players=players,
        )

    async def _run_public_challenge(
        self,
        *,
        interaction: discord.Interaction,
        game_name: str,
        prompt_title: str,
        prompt_description: str,
        expected_answer: str,
        timeout_seconds: int = 20,
    ):
        embed = discord.Embed(
            title=prompt_title,
            description=prompt_description,
            color=0x5865F2,
        )
        embed.set_footer(text=f"Answer in this channel within {timeout_seconds}s • an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

        def _check(message: discord.Message) -> bool:
            return (
                message.guild is not None
                and interaction.guild is not None
                and message.guild.id == interaction.guild.id
                and message.channel.id == interaction.channel_id
                and not message.author.bot
            )

        try:
            response = await self.bot.wait_for("message", timeout=timeout_seconds, check=_check)
        except asyncio.TimeoutError:
            result_embed = discord.Embed(
                title=f"{game_name} — Result",
                description="⏰ No one answered in time. No points changed.",
                color=0x5865F2,
            )
            result_embed.add_field(name="Point Change", value="0", inline=False)
            result_embed.set_footer(text="an app by deep")
            await interaction.followup.send(embed=result_embed, view=_branding_view())
            asyncio.create_task(
                self._record_game_outcome(
                    guild=interaction.guild,
                    game_name=game_name,
                    result="No response (timeout)",
                    profile_updates=[],
                    players=[],
                )
            )
            return

        given = response.content.strip()
        is_correct = secrets.compare_digest(given.lower(), expected_answer.lower())
        points = AUTO_GAME_WIN_POINTS if is_correct else AUTO_GAME_LOSS_POINTS
        result_text = "Correct answer" if is_correct else "Wrong answer"
        result_embed = discord.Embed(
            title=f"{game_name} — Result",
            description=(
                f"{'✅ Correct!' if is_correct else '❌ Wrong!'}\n"
                f"Answer by {response.author.mention}\n"
                f"Expected: **{expected_answer}**"
            ),
            color=0x5865F2,
        )
        result_embed.add_field(name="Point Change", value=_format_points(points), inline=False)
        result_embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=result_embed, view=_branding_view())
        asyncio.create_task(
            self._record_game_outcome(
                guild=interaction.guild,
                game_name=game_name,
                result=result_text,
                profile_updates=[
                    {
                        "member": response.author,
                        "points": points,
                        "wins": 1 if points > 0 else 0,
                        "losses": 1 if points < 0 else 0,
                        "total_games": 1,
                    }
                ],
                players=[(response.author.display_name, response.author.id, _format_points(points))],
            )
        )

    async def _pick_scramble_word_from_history(self, channel: discord.TextChannel) -> str:
        start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        candidates: list[str] = []
        async for msg in channel.history(limit=300, after=start_of_day):
            if msg.author.bot:
                continue
            candidates.extend(_extract_candidate_words(msg.content, min_len=6))
        if candidates:
            return secrets.choice(candidates)
        return secrets.choice(list(HANGMAN_FALLBACK_WORDS))

    async def _pick_hangman_word(self, interaction: discord.Interaction, min_len: int, max_len: int) -> str:
        if isinstance(interaction.channel, discord.TextChannel):
            user_words: list[str] = []
            async for msg in interaction.channel.history(limit=400):
                if msg.author.id != interaction.user.id:
                    continue
                for token in _extract_candidate_words(msg.content, min_len=min_len):
                    if min_len <= len(token) <= max_len:
                        user_words.append(token)
                if len(user_words) >= 100:
                    break
            if user_words:
                return secrets.choice(user_words)

            server_words: list[str] = []
            async for msg in interaction.channel.history(limit=600):
                if msg.author.bot:
                    continue
                for token in _extract_candidate_words(msg.content, min_len=min_len):
                    if min_len <= len(token) <= max_len:
                        server_words.append(token)
            if server_words:
                return secrets.choice(server_words)

        fallback_filtered = [w for w in HANGMAN_FALLBACK_WORDS if min_len <= len(w) <= max_len]
        if fallback_filtered:
            return secrets.choice(fallback_filtered)
        return secrets.choice(list(HANGMAN_FALLBACK_WORDS))

    async def _build_autogame_payload(self, channel: discord.TextChannel) -> tuple[str, str, str]:
        event_type = secrets.choice(["scramble", "math", "quiz"])
        if event_type == "scramble":
            answer = await self._pick_scramble_word_from_history(channel)
            return (
                "Word Scramble",
                f"Unscramble this word: **{_scramble_text(answer)}**",
                answer,
            )
        if event_type == "math":
            left = secrets.randbelow(41) + 10
            right = secrets.randbelow(31) + 1
            operator = secrets.choice(["+", "-", "*"])
            if operator == "+":
                value = left + right
            elif operator == "-":
                value = left - right
            else:
                value = left * right
            return ("Math Challenge", f"Solve: **{left} {operator} {right} = ?**", str(value))

        docs = await quiz_col.find({}).to_list(length=None)
        if docs:
            chosen = secrets.choice(docs)
            return ("Quiz", str(chosen.get("question") or "What is 2 + 2?"), str(chosen.get("correct_answer") or "4"))
        return ("Quiz", "Fallback quiz: What is 2 + 2?", "4")

    async def _run_scheduled_autogame(
        self,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel,
        role: Optional[discord.Role],
        interval_mins: int,
    ):
        game_name, prompt_text, expected_answer = await self._build_autogame_payload(channel)
        deadline_ts = _relative_ts(60)
        embed = discord.Embed(
            title=f"⚡ Auto-Game • {game_name}",
            description=(
                f"{prompt_text}\n\n"
                f"⏳ Ends <t:{deadline_ts}:R>\n"
                "Reply directly to this bot message with your answer."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        ping_content = role.mention if role else None
        game_message = await channel.send(content=ping_content, embed=embed, view=_branding_view())

        updates: list[dict] = []
        player_changes: list[tuple[str, int, str]] = []
        penalized_users: set[int] = set()
        winner: discord.Member | discord.User | None = None
        winner_response: discord.Message | None = None
        timeout_seconds = max(1, deadline_ts - int(datetime.now(timezone.utc).timestamp()))

        def _check(message: discord.Message) -> bool:
            if message.author.bot or message.guild is None:
                return False
            if message.guild.id != guild.id or message.channel.id != channel.id:
                return False
            if not message.reference or message.reference.message_id != game_message.id:
                return False
            return True

        start_ts = time.time()
        while (time.time() - start_ts) < timeout_seconds:
            remaining = timeout_seconds - (time.time() - start_ts)
            if remaining <= 0:
                break
            try:
                reply = await self.bot.wait_for("message", timeout=remaining, check=_check)
            except asyncio.TimeoutError:
                break

            answer = reply.content.strip()
            if secrets.compare_digest(answer.lower(), expected_answer.lower()):
                winner = reply.author
                winner_response = reply
                updates.append(
                    {
                        "member": reply.author,
                        "points": AUTO_GAME_WIN_POINTS,
                        "wins": 1,
                        "losses": 0,
                        "total_games": 1,
                    }
                )
                player_changes.append((reply.author.display_name, reply.author.id, _format_points(AUTO_GAME_WIN_POINTS)))
                break

            if reply.author.id not in penalized_users:
                penalized_users.add(reply.author.id)
                updates.append(
                    {
                        "member": reply.author,
                        "points": AUTO_GAME_LOSS_POINTS,
                        "wins": 0,
                        "losses": 1,
                        "total_games": 1,
                    }
                )
                player_changes.append((reply.author.display_name, reply.author.id, _format_points(AUTO_GAME_LOSS_POINTS)))
                await channel.send(
                    f"❌ {reply.author.mention} wrong reply (-5). Keep trying! Ends <t:{deadline_ts}:R>",
                    view=_branding_view(),
                )

        next_time = datetime.now(timezone.utc) + timedelta(minutes=interval_mins)
        if winner:
            summary = (
                f"🏆 Winner: {winner.mention}\n"
                f"Correct reply: {winner_response.content if winner_response else expected_answer}\n"
                f"Next tentative event: <t:{int(next_time.timestamp())}:R>"
            )
            result = f"{winner.display_name} won auto-game"
        else:
            summary = (
                "⏰ No correct reply in time.\n"
                "No winner this round.\n"
                f"Next tentative event: <t:{int(next_time.timestamp())}:R>"
            )
            result = "No winner (timeout)"

        result_embed = discord.Embed(
            title=f"📢 Auto-Game Result • {game_name}",
            description=summary,
            color=0x5865F2,
        )
        result_embed.add_field(name="Correct Answer", value=f"**{expected_answer}**", inline=False)
        result_embed.set_footer(text="an app by deep")
        await channel.send(embed=result_embed, view=_branding_view())
        await self._record_game_outcome(
            guild=guild,
            game_name=f"Auto {game_name}",
            result=result,
            players=player_changes,
            profile_updates=updates,
        )

    # ── Keyword cache ────────────────────────────────────────────────────────

    @tasks.loop(seconds=KEYWORD_CACHE_TTL)
    async def _refresh_keyword_cache(self):
        try:
            await self._load_keyword_cache()
        except Exception as exc:
            print(f"[GameCog] Keyword cache refresh failed: {exc}")

    @_refresh_keyword_cache.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=15)
    async def _autogame_scheduler(self):
        now = time.time()
        async for settings in db.settings_col.find({}):
            guild_id = settings.get("guild_id")
            channel_id = settings.get("autogame_channel_id")
            role_id = settings.get("autogame_role_id")  # may be None (silent drop)
            interval_mins = int(settings.get("autogame_interval_minutes") or 0)
            if not guild_id or not channel_id or interval_mins <= 0:
                continue

            next_run = self._autogame_next_run.get(guild_id)
            if next_run is None:
                self._autogame_next_run[guild_id] = now + (interval_mins * 60)
                continue
            if now < next_run:
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                self._autogame_next_run[guild_id] = now + (interval_mins * 60)
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                self._autogame_next_run[guild_id] = now + (interval_mins * 60)
                continue
            role = guild.get_role(role_id) if role_id else None
            await self._run_scheduled_autogame(guild=guild, channel=channel, role=role, interval_mins=interval_mins)
            self._autogame_next_run[guild_id] = now + (interval_mins * 60)

    @_autogame_scheduler.before_loop
    async def _before_autogame_scheduler(self):
        await self.bot.wait_until_ready()

    # ── /game group ──────────────────────────────────────────────────────────

    game_group = app_commands.Group(name="game", description="Games & fun commands")
    add_group = app_commands.Group(name="add", description="Securely add game content", parent=game_group)
    send_group = app_commands.Group(name="send", description="Send managed game messages", parent=game_group)
    autoreply_group = app_commands.Group(name="autoreply", description="Manage auto-replies")
    autogame_group = app_commands.Group(
        name="autogame",
        description="Control the auto-game engine",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ── /autogame subcommands ─────────────────────────────────────────────────

    @autogame_group.command(name="edit", description="Edit the auto-game configuration for this server")
    @app_commands.describe(
        channel="New channel for auto-games (leave empty to keep current)",
        ping_role="New role to ping (leave empty to keep current)",
        clear_ping_role="Set to True to remove the ping role and enable silent drops",
        interval_in_minutes="New interval in minutes (leave empty to keep current)",
    )
    async def autogame_edit(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        ping_role: discord.Role | None = None,
        clear_ping_role: bool = False,
        interval_in_minutes: app_commands.Range[int, 1, 1440] | None = None,
    ):
        await interaction.response.defer(thinking=True)
        settings = await db.get_guild_settings(interaction.guild_id)
        if not settings.get("autogame_channel_id"):
            await interaction.followup.send("❌ No auto-game config found. Use `/setup autogame` first.", ephemeral=True)
            return

        updates: dict = {}
        if channel is not None:
            updates["autogame_channel_id"] = channel.id
        if ping_role is not None:
            updates["autogame_role_id"] = ping_role.id
        elif clear_ping_role:
            updates["autogame_role_id"] = None
        if interval_in_minutes is not None:
            updates["autogame_interval_minutes"] = int(interval_in_minutes)

        if not updates:
            await interaction.followup.send("ℹ️ No changes provided — nothing was updated.", ephemeral=True)
            return

        await db.update_guild_settings(interaction.guild_id, updates)

        lines = []
        if channel:
            lines.append(f"Channel → {channel.mention}")
        if ping_role:
            lines.append(f"Ping Role → {ping_role.mention}")
        elif clear_ping_role:
            lines.append("Ping Role → *(cleared — silent drop)*")
        if interval_in_minutes is not None:
            lines.append(f"Interval → **{interval_in_minutes} minute(s)**")

        await interaction.followup.send(
            "✅ Auto-game config updated:\n" + "\n".join(lines),
            view=_branding_view(),
        )

    @autogame_group.command(name="stop", description="Stop the auto-game engine and clear its config for this server")
    async def autogame_stop(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await db.update_guild_settings(
            interaction.guild_id,
            {
                "autogame_channel_id": None,
                "autogame_role_id": None,
                "autogame_interval_minutes": 0,
            },
        )
        self._autogame_next_run.pop(interaction.guild_id, None)
        await interaction.followup.send("🛑 Auto-game engine stopped and config cleared.", view=_branding_view())

    @autogame_group.command(name="restart", description="Restart the auto-game timer from zero for this server")
    async def autogame_restart(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        settings = await db.get_guild_settings(interaction.guild_id)
        interval_mins = int(settings.get("autogame_interval_minutes") or 0)
        if not settings.get("autogame_channel_id") or interval_mins <= 0:
            await interaction.followup.send("❌ No active auto-game config. Use `/setup autogame` first.", ephemeral=True)
            return
        self._autogame_next_run[interaction.guild_id] = time.time() + (interval_mins * 60)
        next_ts = int(datetime.now(timezone.utc).timestamp()) + (interval_mins * 60)
        await interaction.followup.send(
            f"🔄 Auto-game timer reset. Next game: <t:{next_ts}:R>",
            view=_branding_view(),
        )

    @autogame_group.command(name="force", description="Instantly drop an auto-game in the configured channel")
    async def autogame_force(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        settings = await db.get_guild_settings(interaction.guild_id)
        channel_id = settings.get("autogame_channel_id")
        role_id = settings.get("autogame_role_id")
        interval_mins = int(settings.get("autogame_interval_minutes") or 60)
        if not channel_id:
            await interaction.followup.send("❌ No auto-game channel configured. Use `/setup autogame` first.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("❌ Configured channel not found or is not a text channel.", ephemeral=True)
            return
        role = interaction.guild.get_role(role_id) if role_id else None
        await interaction.followup.send("⚡ Forcing auto-game drop now…", ephemeral=True)
        await self._run_scheduled_autogame(
            guild=interaction.guild,
            channel=channel,
            role=role,
            interval_mins=interval_mins,
        )
        self._autogame_next_run[interaction.guild_id] = time.time() + (interval_mins * 60)

    # /game help
    @game_group.command(name="help", description="Show all available game commands")
    async def game_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Game Command Center",
            description="Everything available in the Gamification Engine, grouped by play style.",
            color=0x5865F2,
        )
        embed.add_field(
            name="🧠 Strategy Games",
            value=(
                "`/game ttt <@opponent>` — Multiplayer Tic-Tac-Toe.\n"
                "`/game minesweeper <row> <column>` — Risk a hidden mine.\n"
                "`/game memory <easy|medium|hard>` — Match hidden emoji pairs."
            ),
            inline=False,
        )
        embed.add_field(
            name="⚡ Speed & Focus",
            value=(
                "`/game scramble` — Fastest finger word scramble from today history.\n"
                "`/game math <easy|medium|hard|extreme>` — Timed math race.\n"
                "`/game guess <easy|medium|hard|extreme>` — Limited-attempt number guess.\n"
                "`/game hangman` — Word guessing from message history + fallback list.\n"
                "`/game quiz` — Single-user quick question."
            ),
            inline=False,
        )
        embed.add_field(
            name="🎲 Mini Games",
            value=(
                "`/game toss <heads|tails>` — Instant coin toss.\n"
                "`/game dice` — Secure d6 roll.\n"
                "`/game 8ball <question>` — Magic 8-Ball prediction.\n"
                "`/game rps <@opponent>` — Rock-Paper-Scissors."
            ),
            inline=False,
        )
        embed.add_field(
            name="💰 Economy Rules",
            value=(
                "Direct wins/losses: **+15 / -10**\n"
                "Public fastest-finger: **+20** correct, **-5** wrong, **0** ignore\n"
                "Toss: **+10 / -10**\n"
                "Use `/myprofile` for points, history, and ranking."
            ),
            inline=False,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

    @add_group.command(name="tad", description="Add Truth or Dare entries securely")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def game_add_tad(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_AddTADModal(self))

    @add_group.command(name="quiz", description="Add quiz questions securely")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def game_add_quiz(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_AddQuizModal(self))

    @add_group.command(name="autoreply", description="Add auto-reply keywords securely")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def game_add_autoreply(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_AddAutoReplyModal(self))

    @send_group.command(name="message", description="Send a branded message to a target channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def game_send_message(self, interaction: discord.Interaction, target_channel: discord.TextChannel):
        await interaction.response.send_modal(_SendMessageModal(self, target_channel))

    @autoreply_group.command(name="delete", description="Delete an auto-reply trigger securely")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def autoreply_delete(self, interaction: discord.Interaction, trigger: str):
        lock_key = (interaction.user.id, "autoreply_delete")
        if self._security_failures.get(lock_key, 0) >= 3:
            await interaction.response.send_message("You are locked out after 3 failed password attempts.", ephemeral=True)
            return
        await interaction.response.send_modal(_DeleteAutoReplyModal(self, trigger))

    @autoreply_delete.autocomplete("trigger")
    async def autoreply_delete_autocomplete(self, interaction: discord.Interaction, current: str):
        query = {"trigger": {"$regex": current or "", "$options": "i"}}
        docs = await keywords_col.find(query, {"trigger": 1}).limit(25).to_list(length=25)
        return [app_commands.Choice(name=str(doc.get("trigger", ""))[:100], value=str(doc.get("trigger", ""))) for doc in docs if doc.get("trigger")]

    # /game tad
    @game_group.command(name="tad", description="Get a random Truth or Dare question")
    @app_commands.describe(category="Choose truth or dare")
    @app_commands.choices(category=[
        app_commands.Choice(name="Truth", value="truth"),
        app_commands.Choice(name="Dare", value="dare"),
    ])
    async def game_tad(self, interaction: discord.Interaction, category: app_commands.Choice[str]):
        await interaction.response.defer()
        try:
            docs = await tad_col.find({"type": category.value}).to_list(length=None)
        except Exception as exc:
            print(f"[GameCog] TAD fetch error: {exc}")
            await interaction.followup.send("❌ Database error. Try again later.")
            return

        if not docs:
            await interaction.followup.send(
                f"⚠️ No **{category.name}** questions found. Ask an admin to add some via the Utilities Dashboard.",
                ephemeral=True,
            )
            return

        chosen = secrets.choice(docs)
        embed = discord.Embed(
            title=f"{'🤔 Truth' if category.value == 'truth' else '😈 Dare'}",
            description=chosen["text"],
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_branding_view())

    # /game rps
    @game_group.command(name="rps", description="Play Rock Paper Scissors against someone")
    @app_commands.describe(opponent="The user to play against")
    async def game_rps(self, interaction: discord.Interaction, opponent: discord.Member):
        challenger = interaction.user
        deadline_ts = _relative_ts(120)

        if opponent.id == challenger.id:
            await interaction.response.send_message("❌ You can't play against yourself!", ephemeral=True)
            return

        game_state: dict = {challenger.id: None, opponent.id: None}
        view = RPSView(self, challenger, opponent, game_state)  # type: ignore[arg-type]

        embed = discord.Embed(
            title="🪨 Rock Paper Scissors",
            description=(
                f"{challenger.mention} **vs** {opponent.mention}\n\n"
                "The opening move order was chosen securely.\n"
                "Both players pick below. Results stay hidden until both choices are locked!\n"
                f"Round ends <t:{deadline_ts}:R>"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=view)

    # /game ttt
    @game_group.command(name="ttt", description="Play Tic-Tac-Toe against someone")
    @app_commands.describe(opponent="The user to play against")
    async def game_ttt(self, interaction: discord.Interaction, opponent: discord.Member):
        challenger = interaction.user
        deadline_ts = _relative_ts(180)

        if opponent.id == challenger.id:
            await interaction.response.send_message("❌ You can't play against yourself!", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("❌ The bot can't play Tic-Tac-Toe yet!", ephemeral=True)
            return

        view = TTTView(self, challenger, opponent)  # type: ignore[arg-type]
        starting_player = view.players[0]
        embed = discord.Embed(
            title="❎⭕ Tic-Tac-Toe",
            description=(
                f"{challenger.mention} **vs** {opponent.mention}\n\n"
                f"The opening move order was chosen securely.\n"
                f"Turn: **{starting_player.mention}** {'❌' if view.current_turn == 0 else '⭕'}\n"
                f"Match ends <t:{deadline_ts}:R>"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(
            content=f"{challenger.mention} vs {opponent.mention}",
            embed=embed,
            view=view,
        )

    @game_group.command(name="memory", description="Play a memory match game by difficulty")
    @app_commands.describe(level="Choose memory board level")
    @app_commands.choices(level=[
        app_commands.Choice(name="Easy (2x2)", value="easy"),
        app_commands.Choice(name="Medium (4x4)", value="medium"),
        app_commands.Choice(name="Hard (5x4)", value="hard"),
    ])
    async def game_memory(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        layouts = {
            "easy": (2, 2),
            "medium": (4, 4),
            "hard": (4, 5),
        }
        rows, cols = layouts[level.value]
        deadline_ts = _relative_ts(120)
        view = MemoryView(self, interaction.user, rows=rows, cols=cols, countdown_ts=deadline_ts)  # type: ignore[arg-type]
        embed = discord.Embed(
            title="🧠 Memory Match",
            description=(
                f"Level: **{level.name}**\n"
                f"Find all matching pairs before <t:{deadline_ts}:R>.\n"
                "Click tiles to reveal two emojis at a time."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=view)

    @game_group.command(name="guess", description="Guess the number with level-based ranges")
    @app_commands.describe(level="Choose difficulty")
    @app_commands.choices(level=[
        app_commands.Choice(name="Easy (1-50)", value="easy"),
        app_commands.Choice(name="Medium (1-500)", value="medium"),
        app_commands.Choice(name="Hard (1-5000)", value="hard"),
        app_commands.Choice(name="Extreme (1-10000)", value="extreme"),
    ])
    async def game_guess(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        ranges = {
            "easy": (1, 50, 7),
            "medium": (1, 500, 10),
            "hard": (1, 5000, 12),
            "extreme": (1, 10000, 14),
        }
        low, high, attempts = ranges[level.value]
        answer = secrets.randbelow(high - low + 1) + low
        end_ts = _relative_ts(120)
        embed = discord.Embed(
            title="🎯 Number Guess",
            description=(
                f"Range: **{low}-{high}**\n"
                f"Attempts: **{attempts}**\n"
                f"Time left: <t:{end_ts}:R>\n"
                "Send your guesses in this channel."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

        def _check(message: discord.Message) -> bool:
            return (
                message.guild is not None
                and interaction.guild is not None
                and message.guild.id == interaction.guild.id
                and message.channel.id == interaction.channel_id
                and message.author.id == interaction.user.id
                and message.content.strip().isdigit()
            )

        won = False
        used = 0
        while used < attempts and int(datetime.now(timezone.utc).timestamp()) < end_ts:
            timeout = max(1, end_ts - int(datetime.now(timezone.utc).timestamp()))
            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=_check)
            except asyncio.TimeoutError:
                break
            used += 1
            guess = int(msg.content.strip())
            if guess == answer:
                won = True
                break
            hint = "Higher ⬆️" if guess < answer else "Lower ⬇️"
            remaining = attempts - used
            await interaction.followup.send(
                f"{hint} • Attempts left: **{remaining}** • Ends <t:{end_ts}:R>",
                view=_branding_view(),
            )

        points = DIRECT_GAME_WIN_POINTS if won else DIRECT_GAME_LOSS_POINTS
        result_embed = discord.Embed(
            title="🎯 Number Guess — Result",
            description=(
                f"{'🎉 Correct guess!' if won else '❌ Attempts/time exhausted.'}\n"
                f"Target Number: **{answer}**"
            ),
            color=0x5865F2,
        )
        result_embed.add_field(name="Point Change", value=_format_points(points), inline=False)
        result_embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=result_embed, view=_branding_view())
        asyncio.create_task(
            self._record_game_outcome(
                guild=interaction.guild,
                game_name="Guess Number",
                result=f"{interaction.user.display_name} {'won' if won else 'lost'}",
                profile_updates=[
                    {
                        "member": interaction.user,
                        "points": points,
                        "wins": 1 if won else 0,
                        "losses": 0 if won else 1,
                        "total_games": 1,
                    }
                ],
                players=[(interaction.user.display_name, interaction.user.id, _format_points(points))],
            )
        )

    @game_group.command(name="minesweeper", description="Pick a tile and avoid the hidden mine")
    @app_commands.describe(row="Row number (1-3)", column="Column number (1-3)")
    async def game_minesweeper(
        self,
        interaction: discord.Interaction,
        row: app_commands.Range[int, 1, 3],
        column: app_commands.Range[int, 1, 3],
    ):
        target = (row - 1, column - 1)
        all_cells = [(r, c) for r in range(3) for c in range(3)]
        mine_cell = secrets.choice(all_cells)
        won = target != mine_cell
        points = DIRECT_GAME_WIN_POINTS if won else DIRECT_GAME_LOSS_POINTS

        board_lines = []
        for r in range(3):
            line = []
            for c in range(3):
                if (r, c) == target and won:
                    line.append("✅")
                elif (r, c) == target and not won:
                    line.append("💣")
                else:
                    line.append("⬜")
            board_lines.append(" ".join(line))

        embed = discord.Embed(
            title="💣 Minesweeper",
            description=(
                f"You chose **Row {row}, Col {column}**.\n"
                f"{'🎉 Safe pick! You win.' if won else '💥 You hit the mine and lost.'}\n\n"
                + "\n".join(board_lines)
            ),
            color=0x5865F2,
        )
        embed.add_field(name="Point Change", value=_format_points(points), inline=False)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())
        asyncio.create_task(
            self._record_game_outcome(
                guild=interaction.guild,
                game_name="Minesweeper",
                result=f"{interaction.user.display_name} {'won' if won else 'lost'}",
                profile_updates=[
                    {
                        "member": interaction.user,
                        "points": points,
                        "wins": 1 if points > 0 else 0,
                        "losses": 1 if points < 0 else 0,
                        "total_games": 1,
                    }
                ],
                players=[(interaction.user.display_name, interaction.user.id, _format_points(points))],
            )
        )

    @game_group.command(name="scramble", description="Start a public word scramble challenge")
    async def game_scramble(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ This command only works in text channels.", ephemeral=True)
            return
        answer = await self._pick_scramble_word_from_history(interaction.channel)
        scrambled = _scramble_text(answer)
        end_ts = _relative_ts(60)
        embed = discord.Embed(
            title="🔤 Word Scramble",
            description=(
                f"Unscramble: **{scrambled}**\n"
                f"First correct answer before <t:{end_ts}:R> wins."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

        penalized_users: set[int] = set()
        updates: list[dict] = []
        players: list[tuple[str, int, str]] = []
        winner: discord.Member | discord.User | None = None

        def _check(message: discord.Message) -> bool:
            return (
                message.guild is not None
                and interaction.guild is not None
                and message.guild.id == interaction.guild.id
                and message.channel.id == interaction.channel_id
                and not message.author.bot
            )

        while int(datetime.now(timezone.utc).timestamp()) < end_ts:
            timeout = max(1, end_ts - int(datetime.now(timezone.utc).timestamp()))
            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=_check)
            except asyncio.TimeoutError:
                break
            if secrets.compare_digest(msg.content.strip().lower(), answer.lower()):
                winner = msg.author
                updates.append(
                    {
                        "member": msg.author,
                        "points": AUTO_GAME_WIN_POINTS,
                        "wins": 1,
                        "losses": 0,
                        "total_games": 1,
                    }
                )
                players.append((msg.author.display_name, msg.author.id, _format_points(AUTO_GAME_WIN_POINTS)))
                break
            if msg.author.id not in penalized_users:
                penalized_users.add(msg.author.id)
                updates.append(
                    {
                        "member": msg.author,
                        "points": AUTO_GAME_LOSS_POINTS,
                        "wins": 0,
                        "losses": 1,
                        "total_games": 1,
                    }
                )
                players.append((msg.author.display_name, msg.author.id, _format_points(AUTO_GAME_LOSS_POINTS)))
                await interaction.followup.send(
                    f"❌ {msg.author.mention} wrong answer (-5). Ends <t:{end_ts}:R>",
                    view=_branding_view(),
                )

        result_embed = discord.Embed(
            title="🔤 Word Scramble — Result",
            description=(
                f"{'🏆 Winner: ' + winner.mention if winner else '⏰ No correct answer in time.'}\n"
                f"Word: **{answer}**"
            ),
            color=0x5865F2,
        )
        result_embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=result_embed, view=_branding_view())
        asyncio.create_task(
            self._record_game_outcome(
                guild=interaction.guild,
                game_name="Word Scramble",
                result=f"{winner.display_name} won" if winner else "No winner",
                profile_updates=updates,
                players=players,
            )
        )

    @game_group.command(name="math", description="Start a fastest-finger math challenge by level")
    @app_commands.describe(level="Choose difficulty")
    @app_commands.choices(level=[
        app_commands.Choice(name="Easy", value="easy"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="Hard", value="hard"),
        app_commands.Choice(name="Extreme", value="extreme"),
    ])
    async def game_math(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if level.value in {"easy", "medium"}:
            left = secrets.randbelow(31) + 10
            right = secrets.randbelow(21) + 1
            operator = secrets.choice(["+", "-", "*"])
            if operator == "+":
                answer = left + right
            elif operator == "-":
                answer = left - right
            else:
                answer = left * right
            prompt = f"Solve: **{left} {operator} {right} = ?**"
        elif level.value == "hard":
            a = secrets.randbelow(8) + 2
            b = secrets.randbelow(8) + 2
            c = secrets.randbelow(8) + 2
            answer = (a * b) + c
            prompt = f"Solve (BODMAS): **{a} × {b} + {c} = ?**"
        else:
            x = secrets.randbelow(12) + 1
            y = secrets.randbelow(10) + 1
            z = secrets.randbelow(6) + 1
            answer = (x * y) - z
            prompt = f"Solve (algebra-style): If x={x}, y={y}, z={z}, find **xy - z**."

        end_ts = _relative_ts(60)
        embed = discord.Embed(
            title="🧮 Math Challenge",
            description=(
                f"Level: **{level.name}**\n"
                f"{prompt}\n"
                f"Fastest correct answer before <t:{end_ts}:R> wins."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

        penalized_users: set[int] = set()
        updates: list[dict] = []
        players: list[tuple[str, int, str]] = []
        winner: discord.Member | discord.User | None = None

        def _check(message: discord.Message) -> bool:
            return (
                message.guild is not None
                and interaction.guild is not None
                and message.guild.id == interaction.guild.id
                and message.channel.id == interaction.channel_id
                and not message.author.bot
            )

        while int(datetime.now(timezone.utc).timestamp()) < end_ts:
            timeout = max(1, end_ts - int(datetime.now(timezone.utc).timestamp()))
            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=_check)
            except asyncio.TimeoutError:
                break
            text = msg.content.strip()
            if text.lstrip("-").isdigit() and int(text) == answer:
                winner = msg.author
                updates.append(
                    {
                        "member": msg.author,
                        "points": AUTO_GAME_WIN_POINTS,
                        "wins": 1,
                        "losses": 0,
                        "total_games": 1,
                    }
                )
                players.append((msg.author.display_name, msg.author.id, _format_points(AUTO_GAME_WIN_POINTS)))
                break
            if msg.author.id not in penalized_users:
                penalized_users.add(msg.author.id)
                updates.append(
                    {
                        "member": msg.author,
                        "points": AUTO_GAME_LOSS_POINTS,
                        "wins": 0,
                        "losses": 1,
                        "total_games": 1,
                    }
                )
                players.append((msg.author.display_name, msg.author.id, _format_points(AUTO_GAME_LOSS_POINTS)))
                await interaction.followup.send(
                    f"❌ {msg.author.mention} wrong answer (-5). Ends <t:{end_ts}:R>",
                    view=_branding_view(),
                )

        result_embed = discord.Embed(
            title="🧮 Math Challenge — Result",
            description=(
                f"{'🏆 Winner: ' + winner.mention if winner else '⏰ No correct answer in time.'}\n"
                f"Answer: **{answer}**"
            ),
            color=0x5865F2,
        )
        result_embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=result_embed, view=_branding_view())
        asyncio.create_task(
            self._record_game_outcome(
                guild=interaction.guild,
                game_name="Math Challenge",
                result=f"{winner.display_name} won" if winner else "No winner",
                profile_updates=updates,
                players=players,
            )
        )

    # /game toss
    @game_group.command(name="toss", description="Play a quick heads or tails toss")
    @app_commands.describe(call="Choose heads or tails")
    @app_commands.choices(call=[
        app_commands.Choice(name="Heads", value="heads"),
        app_commands.Choice(name="Tails", value="tails"),
    ])
    async def game_toss(self, interaction: discord.Interaction, call: app_commands.Choice[str]):
        outcome = secrets.choice(["heads", "tails"])
        won = call.value == outcome
        points = TOSS_WIN_POINTS if won else TOSS_LOSS_POINTS
        profile_updates = [
            {
                "member": interaction.user,
                "points": points,
                "wins": 1 if won else 0,
                "losses": 0 if won else 1,
                "total_games": 1,
            }
        ]

        embed = discord.Embed(
            title="🪙 Coin Toss",
            description=(
                f"You picked **{call.value.title()}**.\n"
                f"The coin landed on **{outcome.title()}**.\n\n"
                f"{'🎉 You won!' if won else '😢 You lost!'}"
            ),
            color=0x5865F2,
        )
        embed.add_field(name="Point Change", value=_format_points(points), inline=False)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

        asyncio.create_task(
            self._record_game_outcome(
                guild=interaction.guild,
                game_name="Coin Toss",
                result=f"{interaction.user.display_name} {'won' if won else 'lost'}",
                profile_updates=profile_updates,
                players=[(interaction.user.display_name, interaction.user.id, _format_points(points))],
            )
        )

    @game_group.command(name="8ball", description="Ask the Magic 8-Ball a question")
    @app_commands.describe(question="Your yes/no question")
    async def game_8ball(self, interaction: discord.Interaction, question: str):
        response = secrets.choice(list(EIGHT_BALL_RESPONSES))
        embed = discord.Embed(
            title="🎱 Magic 8-Ball",
            description=f"**Q:** {question}\n**A:** {response}",
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

    @game_group.command(name="dice", description="Roll a secure 6-sided dice")
    async def game_dice(self, interaction: discord.Interaction):
        value = secrets.choice(list(range(1, 7)))
        embed = discord.Embed(
            title="🎲 Dice Roll",
            description=f"You rolled: **{value}** {'🎉' if value == 6 else '🎯'}",
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

    @game_group.command(name="hangman", description="Play reply-based hangman using message-history words")
    async def game_hangman(self, interaction: discord.Interaction):
        difficulty = secrets.choice(["easy", "medium", "hard"])
        length_map = {
            "easy": (4, 6, 8),
            "medium": (6, 9, 9),
            "hard": (8, 20, 10),
        }
        min_len, max_len, lives = length_map[difficulty]
        word = await self._pick_hangman_word(interaction, min_len=min_len, max_len=max_len)
        guessed_letters: set[str] = set()
        wrong_letters: set[str] = set()
        end_ts = _relative_ts(120)

        def _masked() -> str:
            return " ".join([ch if ch in guessed_letters else "＿" for ch in word])

        embed = discord.Embed(
            title="🪢 Hangman",
            description=(
                f"Difficulty: **{difficulty.title()}**\n"
                f"Word: {_masked()}\n"
                f"Lives: **{lives}**\n"
                f"Ends <t:{end_ts}:R>\n"
                "Reply in channel with one letter at a time."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

        def _check(message: discord.Message) -> bool:
            text = message.content.strip().lower()
            return (
                message.guild is not None
                and interaction.guild is not None
                and message.guild.id == interaction.guild.id
                and message.channel.id == interaction.channel_id
                and message.author.id == interaction.user.id
                and len(text) == 1
                and text.isalpha()
            )

        won = False
        while lives > 0 and int(datetime.now(timezone.utc).timestamp()) < end_ts:
            timeout = max(1, end_ts - int(datetime.now(timezone.utc).timestamp()))
            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=_check)
            except asyncio.TimeoutError:
                break
            letter = msg.content.strip().lower()
            if letter in guessed_letters or letter in wrong_letters:
                await interaction.followup.send("⚠️ Letter already used.", view=_branding_view())
                continue
            if letter in word:
                guessed_letters.add(letter)
            else:
                wrong_letters.add(letter)
                lives -= 1
            current_mask = _masked()
            await interaction.followup.send(
                f"Word: **{current_mask}** | Lives: **{lives}** | Ends <t:{end_ts}:R>",
                view=_branding_view(),
            )
            if all(ch in guessed_letters for ch in word):
                won = True
                break

        points = AUTO_GAME_WIN_POINTS if won else AUTO_GAME_LOSS_POINTS
        result_embed = discord.Embed(
            title="🪢 Hangman — Result",
            description=(
                f"{'🏆 You solved it!' if won else '💀 You lost this round.'}\n"
                f"Word: **{word}**"
            ),
            color=0x5865F2,
        )
        result_embed.add_field(name="Point Change", value=_format_points(points), inline=False)
        result_embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=result_embed, view=_branding_view())
        asyncio.create_task(
            self._record_game_outcome(
                guild=interaction.guild,
                game_name="Hangman",
                result=f"{interaction.user.display_name} {'won' if won else 'lost'}",
                profile_updates=[
                    {
                        "member": interaction.user,
                        "points": points,
                        "wins": 1 if won else 0,
                        "losses": 0 if won else 1,
                        "total_games": 1,
                    }
                ],
                players=[(interaction.user.display_name, interaction.user.id, _format_points(points))],
            )
        )

    # /game quiz
    @game_group.command(name="quiz", description="Answer a random quiz question")
    async def game_quiz(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            docs = await quiz_col.find({}).to_list(length=None)
        except Exception as exc:
            print(f"[GameCog] Quiz fetch error: {exc}")
            await interaction.followup.send("❌ Database error. Try again later.")
            return

        if not docs:
            await interaction.followup.send(
                "⚠️ No quiz questions found. Ask an admin to add some via the Utilities Dashboard.",
                ephemeral=True,
            )
            return

        question = secrets.choice(docs)
        embed = discord.Embed(
            title="🧠 Quiz Time!",
            description=question["question"],
            color=0x5865F2,
        )
        embed.set_footer(text="You have 30 seconds to answer • an app by deep")
        view = QuizView(self, question, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="myprofile", description="View your global economy profile")
    @app_commands.guild_only()
    async def myprofile(self, interaction: discord.Interaction):
        await interaction.response.defer()

        profile = await db.get_user_profile(interaction.user.id)
        global_rank = await db.get_user_global_rank(interaction.user.id)
        sorted_profiles = await db.get_sorted_user_profiles()
        guild_member_ids = {member.id for member in interaction.guild.members}
        server_profiles = [item for item in sorted_profiles if item["user_id"] in guild_member_ids]
        server_rank = next(
            (idx for idx, item in enumerate(server_profiles, start=1) if item["user_id"] == interaction.user.id),
            len(server_profiles) + 1,
        )

        total_games = profile.get("total_games", 0)
        wins = profile.get("wins", 0)
        losses = profile.get("losses", 0)
        win_rate = ((wins / total_games) * 100) if total_games else 0.0

        embed = discord.Embed(
            title=f"📊 {interaction.user.display_name}'s Profile",
            color=0x5865F2,
        )
        embed.add_field(name="Points", value=str(profile.get("points", 0)), inline=True)
        embed.add_field(name="Wins", value=str(wins), inline=True)
        embed.add_field(name="Losses", value=str(losses), inline=True)
        embed.add_field(name="Total Games", value=str(total_games), inline=True)
        embed.add_field(name="Win Rate", value=f"{win_rate:.2f}%", inline=True)
        embed.add_field(name="Global Rank", value=f"#{global_rank}", inline=True)
        embed.add_field(name="Server Rank", value=f"#{server_rank}", inline=True)
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=_branding_view())

    # ── on_message auto-responder ─────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        content = message.content.lower()
        for trigger, reply in self._keyword_cache.items():
            if trigger in content:
                try:
                    await message.channel.send(reply)
                except discord.HTTPException:
                    pass
                break  # one reply per message


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCommands(bot))
