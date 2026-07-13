from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database import client as mongo_client
from telemetry import log_exception, send_activity_log
from utils.branding_view import create_branding_view
from utils.discord_resilience import call_with_discord_backoff

PASSWORD = os.getenv("PASSWORD", "")

_db = mongo_client["LeaderboardBotDB"]
status_profiles_col = _db["status_profiles"]


@dataclass(slots=True)
class ProxyProfile:
    user_id: int
    status_text: str
    eta_text: str
    aliases: list[str]
    guild_id: int | None
    missed_pings: list[dict]

    @property
    def alias_patterns(self) -> list[re.Pattern[str]]:
        return [re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE) for alias in self.aliases if alias]


class _OwnerStatusModal(discord.ui.Modal, title="Owner Break Status"):
    current_status = discord.ui.TextInput(
        label="Current Status",
        placeholder="On a hardcore offline study grind.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )
    eta = discord.ui.TextInput(label="ETA", placeholder="Next month / 6 Hours", required=True, max_length=120)
    aliases = discord.ui.TextInput(
        label="Aliases",
        placeholder="Deep, deepdey, future iitian",
        required=True,
        max_length=300,
    )
    password = discord.ui.TextInput(
        label="Password",
        style=discord.TextStyle.short,
        required=True,
        max_length=200,
    )

    def __init__(self, cog: "ProxyCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not PASSWORD:
            await interaction.response.send_message("Status password is not configured.", ephemeral=True)
            return
        if not secrets.compare_digest(self.password.value, PASSWORD):
            await interaction.response.send_message("Invalid password. Status profile was not saved.", ephemeral=True)
            return

        aliases = [item.strip().lower() for item in self.aliases.value.split(",") if item.strip()]
        aliases = list(dict.fromkeys(aliases))[:25]
        payload = {
            "user_id": interaction.user.id,
            "guild_id": interaction.guild_id,
            "status_text": self.current_status.value.strip(),
            "eta_text": self.eta.value.strip(),
            "aliases": aliases,
            "missed_pings": [],
            "updated_at": datetime.now(timezone.utc),
        }
        try:
            await status_profiles_col.update_one({"user_id": interaction.user.id}, {"$set": payload}, upsert=True)
            self.cog._status_cache[interaction.user.id] = ProxyProfile(
                user_id=interaction.user.id,
                status_text=payload["status_text"],
                eta_text=payload["eta_text"],
                aliases=aliases,
                guild_id=interaction.guild_id,
                missed_pings=[],
            )
            self.cog._owner_prompt_suppression.pop(interaction.user.id, None)
            await interaction.response.send_message(
                "Your break status is now active. Alias tracking and proxy responses are enabled.",
                ephemeral=True,
                view=create_branding_view(),
            )
            await send_activity_log(
                self.cog.bot,
                activity_type="Proxy Activated",
                details="Digital proxy status activated.",
                module="Proxy",
                guild=interaction.guild,
                user=interaction.user,
                jump_url=interaction.channel.jump_url if isinstance(interaction.channel, discord.TextChannel) else None,
                fields=[
                    ("ETA", payload["eta_text"][:150], False),
                    ("Aliases", ", ".join(aliases)[:1024] or "None", False),
                ],
            )
        except Exception as exc:
            await interaction.response.send_message("Failed to save the status profile. Please try again.", ephemeral=True)
            await log_exception(
                self.cog.bot,
                title="Proxy Activation Failed",
                error=exc,
                context=f"User {interaction.user.id} in guild {interaction.guild_id}",
            )


class _PublicStatusModal(discord.ui.Modal, title="Set Digital Proxy Status"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Studying / Sleeping / Working",
        required=True,
        max_length=100,
    )
    current_status = discord.ui.TextInput(
        label="Message for Pings",
        placeholder="Leave a message for anyone who pings me.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=400,
    )
    eta = discord.ui.TextInput(
        label="ETA (Kab wapas aaoge?)",
        placeholder="2 hours / Tomorrow morning",
        required=True,
        max_length=120,
    )

    def __init__(self, cog: "ProxyCommands"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        payload = {
            "user_id": interaction.user.id,
            "guild_id": interaction.guild_id,
            "status_text": f"[{self.reason.value.strip()}] {self.current_status.value.strip()}",
            "eta_text": self.eta.value.strip(),
            "aliases": [],  # Normal users ko alias support nahi
            "missed_pings": [],
            "updated_at": datetime.now(timezone.utc),
        }
        try:
            await status_profiles_col.update_one({"user_id": interaction.user.id}, {"$set": payload}, upsert=True)
            self.cog._status_cache[interaction.user.id] = ProxyProfile(
                user_id=interaction.user.id,
                status_text=payload["status_text"],
                eta_text=payload["eta_text"],
                aliases=[],
                guild_id=interaction.guild_id,
                missed_pings=[],
            )
            
            # Bot invite link popup for better reach
            invite_url = discord.utils.oauth_url(interaction.client.user.id, permissions=discord.Permissions(8))
            
            await interaction.response.send_message(
                f"✅ Tumhara proxy status set ho gaya hai!\n\n💡 **Pro Tip:** Bot ko apne personal servers me add karo taaki yeh har jagah tumhare mentions ka reply kar sake. [Click Here to Invite Bot]({invite_url})",
                ephemeral=True,
                view=create_branding_view()
            )
            
            await send_activity_log(
                self.cog.bot,
                activity_type="Public Proxy Activated",
                details="A normal user activated digital proxy status.",
                module="Proxy",
                guild=interaction.guild,
                user=interaction.user,
            )
        except Exception as exc:
            await interaction.response.send_message("Failed to save the status profile. Please try again.", ephemeral=True)
            await log_exception(self.cog.bot, title="Public Proxy Activation Failed", error=exc, context=f"User {interaction.user.id}")

# YAHAN SE ADD KARNA HAI: Missing View Class aur Keep ON button
class _ToggleStatusView(discord.ui.View):
    def __init__(self, cog: "ProxyCommands", owner_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the status owner can use this control.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Keep ON", style=discord.ButtonStyle.success)
    async def keep_on(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        self.cog._owner_prompt_suppression[self.owner_id] = 10
        self.cog._toggle_prompt_inflight.discard(self.owner_id)
        await interaction.response.send_message(
            "Status kept ON. Prompt suppression is active for your next 10 messages.",
            view=create_branding_view(),
        )
        await send_activity_log(
            self.cog.bot,
            activity_type="Proxy Kept Active",
            details="Status owner kept proxy ON and enabled 10-message suppression.",
            module="Proxy",
            guild=interaction.guild,
            user=interaction.user,
        )

    # TUMHARA CODE YAHAN SE CONTINUE HOGA...
    @discord.ui.button(label="Turn OFF", style=discord.ButtonStyle.danger)
    async def turn_off(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        profile = self.cog._status_cache.pop(self.owner_id, None)
        self.cog._owner_prompt_suppression.pop(self.owner_id, None)
        self.cog._toggle_prompt_inflight.discard(self.owner_id)

        db_profile = await status_profiles_col.find_one_and_delete({"user_id": self.owner_id})
        missed = list((db_profile or {}).get("missed_pings", []))
        if profile:
            missed = profile.missed_pings or missed

        embed = discord.Embed(
            title="Break Status Disabled",
            description="Your digital proxy has been turned off.",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        if missed:
            lines = []
            for item in missed[:20]:
                lines.append(
                    f"• **{item.get('author_name', 'Unknown')}** — "
                    f"[Jump]({item.get('jump_url', 'https://discord.com')})\n"
                    f"{str(item.get('content', ''))[:160]}"
                )
            embed.add_field(name="Missed Pings", value="\n\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Missed Pings", value="No missed pings were recorded.", inline=False)

        await interaction.response.send_message("Status is now OFF.", view=create_branding_view())
        try:
            await interaction.user.send(embed=embed, view=create_branding_view())
        except discord.Forbidden:
            pass
        except Exception:
            pass

        await send_activity_log(
            self.cog.bot,
            activity_type="Proxy Disabled",
            details="Status owner disabled proxy and received missed ping summary.",
            module="Proxy",
            guild=interaction.guild,
            user=interaction.user,
            fields=[("Missed Ping Count", str(len(missed)), True)],
        )


class ProxyCommands(commands.Cog):
    status_group = app_commands.Group(name="status", description="Digital proxy status engine")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._status_cache: dict[int, ProxyProfile] = {}
        self._owner_prompt_suppression: dict[int, int] = {}
        self._toggle_prompt_inflight: set[int] = set()

    async def cog_load(self) -> None:
        try:
            docs = await status_profiles_col.find({}).to_list(length=None)
            for doc in docs:
                user_id = int(doc.get("user_id"))
                self._status_cache[user_id] = ProxyProfile(
                    user_id=user_id,
                    status_text=str(doc.get("status_text", "Away")),
                    eta_text=str(doc.get("eta_text", "Unknown")),
                    aliases=[str(a).lower() for a in doc.get("aliases", []) if str(a).strip()],
                    guild_id=doc.get("guild_id"),
                    missed_pings=list(doc.get("missed_pings", [])),
                )
        except Exception as exc:
            await log_exception(self.bot, title="Proxy Cache Load Failed", error=exc, context="cog_load")

    @status_group.command(name="set", description="Set your digital break status")
    async def status_set(self, interaction: discord.Interaction) -> None:
        is_owner = await self.bot.is_owner(interaction.user)
        if is_owner:
            await interaction.response.send_modal(_OwnerStatusModal(self))
        else:
            await interaction.response.send_modal(_PublicStatusModal(self))

    @status_group.command(name="end", description="End your digital proxy status globally")
    async def status_end(self, interaction: discord.Interaction) -> None:
        profile = self._status_cache.pop(interaction.user.id, None)
        self._owner_prompt_suppression.pop(interaction.user.id, None)
        self._toggle_prompt_inflight.discard(interaction.user.id)

        db_profile = await status_profiles_col.find_one_and_delete({"user_id": interaction.user.id})
        
        if not profile and not db_profile:
            await interaction.response.send_message("❌ Tumhara koi active proxy status nahi hai.", ephemeral=True)
            return

        missed = list((db_profile or {}).get("missed_pings", []))
        if profile:
            missed = profile.missed_pings or missed

        embed = discord.Embed(
            title="Break Status Disabled",
            description="Your digital proxy has been turned off globally.",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        if missed:
            lines = []
            for item in missed[:20]:
                lines.append(
                    f"• **{item.get('author_name', 'Unknown')}** — "
                    f"[Jump]({item.get('jump_url', 'https://discord.com')})\n"
                    f"{str(item.get('content', ''))[:160]}"
                )
            embed.add_field(name="Missed Pings", value="\n\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Missed Pings", value="No missed pings were recorded.", inline=False)

        await interaction.response.send_message("✅ Status is now OFF.", view=create_branding_view(), ephemeral=True)
        try:
            await interaction.user.send(embed=embed, view=create_branding_view())
        except discord.Forbidden:
            await interaction.followup.send("⚠️ Tumhara DMs closed hai isliye missed pings ki list DM nahi kar paaya.", ephemeral=True)
            
        await send_activity_log(
            self.bot,
            activity_type="Proxy Ended Manually",
            details="User used /status end to disable proxy.",
            module="Proxy",
            guild=interaction.guild,
            user=interaction.user,
        )

    async def _append_missed_ping(self, owner_id: int, payload: dict) -> None:
        try:
            await status_profiles_col.update_one({"user_id": owner_id}, {"$push": {"missed_pings": payload}})
            profile = self._status_cache.get(owner_id)
            if profile:
                profile.missed_pings.append(payload)
                if len(profile.missed_pings) > 200:
                    profile.missed_pings = profile.missed_pings[-200:]
        except Exception as exc:
            await log_exception(self.bot, title="Proxy Missed Ping Save Failed", error=exc, context=f"owner={owner_id}")

    async def _handle_owner_message(self, message: discord.Message, profile: ProxyProfile) -> None:
        suppression = self._owner_prompt_suppression.get(message.author.id, 0)
        if suppression > 0:
            self._owner_prompt_suppression[message.author.id] = suppression - 1
            if self._owner_prompt_suppression[message.author.id] <= 0:
                self._owner_prompt_suppression.pop(message.author.id, None)
            return

        if message.author.id in self._toggle_prompt_inflight:
            return

        self._toggle_prompt_inflight.add(message.author.id)
        try:
            await call_with_discord_backoff(
                bot=self.bot,
                operation_name="proxy_owner_toggle_prompt",
                guild=message.guild,
                user=message.author,
                factory=lambda: message.author.send(
                    (
                        "Welcome back. Your proxy is active.\n"
                        "Do you want to turn Break Status OFF or keep it ON?"
                    ),
                    view=_ToggleStatusView(self, message.author.id),
                ),
            )
        except discord.Forbidden:
            self._toggle_prompt_inflight.discard(message.author.id)
        except Exception as exc:
            self._toggle_prompt_inflight.discard(message.author.id)
            await log_exception(
                self.bot,
                title="Proxy Toggle Prompt Failed",
                error=exc,
                context=f"user={message.author.id} guild={message.guild.id}",
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        author_profile = self._status_cache.get(message.author.id)
        if author_profile:
            await self._handle_owner_message(message, author_profile)

        content = (message.content or "").lower()
        if not content and not message.mentions:
            return

        matched_owner_ids: set[int] = {member.id for member in message.mentions if member.id in self._status_cache}
        for owner_id, profile in self._status_cache.items():
            if owner_id == message.author.id:
                continue
            if owner_id in matched_owner_ids:
                continue
            if any(pattern.search(content) for pattern in profile.alias_patterns):
                matched_owner_ids.add(owner_id)

        for owner_id in matched_owner_ids:
            profile = self._status_cache.get(owner_id)
            if not profile:
                continue
            member = message.guild.get_member(owner_id) or self.bot.get_user(owner_id)
            owner_label = member.mention if member else f"`{owner_id}`"
            embed = discord.Embed(
                title="Digital Proxy Update",
                description=(
                    f"{owner_label} is currently unavailable.\n\n"
                    f"**Current Status:** {profile.status_text}\n"
                    f"**ETA:** {profile.eta_text}"
                ),
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="Automated proxy response")

            try:
                await call_with_discord_backoff(
                    bot=self.bot,
                    operation_name="proxy_reply_send",
                    guild=message.guild,
                    user=message.author,
                    factory=lambda: message.reply(embed=embed, view=create_branding_view(), mention_author=False),
                )
            except Exception as exc:
                await log_exception(
                    self.bot,
                    title="Proxy Reply Failed",
                    error=exc,
                    context=f"author={message.author.id} owner={owner_id} guild={message.guild.id}",
                )
                continue

            ping_payload = {
                "author_id": message.author.id,
                "author_name": str(message.author),
                "content": (message.content or "")[:350],
                "jump_url": message.jump_url,
                "created_at": datetime.now(timezone.utc),
            }
            await self._append_missed_ping(owner_id, ping_payload)
            await send_activity_log(
                self.bot,
                activity_type="Proxy Triggered",
                details="Proxy responder posted status update and stored missed ping.",
                module="Proxy",
                guild=message.guild,
                user=message.author,
                jump_url=message.jump_url,
                fields=[
                    ("Status Owner ID", str(owner_id), True),
                    ("Author", f"{message.author} ({message.author.id})", False),
                ],
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProxyCommands(bot))
