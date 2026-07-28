"""
Production-ready Discord ticket system cog.
Handles categories, panels, claim/unclaim flow, transcripts, and admin setup.
"""

from __future__ import annotations

import html
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from telemetry import log_exception, send_activity_log

TRANSCRIPT_ROOT = os.path.join("data", "transcripts")
MAX_QUESTIONS = 7
MAX_MODAL_FIELDS = 5
PRIORITIES = ("low", "normal", "high", "urgent")

# Temporary multi-step form answers: {(guild_id, user_id, category_key): {...}}
_form_buffer: dict[tuple[int, int, str], dict[str, Any]] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "ticket").lower()).strip("-")
    return (cleaned or "ticket")[:max_len]


def _safe_channel_name(username: str, ticket_id: int, category_key: str) -> str:
    user_part = _slug(str(username), 20)
    cat_part = _slug(category_key, 20)
    name = f"{user_part}-{ticket_id}-{cat_part}"
    return name[:100]


def _is_admin(member: discord.Member | None) -> bool:
    return bool(member and member.guild_permissions.administrator)


def _category_by_key(config: dict, key: str) -> dict | None:
    for cat in config.get("categories", []):
        if cat.get("key") == key:
            return cat
    return None


def _staff_role_ids(category: dict) -> list[int]:
    return [int(r) for r in category.get("staff_role_ids", []) if str(r).isdigit()]


def _member_has_staff_access(member: discord.Member, category: dict) -> bool:
    if _is_admin(member):
        return True
    staff_ids = set(_staff_role_ids(category))
    return any(role.id in staff_ids for role in member.roles)


def _ticket_staff_ids(ticket: dict, config: dict) -> list[int]:
    category = _category_by_key(config, ticket.get("category_key", ""))
    return _staff_role_ids(category or {})


async def _get_ticket_context(
    interaction: discord.Interaction,
) -> tuple[dict | None, dict | None, discord.TextChannel | None]:
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        return None, None, None
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    if not ticket:
        return None, None, interaction.channel
    config = await db.get_ticket_config(interaction.guild.id)
    return ticket, config, interaction.channel


def _can_manage_ticket(
    member: discord.Member,
    ticket: dict,
    config: dict,
    *,
    owner_ok: bool = True,
) -> bool:
    if _is_admin(member):
        return True
    if owner_ok and member.id == ticket.get("owner_id"):
        return True
    category = _category_by_key(config, ticket.get("category_key", ""))
    if category and _member_has_staff_access(member, category):
        return True
    claimed = ticket.get("claimed_by")
    return bool(claimed and member.id == claimed)


def _build_panel_embed(config: dict) -> discord.Embed:
    title = config.get("panel_title") or "Support Tickets"
    base_desc = config.get("panel_description") or "Select a category below to open a ticket."
    categories = config.get("categories") or []

    lines = ["**Available Categories**"]
    if not categories:
        lines.append("_No categories configured yet. Admins can add them with `/ticket category add`._")
    else:
        for cat in categories:
            emoji = cat.get("emoji") or "🎫"
            q_count = len(cat.get("questions") or [])
            staff_count = len(cat.get("staff_role_ids") or [])
            lines.append(
                f"{emoji} **{cat.get('name', 'Unknown')}** (`{cat.get('key')}`)\n"
                f"> {cat.get('description') or 'No description.'}\n"
                f"> Questions: `{q_count}` • Staff roles: `{staff_count}`"
            )

    embed = discord.Embed(
        title=title,
        description=f"{base_desc}\n\n" + "\n\n".join(lines),
        color=0x5865F2,
        timestamp=_utc_now(),
    )
    embed.set_footer(text="Select a category from the dropdown • an app by deep")
    return embed


