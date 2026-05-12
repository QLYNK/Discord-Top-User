import asyncio
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import database as db
from telemetry import send_activity_log, send_guild_module_log

APP_LINK = "https://deepdey.vercel.app/"
INSTA_LINK = "https://instagram.com/deepdey.official"
GITHUB_REPO_LINK = "https://github.com/deepdeyiitgn/Discord-Top-User"
GITHUB_PROFILE_LINK = "https://github.com/deepdeyiitgn/"
HOME_SERVER_LINK = "https://discord.com/invite/t6ZKNw556n"
MUSIC_LINK = "https://qlynk.vercel.app/sukoon"
QUICKLINK_URL = "https://qlynk.vercel.app/"
STUDYBOT_URL = "https://studybots.vercel.app/"
CLOCK_OVERLAY_URL = "https://qlynk-clock.vercel.app/"
QLYNK_NODE_URL = "https://deydeep-deqlynk.hf.space/"
IST = timezone(timedelta(hours=5, minutes=30))


class PollView(discord.ui.View):
    def __init__(self, question: str, options: list[str], timer_hours: float | None):
        super().__init__(timeout=None)
        self.question = question
        self.options = options
        self.timer_hours = timer_hours
        self.votes: dict[int, int] = {}
        self.message: discord.Message | None = None
        self.ended = False
        self.end_ts: int | None = int(time.time() + int(timer_hours * 3600)) if timer_hours else None

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

        if self.end_ts:
            embed.set_footer(text=f"Poll ends at <t:{self.end_ts}:F>")
        else:
            embed.set_footer(text="No timer set. Poll remains open until manually managed.")
        return embed

    async def close_poll(self) -> str:
        if self.ended:
            return ""
        self.ended = True
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

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

        if self.message:
            await self.message.edit(content=winner_text, embed=self.build_embed(), view=self)
        return winner_text


