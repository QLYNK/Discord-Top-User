import asyncio
import io
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

# Database import
from database import client as mongo_client
from telemetry import log_exception, send_activity_log
from utils.branding_view import create_branding_view

# Naya collection staff roles ke liye
staff_roles_col = mongo_client["LeaderboardBotDB"]["StaffRoles"]

class ManagementCommands(commands.Cog):
    """Cog for Bot Profile Management, Advanced User Info, and Staff Roster System."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    management_group = app_commands.Group(
        name="management", 
        description="Core bot management and staff roster tools",
        default_permissions=discord.Permissions(administrator=True)
    )

    @management_group.command(name="bot_edit", description="Edit Bot's Username, Avatar, and Banner")
    @app_commands.describe(
        name="New username for the bot",
        avatar_link="Direct image link for Avatar (PNG/JPG/GIF)",
        banner_link="Direct image link for Banner (PNG/JPG/GIF)"
    )
    async def bot_edit(self, interaction: discord.Interaction, name: str = None, avatar_link: str = None, banner_link: str = None):
        await interaction.response.defer(thinking=True)
        
        # Security check: Only bot owner can change bot profile
        if not await self.bot.is_owner(interaction.user):
            await interaction.followup.send("❌ Sirf Bot Owner hi bot ki profile edit kar sakta hai.", ephemeral=True)
            return

        avatar_bytes = None
        banner_bytes = None
        
        async with aiohttp.ClientSession() as session:
            if avatar_link:
                try:
                    async with session.get(avatar_link) as resp:
                        if resp.status == 200:
                            avatar_bytes = await resp.read()
                except Exception:
                    await interaction.followup.send("❌ Avatar link fetch karne mein problem aayi.", ephemeral=True)
                    return
            
            if banner_link:
                try:
                    async with session.get(banner_link) as resp:
                        if resp.status == 200:
                            banner_bytes = await resp.read()
                except Exception:
                    await interaction.followup.send("❌ Banner link fetch karne mein problem aayi.", ephemeral=True)
                    return

        try:
            # Preparing kwargs for edit
            edit_kwargs = {}
            if name: edit_kwargs["username"] = name
            if avatar_bytes: edit_kwargs["avatar"] = avatar_bytes
            if banner_bytes: edit_kwargs["banner"] = banner_bytes
            
            if not edit_kwargs:
                await interaction.followup.send("ℹ️ Tumne koi details provide nahi ki edit karne ke liye.", ephemeral=True)
                return

            await self.bot.user.edit(**edit_kwargs)
            await interaction.followup.send("✅ Bot profile successfully update ho gayi hai! (Note: Discord allows limited name/avatar changes per hour).", view=create_branding_view())
            
            await send_activity_log(
                self.bot,
                activity_type="Bot Profile Edited",
                details="Bot's profile was customized by the owner.",
                module="Management",
                guild=interaction.guild,
                user=interaction.user
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Discord API Error (Shayad limit hit ho gayi): {e}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error aayi: {e}", ephemeral=True)

    @app_commands.command(name="user_info", description="Get extremely detailed information about any user")
    async def user_info(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(thinking=True)
        
        # Calculating dates
        created_at = int(member.created_at.timestamp())
        joined_at = int(member.joined_at.timestamp()) if member.joined_at else 0
        premium_since = int(member.premium_since.timestamp()) if member.premium_since else None
        
        # Getting badges
        badges = [flag.name.replace("_", " ").title() for flag, value in member.public_flags if value]
        badge_str = ", ".join(badges) if badges else "No public badges"
        
        # Getting roles
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
        
        # Nitro hint (Animated avatar implies Nitro)
        nitro_status = "Yes (Animated Avatar/Boost)" if member.display_avatar.is_animated() or premium_since else "Likely No (Or Standard/Basic)"
        embed.add_field(name="Nitro Status (Guessed)", value=nitro_status, inline=True)
        embed.add_field(name="Bot?", value="Yes 🤖" if member.bot else "No 👤", inline=True)
        
        embed.set_footer(text="an app by deep")
        await interaction.followup.send(embed=embed, view=create_branding_view())

    @management_group.command(name="add_staff_role", description="Register a staff role, set custom description and powers")
    @app_commands.describe(
        role="The staff role you want to register",
        description="Role details (Use \\n for line breaks)",
        can_ban_kick="Can they Ban, Kick or Timeout?",
        can_manage_messages="Can they delete messages and manage threads?",
        can_manage_server="Can they change server/bot settings?",
        can_manage_roles="Can they give or remove roles?",
        is_event_manager="Do they host events or stage channels?",
        is_support_team="Do they handle tickets and support?"
    )
    async def add_staff_role(
        self, 
        interaction: discord.Interaction, 
        role: discord.Role, 
        description: str,
        can_ban_kick: bool = False,
        can_manage_messages: bool = False,
        can_manage_server: bool = False,
        can_manage_roles: bool = False,
        is_event_manager: bool = False,
        is_support_team: bool = False
    ):
        await interaction.response.defer(thinking=True)
        
        # Formatting description (Replacing \n text with actual newlines)
        formatted_desc = description.replace("\\n", "\n")
        
        payload = {
            "guild_id": interaction.guild.id,
            "role_id": role.id,
            "role_name": role.name,
            "description": formatted_desc,
            "powers": {
                "Moderation (Ban/Kick)": can_ban_kick,
                "Message Management": can_manage_messages,
                "Server Management": can_manage_server,
                "Role Management": can_manage_roles,
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
        
        await interaction.followup.send(f"✅ Staff Role **{role.name}** successfully database mein add ho gaya hai! Tum ise `/management send_staff_data` se check kar sakte ho.", view=create_branding_view())

    @management_group.command(name="remove_staff_role", description="Unregister a staff role from the roster")
    async def remove_staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(thinking=True)
        result = await staff_roles_col.delete_one({"guild_id": interaction.guild.id, "role_id": role.id})
        
        if result.deleted_count > 0:
            await interaction.followup.send(f"🗑️ Staff Role **{role.name}** roster se hata diya gaya hai.", view=create_branding_view())
        else:
            await interaction.followup.send("⚠️ Ye role pehle se hi roster mein registered nahi tha.", view=create_branding_view())

    @management_group.command(name="send_staff_data", description="Send the complete beautifully formatted staff roster to a channel")
    async def send_staff_data(self, interaction: discord.Interaction, target_channel: discord.TextChannel):
        await interaction.response.defer(thinking=True)
        
        docs = await staff_roles_col.find({"guild_id": interaction.guild.id}).to_list(length=None)
        if not docs:
            await interaction.followup.send("❌ Koi staff role registered nahi hai. Pehle `/management add_staff_role` use karo.", ephemeral=True)
            return

        # Notify the admin
        await interaction.followup.send(f"⚡ Processing and sending Staff Data to {target_channel.mention}...", ephemeral=True)

        # Send Header
        header_embed = discord.Embed(
            title=f"🛡️ Official Staff Roster | {interaction.guild.name}",
            description="Below is the official hierarchy, permissions, and active members of our staff team.",
            color=0x2b2d31,
            timestamp=datetime.now(timezone.utc)
        )
        if interaction.guild.icon:
            header_embed.set_thumbnail(url=interaction.guild.icon.url)
        await target_channel.send(embed=header_embed)

        # Sort docs logically (higher role position first)
        def get_role_pos(doc):
            r = interaction.guild.get_role(doc["role_id"])
            return r.position if r else 0
            
        docs = sorted(docs, key=get_role_pos, reverse=True)

        for doc in docs:
            role = interaction.guild.get_role(doc["role_id"])
            if not role:
                continue # Skip if role was deleted from discord
                
            # Build Powers String
            powers_str = ""
            for power_name, has_power in doc["powers"].items():
                emoji = "✅" if has_power else "❌"
                powers_str += f"{emoji} **{power_name}**\n"
                
            # Build the embed for the Role
            role_embed = discord.Embed(title=f"🔰 {role.name}", description=doc["description"], color=role.color if role.color.value else 0x5865F2)
            role_embed.add_field(name="Role Authorities", value=powers_str, inline=False)
            
            await target_channel.send(embed=role_embed)

            # Build members list
            members = role.members
            if not members:
                await target_channel.send("> *Currently no active members in this role.*")
                continue
                
            # Embed chunking for members to look clean
            member_lines = []
            for m in members:
                pfp = m.display_avatar.url
                join_ts = int(m.joined_at.timestamp()) if m.joined_at else 0
                line = f"**{m.display_name}** ({m.mention})\n└ ID: `{m.id}` | Joined: <t:{join_ts}:R>"
                member_lines.append(line)
            
            chunk_size = 15
            for i in range(0, len(member_lines), chunk_size):
                chunk = member_lines[i:i + chunk_size]
                mem_embed = discord.Embed(description="\n\n".join(chunk), color=0x2b2d31)
                
                # Adding a small footer logo to member embeds
                mem_embed.set_footer(text=f"Roster System • an app by deep")
                await target_channel.send(embed=mem_embed, allowed_mentions=discord.AllowedMentions.none())
                
            await asyncio.sleep(1) # Rate limit protection

        # Send Footer View
        await target_channel.send(view=create_branding_view())

    @management_group.command(name="help", description="Show the complete guide for the Management & Staff Roster module")
    async def management_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙️ Management & Staff Roster Guide", 
            description="Welcome to the advanced Server & Bot Management module. This system allows you to edit bot appearance, trace detailed user stats, and automate your staff roster beautifully.",
            color=0x5865F2
        )
        
        embed.add_field(
            name="🤖 1. Bot Editing", 
            value="`/management bot_edit`\nDirectly update the bot's username, avatar, and banner using image links. *(Owner Only)*", 
            inline=False
        )
        
        embed.add_field(
            name="👤 2. Advanced User Info", 
            value="`/user_info`\nGet a detailed profile lookup including Discord badges, Nitro status guesses, server join dates, and active roles.", 
            inline=False
        )
        
        embed.add_field(
            name="🛡️ 3. Adding Staff Roles", 
            value="`/management add_staff_role`\nRegister a role as a staff rank. You can add a detailed description (use `\\n` to create new lines) and toggle 6 specific powers to clarify what they can do.", 
            inline=False
        )
        
        embed.add_field(
            name="🗑️ 4. Removing Staff Roles", 
            value="`/management remove_staff_role`\nUnlink a role from the official roster database if the rank is retired.", 
            inline=False
        )
        
        embed.add_field(
            name="📤 5. Publishing Roster", 
            value="`/management send_staff_data`\nGenerates a beautiful, multi-message layout in your chosen channel. It lists each rank, its powers, and every single member holding that rank with their join details.", 
            inline=False
        )
        
        embed.set_footer(text="an app by deep")
        await interaction.response.send_message(embed=embed, view=create_branding_view())

async def setup(bot: commands.Bot):
    await bot.add_cog(ManagementCommands(bot))