class TicketTranscriptGenerator:
    @staticmethod
    async def collect_messages(channel: discord.TextChannel) -> list[dict]:
        messages: list[dict] = []
        async for msg in channel.history(limit=None, oldest_first=True):
            attachments = [
                {"filename": a.filename, "url": a.url, "size": a.size, "content_type": a.content_type}
                for a in msg.attachments
            ]
            embeds = [
                {
                    "title": e.title,
                    "description": e.description,
                    "url": e.url,
                    "color": e.color.value if e.color else None,
                    "fields": [{"name": f.name, "value": f.value} for f in e.fields],
                }
                for e in msg.embeds
            ]
            ref = msg.reference.resolved if msg.reference and msg.reference.resolved else None
            reply_to = None
            if isinstance(ref, discord.Message):
                reply_to = {"id": str(ref.id), "author": str(ref.author), "content": ref.content[:200]}

            messages.append(
                {
                    "id": str(msg.id),
                    "author_id": str(msg.author.id),
                    "author_name": str(msg.author),
                    "author_bot": msg.author.bot,
                    "content": msg.content or "",
                    "timestamp": msg.created_at.replace(tzinfo=timezone.utc).isoformat(),
                    "edited_at": msg.edited_at.replace(tzinfo=timezone.utc).isoformat() if msg.edited_at else None,
                    "attachments": attachments,
                    "embeds": embeds,
                    "reply_to": reply_to,
                    "pinned": msg.pinned,
                }
            )
        return messages

    @staticmethod
    def _meta(ticket: dict, guild: discord.Guild, channel: discord.TextChannel) -> dict:
        def _fmt_dt(val):
            if isinstance(val, datetime):
                # ensure UTC ISO timestamp
                return val.replace(tzinfo=timezone.utc).isoformat()
            if isinstance(val, str):
                return val
            return None

        return {
            "guild": {"id": str(guild.id), "name": guild.name},
            "channel": {"id": str(channel.id), "name": channel.name},
            "ticket_id": ticket.get("ticket_id"),
            "category": ticket.get("category_key"),
            "owner_id": str(ticket.get("owner_id")),
            "claimed_by": str(ticket.get("claimed_by")) if ticket.get("claimed_by") else None,
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "tags": ticket.get("tags", []),
            "form_answers": ticket.get("form_answers", {}),
            "created_at": _fmt_dt(ticket.get("created_at")),
            "closed_at": _fmt_dt(ticket.get("closed_at")),
            "exported_at": _utc_now().isoformat(),
        }

    @classmethod
    async def generate(
        cls,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: dict,
    ) -> dict[str, str]:
        messages = await cls.collect_messages(channel)
        meta = cls._meta(ticket, guild, channel)
        payload = {"meta": meta, "messages": messages}

        base_dir = os.path.join(
            TRANSCRIPT_ROOT,
            str(guild.id),
            f"ticket-{ticket.get('ticket_id')}-{uuid.uuid4().hex[:8]}",
        )
        os.makedirs(base_dir, exist_ok=True)

        json_path = os.path.join(base_dir, "transcript.json")
        txt_path = os.path.join(base_dir, "transcript.txt")
        html_path = os.path.join(base_dir, "transcript.html")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        txt_lines = [
            f"Ticket #{ticket.get('ticket_id')} — {guild.name}",
            f"Channel: #{channel.name}",
            f"Category: {ticket.get('category_key')}",
            f"Owner: {ticket.get('owner_id')}",
            f"Exported: {meta['exported_at']}",
            "-" * 60,
        ]
        for msg in messages:
            ts = msg["timestamp"]
            txt_lines.append(f"[{ts}] {msg['author_name']}: {msg['content']}")
            if msg["attachments"]:
                for att in msg["attachments"]:
                    txt_lines.append(f"  [Attachment] {att['filename']} — {att['url']}")
            if msg["reply_to"]:
                txt_lines.append(f"  [Reply to {msg['reply_to']['author']}] {msg['reply_to']['content']}")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        rows = ""
        for msg in messages:
            content = html.escape(msg["content"] or "").replace("\n", "<br>")
            att_html = ""
            if msg["attachments"]:
                att_html = "<br>".join(
                    f'<a href="{html.escape(a["url"])}" target="_blank">{html.escape(a["filename"])}</a>'
                    for a in msg["attachments"]
                )
            reply_html = ""
            if msg["reply_to"]:
                reply_html = (
                    f'<div class="reply">↪ Replying to <b>{html.escape(msg["reply_to"]["author"])}</b>: '
                    f'{html.escape(msg["reply_to"]["content"])}</div>'
                )
            embed_html = ""
            for emb in msg["embeds"]:
                if emb.get("title") or emb.get("description"):
                    embed_html += (
                        f'<div class="embed"><b>{html.escape(emb.get("title") or "Embed")}</b><br>'
                        f'{html.escape(emb.get("description") or "")}</div>'
                    )
            rows += f"""
            <div class="message">
                <div class="meta"><span class="author">{html.escape(msg['author_name'])}</span>
                <span class="time">{html.escape(msg['timestamp'])}</span></div>
                {reply_html}
                <div class="content">{content or '<i>[no text]</i>'}</div>
                {att_html}
                {embed_html}
            </div>"""

        html_doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Ticket #{ticket.get('ticket_id')} — {html.escape(guild.name)}</title>
