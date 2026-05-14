import traceback
from datetime import datetime, timezone
from typing import Iterable

import discord

MASTER_GUILD_ID = 1322854959686877185
MASTER_CHANNEL_ID = 1503394648763138088

MODULE_LOG_FIELD_MAP = {
    "pomodoro": "pomodoro_logs_channel_id",
    "afk": "afk_logs_channel_id",
    "utilities": "utilities_logs_channel_id",
    "music": "music_logs_channel_id",
}


def _field_text(value: object | None) -> str:
    if value is None:
        return "-"
    text = str(value)
    return text if text else "-"


def _fmt_server(guild: discord.Guild | None) -> str:
    if not guild:
        return "Unknown Server"
    return f"{guild.name} ({guild.id})"


def _fmt_user(user: discord.abc.User | None) -> str:
    if not user:
        return "Unknown User"
    return f"{user} ({user.id})"


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
            embed.add_field(name=_field_text(name)[:256], value=_field_text(value)[:1024], inline=inline)

    try:
        await channel.send(embed=embed)
    except Exception:
        pass


async def send_activity_log(
    bot: discord.Client,
    *,
    activity_type: str,
    details: str,
    module: str,
    guild: discord.Guild | None = None,
    user: discord.abc.User | None = None,
    jump_url: str | None = None,
    fields: Iterable[tuple[str, str, bool]] | None = None,
    color: int = 0x5865F2,
) -> None:
    ts = int(datetime.now(timezone.utc).timestamp())
    base_fields: list[tuple[str, str, bool]] = [
        ("Server", _fmt_server(guild), False),
        ("User", _fmt_user(user), False),
        ("Activity Type", activity_type, True),
        ("Timestamp", f"<t:{ts}:F>", True),
    ]
    if jump_url:
        base_fields.append(("Reference", jump_url, False))
    if fields:
        base_fields.extend(list(fields))

    await send_master_log(
        bot,
        title=f"{module} • {activity_type}",
        description=details,
        color=color,
        fields=base_fields,
    )


async def send_guild_module_log(
    bot: discord.Client,
    *,
    guild: discord.Guild | None,
    module: str,
    title: str,
    description: str,
    fields: Iterable[tuple[str, str, bool]] | None = None,
    color: int = 0x5865F2,
) -> None:
    if not guild:
        return

    setting_key = MODULE_LOG_FIELD_MAP.get(module.lower())
    if not setting_key:
        return

    try:
        import database as db

        settings = await db.get_guild_settings(guild.id)
        channel_id = settings.get(setting_key)
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=_field_text(name)[:256], value=_field_text(value)[:1024], inline=inline)
        await channel.send(embed=embed)
    except Exception:
        pass


async def send_game_telemetry(
    bot: discord.Client,
    *,
    guild: discord.Guild | None,
    game_name: str,
    result: str,
    players: Iterable[tuple[str, int, str]],
) -> None:
    guild_name = guild.name if guild else "Unknown Server"
    guild_id = guild.id if guild else 0
    player_lines = [f"• {name} ({user_id})" for name, user_id, _ in players]
    point_lines = [f"• {name}: {delta}" for name, _, delta in players]

    await send_master_log(
        bot,
        f"Game Result • {game_name}",
        "Game activity recorded.",
        fields=[
            ("Game", game_name, True),
            ("Result", result, True),
            ("Server", f"{guild_name} ({guild_id})", False),
            ("Players", "\n".join(player_lines) or "-", False),
            ("Point Changes", "\n".join(point_lines) or "-", False),
        ],
    )


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
