import asyncio
import hashlib
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import requests
import yt_dlp

DEFAULT_ARTWORK = "https://deydeep-static-files.hf.space/f/ncs"
CDN_BASE_URL = "https://deydeep-static-files.hf.space"
CDN_API_URL = f"{CDN_BASE_URL}/api/rest"
TMP_DIR = (Path(__file__).resolve().parent.parent / "tmp").resolve()

YTDLP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def build_slug(name: str) -> str:
    sanitized = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-").replace("_", "-"))
    sanitized = sanitized[:60].strip("-")
    suffix = secrets.token_hex(3)
    return f"{sanitized}-{suffix}" if sanitized else suffix


def ensure_tmp_dir() -> Path:
    tmp_dir = TMP_DIR
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def _run_ffmpeg_96k(input_file: Path, output_file: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "96k",
        str(output_file),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


async def convert_to_96k_mp3(input_file: Path, output_name: str | None = None) -> Path:
    loop = asyncio.get_event_loop()
    tmp_dir = ensure_tmp_dir()
    stem = output_name or hashlib.md5(str(input_file).encode()).hexdigest()
    output_file = tmp_dir / f"{stem}.mp3"
    await loop.run_in_executor(None, _run_ffmpeg_96k, input_file, output_file)
    return output_file


async def extract_from_url(url: str) -> tuple[Path, str, str]:
    tmp_dir = ensure_tmp_dir()
    uid = secrets.token_hex(8)
    raw_template = str(tmp_dir / f"{uid}.%(ext)s")

    def _download() -> tuple[Path, str, str]:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": raw_template,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "quiet": True,
            "no_warnings": True,
            "http_headers": YTDLP_HEADERS,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title") or "Untitled Track"
            artwork = info.get("thumbnail") or DEFAULT_ARTWORK

        raw_candidates = sorted(tmp_dir.glob(f"{uid}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not raw_candidates:
            raise FileNotFoundError("Downloaded media not found")
        raw_file = raw_candidates[0]
        return raw_file, title, artwork

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download)


async def upload_to_cdn(file_path: Path, title: str, space_password: str) -> str:
    slug = build_slug(title)
    filename = f"{slug}.mp3"

    headers = {
        "Authorization": f"Bearer {space_password}",
        "password": space_password,
    }
    with open(file_path, "rb") as fh:
        files = {"file": (filename, fh, "audio/mpeg")}
        data = {"password": space_password}
        response = requests.post(CDN_API_URL, headers=headers, data=data, files=files, timeout=180)

    if response.status_code not in (200, 201):
        raise RuntimeError(f"CDN upload failed: {response.status_code} {response.text}")

    return f"{CDN_BASE_URL}/f/{filename}"


def cleanup_path(path: Path) -> None:
    try:
        tmp_root = ensure_tmp_dir().resolve()
        resolved = path.resolve()
        if not str(resolved).startswith(str(tmp_root)):
            return
        if resolved.exists() and resolved.is_file():
            resolved.unlink()
    except Exception:
        pass


def cleanup_tree(path: Path) -> None:
    try:
        tmp_root = ensure_tmp_dir().resolve()
        resolved = path.resolve()
        if not str(resolved).startswith(str(tmp_root)):
            return
        if resolved.exists() and resolved.is_dir():
            shutil.rmtree(resolved)
    except Exception:
        pass