<style>
body {{ font-family: Segoe UI, sans-serif; background:#313338; color:#dbdee1; margin:0; padding:20px; }}
.container {{ max-width:960px; margin:auto; background:#2b2d31; padding:20px; border-radius:8px; }}
.header {{ border-bottom:1px solid #1e1f22; padding-bottom:12px; margin-bottom:16px; }}
.message {{ padding:10px 0; border-bottom:1px solid #1e1f22; }}
.meta {{ color:#949ba4; font-size:12px; margin-bottom:4px; }}
.author {{ color:#fff; font-weight:600; margin-right:8px; }}
.content {{ white-space:pre-wrap; }}
.reply {{ font-size:12px; color:#949ba4; margin-bottom:4px; }}
.embed {{ margin-top:6px; padding:8px; border-left:3px solid #5865F2; background:#1e1f22; font-size:13px; }}
a {{ color:#00aff4; }}
</style></head><body><div class="container">
<div class="header"><h2>Ticket #{ticket.get('ticket_id')} — {html.escape(channel.name)}</h2>
<p>Guild: {html.escape(guild.name)} • Category: {html.escape(str(ticket.get('category_key')))}</p></div>
{rows}
</div></body></html>"""
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_doc)

        return {"json": json_path, "txt": txt_path, "html": html_path}


class TicketFormModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "TicketCommands",
        guild_id: int,
        category: dict,
        questions: list[dict],
        *,
        part: int = 1,
        prior_answers: dict[str, str] | None = None,
    ):
        title = f"{category.get('emoji', '🎫')} {category.get('name', 'Ticket')}"
        if part > 1:
            title = f"{title} (continued)"
        super().__init__(title=title[:45], timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.category = category
        self.part = part
        self.prior_answers = prior_answers or {}
        self.field_map: dict[str, str] = {}

        for idx, q in enumerate(questions[:MAX_MODAL_FIELDS]):
            key = f"q{idx + (part - 1) * MAX_MODAL_FIELDS}"
            style = discord.TextStyle.paragraph if q.get("style") == "paragraph" else discord.TextStyle.short
            field = discord.ui.TextInput(
                label=(q.get("title") or f"Question {idx + 1}")[:45],
                placeholder=(q.get("placeholder") or "")[:100],
                required=bool(q.get("required", True)),
                style=style,
                max_length=1024 if style == discord.TextStyle.paragraph else 400,
            )
            self.field_map[key] = q.get("title") or key
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        answers = dict(self.prior_answers)
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                answers[self.field_map.get(child.label, child.label)] = child.value

        all_questions = self.category.get("questions") or []
        answered_count = len(answers)
        if answered_count < len(all_questions):
            remaining = all_questions[answered_count:]
            modal = TicketFormModal(
                self.cog,
                self.guild_id,
                self.category,
                remaining,
                part=self.part + 1,
                prior_answers=answers,
            )
            await interaction.response.send_modal(modal)
            return

        await self.cog.create_ticket_from_form(interaction, self.category, answers)


class CategorySelect(discord.ui.Select):
    def __init__(self, cog: "TicketCommands", config: dict):
        self.cog = cog
        options: list[discord.SelectOption] = []
        for cat in (config.get("categories") or [])[:25]:
            options.append(
                discord.SelectOption(
                    label=(cat.get("name") or cat.get("key", "Category"))[:100],
                    value=cat.get("key", ""),
                    description=(cat.get("description") or "")[:100] or None,
                    emoji=cat.get("emoji") if cat.get("emoji") else None,
                )
            )
        super().__init__(
            placeholder="Choose a ticket category…",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No categories", value="_none")],
            custom_id="ticket:category_select",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "_none":
            await interaction.response.send_message("❌ No ticket categories configured.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        config = await db.get_ticket_config(interaction.guild.id)
        category = _category_by_key(config, self.values[0])
        if not category:
            await interaction.response.send_message("❌ Category not found.", ephemeral=True)
            return

        existing = await db.tickets_col.find_one(
            {"guild_id": interaction.guild.id, "owner_id": interaction.user.id, "status": "open"}
        )
        if existing:
            ch = interaction.guild.get_channel(existing.get("channel_id"))
            mention = ch.mention if ch else f"ticket #{existing.get('ticket_id')}"
            await interaction.response.send_message(
                f"❌ You already have an open ticket: {mention}", ephemeral=True
            )
            return

        questions = (category.get("questions") or [])[:MAX_QUESTIONS]
        if not questions:
            await self.cog.create_ticket_from_form(interaction, category, {})
            return

        modal = TicketFormModal(self.cog, interaction.guild.id, category, questions[:MAX_MODAL_FIELDS])
        await interaction.response.send_modal(modal)


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "TicketCommands", config: dict):
        super().__init__(timeout=None)
        self.cog = cog
        if config.get("categories"):
            self.add_item(CategorySelect(cog, config))


class TicketControlView(discord.ui.View):
    def __init__(self, cog: "TicketCommands"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket:claim")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_claim(interaction)

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="ticket:unclaim")
    async def unclaim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_unclaim(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.primary, custom_id="ticket:close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_close(interaction, via_button=True)

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.secondary, custom_id="ticket:lock")
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_lock(interaction, locked=True)

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.secondary, custom_id="ticket:unlock")
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_lock(interaction, locked=False)

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, custom_id="ticket:transcript")
    async def transcript_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_transcript(interaction)


class RatingView(discord.ui.View):
    def __init__(self, cog: "TicketCommands", guild_id: int, ticket_id: int):
        super().__init__(timeout=86400)
        self.cog = cog
        self.guild_id = guild_id
        self.ticket_id = ticket_id
        for i in range(1, 6):
            self.add_item(self._make_star(i))

    def _make_star(self, stars: int) -> discord.ui.Button:
        btn = discord.ui.Button(label=str(stars), emoji="⭐", style=discord.ButtonStyle.secondary, row=0)

        async def callback(interaction: discord.Interaction):
            await db.update_ticket(self.guild_id, self.ticket_id, {"rating": stars, "rated_at": _utc_now()})
            await interaction.response.edit_message(content=f"Thanks for rating this ticket **{stars}/5**!", view=None)

        btn.callback = callback
        return btn


class TicketCommands(commands.Cog):
    """Full ticket system: categories, panels, claim flow, transcripts, and logs."""

    ticket_group = app_commands.Group(
        name="ticket",
        description="Ticket system — support channels with categories, claims, and transcripts",
    )
    category_group = app_commands.Group(
        name="category",
        description="Manage ticket categories (admin only)",
        parent=ticket_group,
        default_permissions=discord.Permissions(administrator=True),
    )
    panel_group = app_commands.Group(
        name="panel",
        description="Manage the public ticket panel (admin only)",
        parent=ticket_group,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.auto_close_loop.start()

    def cog_unload(self):
        self.auto_close_loop.cancel()

    async def cog_load(self):
        self.bot.add_view(TicketControlView(self))
        configs = await db.ticket_config_col.find({"panel_message_id": {"$ne": None}}).to_list(length=None)
        for cfg in configs:
            if cfg.get("categories"):
                self.bot.add_view(TicketPanelView(self, cfg))

    @staticmethod
    def _require_admin(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        return interaction.user.guild_permissions.administrator

    async def _log_action(
        self,
        guild: discord.Guild,
        config: dict,
        *,
        title: str,
        description: str,
        user: discord.abc.User,
        color: int = 0x5865F2,
        fields: list[tuple[str, str, bool]] | None = None,
    ):
        logs_id = config.get("logs_channel_id")
        if not logs_id:
            return
        channel = guild.get_channel(int(logs_id))
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(title=title, description=description, color=color, timestamp=_utc_now())
        embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=True)
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name, value=value[:1024], inline=inline)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def _apply_open_permissions(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        owner: discord.Member,
        category: dict,
        *,
        claimed_by: int | None = None,
        locked: bool = False,
    ):
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            owner: discord.PermissionOverwrite(view_channel=True, send_messages=not locked, attach_files=True, embed_links=True),
        }
        staff_ids = _staff_role_ids(category)
        for role_id in staff_ids:
            role = guild.get_role(role_id)
            if not role:
                continue
            staff_send = not locked and not claimed_by
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=staff_send,
                attach_files=staff_send,
                embed_links=staff_send,
            )
        if claimed_by:
            member = guild.get_member(claimed_by)
            if member:
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=not locked,
                    attach_files=not locked,
                    embed_links=not locked,
                )
        await channel.edit(overwrites=overwrites)

    async def create_ticket_from_form(
        self,
        interaction: discord.Interaction,
        category: dict,
        answers: dict[str, str],
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        config = await db.get_ticket_config(interaction.guild.id)

        cat_channel_id = config.get("ticket_category_id")
        if not cat_channel_id:
            await interaction.followup.send(
                "❌ Ticket category channel not set. Admin: `/ticket config set ticket_category #category`",
                ephemeral=True,
            )
            return

        discord_category = interaction.guild.get_channel(int(cat_channel_id))
        if not isinstance(discord_category, discord.CategoryChannel):
            await interaction.followup.send("❌ Configured ticket category is invalid.", ephemeral=True)
            return

        ticket_id = await db.next_ticket_id(interaction.guild.id)
        channel_name = _safe_channel_name(interaction.user.name, ticket_id, category.get("key", "general"))

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        for role_id in _staff_role_ids(category):
            role = interaction.guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)

        try:
            channel = await interaction.guild.create_text_channel(
                name=channel_name,
                category=discord_category,
                overwrites=overwrites,
                topic=f"Ticket #{ticket_id} • {category.get('name')} • Owner: {interaction.user.id}",
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ Missing permissions to create ticket channels.", ephemeral=True)
            return

        now = _utc_now()
        ticket_doc = {
            "ticket_id": ticket_id,
            "guild_id": interaction.guild.id,
            "channel_id": channel.id,
            "owner_id": interaction.user.id,
            "claimed_by": None,
            "category_key": category.get("key"),
            "category_name": category.get("name"),
            "status": "open",
            "locked": False,
            "priority": "normal",
            "tags": [],
            "staff_notes": [],
            "form_answers": answers,
            "transcript_paths": {},
            "log_message_id": None,
            "control_message_id": None,
            "created_at": now,
            "closed_at": None,
            "last_activity_at": now,
            "rating": None,
        }
        await db.create_ticket_record(ticket_doc)

        answer_lines = "\n".join(f"**{k}:** {v}" for k, v in answers.items()) or "_No form questions._"
        welcome = discord.Embed(
            title=f"🎫 Ticket #{ticket_id} — {category.get('name')}",
            description=(
                f"Hello {interaction.user.mention}! Staff will assist you shortly.\n\n"
                f"**Your responses:**\n{answer_lines}"
            ),
            color=0x57F287,
            timestamp=now,
        )
        welcome.add_field(name="Priority", value="`normal`", inline=True)
        welcome.add_field(name="Status", value="`open`", inline=True)
        welcome.set_footer(text="Use buttons below or /ticket commands • an app by deep")

        control_msg = await channel.send(embed=welcome, view=TicketControlView(self))
        await db.update_ticket(interaction.guild.id, ticket_id, {"control_message_id": control_msg.id})

        await self._log_action(
            interaction.guild,
            config,
            title="🎫 Ticket Opened",
            description=f"Ticket #{ticket_id} opened in {channel.mention}",
            user=interaction.user,
            fields=[
                ("Category", category.get("name", "-"), True),
                ("Channel", channel.mention, True),
            ],
        )
        await send_activity_log(
            self.bot,
            activity_type="Ticket Opened",
            details=f"Ticket #{ticket_id} created by {interaction.user}.",
            module="Tickets",
            guild=interaction.guild,
            user=interaction.user,
            jump_url=channel.jump_url,
        )
        await interaction.followup.send(f"✅ Ticket created: {channel.mention}", ephemeral=True)

    # ── Category commands ──────────────────────────────────────────────

    @category_group.command(name="add", description="Add a ticket category")
    @app_commands.describe(
        key="Unique slug (e.g. support)",
        name="Display name",
        description="Category description shown on panel",
        emoji="Optional emoji/icon",
        staff_roles="Staff roles that can handle this category",
    )
    async def category_add(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
        emoji: str | None = None,
        staff_roles: str | None = None,
    ):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        slug = _slug(key)
        config = await db.get_ticket_config(interaction.guild_id)
        if _category_by_key(config, slug):
            await interaction.response.send_message(f"❌ Category `{slug}` already exists.", ephemeral=True)
            return

        role_ids: list[int] = []
        if staff_roles:
            role_ids = [int(m.group(1)) for m in re.finditer(r"(\d{17,20})", staff_roles)]

        category = {
            "key": slug,
            "name": name[:100],
            "description": description[:500],
            "emoji": emoji,
            "staff_role_ids": role_ids,
            "questions": [],
        }
        categories = list(config.get("categories") or [])
        categories.append(category)
        await db.update_ticket_config(interaction.guild_id, {"categories": categories})
        await interaction.response.send_message(
            f"✅ Category **`{slug}`** added.\n"
            f"Add questions with `/ticket category edit` → use `question_json` parameter.",
            ephemeral=True,
        )

    @category_group.command(name="edit", description="Edit a ticket category")
    @app_commands.describe(
        key="Category slug to edit",
        name="New display name",
        description="New description",
        emoji="New emoji",
        staff_roles="New staff role mentions/IDs (comma-separated)",
        question_json='Optional JSON array of up to 7 questions: [{"title":"Issue","description":"...","placeholder":"...","required":true,"style":"short"}]',
    )
    async def category_edit(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str | None = None,
        description: str | None = None,
        emoji: str | None = None,
        staff_roles: str | None = None,
        question_json: str | None = None,
    ):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        config = await db.get_ticket_config(interaction.guild_id)
        categories = list(config.get("categories") or [])
        target = None
        for cat in categories:
            if cat.get("key") == key:
                target = cat
                break
        if not target:
            await interaction.response.send_message(f"❌ Category `{key}` not found.", ephemeral=True)
            return

        if name:
            target["name"] = name[:100]
        if description is not None:
            target["description"] = description[:500]
        if emoji is not None:
            target["emoji"] = emoji or None
        if staff_roles is not None:
            target["staff_role_ids"] = [int(m.group(1)) for m in re.finditer(r"(\d{17,20})", staff_roles)]
        if question_json:
            try:
                parsed = json.loads(question_json)
                if not isinstance(parsed, list):
                    raise ValueError("Must be a JSON array")
                target["questions"] = [
                    {
                        "title": str(q.get("title", f"Q{i+1}"))[:45],
                        "description": str(q.get("description", ""))[:200],
                        "placeholder": str(q.get("placeholder", ""))[:100],
                        "required": bool(q.get("required", True)),
                        "style": "paragraph" if q.get("style") == "paragraph" else "short",
                    }
                    for i, q in enumerate(parsed[:MAX_QUESTIONS])
                ]
            except (json.JSONDecodeError, ValueError) as exc:
                await interaction.response.send_message(f"❌ Invalid question JSON: {exc}", ephemeral=True)
                return

        await db.update_ticket_config(interaction.guild_id, {"categories": categories})
        await interaction.response.send_message(f"✅ Category **`{key}`** updated.", ephemeral=True)

    @category_group.command(name="add-question", description="Add a single question to a category (admin)")
    @app_commands.describe(
        key="Category slug to edit",
        title="Question title",
        description="Optional description shown to the user",
        placeholder="Optional placeholder text",
        required="Whether the question is required (default: true)",
        style="Input style: short or paragraph",
    )
    async def category_add_question(
        self,
        interaction: discord.Interaction,
        key: str,
        title: str,
        description: str | None = None,
        placeholder: str | None = None,
        required: bool = True,
        style: str | None = None,
    ):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        config = await db.get_ticket_config(interaction.guild_id)
        categories = list(config.get("categories") or [])
        target = None
        for cat in categories:
            if cat.get("key") == key:
                target = cat
                break
        if not target:
            await interaction.response.send_message(f"❌ Category `{key}` not found.", ephemeral=True)
            return
        all_qs = list(target.get("questions") or [])
        if len(all_qs) >= MAX_QUESTIONS:
            await interaction.response.send_message(f"❌ Category already has maximum of {MAX_QUESTIONS} questions.", ephemeral=True)
            return
        q = {
            "title": str(title)[:45],
            "description": (str(description) if description else "")[:200],
            "placeholder": (str(placeholder) if placeholder else "")[:100],
            "required": bool(required),
            "style": "paragraph" if (style and style.lower() == "paragraph") else "short",
        }
        all_qs.append(q)
        target["questions"] = all_qs
        await db.update_ticket_config(interaction.guild_id, {"categories": categories})
        await interaction.response.send_message(f"✅ Question added to category **`{key}`** ({len(all_qs)}/{MAX_QUESTIONS}).\nYou can still use `question_json` when editing multiple at once.", ephemeral=True)

    @category_group.command(name="delete", description="Delete a ticket category")
    async def category_delete(self, interaction: discord.Interaction, key: str):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        config = await db.get_ticket_config(interaction.guild_id)
        categories = [c for c in (config.get("categories") or []) if c.get("key") != key]
        if len(categories) == len(config.get("categories") or []):
            await interaction.response.send_message(f"❌ Category `{key}` not found.", ephemeral=True)
            return
        await db.update_ticket_config(interaction.guild_id, {"categories": categories})
        await interaction.response.send_message(f"✅ Category **`{key}`** deleted.", ephemeral=True)

    @category_group.command(name="list", description="List all ticket categories")
    async def category_list(self, interaction: discord.Interaction):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        config = await db.get_ticket_config(interaction.guild_id)
        categories = config.get("categories") or []
        if not categories:
            await interaction.response.send_message("ℹ️ No categories configured.", ephemeral=True)
            return
        lines = []
        for cat in categories:
            lines.append(
                f"**{cat.get('emoji', '🎫')} {cat.get('name')}** (`{cat.get('key')}`)\n"
                f"> {cat.get('description') or 'No description'}\n"
                f"> Staff roles: `{len(cat.get('staff_role_ids') or [])}` • Questions: `{len(cat.get('questions') or [])}`"
            )
        embed = discord.Embed(title="Ticket Categories", description="\n\n".join(lines), color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Panel commands ─────────────────────────────────────────────────

    @panel_group.command(name="create", description="Create the public ticket panel")
    @app_commands.describe(
        channel="Channel to post the panel in",
        title="Panel embed title",
        description="Panel embed description",
    )
    async def panel_create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str | None = None,
        description: str | None = None,
    ):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        config = await db.get_ticket_config(interaction.guild_id)
        if title:
            config["panel_title"] = title
        if description:
            config["panel_description"] = description
        if not config.get("categories"):
            await interaction.response.send_message(
                "❌ Add at least one category first: `/ticket category add`", ephemeral=True
            )
            return

        embed = _build_panel_embed(config)
        view = TicketPanelView(self, config)
        msg = await channel.send(embed=embed, view=view)
        self.bot.add_view(view)

        await db.update_ticket_config(
            interaction.guild_id,
            {
                "panel_title": config.get("panel_title"),
                "panel_description": config.get("panel_description"),
                "panel_channel_id": channel.id,
                "panel_message_id": msg.id,
            },
        )
        await interaction.response.send_message(f"✅ Ticket panel posted in {channel.mention}.", ephemeral=True)

    @panel_group.command(name="update", description="Refresh the existing ticket panel embed")
    async def panel_update(self, interaction: discord.Interaction):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        config = await db.get_ticket_config(interaction.guild_id)
        ch_id = config.get("panel_channel_id")
        msg_id = config.get("panel_message_id")
        if not ch_id or not msg_id:
            await interaction.response.send_message("❌ No panel found. Use `/ticket panel create` first.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(int(ch_id))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Panel channel missing.", ephemeral=True)
            return
        try:
            msg = await channel.fetch_message(int(msg_id))
        except discord.NotFound:
            await interaction.response.send_message("❌ Panel message not found.", ephemeral=True)
            return

        embed = _build_panel_embed(config)
        view = TicketPanelView(self, config)
        await msg.edit(embed=embed, view=view)
        self.bot.add_view(view)
        await interaction.response.send_message("✅ Panel updated.", ephemeral=True)

    # ── Config command ─────────────────────────────────────────────────

    @ticket_group.command(name="config", description="Configure ticket system settings (admin only)")
    @app_commands.describe(
        ticket_category="Discord category where ticket channels are created",
        logs_channel="Channel for ticket action logs",
        auto_close_hours="Auto-close open tickets after N hours of inactivity (0 = disabled)",
        panel_title="Panel embed title",
        panel_description="Panel embed description",
    )
    @app_commands.default_permissions(administrator=True)
    async def config_set(
        self,
        interaction: discord.Interaction,
        ticket_category: discord.CategoryChannel | None = None,
        logs_channel: discord.TextChannel | None = None,
        auto_close_hours: app_commands.Range[int, 0, 720] | None = None,
        panel_title: str | None = None,
        panel_description: str | None = None,
    ):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        payload: dict[str, Any] = {}
        if ticket_category:
            payload["ticket_category_id"] = ticket_category.id
        if logs_channel:
            payload["logs_channel_id"] = logs_channel.id
        if auto_close_hours is not None:
            payload["auto_close_hours"] = int(auto_close_hours)
        if panel_title:
            payload["panel_title"] = panel_title[:256]
        if panel_description:
            payload["panel_description"] = panel_description[:2000]

        if not payload:
            config = await db.get_ticket_config(interaction.guild_id)
            embed = discord.Embed(title="Ticket Config", color=0x5865F2)
            embed.add_field(name="Ticket category", value=str(config.get("ticket_category_id") or "Not set"), inline=False)
            embed.add_field(name="Logs channel", value=str(config.get("logs_channel_id") or "Not set"), inline=False)
            embed.add_field(name="Auto-close hours", value=str(config.get("auto_close_hours", 72)), inline=True)
            embed.add_field(name="Categories", value=str(len(config.get("categories") or [])), inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await db.update_ticket_config(interaction.guild_id, payload)
        await interaction.response.send_message("✅ Ticket configuration updated.", ephemeral=True)

    # ── Ticket action handlers ─────────────────────────────────────────

    async def handle_claim(self, interaction: discord.Interaction):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel or not interaction.guild:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config, owner_ok=False):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        if ticket.get("claimed_by"):
            await interaction.response.send_message("❌ Already claimed.", ephemeral=True)
            return

        category = _category_by_key(config, ticket.get("category_key", "")) or {}
        owner = interaction.guild.get_member(ticket.get("owner_id"))
        await self._apply_open_permissions(
            channel,
            interaction.guild,
            owner or interaction.guild.me,
            category,
            claimed_by=member.id,
            locked=ticket.get("locked", False),
        )
        await db.update_ticket(interaction.guild.id, ticket["ticket_id"], {"claimed_by": member.id})
        await interaction.response.send_message(f"✅ {member.mention} claimed this ticket.", ephemeral=False)
        await self._log_action(
            interaction.guild, config, title="✋ Ticket Claimed",
            description=f"Ticket #{ticket['ticket_id']} claimed by {member.mention}",
            user=member, fields=[("Channel", channel.mention, True)],
        )

    async def handle_unclaim(self, interaction: discord.Interaction):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel or not interaction.guild:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member:
            return
        claimed = ticket.get("claimed_by")
        if not claimed:
            await interaction.response.send_message("❌ Ticket is not claimed.", ephemeral=True)
            return
        if not (_is_admin(member) or member.id == claimed):
            await interaction.response.send_message("❌ Only the claimant or an admin can unclaim.", ephemeral=True)
            return

        category = _category_by_key(config, ticket.get("category_key", "")) or {}
        owner = interaction.guild.get_member(ticket.get("owner_id"))
        await self._apply_open_permissions(
            channel,
            interaction.guild,
            owner or interaction.guild.me,
            category,
            claimed_by=None,
            locked=ticket.get("locked", False),
        )
        await db.update_ticket(interaction.guild.id, ticket["ticket_id"], {"claimed_by": None})
        await interaction.response.send_message("✅ Ticket unclaimed. All staff can respond again.", ephemeral=False)
        await self._log_action(
            interaction.guild, config, title="↩ Ticket Unclaimed",
            description=f"Ticket #{ticket['ticket_id']} unclaimed",
            user=member,
        )

    async def handle_close(self, interaction: discord.Interaction, *, via_button: bool = False):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel or not interaction.guild:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return

        if via_button:
            await interaction.response.defer(ephemeral=True)
        else:
            await interaction.response.defer()

        paths = await TicketTranscriptGenerator.generate(interaction.guild, channel, ticket)
        now = _utc_now()
        await db.update_ticket(
            interaction.guild.id,
            ticket["ticket_id"],
            {"status": "closed", "closed_at": now, "transcript_paths": paths},
        )

        owner = interaction.guild.get_member(ticket.get("owner_id"))
        for target, send in ((owner, True), (member, False)):
            if target and send:
                try:
                    files = []
                    for fmt in ("html", "json", "txt"):
                        p = paths.get(fmt)
                        if p and os.path.isfile(p):
                            files.append(discord.File(p, filename=os.path.basename(p)))
                    await target.send(
                        f"Your ticket **#{ticket['ticket_id']}** was closed.",
                        files=files[:3] if files else None,
                        view=RatingView(self, interaction.guild.id, ticket["ticket_id"]),
                    )
                except discord.HTTPException:
                    pass

        await channel.set_permissions(interaction.guild.default_role, view_channel=False)
        if owner:
            await channel.set_permissions(owner, send_messages=False)
        await channel.send(
            embed=discord.Embed(
                title="🔒 Ticket Closed",
                description=f"Closed by {member.mention}. Transcript saved.\nUse `/ticket reopen` or `/ticket delete`.",
                color=0xED4245,
                timestamp=now,
            )
        )
        await self._log_action(
            interaction.guild, config, title="🔒 Ticket Closed",
            description=f"Ticket #{ticket['ticket_id']} closed",
            user=member, color=0xED4245,
            fields=[("Transcript", ", ".join(paths.values()), False)],
        )
        if via_button:
            await interaction.followup.send("✅ Ticket closed.", ephemeral=True)
        else:
            await interaction.followup.send("✅ Ticket closed and transcript saved.")

    async def handle_lock(self, interaction: discord.Interaction, *, locked: bool):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel or not interaction.guild:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config, owner_ok=False):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        category = _category_by_key(config, ticket.get("category_key", "")) or {}
        owner = interaction.guild.get_member(ticket.get("owner_id"))
        await self._apply_open_permissions(
            channel,
            interaction.guild,
            owner or interaction.guild.me,
            category,
            claimed_by=ticket.get("claimed_by"),
            locked=locked,
        )
        await db.update_ticket(interaction.guild.id, ticket["ticket_id"], {"locked": locked})
        state = "locked" if locked else "unlocked"
        await interaction.response.send_message(f"✅ Ticket {state}.", ephemeral=True)

    async def handle_transcript(self, interaction: discord.Interaction):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel or not interaction.guild:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        paths = ticket.get("transcript_paths") or {}
        if not paths or not all(os.path.isfile(p) for p in paths.values() if p):
            paths = await TicketTranscriptGenerator.generate(interaction.guild, channel, ticket)
            await db.update_ticket(interaction.guild.id, ticket["ticket_id"], {"transcript_paths": paths})

        files = [
            discord.File(paths[k], filename=os.path.basename(paths[k]))
            for k in ("html", "json", "txt")
            if paths.get(k) and os.path.isfile(paths[k])
        ]
        await interaction.followup.send("📄 Transcript files:", files=files, ephemeral=True)

    # ── Slash ticket commands ──────────────────────────────────────────

    @ticket_group.command(name="close", description="Close the current ticket")
    async def ticket_close(self, interaction: discord.Interaction):
        await self.handle_close(interaction)

    @ticket_group.command(name="delete", description="Delete the current ticket channel")
    async def ticket_delete(self, interaction: discord.Interaction):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel or not interaction.guild:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not (_is_admin(member) or _can_manage_ticket(member, ticket, config, owner_ok=False)):
            await interaction.response.send_message("❌ Staff/admin only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if ticket.get("status") != "closed":
            paths = await TicketTranscriptGenerator.generate(interaction.guild, channel, ticket)
            await db.update_ticket(
                interaction.guild.id,
                ticket["ticket_id"],
                {"status": "deleted", "closed_at": _utc_now(), "transcript_paths": paths},
            )
        else:
            await db.update_ticket(interaction.guild.id, ticket["ticket_id"], {"status": "deleted"})

        await self._log_action(
            interaction.guild, config, title="🗑 Ticket Deleted",
            description=f"Ticket #{ticket['ticket_id']} deleted",
            user=member, color=0xED4245,
        )
        await channel.delete(reason=f"Ticket deleted by {member}")
        await interaction.followup.send("✅ Ticket deleted.", ephemeral=True)

    @ticket_group.command(name="claim", description="Claim the current ticket")
    async def ticket_claim(self, interaction: discord.Interaction):
        await self.handle_claim(interaction)

    @ticket_group.command(name="unclaim", description="Unclaim the current ticket")
    async def ticket_unclaim(self, interaction: discord.Interaction):
        await self.handle_unclaim(interaction)

    @ticket_group.command(name="adduser", description="Add a user to the current ticket")
    @app_commands.describe(member="User to add")
    async def ticket_adduser(self, interaction: discord.Interaction, member: discord.Member):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        actor = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not actor or not _can_manage_ticket(actor, ticket, config):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        await channel.set_permissions(member, view_channel=True, send_messages=True, attach_files=True)
        await interaction.response.send_message(f"✅ Added {member.mention} to this ticket.", ephemeral=True)

    @ticket_group.command(name="removeuser", description="Remove a user from the current ticket")
    async def ticket_removeuser(self, interaction: discord.Interaction, member: discord.Member):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        actor = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not actor or not _can_manage_ticket(actor, ticket, config, owner_ok=False):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        if member.id == ticket.get("owner_id"):
            await interaction.response.send_message("❌ Cannot remove the ticket owner.", ephemeral=True)
            return
        await channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(f"✅ Removed {member.mention}.", ephemeral=True)

    @ticket_group.command(name="transcript", description="Export transcript for the current ticket")
    async def ticket_transcript(self, interaction: discord.Interaction):
        await self.handle_transcript(interaction)

    @ticket_group.command(name="reopen", description="Reopen a closed ticket")
    async def ticket_reopen(self, interaction: discord.Interaction):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel or not interaction.guild:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config, owner_ok=False):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        if ticket.get("status") != "closed":
            await interaction.response.send_message("❌ Ticket is not closed.", ephemeral=True)
            return

        category = _category_by_key(config, ticket.get("category_key", "")) or {}
        owner = interaction.guild.get_member(ticket.get("owner_id"))
        await self._apply_open_permissions(
            channel, interaction.guild, owner or interaction.guild.me, category,
            claimed_by=ticket.get("claimed_by"), locked=False,
        )
        await db.update_ticket(
            interaction.guild.id, ticket["ticket_id"],
            {"status": "open", "closed_at": None, "locked": False, "last_activity_at": _utc_now()},
        )
        await interaction.response.send_message("✅ Ticket reopened.", ephemeral=True)

    @ticket_group.command(name="lock", description="Lock the ticket (prevent sending)")
    async def ticket_lock(self, interaction: discord.Interaction):
        await self.handle_lock(interaction, locked=True)

    @ticket_group.command(name="unlock", description="Unlock the ticket")
    async def ticket_unlock(self, interaction: discord.Interaction):
        await self.handle_lock(interaction, locked=False)

    @ticket_group.command(name="rename", description="Rename the ticket channel")
    @app_commands.describe(name="New channel name (without #)")
    async def ticket_rename(self, interaction: discord.Interaction, name: str):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        safe = _slug(name, 90)
        await channel.edit(name=safe)
        await interaction.response.send_message(f"✅ Renamed to `{safe}`.", ephemeral=True)

    @ticket_group.command(name="priority", description="Set ticket priority")
    @app_commands.describe(level="Priority level")
    @app_commands.choices(level=[app_commands.Choice(name=p, value=p) for p in PRIORITIES])
    async def ticket_priority(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config, owner_ok=False):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await db.update_ticket(interaction.guild_id, ticket["ticket_id"], {"priority": level.value})
        await interaction.response.send_message(f"✅ Priority set to `{level.value}`.", ephemeral=True)

    @ticket_group.command(name="tag", description="Add or remove a ticket tag")
    @app_commands.describe(tag="Tag text", action="Add or remove")
    @app_commands.choices(action=[
        app_commands.Choice(name="add", value="add"),
        app_commands.Choice(name="remove", value="remove"),
    ])
    async def ticket_tag(
        self,
        interaction: discord.Interaction,
        tag: str,
        action: app_commands.Choice[str],
    ):
        ticket, config, _ = await _get_ticket_context(interaction)
        if not ticket:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config, owner_ok=False):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        tags = list(ticket.get("tags") or [])
        clean = tag.strip().lower()[:32]
        if action.value == "add" and clean not in tags:
            tags.append(clean)
        elif action.value == "remove" and clean in tags:
            tags.remove(clean)
        await db.update_ticket(interaction.guild_id, ticket["ticket_id"], {"tags": tags})
        await interaction.response.send_message(f"✅ Tags: `{', '.join(tags) or 'none'}`", ephemeral=True)

    @ticket_group.command(name="note", description="Add an internal staff note to the ticket")
    @app_commands.describe(note="Internal note (staff only)")
    async def ticket_note(self, interaction: discord.Interaction, note: str):
        ticket, config, channel = await _get_ticket_context(interaction)
        if not ticket or not channel:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not _can_manage_ticket(member, ticket, config, owner_ok=False):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        notes = list(ticket.get("staff_notes") or [])
        notes.append({"author_id": member.id, "note": note[:1000], "at": _utc_now().isoformat()})
        await db.update_ticket(interaction.guild_id, ticket["ticket_id"], {"staff_notes": notes[-50:]})
        await interaction.response.send_message("✅ Staff note saved.", ephemeral=True)

    @ticket_group.command(name="setup", description="Show recommended server setup steps for this ticket system (admin only)")
    async def ticket_setup(self, interaction: discord.Interaction):
        if not self._require_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        steps = [
            "1. Create a dedicated 'Support' category and staff roles (IDs will be used when configuring categories).",
            "2. Ensure the bot has Manage Channels, Send Messages, Manage Permissions, and Read Message History in ticket channels.",
            "3. Create a channel for the public ticket panel where users open tickets (bot posts a panel message).",
            "4. Configure categories with `/ticket category add` and use `/ticket category edit` to add questions (or `/ticket category add-question` to add one).",
            "5. Optional: Enable auto-close in the config and ensure the bot can send DMs if you want owner notifications.",
            "6. Transcripts are stored under data/transcripts/{guild_id}/ locally. Make sure the hosting environment preserves that directory or configure backup/export.",
            "7. If using a database (MongoDB), ensure the connection string is set in your environment and the bot can reach it.",
            "8. For panels and permissions: add staff role IDs to categories so staff can manage tickets and claim them.",
            "9. To add multiple questions at once, pass a JSON array to `question_json` in `/ticket category edit` (existing behavior preserved).",
            "10. Use `/ticket panel create` (if available) to post the ticket panel in the chosen channel.",
        ]
        # Send as ephemeral paginated message (simple single message here)
        await interaction.response.send_message("\n".join(steps), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        ticket = await db.get_ticket_by_channel(message.channel.id)
        if not ticket or ticket.get("status") != "open":
            return
        await db.update_ticket_by_channel(message.channel.id, {"last_activity_at": _utc_now()})

    @tasks.loop(minutes=15)
    async def auto_close_loop(self):
        await self.bot.wait_until_ready()
        now = _utc_now()
        async for config in db.ticket_config_col.find({"auto_close_hours": {"$gt": 0}}):
            hours = int(config.get("auto_close_hours") or 0)
            if hours <= 0:
                continue
            guild = self.bot.get_guild(config["guild_id"])
            if not guild:
                continue
            cutoff = now - timedelta(hours=hours)
            cursor = db.tickets_col.find({
                "guild_id": config["guild_id"],
                "status": "open",
                "last_activity_at": {"$lt": cutoff},
            })
            async for ticket in cursor:
                channel = guild.get_channel(ticket.get("channel_id"))
                if not isinstance(channel, discord.TextChannel):
                    continue
                try:
                    paths = await TicketTranscriptGenerator.generate(guild, channel, ticket)
                    await db.update_ticket(
                        guild.id, ticket["ticket_id"],
                        {"status": "closed", "closed_at": now, "transcript_paths": paths},
                    )
                    await channel.send(
                        embed=discord.Embed(
                            title="⏰ Auto-closed",
                            description=f"No activity for {hours}h. Ticket closed automatically.",
                            color=0xFEE75C,
                        )
                    )
                    owner = guild.get_member(ticket.get("owner_id"))
                    if owner:
                        try:
                            await owner.send(
                                f"Your ticket **#{ticket['ticket_id']}** was auto-closed due to inactivity.",
                                view=RatingView(self, guild.id, ticket["ticket_id"]),
                            )
                        except discord.HTTPException:
                            pass
                    await self._log_action(
                        guild, config, title="⏰ Ticket Auto-Closed",
                        description=f"Ticket #{ticket['ticket_id']} auto-closed",
                        user=guild.me,
                        color=0xFEE75C,
                    )
                except Exception as exc:
                    await log_exception(self.bot, title="Ticket Auto-Close Error", error=exc)

    @auto_close_loop.before_loop
    async def before_auto_close(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    cog = TicketCommands(bot)
    await bot.add_cog(cog)
 #  bot.tree.add_command(TicketCommands.ticket_group)
