"""Interactive Games & Utilities Engine — Discord Cog."""

import asyncio
import os
import secrets
import time
from typing import Optional

import discord
from bson import ObjectId
from discord import app_commands
from discord.ext import commands, tasks

from database import client as _mongo_client
from telemetry import log_exception, send_master_log

_game_db = _mongo_client["LeaderboardBotDB"]
keywords_col = _game_db["GameKeywords"]
tad_col = _game_db["TruthOrDare"]
quiz_col = _game_db["QuizQuestions"]

APP_LINK = "https://deepdey.vercel.app/"
INSTA_LINK = "https://instagram.com/deepdey.official"
KEYWORD_CACHE_TTL = 300  # seconds between keyword cache refreshes
RPS_CHOICE_EMOJIS = ("✊", "✋", "✌️")
PASSWORD = os.getenv("PASSWORD")

# ── Branding view ────────────────────────────────────────────────────────────

def _branding_view() -> discord.ui.View:
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label="an app by deep", url=APP_LINK, style=discord.ButtonStyle.link))
    v.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))
    return v


# ── RPS View ─────────────────────────────────────────────────────────────────

class RPSView(discord.ui.View):
    CHOICES = {"✊": "Rock", "✋": "Paper", "✌️": "Scissors"}
    WINS = {"Rock": "Scissors", "Paper": "Rock", "Scissors": "Paper"}

    def __init__(self, challenger: discord.Member, opponent: discord.Member, game_state: dict):
        super().__init__(timeout=120)
        self.challenger = challenger
        self.opponent = opponent
        self.game_state = game_state
        self.add_item(discord.ui.Button(label="an app by deep", url=APP_LINK, style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))

    async def _handle_pick(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if uid not in (self.challenger.id, self.opponent.id):
            await interaction.response.send_message("⛔ You are not a player in this game.", ephemeral=True)
            return
        if self.game_state.get(uid):
            await interaction.response.send_message("✅ You have already chosen!", ephemeral=True)
            return
        self.game_state[uid] = choice
        await interaction.response.send_message(f"✅ You chose {choice}. Waiting for the other player…", ephemeral=True)

        if self.game_state.get(self.challenger.id) and self.game_state.get(self.opponent.id):
            await self._resolve(interaction)

    async def _resolve(self, interaction: discord.Interaction):
        c_pick = self.game_state[self.challenger.id]
        o_pick = self.game_state[self.opponent.id]
        c_name = self.CHOICES[c_pick]
        o_name = self.CHOICES[o_pick]

        if c_name == o_name:
            result = "🤝 It's a **Draw**!"
        elif self.WINS[c_name] == o_name:
            result = f"🎉 **{self.challenger.display_name}** wins!"
        else:
            result = f"🎉 **{self.opponent.display_name}** wins!"

        embed = discord.Embed(title="🪨 Rock Paper Scissors — Result", color=0x5865F2)
        embed.add_field(name=self.challenger.display_name, value=c_pick, inline=True)
        embed.add_field(name=self.opponent.display_name, value=o_pick, inline=True)
        embed.add_field(name="Result", value=result, inline=False)

        self.stop()
        for item in self.children:
            if isinstance(item, discord.ui.Button) and not item.url:
                item.disabled = True
        try:
            await interaction.message.edit(embed=embed, view=_branding_view())
        except Exception:
            pass

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
            view.disable_all()
            view.stop()
            await interaction.response.edit_message(embed=embed, view=view)
        elif is_draw:
            embed = discord.Embed(
                title="❎⭕ Tic-Tac-Toe — Result",
                description="🤝 It's a **Draw**!",
                color=0x5865F2,
            )
            view.disable_all()
            view.stop()
            await interaction.response.edit_message(embed=embed, view=view)
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
    def __init__(self, p1: discord.Member, p2: discord.Member):
        super().__init__(timeout=180)
        self.players = [p1, p2]
        self.current_turn = 0
        self.board: list[list[str]] = [["", "", ""], ["", "", ""], ["", "", ""]]

        for r in range(3):
            for c in range(3):
                self.add_item(TTTButton(r, c))

        self.add_item(discord.ui.Button(label="an app by deep", url=APP_LINK, style=discord.ButtonStyle.link, row=4))
        self.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link, row=4))

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


