import json
import io
import discord
from datetime import datetime

def generate_json_file(data: list) -> discord.File:
    """List of dictionaries ko JSON file me convert karta hai."""
    # Data ko json string me format karke bytes me convert karna
    json_data = json.dumps(data, indent=4, default=str)
    file_bytes = io.BytesIO(json_data.encode('utf-8'))
    return discord.File(fp=file_bytes, filename=f"activity_log_{datetime.now().strftime('%Y%m%d')}.json")

def generate_html_file(data: list, guild_name: str, guild_icon_url: str) -> discord.File:
    """Data ko ek sundar HTML table me convert karta hai dark theme ke sath."""
    
    # Table rows banana
    rows = ""
    for index, user_data in enumerate(data, start=1):
        # Database me user_id aur message_count save hai
        uid = user_data.get('user_id', 'Unknown')
        count = user_data.get('message_count', 0)
        rows += f"""
        <tr>
            <td>#{index}</td>
            <td>{uid}</td>
            <td>{count}</td>
        </tr>
        """

    # Logo check
    logo_img = f'<img src="{guild_icon_url}" alt="Server Logo">' if guild_icon_url else ''

    # HTML Template
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{guild_name} - Activity Logs</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #2b2d31; color: #dbdee1; margin: 0; padding: 20px; }}
        .container {{ max-width: 800px; margin: auto; background-color: #313338; padding: 20px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }}
        .header {{ text-align: center; margin-bottom: 20px; border-bottom: 2px solid #1e1f22; padding-bottom: 20px; }}
        .header img {{ border-radius: 50%; width: 80px; height: 80px; object-fit: cover; }}
        .header h2 {{ margin: 10px 0 5px 0; color: #ffffff; }}
        .header p {{ margin: 0; font-size: 14px; color: #949ba4; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #1e1f22; }}
        th {{ background-color: #1e1f22; color: #ffffff; font-weight: 600; text-transform: uppercase; font-size: 14px; }}
        tr:hover {{ background-color: #2b2d31; }}
        .footer {{ text-align: center; margin-top: 30px; font-size: 14px; border-top: 2px solid #1e1f22; padding-top: 20px; }}
        .footer a {{ color: #5865F2; text-decoration: none; font-weight: bold; }}
        .footer a:hover {{ text-decoration: underline; color: #7289da; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            {logo_img}
            <h2>{guild_name} - Activity Logs</h2>
            <p>Generated on {datetime.now().strftime("%B %d, %Y at %I:%M %p")}</p>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Rank</th>
                    <th>User ID</th>
                    <th>Messages Sent</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
        <div class="footer">
            <p><a href="https://deepdey.vercel.app/" target="_blank">Deep Dey</a> | <a href="https://deepdey.vercel.app/insta" target="_blank">Instagram</a></p>
        </div>
    </div>
</body>
</html>"""

    file_bytes = io.BytesIO(html_content.encode('utf-8'))
    return discord.File(fp=file_bytes, filename=f"activity_log_{datetime.now().strftime('%Y%m%d')}.html")

def format_uptime(start_time: datetime) -> str:
    """Bot ka uptime calculate karta hai."""
    now = datetime.now()
    delta = now - start_time
    
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return f"{days}d {hours}h {minutes}m {seconds}s"
