# Discord Top User Bot

A production-focused Discord bot with:

- Activity leaderboard tracking
- Setup/admin slash commands
- Music engine with caching, CDN upload, and live dashboard

## Features

- Automated leaderboard reset cycle
- JSON/HTML backup logs
- Reward role handling for top members
- Voice/music commands (`/music ...`)
- 24/7 music mode controls

## Requirements

- Python 3.10+
- MongoDB URI
- Discord bot token
- FFmpeg (required for voice/music)

## Environment Variables

- `DISCORD_TOKEN` — Discord bot token
- `MONGO_URI` — MongoDB connection URI
- `PASSWORD` — Shared admin password for secure dashboards and Discord modals
- `SPACE_PASSWORD` *(optional at runtime for `/music add` flow; entered securely via modal)*

## Local Setup

```bash
pip install -r requirements.txt
python main.py
```

## Deployment Guide

### Deploy with Docker

> This repository intentionally uses `Docerfile` (name kept as-is).

```bash
docker build -f Docerfile -t discord-top-user .
docker run -e DISCORD_TOKEN=your_token -e MONGO_URI=your_mongo_uri discord-top-user
```

### Deploy on Render (or similar)

1. Create a new service from this repository.
2. Ensure build uses `Docerfile`.
3. Set environment variables:
   - `DISCORD_TOKEN`
   - `MONGO_URI`
   - `PASSWORD`
   - `SPACE_PASSWORD`
4. Deploy.

## Commands

### Setup

- `/setup channel`
- `/setup logs`
- `/setup role`
- `/setup days`
- `/setup top_count`
- `/setup reset`
- `/setup hard_reset`
- `/setup ping`

### Music

- `/music help`
- `/music join`
- `/music leave`
- `/music add`
- `/music start`
- `/music temp <link>`
- `/music pause`
- `/music resume`
- `/music nowplaying`
- `/music live`
- `/music 247`

### Utilities

- `/now`
- `/weather <city>`
- `/links`
- `/pomodoro [minutes]`

### Game Admin

- `/game add tad`
- `/game add quiz`
- `/game add autoreply`
- `/game send message <target_channel>`

## Project Structure

```text
cogs/
  setup_commands.py
  music_commands.py
  game_commands.py
  utility_commands.py
audio_manager.py
keep_alive.py
telemetry.py
database.py
main.py
Docerfile
```

## Community & Policies

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security Policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [License](LICENSE)