class UtilityCommands(commands.Cog):
    utilities_group = app_commands.Group(
        name="utilities",
        description="Utility module administration",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _stats_links_view() -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Website", url=APP_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))
        return view

    @staticmethod
    def _links_view() -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🌐 Portfolio", url=APP_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="🏠 Home Server Invite", url=HOME_SERVER_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="💻 GitHub Repo", url=GITHUB_REPO_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="💻 GitHub", url=GITHUB_PROFILE_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="🎶 Music", url=MUSIC_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="🔗 QuickLink", url=QUICKLINK_URL, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="📚 StudyBot", url=STUDYBOT_URL, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="⏱️ Transparent Clock", url=CLOCK_OVERLAY_URL, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="☁️ QLYNK Node Server", url=QLYNK_NODE_URL, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Instagram", url=INSTA_LINK, style=discord.ButtonStyle.link))
        return view

    async def _emit_utility_logs(
        self,
        interaction: discord.Interaction,
        *,
        activity_type: str,
        details: str,
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        await send_activity_log(
            self.bot,
            activity_type=activity_type,
            details=details,
            module="Utilities",
            guild=interaction.guild,
            user=interaction.user,
            jump_url=interaction.channel.jump_url if isinstance(interaction.channel, discord.TextChannel) else None,
            fields=fields,
        )
        await send_guild_module_log(
            self.bot,
            guild=interaction.guild,
            module="utilities",
            title=f"Utilities • {activity_type}",
            description=details,
            fields=fields,
        )

    @utilities_group.command(name="logs", description="Set the dedicated utilities log channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def utilities_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await db.update_guild_settings(interaction.guild_id, {"utilities_logs_channel_id": channel.id})
        await interaction.response.send_message(f"Utilities logs channel set to {channel.mention}.")

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
        await self._emit_utility_logs(interaction, activity_type="Stats Command", details="Server and bot stats requested.")

    @app_commands.command(name="now", description="Show current Indian Standard Time and bot uptime")
    async def now(self, interaction: discord.Interaction):
        now_utc_ts = int(time.time())
        now_ist = datetime.now(IST)
        start_ts = int(self.bot.start_time.replace(tzinfo=timezone.utc).timestamp()) if getattr(self.bot, "start_time", None) else now_utc_ts

        embed = discord.Embed(title="🕒 Current Indian Standard Time", color=0x5865F2)
        embed.add_field(name="Time (IST)", value=f"<t:{now_utc_ts}:T>", inline=True)
        embed.add_field(name="Date (IST)", value=f"<t:{now_utc_ts}:D>", inline=True)
        embed.add_field(name="Day", value=now_ist.strftime("%A"), inline=True)
        embed.add_field(name="Bot Uptime", value=f"<t:{start_ts}:R>", inline=False)
        embed.set_footer(text="Uses Discord dynamic timestamps for real-time client-side updates")

        await interaction.response.send_message(embed=embed)
        await self._emit_utility_logs(interaction, activity_type="Now Command", details="Requested real-time IST information.")

    async def _fetch_weather(self, session: aiohttp.ClientSession, latitude: float, longitude: float, timezone_name: str):
        weather_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={latitude}&longitude={longitude}&current_weather=true&timezone={quote(timezone_name or 'auto')}"
        )
        async with session.get(weather_url) as response:
            response.raise_for_status()
            return await response.json()

    @app_commands.command(name="weather", description="Get current weather for top location matches")
    async def weather(self, interaction: discord.Interaction, city: str):
        await interaction.response.defer()
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote(city)}"
        timeout = aiohttp.ClientTimeout(total=20)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(geo_url) as response:
                response.raise_for_status()
                geo_data = await response.json()

            results = geo_data.get("results", [])
            if not results:
                await interaction.followup.send("No matching locations were found for that city.", ephemeral=True)
                return

            chosen = results[: min(5, max(3, len(results))) ] if len(results) >= 3 else results[: len(results)]

            tasks = [
                self._fetch_weather(
                    session,
                    float(item.get("latitude", 0)),
                    float(item.get("longitude", 0)),
                    item.get("timezone", "auto"),
                )
                for item in chosen
            ]
            weather_data = await asyncio.gather(*tasks, return_exceptions=True)

        embed = discord.Embed(
            title=f"🌦️ Weather Snapshot — {city.title()}",
            description="Top matching locations and their current weather.",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )

        for place, weather in zip(chosen, weather_data):
            location_name = ", ".join(
                part
                for part in [
                    place.get("name", "Unknown"),
                    place.get("admin1", "Unknown"),
                    place.get("country", "Unknown"),
                ]
                if part
            )

            if isinstance(weather, Exception):
                embed.add_field(name=location_name, value="Weather data unavailable.", inline=True)
                continue

            current = weather.get("current_weather") or {}
            temp = current.get("temperature", "N/A")
            code = current.get("weathercode", "N/A")
            wind = current.get("windspeed", "N/A")
            embed.add_field(
                name=location_name,
                value=f"🌡️ **{temp}°C** | Code: **{code}**\n💨 Wind: **{wind} km/h**",
                inline=True,
            )

        embed.set_footer(text="Data source: Open-Meteo")
        await interaction.followup.send(embed=embed)
        await self._emit_utility_logs(
            interaction,
            activity_type="Weather Command",
            details=f"Weather lookup completed for city query: {city}",
            fields=[("City Search", city, False)],
        )

    @app_commands.command(name="links", description="Show official portfolio and project links")
    async def links(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔗 Creator Portfolio and Project Hub",
            description="Official links, resources, and projects.",
            color=0x5865F2,
        )
        embed.add_field(name="Core Links", value="Portfolio, Home Server, GitHub, and Music.", inline=False)
        embed.add_field(name="Project Links", value="QuickLink, StudyBot, Transparent Clock, and Node Server.", inline=False)
        embed.set_footer(text="Professional utility links dashboard")

        await interaction.response.send_message(embed=embed, view=self._links_view())
        await self._emit_utility_logs(interaction, activity_type="Links Command", details="Requested official links dashboard.")

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
        view = PollView(question, options, timer_in_hours)
        await interaction.response.send_message(embed=view.build_embed(), view=view)
        message = await interaction.original_response()
        view.message = message

        await self._emit_utility_logs(
            interaction,
            activity_type="Poll Created",
            details=f"Poll created with {len(options)} option(s).",
            jump_url=message.jump_url if hasattr(message, "jump_url") else None,
            fields=[("Question", question[:200], False)],
        )

        if timer_in_hours:
            async def close_later() -> None:
                await asyncio.sleep(timer_in_hours * 3600)
                winner_text = await view.close_poll()
                await self._emit_utility_logs(
                    interaction,
                    activity_type="Poll Ended",
                    details=winner_text or "Timed poll ended.",
                    fields=[("Question", question[:200], False)],
                )

            asyncio.create_task(close_later())


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCommands(bot))
