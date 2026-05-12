from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

import discord

from telemetry import send_activity_log

_T = TypeVar("_T")


async def call_with_discord_backoff(
    *,
    bot: discord.Client | None,
    operation_name: str,
    factory: Callable[[], Awaitable[_T]],
    guild: discord.Guild | None = None,
    user: discord.abc.User | None = None,
    max_attempts: int = 4,
    base_delay: float = 1.25,
) -> _T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await factory()
        except discord.HTTPException as exc:
            retryable = exc.status in (429, 500, 502, 503, 504)
            if not retryable or attempt >= max_attempts:
                raise
            delay = round(base_delay * (2 ** (attempt - 1)), 2)
            if bot is not None:
                await send_activity_log(
                    bot,
                    activity_type="Discord API Backoff",
                    details=f"Discord API Limit reached. Retrying in {delay} seconds...",
                    module="Resilience",
                    guild=guild,
                    user=user,
                    fields=[
                        ("Operation", operation_name, False),
                        ("HTTP Status", str(exc.status), True),
                        ("Attempt", f"{attempt}/{max_attempts}", True),
                    ],
                )
            await asyncio.sleep(delay)
        except asyncio.TimeoutError:
            if attempt >= max_attempts:
                raise
            delay = round(base_delay * (2 ** (attempt - 1)), 2)
            if bot is not None:
                await send_activity_log(
                    bot,
                    activity_type="Discord API Backoff",
                    details=f"Discord API Limit reached. Retrying in {delay} seconds...",
                    module="Resilience",
                    guild=guild,
                    user=user,
                    fields=[
                        ("Operation", operation_name, False),
                        ("Reason", "Timeout", True),
                        ("Attempt", f"{attempt}/{max_attempts}", True),
                    ],
                )
            await asyncio.sleep(delay)

