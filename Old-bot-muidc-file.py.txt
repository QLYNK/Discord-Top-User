"""
Music Cog - Advanced Hybrid System for Discord (Auto-Pilot Edition)
Author: Deep Dey's Assistant (Jadoo)
Version: 3.0.0 (Automation + VC Status)

Description:
This module handles high-quality music playback with a specialized hybrid engine.
It prioritizes immediate playback via streaming while simultaneously caching audio
files in the background for future efficiency.

Features:
- AUTO-START: Automatically joins specific VC, starts music, and deploys dashboard.
- /start: Initializes the voice connection and begins the fixed playlist loop.
- /live: Deploys a persistent, real-time updating dashboard for playback status.
- /leave: Safely disconnects the bot and cleans up resources (Owner Only).
- /skip: Forces the player to move to the next track immediately.
- /volume: Adjusts the global audio output volume.
- /pause & /resume: accurate timer handling for pauses.

Technical Implementation:
- Hybrid Engine: Checks for local file -> If missing, Streams URL + Downloads in Background.
- Smart Cache: Auto-deletes old files if storage exceeds 1GB.
- Async I/O: Uses aiohttp for non-blocking downloads.
- FFmpeg Reconnect: Ensures stream stability even if internet fluctuates.
"""

from __future__ import annotations

import asyncio
import os
import time
import math
import datetime
import aiohttp  # Essential for non-blocking file downloads
from typing import List, Optional, Dict, Any, Union

import discord
from discord import Embed, Color
from discord.ext import commands, tasks
from discord.utils import get
from utils import db

# =============================================================================
# CONFIGURATION & CONSTANTS
# =============================================================================

# ID of the Voice Channel to Auto-Join on Startup
AUTO_JOIN_VC_ID = 1444592702376251504
# Optional override for live dashboard target channel
LIVE_MESSAGE_CHANNEL_ID = int(os.getenv("LIVE_MESSAGE_CHANNEL_ID", "0") or 0)

# Maximum allowed cache size in Megabytes (MB).
# Render Free Tier allows limited ephemeral storage, so 1024MB (1GB) is safe.
MAX_CACHE_SIZE_MB = 1024

# Directory name where music files will be stored.
MUSIC_CACHE_DIR = "music_cache"

# User Agent to prevent 403 Forbidden errors from some file hosts.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"


# The Fixed Playlist Data
# This list contains the metadata for the tracks that will loop indefinitely.
FIXED_PLAYLIST_DATA: List[Dict[str, Any]] = [
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 1: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 2: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 3: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 4: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 5: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/864e3f49080443419ad3868ee1f8bc75",
        "duration": 4004,
        "title": "Track 6: Saiyaara (Extended Album) | Audio Jukebox (1:06:44)",
        "artwork": "https://deydeep-static-files.hf.space/f/45272d0884914e3ca938d9772ad589bf"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/850a4dc785bc4a43877e8b873c468bfb",
        "duration": 1939,
        "title": "Track 7: Full Album : Loveyatri | Audio Jukebox (32:19)",
        "artwork": "https://deydeep-static-files.hf.space/f/4d6c8324364a5ca33a1d3a74c7c0fb93"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/fcff42a9a141eff028f758e70d70a60d",
        "duration": 1234,
        "title": "Track 8: Bairan Mashup 2026 | Banjaare | SiDD iNSANEZ | Bollywood Love Mashup | Nonstop | Jukebox | 2026 - Audio (20:34)",
        "artwork": "https://deydeep-static-files.hf.space/f/b7aa8437430d00a3036833149bd62c0d"
    },
    {
        "url": "https://files.catbox.moe/u0kzzv.mp3",
        "duration": 166,
        "title": "Track 9: Diwali - Aditya Bhardwaj (2:46)",
        "artwork": "https://i.postimg.cc/SR37kFRm/Diwali-Aditya-Bhardwaj-500-500.jpg"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 10: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 11: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 12: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 13: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 14: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/rlio97.mp3",
        "duration": 295,
        "title": "Track 15: Arz Kiya Hai - Anuv Jain (Coke Studio Bharat) (4:55)",
        "artwork": "https://images.genius.com/92e6406cfa47bde718c15ce9869e357d.1000x1000x1.png"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 16: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 17: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 18: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 19: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 20: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/8gtvio.mp3",
        "duration": 217,
        "title": "Track 21: Husn - Anuv Jain (3:37)",
        "artwork": "https://i.postimg.cc/nzK6X2BZ/Husn-Anuv-Jain-500-500.jpg"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 22: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 23: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 24: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 25: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 26: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/elr8oc.mp3",
        "duration": 188,
        "title": "Track 27: Esho Hey - LoFi - Shreya Ghoshal (3:08)",
        "artwork": "https://i.postimg.cc/2yPMVmNd/output.jpg"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 28: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 29: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 30: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 31: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 32: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/qudqsz.mp3",
        "duration": 321,
        "title": "Track 33: Papa Meri Jaan - Sonu Nigam (Animal) (5:21)",
        "artwork": "https://i.postimg.cc/qvbBRJ0w/papa-meri-jaan-animal-500-500.jpg"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 34: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 35: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 36: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 37: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 38: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/62uskz.mp3",
        "duration": 1410,
        "title": "Track 39: Dhadak 2 (2025) Jukebox (23:30)",
        "artwork": "https://upload.wikimedia.org/wikipedia/en/b/b3/Dhadak_2.jpg"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 40: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 41: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 42: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 43: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 44: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/p3lyfh.mp3",
        "duration": 1930,
        "title": "Track 45: Saiyaara (2025) Jukebox (32:10)",
        "artwork": "https://upload.wikimedia.org/wikipedia/en/d/db/Saiyaara_film_poster.jpg"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 46: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 47: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 48: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 49: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 50: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/p968vq.mp3",
        "duration": 4005,
        "title": "Track 51: Saiyaara - Extended Album (2025) Jukebox (1:06:45)",
        "artwork": "https://i.postimg.cc/wTPr3WQy/y-SX4E5U-ZOQ-HD.jpg"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 52: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 53: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 54: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 55: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 56: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/x9dtcn.mp3",
        "duration": 188,
        "title": "Track 57: Motivation Beat (3:08)",
        "artwork": "https://qlynk.vercel.app/Deep_Dey.gif/"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 58: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 59: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 60: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 61: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 62: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/1hpn4b.MP3",
        "duration": 131,
        "title": "Track 63: Need Less To Say [Raw & Real] (2025) - Shreya & Ashley (2:11)",
        "artwork": "https://files.catbox.moe/jqe3bp.gif"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 64: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/bc767bae07a64cd08b49f401189c5009",
        "duration": 168,
        "title": "Track 65: Saiyaara (Female Version) (2:48)",
        "artwork": "https://deydeep-static-files.hf.space/f/1c05285ddf604b2c82a3521d6fd4ba42"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd",
        "duration": 169,
        "title": "Track 66: Promises - Official Music (2:49)",
        "artwork": "https://deydeep-static-files.hf.space/f/ddea432cb18448508e639599e1fbaddd_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d",
        "duration": 157,
        "title": "Track 67: Promises (Reprise) (2:37)",
        "artwork": "https://deydeep-static-files.hf.space/f/0cf1307e36ca4071a85f6ba5189b158d_thumb.jpg"
    },
    {
        "url": "https://deydeep-static-files.hf.space/f/9d3af43b61f94be297185fd20f67b68e",
        "duration": 216,
        "title": "Track 68: Iqlipse Nova - Gumshuda (Official Music video) (3:36)",
        "artwork": "https://deydeep-static-files.hf.space/f/f0439ce19ee3437a9dc776d7feb4b8d2"
    },
    {
        "url": "https://files.catbox.moe/k6mnbf.mp3",
        "duration": 215,
        "title": "Track 69: Nachaya Dil - Saaj Bhatt (Voilà! Digi) (3:35)",
        "artwork": "https://i.postimg.cc/pXjp3Y9P/Nachaya-Dil.jpg"
    }
]


