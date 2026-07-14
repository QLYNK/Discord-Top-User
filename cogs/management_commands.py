import asyncio
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands

# Database import
from database import client as mongo_client
from telemetry import log_exception, send_activity_log
from utils.branding_view import create_branding_view

# Naya collection staff roles ke liye
staff_roles_col = mongo_client["LeaderboardBotDB"]["StaffRoles"]

class StaffDropdownSelect(discord.ui.Select):
    def __init__(self, placeholder: str, options: list[discord.SelectOption], staff_data_map: dict):
        super().__init__(placeholder=placeholder, options=options)
        self.staff_data_map = staff_data_map

    async def callback(self, interaction: discord.Interaction):
        user_id = int(self.values[0])
        member = interaction.guild.get_member(user_id)
        
        if not member:
            await interaction.response.send_message("❌ This staff member is no longer in the server.", ephemeral=True)
            return
            
        role_doc = self.staff_data_map.get(str(user_id))
        if not role_doc:
            await interaction.response.send_message("❌ Data for this staff member could not be loaded.", ephemeral=True)
            return

        # Building the Private Ephemeral Profile Embed
        embed = discord.Embed(
            title=f"🛡️ Staff Profile: {member.display_name}", 
            color=member.color if member.color.value else 0x2b2d31,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if member.banner:
            embed.set_image(url=member.banner.url)

        # Basic Info
        join_ts = int(member.joined_at.timestamp()) if member.joined_at else 0
        embed.add_field(name="👤 User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="📅 Joined Server", value=f"<t:{join_ts}:R>", inline=True)

        # Role Details
        embed.add_field(
            name=f"🔰 Role: {role_doc['role_name']}", 
            value=role_doc['description'] or "No specific role description provided.", 
            inline=False
        )

        # Parsing Powers
        powers = role_doc.get("powers", {})
        power_lines = []
        for power_name, has_power in powers.items():
            icon = "✅" if has_power else "❌"
            power_lines.append(f"{icon} {power_name}")
        
        if power_lines:
            embed.add_field(name="⚙️ Authorities & Permissions", value="\n".join(power_lines), inline=False)

        embed.set_footer(text="Confidential Staff Lookup • an app by deep")
        
        # Sending the response Ephemerally (Only visible to the user who clicked)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class StaffDropdownView(discord.ui.View):
    def __init__(self, options_chunks: list, staff_data_map: dict):
        super().__init__(timeout=None)
        # Discord allows max 5 selects per view, each select max 25 options.
        for i, chunk in enumerate(options_chunks[:5]):
            placeholder = f"Select a staff member to view profile (Part {i+1})" if len(options_chunks) > 1 else "Select a staff member to view their private profile..."
            self.add_item(StaffDropdownSelect(placeholder, chunk, staff_data_map))


class ManagementCommands(commands.Cog):
    """Cog for Server-Specific Bot Profile Management, Advanced User Info, and Staff Roster System."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    management_group = app_commands.Group(
        name="management", 
        description="Core bot management and professional staff roster tools",
        default_permissions=discord.Permissions(administrator=True)
    )

    # ==========================================
    # 1. SERVER-SPECIFIC BOT PROFILE COMMANDS
    # ==========================================
    
    @management_group.command(name="bot_local_edit", description="Change the bot's Nickname specifically for this server")
    @app_commands.describe(nickname="The new nickname for the bot in this server")
    async def bot_local_edit(self, interaction: discord.Interaction, nickname: str):
        """Changes the bot's nickname for the current guild only."""
        await interaction.response.defer(thinking=True)
        try:
            await interaction.guild.me.edit(nick=nickname)
            await interaction.followup.send(f"✅ Bot's nickname successfully updated to **{nickname}** for this server only.\n*(Note: Discord API restricts bots from having per-server custom Avatars or Banners)*", view=create_branding_view())
            await send_activity_log(
                self.bot,
                activity_type="Local Bot Profile Edited",
                details=f"Bot nickname changed to {nickname} in {interaction.guild.name}.",
                module="Management",
                guild=interaction.guild,
                user=interaction.user
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to change my own nickname here.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {e}", ephemeral=True)

    @management_group.command(name="bot_local_reset", description="Reset the bot's Nickname back to default in this server")
    async def bot_local_reset(self, interaction: discord.Interaction):
        """Resets the bot's nickname in the current guild."""
        await interaction.response.defer(thinking=True)
        try:
            await interaction.guild.me.edit(nick=None)
            await interaction.followup.send("✅ Bot's nickname has been reset to default.", view=create_branding_view())
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to change my nickname here.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {e}", ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Auto-clears staff database entries when the bot leaves or is kicked from a server."""
        try:
            await staff_roles_col.delete_many({"guild_id": guild.id})
            print(f"[MANAGEMENT] Cleared staff role database for left guild: {guild.name} ({guild.id})")
        except Exception as e:
            print(f"[MANAGEMENT] Failed to clear DB for left guild {guild.id}: {e}")

    # ==========================================
    # 2. ADVANCED USER INFO COMMAND
    # ==========================================

    @app_commands.command(name="user_info", description="Get extremely detailed information about any user")
    async def user_info(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(thinking=True)
        
        # Calculating dates
        created_at = int(member.created_at.timestamp())
        joined_at = int(member.joined_at.timestamp()) if member.joined_at else 0
        premium_since = int(member.premium_since.timestamp()) if member.premium_since else None
        
        # Fixing the public_flags issue (iterating properly over boolean flags)
        badges = [name.replace("_", " ").title() for name, value in member.public_flags if value]
        badge_str = ", ".join(badges) if badges else "No public badges"
        
        # Getting roles safely
        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        roles_str = " ".join(roles[:15]) + ("..." if len(roles) > 15 else "") if roles else "None"
        
        embed = discord.Embed(title=f"👤 User Info: {member.name}", color=member.color if member.color.value else 0x5865F2)
        embed.set_thumbnail(url=member.display_avatar.url)
        if member.banner:
            embed.set_image(url=member.banner.url)
            
        embed.add_field(name="Username", value=f"`{member.name}`", inline=True)
        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Nickname", value=f"`{member.nick}`" if member.nick else "`None`", inline=True)
        
        embed.add_field(name="Discord Joined", value=f"<t:{created_at}:D> (<t:{created_at}:R>)", inline=True)
        embed.add_field(name="Server Joined", value=f"<t:{joined_at}:D> (<t:{joined_at}:R>)", inline=True)
        
        if premium_since:
            embed.add_field(name="Boosting Since", value=f"<t:{premium_since}:D> 🚀", inline=True)
        
        embed.add_field(name="Badges", value=badge_str, inline=False)
        embed.add_field(name=f"Roles [{len(member.roles)-1}]", value=roles_str, inline=False)
        
        # Nitro hint
        nitro_status = "Yes (Animated Avatar/Boost)" if member.display_avatar.is_animated() or premium_since else "Likely No (Or Standard/Basic)"
        embed.add_field(name="Nitro Status (Guessed)", value=nitro_status, inline=True)
        embed.add_field(name="Bot?", value="Yes 🤖" if member.bot else "No 👤", inline=True)
        
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=create_branding_view())

    # ==========================================
    # 3. STAFF ROSTER SYSTEM
    # ==========================================

    @management_group.command(name="add_staff_role", description="Register a staff role, set custom description and detailed powers")
    @app_commands.describe(
        role="The staff role you want to register",
        description="Role details (Use \\n for line breaks)",
        can_ban_kick="Can they Ban or Kick members?",
        can_timeout_mute="Can they Timeout or Mute members?",
        can_manage_messages="Can they delete messages and manage threads?",
        can_manage_server="Do they help in Server Creation/Settings?",
        can_manage_roles="Can they give or remove roles?",
        is_event_manager="Do they host events or stage channels?",
        is_support_team="Do they handle tickets and support?",
        can_mention_everyone="Can they ping @everyone or @here?"
    )
    async def add_staff_role(
        self, 
        interaction: discord.Interaction, 
        role: discord.Role, 
        description: str,
        can_ban_kick: bool = False,
        can_timeout_mute: bool = False,
        can_manage_messages: bool = False,
        can_manage_server: bool = False,
        can_manage_roles: bool = False,
        is_event_manager: bool = False,
        is_support_team: bool = False,
        can_mention_everyone: bool = False
    ):
        await interaction.response.defer(thinking=True)
        
        # Format description (Support for manual line breaks)
        formatted_desc = description.replace("\\n", "\n")
        
        payload = {
            "guild_id": interaction.guild.id,
            "role_id": role.id,
            "role_name": role.name,
            "description": formatted_desc,
            "powers": {
                "Server Management / Creation": can_manage_server,
                "Ban & Kick Authority": can_ban_kick,
                "Timeout & Mute Authority": can_timeout_mute,
                "Message Management (Delete/Threads)": can_manage_messages,
                "Role Management": can_manage_roles,
                "Mention @everyone / @here": can_mention_everyone,
                "Event Management": is_event_manager,
                "Support & Tickets": is_support_team
            },
            "updated_at": datetime.now(timezone.utc)
        }
        
        await staff_roles_col.update_one(
            {"guild_id": interaction.guild.id, "role_id": role.id}, 
            {"$set": payload}, 
            upsert=True
        )
        
        await interaction.followup.send(f"✅ Staff Role **{role.name}** is now registered! Use `/management send_staff_data` to publish the roster.", ephemeral=True)

    @management_group.command(name="remove_staff_role", description="Unregister a staff role from the roster")
    async def remove_staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(thinking=True)
        result = await staff_roles_col.delete_one({"guild_id": interaction.guild.id, "role_id": role.id})
        
        if result.deleted_count > 0:
            await interaction.followup.send(f"🗑️ Staff Role **{role.name}** removed from roster.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ This role was not registered in the roster.", ephemeral=True)

    @management_group.command(name="send_staff_data", description="Publish the complete formatted staff roster to a channel")
    @app_commands.describe(
        target_channel="The channel to post the roster in",
        custom_banner_link="Optional direct image link (GIF/PNG) for the roster header"
    )
    async def send_staff_data(self, interaction: discord.Interaction, target_channel: discord.TextChannel, custom_banner_link: str = None):
        await interaction.response.defer(thinking=True)
        
        docs = await staff_roles_col.find({"guild_id": interaction.guild.id}).to_list(length=None)
        if not docs:
            await interaction.followup.send("❌ No staff roles registered. Please use `/management add_staff_role` first.", ephemeral=True)
            return

        await interaction.followup.send(f"⚡ Processing and deploying Staff Roster to {target_channel.mention}...", ephemeral=True)

        # 1. SEND HEADER
        header_embed = discord.Embed(
            title=f"🛡️ Official Staff Profile | {interaction.guild.name}",
            description="Below is the official staff members of our server.\nSelect a member from the dropdown menu at the bottom to view their specific powers and detailed profile.",
            color=0x2b2d31
        )
        
        # Banner Logic: Use custom link -> fallback to server banner -> fallback to None
        if custom_banner_link:
            header_embed.set_image(url=custom_banner_link)
        elif interaction.guild.banner:
            header_embed.set_image(url=interaction.guild.banner.url)
            
        await target_channel.send(embed=header_embed)

        # Sort roles logically (Highest discord position first)
        def get_role_pos(doc):
            r = interaction.guild.get_role(doc["role_id"])
            return r.position if r else 0
            
        docs = sorted(docs, key=get_role_pos, reverse=True)

        # Variables for building the Dropdown menu later
        staff_options = []
        staff_data_map = {}
        added_user_ids = set()

        # 2. SEND ROLE AND MEMBER CHUNKS
        for doc in docs:
            role = interaction.guild.get_role(doc["role_id"])
            if not role:
                continue 
                
            # Build the embed for the Role category
            role_embed = discord.Embed(
                title=f"🔰 {role.name}", 
                description=doc["description"] or "Staff Team", 
                color=role.color if role.color.value else 0x2b2d31
            )
            
            members = role.members
            if not members:
                role_embed.add_field(name="Active Members", value="*No active members in this role.*", inline=False)
                await target_channel.send(embed=role_embed)
                continue
                
            # Build clean string list of members: 1. @User (ID)
            member_lines = []
            for idx, m in enumerate(members, start=1):
                member_lines.append(f"**{idx}.** {m.mention}  *(ID: `{m.id}`)*")
                
                # Register user for the Dropdown menu (Limit to 125 total options due to API limits)
                if m.id not in added_user_ids and len(staff_options) < 125:
                    added_user_ids.add(m.id)
                    staff_data_map[str(m.id)] = doc
                    staff_options.append(discord.SelectOption(
                        label=m.display_name[:25],
                        description=f"Rank: {role.name[:50]}",
                        value=str(m.id),
                        emoji="🛡️"
                    ))

            role_embed.add_field(name="Active Members", value="\n".join(member_lines)[:1024], inline=False)
            await target_channel.send(embed=role_embed)
            await asyncio.sleep(0.5) # Anti-rate limit

        # 3. SEND THE DROPDOWN AND FOOTER (With Branding)
        if staff_options:
            # Chunking options into lists of 25 (Discord's max per Select menu)
            options_chunks = [staff_options[i:i + 25] for i in range(0, len(staff_options), 25)]
            
            dropdown_view = StaffDropdownView(options_chunks, staff_data_map)
            
            # Combine the Dropdown View with the Branding View for the final message
            branding = create_branding_view()
            for child in branding.children:
                dropdown_view.add_item(child)

            footer_embed = discord.Embed(
                description="👇 **Click the dropdown below to view a specific staff member's private profile and exact permissions.**",
                color=0x2b2d31,
                timestamp=datetime.now(timezone.utc)
            )
            await target_channel.send(embed=footer_embed, view=dropdown_view)
        else:
            # If no staff options, just send branding
            await target_channel.send(view=create_branding_view())

    @management_group.command(name="help", description="Show the complete guide for the Management & Staff Roster module")
    async def management_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙️ Management & Staff Roster Guide", 
            description="Welcome to the advanced Server & Bot Management module. Customize your bot locally, pull deep user stats, and create interactive staff panels.",
            color=0x5865F2
        )
        
        embed.add_field(
            name="🤖 1. Local Bot Setup", 
            value="`/management bot_local_edit` - Change the bot's Nickname for this server only.\n`/management bot_local_reset` - Revert the nickname back to default.", 
            inline=False
        )
        
        embed.add_field(
            name="👤 2. Advanced User Info", 
            value="`/user_info`\nGet a detailed profile lookup including Discord badges, Nitro status guesses, server join dates, and active roles.", 
            inline=False
        )
        
        embed.add_field(
            name="🛡️ 3. Adding Staff Roles", 
            value="`/management add_staff_role`\nRegister a role as a staff rank. Add a detailed description (use `\\n` for new lines) and toggle 8 specific powers (Ban, Mute, Server Management, etc.).", 
            inline=False
        )
        
        embed.add_field(
            name="🗑️ 4. Removing Staff Roles", 
            value="`/management remove_staff_role`\nUnlink a role from the official roster database if the rank is retired.", 
            inline=False
        )
        
        embed.add_field(
            name="📤 5. Publishing Roster", 
            value="`/management send_staff_data`\nGenerates a clean, professional hierarchy in a channel. Creates a dropdown menu at the bottom where users can click a staff name to view an ephemeral (private) profile of their exact powers.", 
            inline=False
        )
        
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=create_branding_view())

async def setup(bot: commands.Bot):
    await bot.add_cog(ManagementCommands(bot))