# ── Quiz View ─────────────────────────────────────────────────────────────────

class QuizView(discord.ui.View):
    def __init__(self, question: dict, invoker_id: int):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.correct = question["correct_answer"]
        self.answered = False

        options = question.get("options", [])
        for opt in options:
            btn = discord.ui.Button(label=opt, style=discord.ButtonStyle.primary)
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

        self.add_item(discord.ui.Button(label="an app by deep", url=APP_LINK, style=discord.ButtonStyle.link))

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
            else:
                msg = f"❌ Wrong! The correct answer was **{self.correct}**."

            await interaction.response.edit_message(content=msg, view=self)

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
        if not _password_ok(str(self.password)):
            await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
            return

        truth_text = str(self.truth).strip()
        dare_text = str(self.dare).strip()
        if not truth_text and not dare_text:
            await interaction.response.send_message("❌ Add at least one Truth or Dare text.", ephemeral=True)
            return

        entries = []
        if truth_text:
            entries.append({"type": "truth", "text": truth_text})
        if dare_text:
            entries.append({"type": "dare", "text": dare_text})

        await tad_col.insert_many(entries)
        await interaction.response.send_message("✅ Truth/Dare entries saved successfully.", ephemeral=True)


class _AddQuizModal(discord.ui.Modal, title="Add Quiz Question"):
    question = discord.ui.TextInput(label="Question", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    options = discord.ui.TextInput(label="Options (Comma-separated)", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    correct_answer = discord.ui.TextInput(label="Correct Answer", required=True, style=discord.TextStyle.short, max_length=200)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not _password_ok(str(self.password)):
            await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
            return

        options = [part.strip() for part in str(self.options).split(",") if part.strip()]
        correct = str(self.correct_answer).strip()
        question = str(self.question).strip()
        if len(options) < 2:
            await interaction.response.send_message("❌ Please provide at least two options.", ephemeral=True)
            return
        if correct not in options:
            await interaction.response.send_message("❌ Correct answer must match one of the provided options.", ephemeral=True)
            return

        await quiz_col.insert_one({"question": question, "options": options, "correct_answer": correct})
        await interaction.response.send_message("✅ Quiz question saved successfully.", ephemeral=True)


class _AddAutoReplyModal(discord.ui.Modal, title="Add Auto Reply"):
    keywords = discord.ui.TextInput(label="Keywords (Comma-separated)", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    reply = discord.ui.TextInput(label="Reply Message", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not _password_ok(str(self.password)):
            await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
            return

        parsed_keywords = [kw.strip().lower() for kw in str(self.keywords).split(",") if kw.strip()]
        reply_msg = str(self.reply).strip()
        if not parsed_keywords or not reply_msg:
            await interaction.response.send_message("❌ Keywords and reply are required.", ephemeral=True)
            return

        await keywords_col.insert_many([{"trigger": kw, "reply": reply_msg} for kw in parsed_keywords])
        await self.cog._load_keyword_cache()
        await interaction.response.send_message("✅ Auto-reply keywords saved successfully.", ephemeral=True)


class _SendMessageModal(discord.ui.Modal, title="Send Branded Message"):
    message = discord.ui.TextInput(label="Message Content", required=True, style=discord.TextStyle.paragraph, max_length=2000)
    password = discord.ui.TextInput(label="Password", required=True, style=discord.TextStyle.short, max_length=200)

    def __init__(self, cog: "GameCommands", target_channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        if not _password_ok(str(self.password)):
            await interaction.response.send_message("❌ Invalid password.", ephemeral=True)
            return

        embed = discord.Embed(description=str(self.message).strip(), color=0x5865F2)
        embed.set_footer(text="Sent by Deep")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Website", url=APP_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))

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


# ── Game Cog ─────────────────────────────────────────────────────────────────

class GameCommands(commands.Cog):
    """Cog housing all /game subcommands and the auto-responder."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._keyword_cache: dict[str, str] = {}
        self._cache_ts = 0.0
        self._refresh_keyword_cache.start()

    def cog_unload(self):
        self._refresh_keyword_cache.cancel()

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

    # ── /game group ──────────────────────────────────────────────────────────

    game_group = app_commands.Group(name="game", description="Games & fun commands")
    add_group = app_commands.Group(name="add", description="Securely add game content", parent=game_group)
    send_group = app_commands.Group(name="send", description="Send managed game messages", parent=game_group)

    # /game help
    @game_group.command(name="help", description="Show all available game commands")
    async def game_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Game Commands",
            description=(
                "**`/game tad <truth|dare>`** — Get a random Truth or Dare question.\n"
                "**`/game rps <@opponent>`** — Rock Paper Scissors vs a user (or me!).\n"
                "**`/game ttt <@opponent>`** — Tic-Tac-Toe in Discord buttons.\n"
                "**`/game quiz`** — Answer a random quiz question.\n"
                "**`/game help`** — Shows this message.\n"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=_branding_view())

    @add_group.command(name="tad", description="Add Truth or Dare entries securely")
    async def game_add_tad(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_AddTADModal(self))

    @add_group.command(name="quiz", description="Add quiz questions securely")
    async def game_add_quiz(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_AddQuizModal(self))

    @add_group.command(name="autoreply", description="Add auto-reply keywords securely")
    async def game_add_autoreply(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_AddAutoReplyModal(self))

    @send_group.command(name="message", description="Send a branded message to a target channel")
    async def game_send_message(self, interaction: discord.Interaction, target_channel: discord.TextChannel):
        await interaction.response.send_modal(_SendMessageModal(self, target_channel))

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

        if opponent.id == challenger.id:
            await interaction.response.send_message("❌ You can't play against yourself!", ephemeral=True)
            return

        game_state: dict = {challenger.id: None, opponent.id: None}
        view = RPSView(challenger, opponent, game_state)  # type: ignore[arg-type]

        embed = discord.Embed(
            title="🪨 Rock Paper Scissors",
            description=(
                f"{challenger.mention} **vs** {opponent.mention}\n\n"
                "Both players pick a choice below. Results are hidden until both have chosen!"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=view)

        # If opponent is the bot, auto-pick instantly
        if opponent.id == self.bot.user.id:  # type: ignore[union-attr]
            bot_pick = secrets.choice(RPS_CHOICE_EMOJIS)
            game_state[opponent.id] = bot_pick

            if game_state[challenger.id] is not None:
                msg = await interaction.original_response()
                c_pick = game_state[challenger.id]
                o_pick = bot_pick
                c_name = RPSView.CHOICES[c_pick]
                o_name = RPSView.CHOICES[o_pick]
                if c_name == o_name:
                    result = "🤝 It's a **Draw**!"
                elif RPSView.WINS[c_name] == o_name:
                    result = f"🎉 **{challenger.display_name}** wins!"
                else:
                    result = f"🎉 **{opponent.display_name}** wins!"
                result_embed = discord.Embed(title="🪨 Rock Paper Scissors — Result", color=0x5865F2)
                result_embed.add_field(name=challenger.display_name, value=c_pick, inline=True)
                result_embed.add_field(name=opponent.display_name, value=o_pick, inline=True)
                result_embed.add_field(name="Result", value=result, inline=False)
                view.stop()
                await msg.edit(embed=result_embed, view=_branding_view())

    # /game ttt
    @game_group.command(name="ttt", description="Play Tic-Tac-Toe against someone")
    @app_commands.describe(opponent="The user to play against")
    async def game_ttt(self, interaction: discord.Interaction, opponent: discord.Member):
        challenger = interaction.user

        if opponent.id == challenger.id:
            await interaction.response.send_message("❌ You can't play against yourself!", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("❌ The bot can't play Tic-Tac-Toe yet!", ephemeral=True)
            return

        view = TTTView(challenger, opponent)  # type: ignore[arg-type]
        embed = discord.Embed(
            title="❎⭕ Tic-Tac-Toe",
            description=f"{challenger.mention} ❌ **vs** {opponent.mention} ⭕\n\nTurn: **{challenger.mention}** ❌",
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(
            content=f"{challenger.mention} vs {opponent.mention}",
            embed=embed,
            view=view,
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
        view = QuizView(question, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view)

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