# =============================================================================
# MAIN MUSIC COG CLASS
# =============================================================================

class Music(commands.Cog):
    """
    The main class responsible for handling music commands and playback logic.
    Inherits from discord.ext.commands.Cog.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # Voice Client State
        self.voice_client: Optional[discord.VoiceClient] = None
        
        # Playback Queue & Cache
        self.queue: List[Dict[str, Any]] = []
        self._playlist_cache: List[Dict[str, Any]] = []
        
        # Playback Configuration
        self.volume = 0.50  # Default volume at 50%
        
        # Background Tasks
        self._player_task: Optional[asyncio.Task] = None
        
        # Dashboard / Live Message State
        self._announce_channel = {}
        self._now_playing_task: Dict[int, asyncio.Task] = {}
        self._now_playing_message: Dict[int, discord.Message] = {}
        self._cooldown_until: Dict[int, float] = {}
        
        # Track Information State
        self.current_track_info: Dict[int, Dict[str, Any]] = {}
        self.current_track_number: Dict[int, int] = {}
        self._track_duration: Dict[int, int] = {}
        
        # Timing & Progress State
        self._track_start_time: Dict[int, float] = {}
        self._pause_start_time: Dict[int, float] = {}

        # Recovery bookkeeping
        self._resume_at: Dict[int, float] = {}
        self._recovery_state: Optional[Dict[str, Any]] = None

        # Initialization Logic
        self._ensure_cache_directory()
        self.cleanup_cache_task.start()
        self.bot.loop.create_task(self._ensure_live_message())

    def _get_channel_by_id(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        """Fetch a channel by ID with safe attribute checks."""
        try:
            return self.bot.get_channel(channel_id)
        except Exception:
            return None

    def _is_sendable_channel(self, channel: Optional[discord.abc.Messageable]) -> bool:
        if not channel or not hasattr(channel, "send"):
            return False
        guild = getattr(channel, "guild", None)
        me = getattr(guild, "me", None) if guild else None
        if not me:
            return True
        try:
            perms = channel.permissions_for(me)
        except Exception:
            return True
        return perms.send_messages and perms.embed_links

    def _get_live_message_channel(self, voice_channel: Optional[discord.VoiceChannel] = None) -> Optional[discord.abc.Messageable]:
        """Return the live dashboard channel, preferring the voice channel message area."""
        vc_chat = self._get_voice_chat_channel(voice_channel)
        if vc_chat and self._is_sendable_channel(vc_chat):
            return vc_chat
        if LIVE_MESSAGE_CHANNEL_ID:
            ch = self._get_channel_by_id(LIVE_MESSAGE_CHANNEL_ID)
            if self._is_sendable_channel(ch):
                return ch
        return None

    def _load_playlist_cache(self):
        """Load fixed playlist with indices for recovery and (re)prime queue."""
        self._playlist_cache = [dict(track, origin_index=i) for i, track in enumerate(FIXED_PLAYLIST_DATA)]
        if not self.queue:
            self.queue = list(self._playlist_cache)

    def _prime_queue_from_index(self, start_index: int):
        """Rotate queue so playback resumes from a specific playlist index."""
        if not self._playlist_cache:
            self._load_playlist_cache()
        start_index = max(0, min(start_index, max(len(self._playlist_cache) - 1, 0)))
        rotated = self._playlist_cache[start_index:] + self._playlist_cache[:start_index]
        self.queue = list(rotated)

    def _ensure_cache_directory(self):
        """Creates the music cache directory if it doesn't exist."""
        if not os.path.exists(MUSIC_CACHE_DIR):
            try:
                os.makedirs(MUSIC_CACHE_DIR)
                print(f"[MUSIC SYSTEM] Created cache directory: {MUSIC_CACHE_DIR}")
            except OSError as e:
                print(f"[MUSIC SYSTEM] Error creating cache directory: {e}")


    def _get_voice_chat_channel(self, voice_channel: Optional[discord.VoiceChannel]) -> Optional[discord.abc.Messageable]:
        """Prefer the voice channel's own message area when available."""
        if not voice_channel:
            return None

        if hasattr(voice_channel, "send"):
            return voice_channel

        guild = getattr(voice_channel, "guild", None)
        me = getattr(guild, "me", None) if guild else None

        vc_text = getattr(voice_channel, "text_channel", None)
        if vc_text and hasattr(vc_text, "send"):
            try:
                perms = vc_text.permissions_for(me) if me else None
                if perms is None or (perms.send_messages and perms.embed_links):
                    return vc_text
            except Exception:
                return vc_text

        threads = getattr(voice_channel, "threads", None) or []
        for thread in threads:
            if not thread or not hasattr(thread, "send"):
                continue
            try:
                perms = thread.permissions_for(me) if me else None
                if perms is None or (perms.send_messages and perms.embed_links):
                    return thread
            except Exception:
                continue

        return None


    def _choose_announce_channel(
        self,
        guild: Optional[discord.Guild],
        voice_channel: Optional[discord.VoiceChannel] = None,
        saved_channel: Optional[discord.abc.Messageable] = None,
    ) -> Optional[discord.abc.Messageable]:
        """Pick the best channel to post live updates, prioritizing VC's attached text chat if available."""
        if not guild:
            return None

        me = getattr(guild, "me", None)
        if not me:
            return saved_channel if saved_channel and hasattr(saved_channel, "send") else None

        # 1. VC's attached text chat (voice channel's message area)
        if voice_channel:
            # Discord.py 2.0+ exposes voice_channel.text_channel for attached chat, else try get_thread
            vc_text = getattr(voice_channel, "text_channel", None)
            if vc_text and hasattr(vc_text, "send"):
                perms = vc_text.permissions_for(me)
                if perms.send_messages and perms.embed_links:
                    return vc_text
            if hasattr(voice_channel, "send"):
                return voice_channel
            # Some bots use voice_channel.threads or voice_channel.get_thread()
            # Try to find a thread attached to the VC
            threads = getattr(voice_channel, "threads", None)
            if threads:
                for thread in threads:
                    if hasattr(thread, "send"):
                        perms = thread.permissions_for(me)
                        if perms.send_messages and perms.embed_links:
                            return thread

        candidates = []

        # 2. Text channel in the same category as the voice channel
        if voice_channel and getattr(voice_channel, "category", None):
            try:
                same_cat_texts = [
                    tc for tc in getattr(guild, "text_channels", [])
                    if getattr(tc, "category", None) and tc.category.id == voice_channel.category.id
                ]
                # Prefer one whose name contains the voice channel name or 'chat'
                preferred = None
                for tc in same_cat_texts:
                    name_l = (tc.name or "").lower()
                    if voice_channel.name.lower() in name_l or "chat" in name_l:
                        preferred = tc
                        break
                if preferred:
                    candidates.append(preferred)
                else:
                    candidates.extend(same_cat_texts)
            except Exception:
                pass

        # 3. Attempt to locate a likely VC-chat by name patterns across guild
        try:
            patterns = [
                voice_channel.name.lower() if voice_channel else None,
                "bot-commands","voice","voice-chat","vc","vc-chat","general"
            ]
            patterns = [p for p in patterns if p]
            for tc in getattr(guild, "text_channels", []):
                name_l = (tc.name or "").lower()
                if any(p in name_l for p in patterns):
                    candidates.append(tc)
        except Exception:
            pass

        # 4. Then: previously saved announce channel
        if saved_channel and hasattr(saved_channel, "send"):
            candidates.append(saved_channel)

        # 5. System channel next
        if guild.system_channel:
            candidates.append(guild.system_channel)

        # 6. Finally: all text channels as fallback
        candidates.extend(getattr(guild, "text_channels", []))

        for channel in candidates:
            if not channel:
                continue
            try:
                perms = channel.permissions_for(me)
            except Exception:
                continue
            if perms.send_messages and perms.embed_links:
                return channel
        return None

    async def _ensure_live_message(self):
        """Post or refresh the live dashboard in the target voice channel's chat area once the bot is ready."""
        try:
            await self.bot.wait_until_ready()
            for guild in self.bot.guilds:
                vc = guild.get_channel(AUTO_JOIN_VC_ID)
                if isinstance(vc, discord.VoiceChannel):
                    # Force target announce channel to the requested ID when available
                    forced_channel = self._get_live_message_channel(vc)
                    if forced_channel and hasattr(forced_channel, "send"):
                        try:
                            initial_embed = self._build_now_playing_embed(guild)
                            sent_message = await forced_channel.send(embed=initial_embed)
                            self._announce_channel[guild.id] = forced_channel
                            self._now_playing_message[guild.id] = sent_message
                            self._now_playing_task[guild.id] = self.bot.loop.create_task(
                                self._update_now_playing_message(guild.id)
                            )
                            continue
                        except Exception:
                            pass
                    try:
                        await self.auto_post_live(guild, vc)
                    except Exception:
                        continue
        except Exception:
            return

    async def cog_load(self):
        """Called when the Cog is loaded by the bot."""
        self._load_playlist_cache()
        print(f'[MUSIC SYSTEM] Music Cog loaded successfully.')
        print(f'[MUSIC SYSTEM] Loaded {len(self._playlist_cache)} tracks into the fixed playlist.')
        
        if not self._playlist_cache:
            print('[MUSIC SYSTEM] CRITICAL WARNING: Fixed playlist is empty!')
            
        # --- AUTO-PILOT INITIATION ---
        # Starts the auto-join sequence in the background
        self.bot.loop.create_task(self._auto_pilot_sequence())

    def cog_unload(self):
        """Called when the Cog is unloaded. Cleans up tasks."""
        print('[MUSIC SYSTEM] Unloading Cog. Cancelling background tasks...')
        self.cleanup_cache_task.cancel()
        for guild_id, task in self._now_playing_task.items():
            if not task.done():
                task.cancel()
        print('[MUSIC SYSTEM] Cog unloaded.')

    async def _auto_pilot_sequence(self):
        """
        [AUTO-PILOT]
        This function handles the automatic joining, playing, and dashboard deployment
        on bot startup.
        """
        await self.bot.wait_until_ready()
        print("[AUTO-PILOT] 🚀 Initiating launch sequence...")
        
        # Give Discord some time to sync cache
        await asyncio.sleep(5)
        
        try:
            # Recover last known state if available
            saved_state = await db.DB.get_latest_music_state()
            target_vc = None
            announce_channel = None
            resume_offset = 0
            playlist_index = None
            if saved_state:
                vc_id = saved_state.get("voice_channel_id")
                ch_id = saved_state.get("announce_channel_id")
                playlist_index = saved_state.get("playlist_index")
                resume_offset = saved_state.get("position", 0)
                target_vc = self.bot.get_channel(vc_id) if vc_id else None
                if ch_id:
                    announce_channel = self.bot.get_channel(ch_id)
                self._recovery_state = saved_state
                print(f"[AUTO-PILOT] Found saved music state for guild {saved_state.get('guild_id')} (playlist idx {playlist_index}, offset {resume_offset}s)")

            # 1. Fetch Target Voice Channel
            if target_vc is None:
                target_vc = self.bot.get_channel(AUTO_JOIN_VC_ID)
            
            if not target_vc:
                print(f"[AUTO-PILOT] ❌ Critical Error: Could not find VC with ID {AUTO_JOIN_VC_ID}")
                return

            if not isinstance(target_vc, discord.VoiceChannel):
                print(f"[AUTO-PILOT] ❌ Error: ID {AUTO_JOIN_VC_ID} is not a Voice Channel!")
                return

            # 2. Connect to VC
            if self.voice_client is None or not self.voice_client.is_connected():
                try:
                    self.voice_client = await target_vc.connect()
                    print(f"[AUTO-PILOT] ✅ Connected to Voice Channel: {target_vc.name}")
                except Exception as e:
                    print(f"[AUTO-PILOT] ❌ Connection failed: {e}")
                    return
            else:
                # If already connected somewhere else, move here
                await self.voice_client.move_to(target_vc)
                print(f"[AUTO-PILOT] 🔄 Moved to target VC: {target_vc.name}")

            # 3. Start Music Engine
            # Reset/rotate queue for recovery
            if playlist_index is not None:
                self._prime_queue_from_index(playlist_index)
            elif not self.queue:
                self.queue = list(self._playlist_cache)
            
            # Set the announcement channel to the fixed live channel when available
            forced_live = self._get_live_message_channel(target_vc)
            announce_ch = forced_live or self._choose_announce_channel(target_vc.guild, target_vc, announce_channel)
            if announce_ch:
                self._announce_channel[target_vc.guild.id] = announce_ch
            else:
                print(f"[AUTO-PILOT] ⚠️ No suitable text channel found for guild {target_vc.guild.id} to post live dashboard.")

            
            # If recovering, seed resume offset for guild
            if resume_offset and target_vc.guild:
                self._resume_at[target_vc.guild.id] = resume_offset
            
            self._ensure_player()
            print("[AUTO-PILOT] 🎵 Music Engine Started.")

            # 4. Deploy Live Dashboard Automatically
            # We wait a bit for the first track to start processing
            await asyncio.sleep(3)
            
            guild = target_vc.guild
            # Build initial embed
            embed = self._build_now_playing_embed(guild)
            
            # Send message to the Voice Channel's Text Chat
            try:
                dash_channel = self._announce_channel.get(guild.id)
                if not dash_channel:
                    print(f"[AUTO-PILOT] ⚠️ No announce channel available; skipping live dashboard.")
                    return
                sent_msg = await dash_channel.send(embed=embed)
                
                # Register the dashboard task
                if guild.id in self._now_playing_task:
                    self._now_playing_task[guild.id].cancel()
                
                self._now_playing_message[guild.id] = sent_msg
                self._now_playing_task[guild.id] = self.bot.loop.create_task(
                    self._update_now_playing_message(guild.id)
                )
                print(f"[AUTO-PILOT] 🔴 Live Dashboard Deployed in {target_vc.name}")
                
            except Exception as e:
                print(f"[AUTO-PILOT] ⚠️ Could not send Live Dashboard: {e}")

        except Exception as e:
            print(f"[AUTO-PILOT] 💥 Fatal Error in sequence: {e}")

    # =========================================================================
    # BACKGROUND TASKS: CLEANUP & DOWNLOADER
    # =========================================================================

    @tasks.loop(minutes=30)
    async def cleanup_cache_task(self):
        """
        Periodically checks the size of the music_cache folder.
        If it exceeds MAX_CACHE_SIZE_MB, it deletes the oldest files until
        space is freed.
        """
        try:
            total_size_bytes = 0
            files_list = []
            
            # Step 1: Scan directory and calculate size
            for filename in os.listdir(MUSIC_CACHE_DIR):
                file_path = os.path.join(MUSIC_CACHE_DIR, filename)
                
                # Skip incomplete downloads (.part files)
                if filename.endswith(".part"):
                    continue
                    
                if os.path.isfile(file_path):
                    file_size = os.path.getsize(file_path)
                    total_size_bytes += file_size
                    # Store file data: (path, modification_time, size)
                    files_list.append((file_path, os.path.getmtime(file_path), file_size))
            
            total_size_mb = total_size_bytes / (1024 * 1024)
            
            # Step 2: Check if limit is exceeded
            if total_size_mb > MAX_CACHE_SIZE_MB:
                print(f"[CACHE MANAGER] Limit Exceeded! Current: {total_size_mb:.2f} MB / Limit: {MAX_CACHE_SIZE_MB} MB")
                print("[CACHE MANAGER] Starting cleanup protocol...")
                
                # Sort files by modification time (Oldest first)
                files_list.sort(key=lambda x: x[1])
                
                deleted_bytes = 0
                for file_path, _, file_size in files_list:
                    try:
                        os.remove(file_path)
                        deleted_bytes += file_size
                        current_mb = (total_size_bytes - deleted_bytes) / (1024 * 1024)
                        print(f"[CACHE MANAGER] Deleted: {os.path.basename(file_path)} | Freed: {file_size/1024:.2f} KB")
                        
                        # Stop if we are safely below 80% of the limit
                        if current_mb <= (MAX_CACHE_SIZE_MB * 0.8):
                            print("[CACHE MANAGER] Cleanup complete. Storage level normalized.")
                            break
                    except Exception as e:
                        print(f"[CACHE MANAGER] Failed to delete file {file_path}: {e}")
            else:
                pass # Cache levels are healthy

        except Exception as e:
            print(f"[CACHE MANAGER] Critical Error during cleanup scan: {e}")

    @cleanup_cache_task.before_loop
    async def before_cleanup(self):
        """Waits for the bot to be fully ready before starting cleanup."""
        await self.bot.wait_until_ready()

    async def _download_track_background(self, url: str, filename: str):
        """
        Downloads a file in the BACKGROUND.
        This function is designed to be run as an asyncio Task so it does NOT
        block the main music player.
        """
        final_path = os.path.join(MUSIC_CACHE_DIR, filename)
        temp_path = final_path + ".part" # Temporary file during download
        
        # Double check existence to prevent redundant downloads
        if os.path.exists(final_path):
            return 
            
        print(f"[DOWNLOADER] ⬇️ Started background download for: {filename}")
        print(f"[DOWNLOADER] Source URL: {url}")
        
        headers = {"User-Agent": USER_AGENT}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=600) as resp:
                    if resp.status == 200:
                        with open(temp_path, 'wb') as f:
                            downloaded_size = 0
                            while True:
                                chunk = await resp.content.read(1024 * 1024) # 1MB Chunk
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded_size += len(chunk)
                        
                        # Download complete. Rename .part to .mp3
                        if os.path.getsize(temp_path) > 0:
                            if os.path.exists(final_path):
                                os.remove(final_path)
                            os.rename(temp_path, final_path)
                            print(f"[DOWNLOADER] ✅ Download Success: {filename} ({downloaded_size/(1024*1024):.2f} MB)")
                        else:
                            print(f"[DOWNLOADER] ⚠️ Downloaded file was empty: {filename}")
                            os.remove(temp_path)
                    else:
                        print(f"[DOWNLOADER] ❌ Failed to download. HTTP Status: {resp.status}")
                        
        except asyncio.TimeoutError:
            print(f"[DOWNLOADER] ❌ Connection Timed Out for: {filename}")
            if os.path.exists(temp_path): os.remove(temp_path)
        except Exception as e:
            print(f"[DOWNLOADER] ❌ Unexpected Error: {e}")
            if os.path.exists(temp_path): os.remove(temp_path)

    # =========================================================================
    # UTILITY FUNCTIONS
    # =========================================================================

    def _format_duration(self, seconds: Optional[int]) -> str:
        """Formats seconds into HH:MM:SS or MM:SS string."""
        if seconds is None: return 'N/A'
        try:
            seconds = int(max(0, seconds))
            m, s = divmod(seconds, 60)
            h, m = divmod(m, 60)
            if h > 0:
                return f'{h}:{m:02d}:{s:02d}'
            else:
                return f'{m:02d}:{s:02d}'
        except (ValueError, TypeError):
            return 'N/A'

    def _format_cooldown_bar(self, remaining: int, total: int = 10) -> str:
        total = max(1, total)
        remaining = max(0, remaining)
        elapsed = max(0, total - remaining)
        bar_length = 20
        filled = int(bar_length * elapsed / total)
        bar = '▬' * filled + '⚪' + '—' * max(0, bar_length - filled - 1)
        return f"🕒 `{bar}` `{remaining}s left`"

    def _get_elapsed_time(self, guild_id: int) -> int:
        """
        Calculates the accurate elapsed time of the current track,
        taking into account if the player is currently paused.
        """
        start_time = self._track_start_time.get(guild_id)
        if not start_time:
            return 0
        
        # If currently paused, return time relative to when pause started
        if guild_id in self._pause_start_time:
             return int(self._pause_start_time[guild_id] - start_time)
        
        # Otherwise, return current time minus start time
        return int(time.time() - start_time)

    def _format_progress_bar(self, guild_id: int) -> str:
        """Generates a text-based progress bar for the embed."""
        duration = self._track_duration.get(guild_id, 0)
        elapsed_time = self._get_elapsed_time(guild_id)
        
        if duration <= 0:
            return "🔴 Live Stream / Calculating..."

        percent = min(1.0, elapsed_time / duration)
        
        bar_length = 20 
        filled_length = int(bar_length * percent)
        
        if elapsed_time >= duration:
            bar = '▬' * bar_length
        else:
            bar = '▬' * filled_length + '⚪' + '—' * (bar_length - filled_length - 1)
        
        time_display = f"{self._format_duration(elapsed_time)}/{self._format_duration(duration)}"
        
        status_icon = "⏸️" if guild_id in self._pause_start_time else "▶️"
        
        if elapsed_time >= duration:
             time_display += " (Looping...)"

        return f"{status_icon} `{bar}` `{time_display}`"

    def _build_now_playing_embed(self, guild: discord.Guild) -> discord.Embed:
        """Constructs the rich embed for the /live dashboard."""
        cooldown_until = self._cooldown_until.get(guild.id)
        if cooldown_until and cooldown_until > time.time():
            remaining = int(max(0, cooldown_until - time.time()))
            embed = Embed(
                title="⏳ Restarting Playlist",
                description=f"Cooldown in progress. Restarting in {remaining}s.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Cooldown", value=self._format_cooldown_bar(remaining), inline=False)
            last_track = self.current_track_info.get(guild.id)
            if last_track:
                embed.add_field(name="Last Track", value=last_track.get('title', 'Unknown'), inline=False)
            embed.set_footer(text="Loop resumes after cooldown.")
            return embed

        track_data = self.current_track_info.get(guild.id)
        
        if not track_data:
             embed = Embed(
                title="🎧 Music Player",
                description="**Status:** Initializing / Waiting for track...",
                color=discord.Color.orange()
             )
             return embed

        # Determine source label
        if track_data.get("is_cached"):
            source_label = "📂 Local Cache (Zero Latency)"
            color = discord.Color.green()
        else:
            source_label = "🌐 Direct Stream (Background Downloading...)"
            color = discord.Color.blue()
        
        if guild.id in self._pause_start_time:
            color = discord.Color.red()
            status_text = "PAUSED"
        else:
            status_text = "PLAYING"

        embed = Embed(
            title=f"🔊 Now Playing: Track #{track_data.get('number', 0)}",
            description=f"**Title:** `{track_data.get('title', 'Unknown')}`\n**Status:** {status_text}",
            color=color
        )
        
        embed.add_field(name="Source Type", value=source_label, inline=False)
        embed.add_field(name="Playback Progress", value=self._format_progress_bar(guild.id), inline=False)
        
        artwork_url = track_data.get("artwork")
        if artwork_url and artwork_url.startswith("http"):
            embed.set_thumbnail(url=artwork_url)
        elif guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        total_dur = self._format_duration(track_data.get('duration'))
        embed.set_footer(text=f"Deep Dey's Jukebox System • Total Duration: {total_dur}")
        
        return embed

    async def _refresh_live_message(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        message = self._now_playing_message.get(guild_id)
        if not guild or not message:
            return
        try:
            await message.edit(embed=self._build_now_playing_embed(guild))
        except Exception:
            pass

    async def _update_now_playing_message(self, guild_id: int):
        """
        A background loop that updates the /live message every few seconds
        to create a real-time progress bar effect.
        """
        await asyncio.sleep(2) # Initial buffer
        
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        
        print(f"[DASHBOARD] Started live updates for Guild ID: {guild_id}")

        update_interval = 30
        while True:
            # Update frequency: 30 seconds to avoid rate limits
            await asyncio.sleep(update_interval)
            
            # 1. Check Connection Integrity
            if not self.voice_client or not self.voice_client.is_connected():
                if guild_id in self._now_playing_task:
                    print("[DASHBOARD] Bot disconnected. Stopping updates.")
                    break 
            
            # 2. Get the message object
            message = self._now_playing_message.get(guild_id)
            if not message:
                break

            # 3. Rebuild Embed with Fresh Data
            new_embed = self._build_now_playing_embed(guild)
            
            # 4. Attempt Edit
            try:
                await message.edit(embed=new_embed)
            except discord.NotFound:
                print("[DASHBOARD] Message deleted by user. Stopping updates.")
                break 
            except discord.Forbidden:
                print("[DASHBOARD] Missing permissions to edit message.")
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(5)
                    continue
                print(f"[DASHBOARD] Update error: {e}")
            except Exception as e:
                print(f"[DASHBOARD] Update error: {e}")
                # Continue loop despite minor errors

    # =========================================================================
    # CORE PLAYER LOOP (THE ENGINE)
    # =========================================================================

    def _ensure_player(self):
        """Checks if the player loop is running, if not, starts it."""
        if not self._player_task or self._player_task.done():
            self._player_task = self.bot.loop.create_task(self._player_loop())
            print("[PLAYER ENGINE] Player task started.")

    async def _player_loop(self):
        """
        The main infinite loop that processes the queue and plays audio.
        Handles the Hybrid Logic (Cache vs Stream).
        """
        track_count = 0
        if self._recovery_state and self._recovery_state.get("track_number"):
            try:
                track_count = max(0, int(self._recovery_state.get("track_number", 1)) - 1)
            except Exception:
                track_count = 0
        self._recovery_state = None
        
        while True:
            try:
                # --- Step 1: Queue Management ---
                if not self.queue:
                    if self._playlist_cache:
                        guild_id = None
                        if self.voice_client and self.voice_client.channel and self.voice_client.channel.guild:
                            guild_id = self.voice_client.channel.guild.id

                        if guild_id:
                            cooldown_until = time.time() + 10
                            self._cooldown_until[guild_id] = cooldown_until
                            await self._refresh_live_message(guild_id)
                            print("[PLAYER ENGINE] Playlist finished. Entering cooldown before restart.")
                            while time.time() < cooldown_until:
                                if not self.voice_client or not self.voice_client.is_connected():
                                    break
                                await asyncio.sleep(1)
                            self._cooldown_until.pop(guild_id, None)
                            await self._refresh_live_message(guild_id)

                        self.queue = list(self._playlist_cache)
                        track_count = 0 
                        print("[PLAYER ENGINE] Playlist reset to beginning.")
                        continue
                    else:
                        print("[PLAYER ENGINE] Critical: No tracks available. Waiting 60s...")
                        await asyncio.sleep(60)
                        continue

                # --- Step 2: Get Track Data ---
                item_data = self.queue.pop(0)
                item_url = item_data["url"]
                
                # Extract filename from URL (e.g., "song.mp3")
                filename = item_url.split('/')[-1]
                file_path = os.path.join(MUSIC_CACHE_DIR, filename)
                
                track_count += 1
                
                # --- Step 3: Check Voice Connection ---
                if not self.voice_client or not self.voice_client.is_connected():
                    # If disconnected, push track back to queue and wait
                    self.queue.insert(0, item_data) 
                    track_count -= 1 
                    print("[PLAYER ENGINE] Voice client not connected. Waiting...")
                    await asyncio.sleep(10)
                    continue

                guild = self.voice_client.channel.guild
                
                # --- Step 4: Hybrid Source Selection (THE FIX) ---
                source_to_play = None
                is_cached = False
                ffmpeg_options_extra = ""

                # Condition: File exists AND has valid size (> 1KB)
                if os.path.exists(file_path) and os.path.getsize(file_path) > 1024:
                    # >>> PATH A: PLAY FROM LOCAL CACHE
                    print(f"[PLAYER ENGINE] ✅ Cache Hit! Playing local file: {filename}")
                    source_to_play = file_path
                    is_cached = True
                else:
                    # >>> PATH B: STREAM + BACKGROUND DOWNLOAD
                    print(f"[PLAYER ENGINE] 🌐 Cache Miss. Streaming URL: {filename}")
                    print(f"[PLAYER ENGINE] 🚀 Triggering background download task...")
                    
                    # 1. Start download independently (Fire and Forget)
                    self.bot.loop.create_task(self._download_track_background(item_url, filename))
                    
                    # 2. Set source to URL
                    source_to_play = item_url
                    is_cached = False
                    
                    # 3. Important: Add Reconnect options for FFmpeg streaming stability
                    ffmpeg_options_extra = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

                # --- Step 5: Start Playback ---
                try:
                    # Reset Pause State
                    if guild.id in self._pause_start_time:
                        del self._pause_start_time[guild.id]

                    # FFmpeg Setup with optional resume seek
                    seek_position = self._resume_at.pop(guild.id, 0)
                    max_seek = max(item_data.get("duration", 0) - 1, 0)
                    seek_position = max(0, min(seek_position, max_seek))
                    before_opts = ffmpeg_options_extra.strip()
                    if seek_position > 0:
                        before_opts = f"-ss {seek_position} {before_opts}".strip()

                    player = discord.FFmpegPCMAudio(
                        source_to_play,
                        before_options=before_opts if before_opts else None,
                        options='-vn'
                    )
                    
                    # Volume Transformer
                    audio_source = discord.PCMVolumeTransformer(player, volume=self.volume)

                    # Update Tracking Variables
                    item_data["number"] = track_count
                    item_data["is_cached"] = is_cached
                    
                    self._track_start_time[guild.id] = time.time() - seek_position
                    self._track_duration[guild.id] = item_data["duration"]
                    self.current_track_info[guild.id] = item_data

                    # Persist state for auto-recovery
                    state_payload = {
                        "voice_channel_id": self.voice_client.channel.id if self.voice_client and self.voice_client.channel else None,
                        "announce_channel_id": self._announce_channel.get(guild.id).id if self._announce_channel.get(guild.id) else None,
                        "track_number": track_count,
                        "track_url": item_data.get("url"),
                        "track_title": item_data.get("title"),
                        "playlist_index": item_data.get("origin_index", 0),
                        "duration": item_data.get("duration"),
                        "position": seek_position,
                        "status": "playing"
                    }
                    try:
                        await db.DB.set_music_state(guild.id, state_payload)
                    except Exception as e:
                        print(f"[PLAYER ENGINE] Failed to persist music state: {e}")
                    
                    # Play Audio
                    self.voice_client.play(audio_source, after=lambda e: print(f'[PLAYER ERROR] {e}') if e else None)
                    
                    # Update Bot Presence
                    track_title = item_data.get("title", f"Track #{track_count}")
                    await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{track_title}"))

                    # --- [NEW] UPDATE VOICE CHANNEL STATUS ---
                    # This block updates the voice channel's status text to the current song name.
                    if self.voice_client and self.voice_client.channel:
                        try:
                            # Create status string (e.g. "🎵 Track Name")
                            status_text = f"🎵 {track_title}"
                            # Discord has a limit for status length (safe limit around 500 chars)
                            if len(status_text) > 480:
                                status_text = status_text[:475] + "..."
                            
                            await self.voice_client.channel.edit(status=status_text)
                            print(f"[STATUS] Updated VC Status to: {status_text}")
                        except discord.Forbidden:
                            print("[STATUS] ❌ Missing 'Manage Channels' permission to update VC Status.")
                        except Exception as e:
                            print(f"[STATUS] ❌ Error updating VC Status: {e}")
                    # ----------------------------------------

                    # --- Step 6: Wait for Track to Finish ---
                    last_state_sync = time.time()
                    while self.voice_client.is_playing() or self.voice_client.is_paused():
                        await asyncio.sleep(1)
                        if time.time() - last_state_sync >= 10:
                            last_state_sync = time.time()
                            position = self._get_elapsed_time(guild.id)
                            try:
                                await db.DB.set_music_state(guild.id, {
                                    "voice_channel_id": self.voice_client.channel.id if self.voice_client and self.voice_client.channel else None,
                                    "announce_channel_id": self._announce_channel.get(guild.id).id if self._announce_channel.get(guild.id) else None,
                                    "track_number": track_count,
                                    "track_url": item_data.get("url"),
                                    "track_title": item_data.get("title"),
                                    "playlist_index": item_data.get("origin_index", 0),
                                    "duration": item_data.get("duration"),
                                    "position": position,
                                    "status": "paused" if guild.id in self._pause_start_time else "playing"
                                })
                            except Exception as e:
                                print(f"[PLAYER ENGINE] Failed to update music state: {e}")

                    print(f"[PLAYER ENGINE] Finished Track #{track_count}")
                    self._resume_at.pop(guild.id, None)
                    
                except Exception as e:
                    print(f"[PLAYER ENGINE] ❌ Fatal Playback Error: {e}")
                    await asyncio.sleep(2)

            except Exception as e:
                print(f'[PLAYER ENGINE] Loop Exception: {e}')
                await asyncio.sleep(5) 

    # =========================================================================
    # COMMANDS
    # =========================================================================

    @commands.hybrid_command(name='start', description='Starts the music bot in your voice channel.')
    async def start(self, ctx: commands.Context):
        """
        Command: /start
        Connects to the user's voice channel and begins the playback loop.
        """
        await ctx.defer() 
        
        # Check if user is in a voice channel
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send('❌ You must be in a voice channel to use this command.')
        
        channel = ctx.author.voice.channel

        # Initialize Cache if empty
        if not self._playlist_cache:
            self._load_playlist_cache()

        # Connect or Move Voice Client
        if self.voice_client is None or not self.voice_client.is_connected():
            try:
                self.voice_client = await channel.connect()
                print(f"[COMMAND] Connected to voice channel: {channel.name}")
            except Exception as e:
                return await ctx.send(f"❌ Failed to connect to voice channel: {e}")
        else:
            try:
                await self.voice_client.move_to(channel)
                print(f"[COMMAND] Moved to voice channel: {channel.name}")
            except Exception as e:
                 return await ctx.send(f"❌ Failed to move to voice channel: {e}")

        await ctx.send(f'✅ Joined **{channel.name}**. Starting QuickLink Hybrid Player (Stream+Cache).')
        
        # Prefer the voice channel's own message area for live updates
        announce_ch = self._get_voice_chat_channel(channel)
        if announce_ch is None:
            announce_ch = ctx.channel
        self._announce_channel[ctx.guild.id] = announce_ch

        # Start the engine and auto-deploy live dashboard in the voice chat
        self._ensure_player()
        self.bot.loop.create_task(self.auto_post_live(ctx.guild, channel))


    @commands.hybrid_command(name='queue', description='Show the next songs in the queue with paging controls.')
    async def show_queue(self, ctx: commands.Context):
        """Display the next tracks (5 per page) with buttons to page and view details."""
        snapshot = list(self.queue)
        if not snapshot:
            return await ctx.send('📭 Queue is empty. Use /start to begin playback.', ephemeral=True)

        page_size = 5

        class QueueView(discord.ui.View):
            def __init__(self, outer: 'Music', tracks: list, page: int = 0):
                super().__init__(timeout=120)
                self.outer = outer
                self.tracks = tracks
                self.page = page
                self.page_size = page_size
                self.total_pages = max((len(self.tracks) - 1) // self.page_size + 1, 1)
                self._render_buttons()

            def _render_buttons(self):
                self.clear_items()
                start = self.page * self.page_size
                chunk = self.tracks[start:start + self.page_size]
                for idx, track in enumerate(chunk):
                    global_pos = start + idx + 1
                    label = f"{global_pos}."
                    btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=f"queue_item_{global_pos}")

                    async def cb(interaction: discord.Interaction, t=track, pos=global_pos):
                        await self._send_detail(interaction, t, pos)

                    btn.callback = cb
                    self.add_item(btn)

                prev_disabled = self.page <= 0
                next_disabled = self.page >= self.total_pages - 1

                @discord.ui.button(label='⬅️ Prev', style=discord.ButtonStyle.primary, disabled=prev_disabled)
                async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
                    self.page = max(0, self.page - 1)
                    await self._update(interaction)

                @discord.ui.button(label='Next ➡️', style=discord.ButtonStyle.primary, disabled=next_disabled)
                async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
                    self.page = min(self.total_pages - 1, self.page + 1)
                    await self._update(interaction)

            async def _send_detail(self, interaction: discord.Interaction, track: dict, position: int):
                title = track.get('title', f'Track {position}')
                dur = self.outer._format_duration(track.get('duration'))
                url = track.get('url', 'N/A')
                artwork = track.get('artwork')
                detail = Embed(title=f"{title}", description=f"**Position:** {position}\n**Duration:** {dur}\n**Link:** {url}", color=discord.Color.green())
                if artwork:
                    detail.set_thumbnail(url=artwork)

                class DismissView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)

                    @discord.ui.button(label='Dismiss', style=discord.ButtonStyle.danger)
                    async def dismiss(self, interaction_inner: discord.Interaction, button: discord.ui.Button):
                        try:
                            await interaction_inner.message.delete()
                        except Exception:
                            pass

                await interaction.response.send_message(embed=detail, ephemeral=False, view=DismissView())

            def build_embed(self):
                start = self.page * self.page_size
                chunk = self.tracks[start:start + self.page_size]
                lines = []
                for i, track in enumerate(chunk, start=start + 1):
                    title = track.get('title', f'Track {i}')
                    dur = self.outer._format_duration(track.get('duration'))
                    lines.append(f"**{i}.** {title} — {dur}")
                desc = '\n'.join(lines) if lines else 'No items on this page.'
                embed = Embed(title='🎵 Queue', description=desc, color=discord.Color.blurple())
                embed.set_footer(text=f'Page {self.page + 1}/{self.total_pages}')
                return embed

            async def _update(self, interaction: discord.Interaction):
                # Recreate buttons to reflect enabled/disabled state
                self._render_buttons()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

        view = QueueView(self, snapshot, 0)
        await ctx.send(embed=view.build_embed(), view=view)


    @commands.hybrid_command(name='live', description='Shows a real-time updating status of the music playing.')
    async def live(self, ctx: commands.Context):
        """
        Command: /live
        Posts/edits in the channel where the command was used (if allowed), else falls back to VC chat.
        """
        try:
            await ctx.defer(ephemeral=False)
        except Exception:
            pass

        guild = ctx.guild
        vc = None
        if self.voice_client and self.voice_client.is_connected():
            vc = self.voice_client.channel
        elif ctx.author and getattr(ctx.author, "voice", None):
            vc = ctx.author.voice.channel
        if not vc or not isinstance(vc, discord.VoiceChannel):
            return await ctx.send("❌ Bot is not connected to a voice channel. Use `/start` first.")

        guild_id = guild.id
        # Always use the channel where /live was invoked so the response appears there.
        # If for some reason ctx.channel is not sendable, fall back to VC chat area.
        announce_target = ctx.channel
        if not self._is_sendable_channel(announce_target):
            announce_target = self._get_voice_chat_channel(vc) or self._choose_announce_channel(guild, vc, ctx.channel)
        if announce_target is None:
            return await ctx.send("❌ No suitable channel to post live dashboard.")

        # Clean up existing task for this guild to prevent duplicate updates
        if guild_id in self._now_playing_task:
            try:
                self._now_playing_task[guild_id].cancel()
            except Exception:
                pass

        # If a live message exists, edit it; otherwise, send a new one
        initial_embed = self._build_now_playing_embed(guild)
        sent_message = None
        old_message = self._now_playing_message.get(guild_id)
        if old_message:
            try:
                await old_message.edit(embed=initial_embed)
                sent_message = old_message
            except Exception:
                try:
                    sent_message = await announce_target.send(embed=initial_embed)
                except Exception as e:
                    return await ctx.send(f"❌ Failed to send live dashboard: {e}")
        else:
            try:
                sent_message = await announce_target.send(embed=initial_embed)
            except Exception as e:
                return await ctx.send(f"❌ Failed to send live dashboard: {e}")

        # Register the message and start the update loop
        self._announce_channel[guild_id] = announce_target
        self._now_playing_message[guild_id] = sent_message
        self._now_playing_task[guild_id] = self.bot.loop.create_task(
            self._update_now_playing_message(guild_id)
        )
        try:
            vc_name = vc.name if vc else 'Unknown VC'
            ch_name = getattr(announce_target, 'name', 'unknown-channel')
            print(f"[COMMAND] /live dashboard initialized for Guild {guild_id} in #{ch_name} (vc: {vc_name})")
        except Exception:
            print(f"[COMMAND] /live dashboard initialized for Guild {guild_id}")

    async def auto_post_live(self, guild: discord.Guild, voice_channel: discord.VoiceChannel):
        """
        Automatically post or edit the /live dashboard in the correct VC message area when joining a VC.
        """
        announce_target = self._get_live_message_channel(voice_channel) or self._choose_announce_channel(guild, voice_channel)
        if not announce_target:
            print(f"[AUTO-LIVE] No suitable channel to post live dashboard for guild {guild.id}")
            return
        initial_embed = self._build_now_playing_embed(guild)
        sent_message = None
        old_message = self._now_playing_message.get(guild.id)
        if old_message:
            try:
                await old_message.edit(embed=initial_embed)
                sent_message = old_message
            except Exception:
                try:
                    sent_message = await announce_target.send(embed=initial_embed)
                except Exception as e:
                    print(f"[AUTO-LIVE] Failed to send live dashboard: {e}")
                    return
        else:
            try:
                sent_message = await announce_target.send(embed=initial_embed)
            except Exception as e:
                print(f"[AUTO-LIVE] Failed to send live dashboard: {e}")
                return
        self._announce_channel[guild.id] = announce_target
        self._now_playing_message[guild.id] = sent_message
        self._now_playing_task[guild.id] = self.bot.loop.create_task(
            self._update_now_playing_message(guild.id)
        )
        print(f"[AUTO-LIVE] Live dashboard auto-posted/edited for Guild {guild.id} in #{getattr(announce_target, 'name', 'unknown')}")


    @commands.hybrid_command(name='leave', description='Stops music and disconnects the bot (Bot Owner only).')
    async def leave(self, ctx: commands.Context):
        """
        Command: /leave
        Disconnects the bot and cleans up tasks. Restricted to Bot Owner.
        """
        # Help avoid slash timeouts while we resolve target channel
        try:
            await ctx.defer(ephemeral=False)
        except Exception:
            pass
        if not await self.bot.is_owner(ctx.author):
            return await ctx.send("🔒 Only my owner can make me leave.")

        if self.voice_client:
            # Capture channel BEFORE disconnecting to clear status
            channel = self.voice_client.channel
            
            # Stop audio
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
            
            # Cancel Dashboard Tasks
            if ctx.guild.id in self._now_playing_task:
                 self._now_playing_task[ctx.guild.id].cancel()
                 del self._now_playing_task[ctx.guild.id] 
            
            # Remove Message Reference
            if ctx.guild.id in self._now_playing_message:
                 del self._now_playing_message[ctx.guild.id]

            # Disconnect
            await self.voice_client.disconnect()
            self.voice_client = None
            
            # Reset Activity
            try:
                await self.bot.change_presence(activity=None)
            except Exception as e:
                 print(f"[MUSIC] Failed to clear presence: {e}")
            
            # --- [NEW] Clear Voice Channel Status ---
            if channel:
                try:
                    await channel.edit(status=None)
                    print("[STATUS] Cleared VC Status on leave.")
                except Exception as e:
                    print(f"[STATUS] Failed to clear status: {e}")
            # ----------------------------------------

            try:
                await db.DB.clear_music_state(ctx.guild.id)
            except Exception:
                pass

            await ctx.send('👋 Disconnected by owner. Sessions cleaned.')
        else:
             await ctx.send("❌ Not connected to a voice channel.")

    @commands.hybrid_command(name='pause', description='Pauses the music (Bot Owner only).')
    async def pause(self, ctx: commands.Context):
        """
        Command: /pause
        Pauses playback and records the timestamp for accurate resume timing.
        """
        if not await self.bot.is_owner(ctx.author):
            return await ctx.send("🔒 Only my owner can pause the music.")


        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            self._pause_start_time[ctx.guild.id] = time.time()
            await ctx.send('⏸️ Music paused.')
        elif self.voice_client and self.voice_client.is_paused():
            await ctx.send("⚠️ Music is already paused.")
        else:
            await ctx.send("⚠️ Nothing is playing to pause.")


    @commands.hybrid_command(name='skip', description='Skips the current track.')
    async def skip(self, ctx: commands.Context):
        """
        Command: /skip
        Forces the audio player to stop, which triggers the loop to fetch the next song.
        """
        if not self.voice_client or not self.voice_client.is_playing():
             return await ctx.send("⚠️ Not playing anything to skip.")
             
        # Guard clause: Don't skip if currently buffering/loading
        if self.voice_client.is_paused() and not self.voice_client.source:
             return await ctx.send("❌ Cannot skip while audio is loading.")
             
        self.voice_client.stop()
        await ctx.send('⏩ Track skipped.')


    @commands.hybrid_command(name='volume', description='Sets the music volume (0-200).')
    async def volume_cmd(self, ctx: commands.Context, vol: int):
        """
        Command: /volume <0-200>
        Adjusts playback volume. Applies immediately to current track if supported.
        """
        if not (0 <= vol <= 200):
            return await ctx.send('❌ Volume must be between 0 and 200.')

        self.volume = vol / 100.0
        
        # Apply to current source if it exists and is a transformer
        if self.voice_client and self.voice_client.source:
            if isinstance(self.voice_client.source, discord.PCMVolumeTransformer):
                self.voice_client.source.volume = self.volume
                await ctx.send(f'🔊 Volume set to **{vol}%**.')
            else:
                 await ctx.send(f'⚠️ Volume set to **{vol}%** (Applies to next track).')
        elif self.voice_client:
             await ctx.send(f'🔊 Volume set to **{vol}%** (Applies to next track).')
        else:
             await ctx.send("❌ Not connected to a voice channel.")


    @commands.hybrid_command(name='nowplaying', description='Shows details about the currently playing song.')
    async def nowplaying(self, ctx: commands.Context):
        """
        Command: /nowplaying
        One-time command to show current song status (Non-updating).
        """
        guild_id = ctx.guild.id if ctx.guild else None
        if not guild_id:
            return await ctx.send("This command can only be used in a server.")

        track_data = self.current_track_info.get(guild_id)

        if not self.voice_client or not (self.voice_client.is_playing() or self.voice_client.is_paused()) or not track_data:
            return await ctx.send('❌ Not playing anything right now.')

        embed = self._build_now_playing_embed(ctx.guild)
        await ctx.send(embed=embed)


async def setup(bot):
    # Remove any existing app commands for music cog to force re-sync
    try:
        for cmd in list(bot.tree.get_commands()):
            if getattr(cmd, "cog_name", None) == "Music":
                bot.tree.remove_command(cmd.name)
    except Exception:
        pass
    await bot.add_cog(Music(bot))
