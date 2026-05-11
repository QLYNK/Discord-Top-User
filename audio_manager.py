"""
audio_manager.py — Smart Downloader, 96kbps LRU Cache & CDN Uploader
for the Discord Music Engine.
"""

import asyncio
import hashlib
import re
import secrets

import aiohttp
import yt_dlp
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

TMP_DIR = Path("./tmp")
TMP_DIR.mkdir(parents=True, exist_ok=True)

MAX_CACHE_BYTES = 900 * 1024 * 1024  # 900 MB hard limit

CDN_BASE_URL = "https://deydeep-static-files.hf.space"
CDN_API_URL = f"{CDN_BASE_URL}/api/rest"

BYPASS_DOMAINS = (
    "catbox.moe",
    "qlynk.me",
    "static.qlynk.me",
    "deydeep-static-files.hf.space",
)

_YTDLP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_YTDLP_POSTPROCESSORS = [
    {
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "96",
    }
]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _url_hash(url: str) -> str:
    """Returns a stable MD5 hex digest of the URL (used as cache filename key)."""
    return hashlib.md5(url.encode()).hexdigest()


def get_dir_size() -> int:
    """Returns the total byte size of all .mp3 files in TMP_DIR."""
    total = 0
    for f in TMP_DIR.glob("*.mp3"):
        try:
            total += f.stat().st_size
        except FileNotFoundError:
            pass
    return total


def purge_lru_cache() -> None:
    """
    Evicts the least-recently-used .mp3 files from TMP_DIR until the
    directory is below MAX_CACHE_BYTES.
    """
    mp3_files = list(TMP_DIR.glob("*.mp3"))
    # Sort ascending by last-access time (oldest first)
    mp3_files.sort(key=lambda f: f.stat().st_atime)
    while get_dir_size() > MAX_CACHE_BYTES and mp3_files:
        oldest = mp3_files.pop(0)
        try:
            oldest.unlink()
            print(f"[Cache] Evicted {oldest.name}")
        except FileNotFoundError:
            pass


def is_bypass_domain(url: str) -> bool:
    """Returns True if *url* is hosted on a trusted CDN bypass domain."""
    lower_url = url.lower()
    return any(domain in lower_url for domain in BYPASS_DOMAINS)


def build_slug(name: str) -> str:
    """
    Generates a URL-safe slug from *name* with 6 random hex chars appended,
    following the same logic as the JS proxy's `buildSlug`.
    """
    sanitized = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-").replace("_", "-"))
    sanitized = sanitized[:40].strip("-")
    suffix = secrets.token_hex(3)  # 6 hex characters
    return f"{sanitized}-{suffix}" if sanitized else suffix


# ── Downloaders ────────────────────────────────────────────────────────────────


async def download_and_convert(url: str) -> "Path | None":
    """
    Downloads audio via yt-dlp and converts to 96 kbps MP3.
    Supports YouTube, SoundCloud, raw googlevideo URLs, and most public
    media platforms.  Results are cached in TMP_DIR by URL hash.

    Returns the Path to the cached .mp3 on success, or None on failure.
    """
    h = _url_hash(url)
    cached = TMP_DIR / f"{h}.mp3"
    if cached.exists():
        cached.touch()  # refresh atime for LRU tracking
        return cached

    def _run() -> None:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(TMP_DIR / f"{h}.%(ext)s"),
            "nocheckcertificate": True,
            "geo_bypass": True,
            "quiet": True,
            "no_warnings": True,
            "http_headers": _YTDLP_HEADERS,
            "postprocessors": _YTDLP_POSTPROCESSORS,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run)
        if cached.exists():
            purge_lru_cache()
            return cached
    except Exception as exc:
        print(f"[AudioManager] yt-dlp error for {url!r}: {exc}")
    return None


async def download_direct(url: str) -> "Path | None":
    """
    Downloads a direct audio file URL (e.g. CDN .mp3) via aiohttp.
    Results are cached in TMP_DIR by URL hash.

    Returns the Path to the cached .mp3 on success, or None on failure.
    """
    h = _url_hash(url)
    cached = TMP_DIR / f"{h}.mp3"
    if cached.exists():
        cached.touch()
        return cached

    try:
        headers = {"User-Agent": _YTDLP_HEADERS["User-Agent"]}
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    with open(cached, "wb") as fh:
                        async for chunk in resp.content.iter_chunked(65536):
                            fh.write(chunk)
                    purge_lru_cache()
                    return cached
                print(f"[AudioManager] Direct download failed: HTTP {resp.status}")
    except Exception as exc:
        print(f"[AudioManager] Direct download error for {url!r}: {exc}")
    return None


async def smart_download(url: str) -> "Path | None":
    """
    Chooses the correct download strategy:
    - Bypass-domain URLs → direct aiohttp download.
    - Everything else    → yt-dlp with 96 kbps MP3 conversion.
    """
    if is_bypass_domain(url):
        return await download_direct(url)
    return await download_and_convert(url)


# ── CDN Uploader ───────────────────────────────────────────────────────────────


async def upload_to_cdn(file_path: Path, password: str, original_name: str) -> "str | None":
    """
    Uploads *file_path* to deydeep-static-files.hf.space via multipart form.

    Headers mirror the JS proxy logic:
        Authorization: Bearer <password>
        password: <password>   (form field)

    Returns the public CDN URL on success, or None on failure.
    """
    slug = build_slug(original_name)
    filename = f"{slug}.mp3"

    try:
        async with aiohttp.ClientSession() as session:
            with open(file_path, "rb") as fh:
                form = aiohttp.FormData()
                form.add_field("file", fh, filename=filename, content_type="audio/mpeg")
                form.add_field("password", password)

                headers = {"Authorization": f"Bearer {password}"}
                timeout = aiohttp.ClientTimeout(total=180)

                async with session.post(
                    CDN_API_URL, data=form, headers=headers, timeout=timeout
                ) as resp:
                    body = await resp.text()
                    if resp.status in (200, 201):
                        return f"{CDN_BASE_URL}/f/{filename}"
                    print(f"[AudioManager] CDN upload failed: HTTP {resp.status} — {body}")
    except Exception as exc:
        print(f"[AudioManager] CDN upload exception: {exc}")
    return None
