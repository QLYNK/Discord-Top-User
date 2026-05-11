import traceback
from datetime import datetime, timezone
from typing import Iterable

import discord

MASTER_GUILD_ID = 1322854959686877185
MASTER_CHANNEL_ID = 1503394648763138088


async def get_master_log_channel(bot: discord.Client) -> discord.TextChannel | None:
    channel = bot.get_channel(MASTER_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        return channel

    guild = bot.get_guild(MASTER_GUILD_ID)
    if guild:
        fetched = guild.get_channel(MASTER_CHANNEL_ID)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    return None


async def send_master_log(
    bot: discord.Client,
    title: str,
    description: str,
    *,
    color: int = 0x5865F2,
    fields: Iterable[tuple[str, str, bool]] | None = None,
) -> None:
    channel = await get_master_log_channel(bot)
    if not channel:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value[:1024] if value else "-", inline=inline)
    try:
        await channel.send(embed=embed)
    except Exception:
        pass


async def log_exception(
    bot: discord.Client,
    *,
    title: str,
    error: Exception,
    context: str,
    fields: Iterable[tuple[str, str, bool]] | None = None,
) -> None:
    trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if len(trace) > 3500:
        trace = trace[:3500] + "\n... (truncated)"
    extra_fields = list(fields or [])
    extra_fields.append(("Context", context, False))
    extra_fields.append(("Traceback", f"```py\n{trace}\n```", False))
    await send_master_log(bot, title, f"{type(error).__name__}: {error}", color=0xED4245, fields=extra_fields)
