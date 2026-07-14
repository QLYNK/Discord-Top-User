import json
import io
import discord
from datetime import datetime, timezone

def generate_json_file(data: list, guild: discord.Guild) -> discord.File:
    """List of dictionaries ko enriched JSON file me convert karta hai."""
    export_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    enriched_data = []
    
    for user_data in data:
        uid = int(user_data.get('user_id', 0))
        member = guild.get_member(uid)
        
        enriched_data.append({
            "user_id": str(uid),
            "username": str(member.name) if member else "Unknown",
            "global_name": str(member.global_name) if member and member.global_name else "Unknown",
            "nickname": str(member.nick) if member and member.nick else "None",
            "profile_picture": str(member.display_avatar.url) if member else "https://cdn.discordapp.com/embed/avatars/0.png",
            "profile_link": f"https://discord.com/users/{uid}",
            "message_count": user_data.get('message_count', 0),
        })
    
    final_payload = {
        "server_info": {
            "name": guild.name,
            "id": str(guild.id),
            "member_count": guild.member_count
        },
        "export_time": export_time,
        "credits": {
            "author": "Deep Dey",
            "website": "https://deepdey.vercel.app/",
            "instagram": "https://deepdey.vercel.app/insta"
        },
        "users": enriched_data
    }
    
    json_data = json.dumps(final_payload, indent=4, ensure_ascii=False)
    file_bytes = io.BytesIO(json_data.encode('utf-8'))
    return discord.File(fp=file_bytes, filename=f"activity_{guild.id}_{int(datetime.now().timestamp())}.json")

def generate_html_file(data: list, guild: discord.Guild) -> discord.File:
    """Data ko ek beautiful HTML table me convert karta hai dark theme ke sath."""
    export_time = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
    guild_icon_url = guild.icon.url if guild.icon else "https://cdn.discordapp.com/embed/avatars/0.png"
    
    invite_link = f"https://discord.gg/{guild.vanity_url_code}" if guild.vanity_url_code else "No Vanity Link"
    
    rows = ""
    for index, user_data in enumerate(data, start=1):
        uid = int(user_data.get('user_id', 0))
        count = user_data.get('message_count', 0)
        member = guild.get_member(uid)
        
        name = member.name if member else "Unknown"
        nick = member.nick if member and member.nick else "-"
        pfp = member.display_avatar.url if member else "https://cdn.discordapp.com/embed/avatars/0.png"
        profile_link = f"https://discord.com/users/{uid}"
        
        rows += f"""
        <tr>
            <td>#{index}</td>
            <td><img src="{pfp}" alt="pfp" style="width:40px;height:40px;border-radius:50%; object-fit: cover;"></td>
            <td>{name}</td>
            <td>{nick}</td>
            <td><a href="{profile_link}" target="_blank" style="color:#5865F2;">{uid}</a></td>
            <td style="font-weight:bold; color:#1DB954;">{count}</td>
        </tr>
        """
        
    html_content = f"""<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{guild.name} - Activity Logs</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #2b2d31; color: #dbdee1; margin: 0; padding: 20px; }}
            .container {{ max-width: 900px; margin: auto; background-color: #313338; padding: 20px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }}
            .header {{ text-align: center; margin-bottom: 20px; border-bottom: 2px solid #1e1f22; padding-bottom: 20px; }}
            .header img {{ border-radius: 50%; width: 100px; height: 100px; object-fit: cover; border: 3px solid #5865F2; }}
            .header h2 {{ margin: 10px 0 5px 0; color: #ffffff; font-size: 28px; }}
            .header p {{ margin: 5px 0; font-size: 14px; color: #949ba4; }}
            .stats {{ display: flex; justify-content: center; gap: 30px; margin-bottom: 20px; font-size: 14px; background: #1e1f22; padding: 12px; border-radius: 8px; flex-wrap: wrap; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #1e1f22; vertical-align: middle; }}
            th {{ background-color: #1e1f22; color: #ffffff; font-weight: 600; text-transform: uppercase; font-size: 13px; }}
            tr:hover {{ background-color: #2b2d31; transition: 0.2s; }}
            .footer {{ text-align: center; margin-top: 30px; font-size: 14px; border-top: 2px solid #1e1f22; padding-top: 20px; }}
            .footer a {{ color: #5865F2; text-decoration: none; font-weight: bold; }}
            .footer a:hover {{ text-decoration: underline; color: #7289da; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <img src="{guild_icon_url}" alt="Server Logo">
                <h2>{guild.name}</h2>
                <p>Server ID: {guild.id} | Boost Level: {guild.premium_tier} | Total Members: {guild.member_count}</p>
                <p>Invite: <a href="{invite_link}" style="color:#5865F2;">{invite_link}</a></p>
            </div>
            <div class="stats">
                <span><strong>Total Tracked Users:</strong> {len(data)}</span>
                <span><strong>Exported On:</strong> {export_time}</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Profile</th>
                        <th>Username</th>
                        <th>Nickname</th>
                        <th>User ID / Link</th>
                        <th>Messages</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
            <div class="footer">
                <p>Built with ❤️ by <a href="https://deepdey.vercel.app/" target="_blank">Deep Dey</a> | <a href="https://deepdey.vercel.app/insta" target="_blank">Instagram</a></p>
            </div>
        </div>
    </body>
    </html>"""
    file_bytes = io.BytesIO(html_content.encode('utf-8'))
    return discord.File(fp=file_bytes, filename=f"activity_{guild.id}_{int(datetime.now().timestamp())}.html")

