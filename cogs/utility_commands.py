import asyncio
import time
from datetime import timezone

import discord
from discord import app_commands
from discord.ext import commands

APP_LINK = "https://deepdey.vercel.app/"
INSTA_LINK = "https://instagram.com/deepdey.official"


class PollView(discord.ui.View):
    def __init__(self, author_id: int, question: str, options: list[str], timer_hours: float | None):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.question = question
        self.options = options
        self.timer_hours = timer_hours
        self.votes: dict[int, int] = {}
        self.message: discord.Message | None = None
        self.ended = False

        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        for idx, option in enumerate(options):
            button = discord.ui.Button(
                label=f"{emojis[idx]} {option[:60]}",
                style=discord.ButtonStyle.primary,
                custom_id=f"poll_vote_{idx}",
            )

            async def callback(interaction: discord.Interaction, choice_index: int = idx):
                if self.ended:
                    await interaction.response.send_message("This poll has ended.", ephemeral=True)
                    return
                self.votes[interaction.user.id] = choice_index
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            button.callback = callback
            self.add_item(button)

    def build_embed(self) -> discord.Embed:
        total_votes = len(self.votes)
        embed = discord.Embed(title="📊 Interactive Poll", color=0x5865F2)
        embed.add_field(name="Question", value=self.question, inline=False)

        counts = [0] * len(self.options)
        for vote in self.votes.values():
            if 0 <= vote < len(counts):
                counts[vote] += 1

        for idx, option in enumerate(self.options):
            count = counts[idx]
            percent = (count / total_votes * 100) if total_votes else 0
            embed.add_field(
                name=f"Option {idx + 1}",
                value=f"**{option}**\nVotes: **{count}** ({percent:.1f}%)",
                inline=False,
            )

        if self.timer_hours:
            end_ts = int(time.time() + int(self.timer_hours * 3600))
            embed.set_footer(text=f"Poll ends at <t:{end_ts}:F>")
        else:
            embed.set_footer(text="No timer set. Poll stays open until bot restart.")
        return embed

    async def close_poll(self) -> None:
        if self.ended:
            return
        self.ended = True
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        if not self.message:
            return

        counts = [0] * len(self.options)
        for vote in self.votes.values():
            if 0 <= vote < len(counts):
                counts[vote] += 1

        total_votes = len(self.votes)
        if total_votes == 0:
            winner_text = "Poll ended with no votes."
        else:
            max_votes = max(counts)
            winner_indexes = [i for i, c in enumerate(counts) if c == max_votes]
            if len(winner_indexes) > 1:
                winners = ", ".join(self.options[i] for i in winner_indexes)
                pct = (max_votes / total_votes) * 100
                winner_text = f"Poll ended in a tie: **{winners}** with **{max_votes}** votes each ({pct:.1f}%)."
            else:
                win_idx = winner_indexes[0]
                pct = (max_votes / total_votes) * 100
                winner_text = (
                    f"Poll ended. Winning option: **{self.options[win_idx]}** "
                    f"with **{max_votes}** votes ({pct:.1f}%)."
                )

        await self.message.edit(content=winner_text, embed=self.build_embed(), view=self)


class UtilityCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _stats_links_view() -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Website", url=APP_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))
        return view

    @app_commands.command(name="stats", description="Show server and bot statistics")
    async def stats(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        owner = guild.owner or (await self.bot.fetch_user(guild.owner_id) if guild.owner_id else None)
        bots = sum(1 for member in guild.members if member.bot)
        online = sum(1 for member in guild.members if member.status != discord.Status.offline)

        app_info = await self.bot.application_info()
        bot_owner = app_info.owner
        now_ts = int(time.time())
        start_ts = int(self.bot.start_time.replace(tzinfo=timezone.utc).timestamp()) if getattr(self.bot, "start_time", None) else now_ts

        embed = discord.Embed(title="📈 Server and Bot Stats", color=0x5865F2)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="Server Name", value=guild.name, inline=True)
        embed.add_field(name="Description", value=guild.description or "No description set.", inline=True)
        embed.add_field(name="Boost Level", value=f"Level {guild.premium_tier}", inline=True)
        embed.add_field(name="Total Members", value=str(guild.member_count or 0), inline=True)
        embed.add_field(name="Bot Count", value=str(bots), inline=True)
        embed.add_field(name="Online Count", value=str(online), inline=True)
        embed.add_field(name="Server Owner", value=owner.mention if owner else "Unknown", inline=True)
        embed.add_field(name="Server Logo", value="Available" if guild.icon else "Not set", inline=True)

        if self.bot.user and self.bot.user.display_avatar:
            embed.set_author(name=f"{self.bot.user.name}", icon_url=self.bot.user.display_avatar.url)

        embed.add_field(name="Bot Owner", value=str(bot_owner), inline=True)
        embed.add_field(name="Uptime", value=f"<t:{start_ts}:R>", inline=True)
        embed.add_field(name="API Latency", value=f"{round(self.bot.latency * 1000)} ms", inline=True)
        embed.set_footer(text="Professional utility dashboard")

        await interaction.response.send_message(embed=embed, view=self._stats_links_view())

    @app_commands.command(name="poll", description="Create an interactive poll with up to 5 options")
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        opt1: str,
        opt2: str,
        opt3: str | None = None,
        opt4: str | None = None,
        opt5: str | None = None,
        timer_in_hours: app_commands.Range[float, 0.1, 168] | None = None,
    ):
        options = [opt1, opt2] + [opt for opt in [opt3, opt4, opt5] if opt]
        view = PollView(interaction.user.id, question, options, timer_in_hours)
        await interaction.response.send_message(embed=view.build_embed(), view=view)
        message = await interaction.original_response()
        view.message = message

        if timer_in_hours:
            async def close_later() -> None:
                await asyncio.sleep(timer_in_hours * 3600)
                await view.close_poll()

            asyncio.create_task(close_later())


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCommands(bot))
