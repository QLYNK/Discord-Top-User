import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from telemetry import send_master_log

APP_LINK = "https://deepdey.vercel.app/"
INSTA_LINK = "https://instagram.com/deepdey.official"
GITHUB_LINK = "https://github.com/deepdeyiitgn/Discord-Top-User"
QUICKLINK_URL = "https://qlynk.me/"
STUDYBOT_URL = "https://studybot.qlynk.me/"
CLOCK_OVERLAY_URL = "https://clock.qlynk.me/"
QLYNK_NODE_URL = "https://node.qlynk.me/"


class UtilityCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _links_view() -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Portfolio", url=APP_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Home Server", url="https://discord.com/invite/t6ZKNw556n", style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="GitHub Repo", url=GITHUB_LINK, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="QuickLink", url=QUICKLINK_URL, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="StudyBot", url=STUDYBOT_URL, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Clock Overlay", url=CLOCK_OVERLAY_URL, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="QLYNK Node Server", url=QLYNK_NODE_URL, style=discord.ButtonStyle.link))
        return view

    @app_commands.command(name="now", description="Show live current time, date, and uptime")
    async def now(self, interaction: discord.Interaction):
        now_ts = int(time.time())
        start_ts = int(self.bot.start_time.replace(tzinfo=timezone.utc).timestamp()) if getattr(self.bot, "start_time", None) else now_ts
        embed = discord.Embed(title="🕒 Current Time", color=0x5865F2)
        embed.add_field(name="Live Clock", value=f"<t:{now_ts}:T>", inline=True)
        embed.add_field(name="Date", value=f"<t:{now_ts}:D>", inline=True)
        embed.add_field(name="Uptime", value=f"<t:{start_ts}:R>", inline=False)
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed)

    async def _fetch_weather(self, session: aiohttp.ClientSession, latitude: float, longitude: float, timezone_name: str):
        weather_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={latitude}&longitude={longitude}&current_weather=true&timezone={quote(timezone_name or 'auto')}"
        )
        async with session.get(weather_url) as response:
            response.raise_for_status()
            return await response.json()

    @app_commands.command(name="weather", description="Get weather for top city matches")
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
                await interaction.followup.send("No locations found for that city.", ephemeral=True)
                return

            count = min(5, len(results))
            if count >= 3:
                chosen = results[:count]
            else:
                chosen = results[:len(results)]

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
            place_name = f"{place.get('name', 'Unknown')}, {place.get('country', 'Unknown')}"
            if isinstance(weather, Exception):
                embed.add_field(name=place_name, value="Unable to fetch weather for this location.", inline=False)
                continue

            current = weather.get("current_weather") or {}
            temp = current.get("temperature", "N/A")
            wind = current.get("windspeed", "N/A")
            code = current.get("weathercode", "N/A")
            observed_at = current.get("time", "Unknown")
            embed.add_field(
                name=place_name,
                value=(
                    f"**Temperature:** {temp}°C\n"
                    f"**Wind:** {wind} km/h\n"
                    f"**Weather Code:** {code}\n"
                    f"**Observed:** `{observed_at}`"
                ),
                inline=False,
            )

        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="links", description="Show useful links")
    async def links(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔗 Official Links",
            description="Useful resources from Deep and QLYNK ecosystem.",
            color=0x5865F2,
        )
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=self._links_view())

    @app_commands.command(name="pomodoro", description="Start a Pomodoro timer")
    async def pomodoro(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 180] = 25):
        await interaction.response.send_message(f"🍅 Pomodoro started for **{minutes} minutes**.")
        start_text = (
            f"Pomodoro started by {interaction.user.mention} for {minutes} minutes "
            f"in {interaction.channel.mention if interaction.channel else 'a channel'}."
        )
        await send_master_log(self.bot, "Pomodoro Started", start_text)

        dm_text = (
            f"🍅 Your Pomodoro has started for **{minutes} minutes** in "
            f"**{interaction.guild.name if interaction.guild else 'Discord'}**."
        )
        try:
            await interaction.user.send(dm_text)
        except Exception:
            pass

        async def finish_timer() -> None:
            await asyncio.sleep(minutes * 60)
            done_text = f"✅ Pomodoro ended for {interaction.user.mention}. Great work!"

            if interaction.channel:
                try:
                    await interaction.channel.send(done_text)
                except Exception:
                    pass
            try:
                await interaction.user.send("✅ Your Pomodoro session has ended. Time for a short break!")
            except Exception:
                pass

            await send_master_log(
                self.bot,
                "Pomodoro Completed",
                f"Pomodoro completed by {interaction.user.mention} after {minutes} minutes.",
            )

        asyncio.create_task(finish_timer())


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCommands(bot))