def format_uptime(start_time: datetime) -> str:
    """Bot ka uptime calculate karta hai."""
    now = datetime.now()
    delta = now - start_time
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"

async def send_paginated_backup_logs(logs_channel: discord.TextChannel, guild: discord.Guild, users_data: list, time_range_str: str, action: str):
    """Bada user data split karke silent embeds aur last me files bhejta hai."""
    if not users_data:
        await logs_channel.send(f"⚠️ **Log Action: {action}**\nNo activity data found for this cycle.")
        return

    # Embed me dikhane ke liye strings banayenge
    lines = []
    for rank, u in enumerate(users_data, 1):
        member = guild.get_member(int(u.get('user_id', 0)))
        display = member.mention if member else f"`{u['user_id']}`"
        lines.append(f"**#{rank}** {display} — **{u.get('message_count', 0)}** msgs")

    chunk_size = 40 # 40 users per embed taaki 4096 character limit hit na ho
    embeds = []
    for i in range(0, len(lines), chunk_size):
        chunk = lines[i:i + chunk_size]
        emb = discord.Embed(
            title=f"📊 Full Activity Leaderboard Snapshot",
            description="\n".join(chunk),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        if i == 0:
            emb.add_field(name="Time Range", value=time_range_str, inline=False)
            emb.add_field(name="Action Trigger", value=action, inline=False)
        emb.set_footer(text=f"Page {(i//chunk_size)+1} • an app by deep")
        embeds.append(emb)

    # Silent Mentions: Kisi ko bhi ping nahi jayega
    allowed = discord.AllowedMentions(users=False, roles=False, everyone=False)
    
    # Generate Files
    json_file = generate_json_file(users_data, guild)
    html_file = generate_html_file(users_data, guild)

    if not embeds:
        await logs_channel.send(f"📄 **Log Action: {action}**\nData backup:", files=[json_file, html_file])
        return

    # Sab embeds bhejenge, aur sabse aakhri wale me HTML/JSON files attach karenge
    for idx, emb in enumerate(embeds):
        if idx == len(embeds) - 1:
            await logs_channel.send(embed=emb, files=[json_file, html_file], allowed_mentions=allowed)
        else:
            await logs_channel.send(embed=emb, allowed_mentions=allowed)
