from __future__ import annotations

from typing import Any, Awaitable, Callable, TypeVar

import discord

DEEP_DEY_LABEL = "Deep Dey"
DEEP_DEY_URL = "https://deepdey.vercel.app/"
INSTAGRAM_LABEL = "Instagram"
INSTAGRAM_URL = "https://deepdey.vercel.app/insta"

_PATCHED = False
_T = TypeVar("_T")


def create_branding_view() -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=DEEP_DEY_LABEL, url=DEEP_DEY_URL, style=discord.ButtonStyle.link))
    view.add_item(discord.ui.Button(label=INSTAGRAM_LABEL, url=INSTAGRAM_URL, style=discord.ButtonStyle.link))
    return view


def with_branding_view(existing: discord.ui.View | None = None) -> discord.ui.View:
    return existing if existing is not None else create_branding_view()


def install_global_branding_enforcer() -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    original_messageable_send = discord.abc.Messageable.send
    original_response_send = discord.InteractionResponse.send_message
    original_webhook_send = discord.Webhook.send

    async def _patched_messageable_send(self: Any, *args: Any, **kwargs: Any):
        kwargs["view"] = with_branding_view(kwargs.get("view"))
        return await original_messageable_send(self, *args, **kwargs)

    async def _patched_response_send(self: discord.InteractionResponse, *args: Any, **kwargs: Any):
        kwargs["view"] = with_branding_view(kwargs.get("view"))
        return await original_response_send(self, *args, **kwargs)

    async def _patched_webhook_send(self: discord.Webhook, *args: Any, **kwargs: Any):
        kwargs["view"] = with_branding_view(kwargs.get("view"))
        return await original_webhook_send(self, *args, **kwargs)

    discord.abc.Messageable.send = _patched_messageable_send  # type: ignore[assignment]
    discord.InteractionResponse.send_message = _patched_response_send  # type: ignore[assignment]
    discord.Webhook.send = _patched_webhook_send  # type: ignore[assignment]

