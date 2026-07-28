# Discord-Top-User

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Discord](https://img.shields.io/badge/Discord-Bot-5865F2?logo=discord&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![CI](https://img.shields.io/github/actions/workflow/status/deepdeyiitgn/Discord-Top-User/main.yml?label=CI)
![MongoDB](https://img.shields.io/badge/Database-MongoDB-47A248?logo=mongodb&logoColor=white)

Production-ready Discord ecosystem by **[Deep Dey](https://deepdey.vercel.app/)** with modular cogs for leaderboard automation, music administration, game engines, utility workflows, telemetry, and web dashboard tooling.

## Project Overview

Discord-Top-User is a full bot platform that combines:

- **Leaderboard Engine**: tracks message activity and computes top members.
- **Music V2 Engine**: track management + streaming playback + admin dashboard.
- **Gamification Suite**: interactive games, auto-games, quiz/TAD, and points.
- **Utilities Admin**: keyword/auto-reply + moderation-friendly content tools.
- **Digital Proxy Status Engine**: status profiles, alias detection, missed-pings vault.
- **Telemetry Pipeline**: centralized error/activity/game/security reporting.
- **Web Discovery Dashboard**: searchable public server listing + bot stats.

## Key Features

### 1) Leaderboard
- Guild settings for cycle timing, top count, channels, and role rewards.
- Auto and manual resets with JSON/HTML backup exports.
- Role re-assignment for current cycle winners.

### 2) Music
- Music command group with join/start/now-playing/queue controls.
- Track management via Discord modals and secure web dashboard.
- FFmpeg-backed playback and persistent session behavior.

### 3) Games
- Fast interaction games (e.g., scramble/math/toss/RPS/TTT/quiz/guessing).
- Auto-game scheduler with role pinging and instant winner resolution.
- Points + profile rank updates with telemetry logging.
- `/myprofile` shows total points, join time, member status, global/server ranking, and Pomodoro snapshot.

### 4) Utilities
- Keyword auto-reply and managed content administration.
- Utility commands for productivity and common server workflows.
- Secure dashboard routes protected by password session auth.

### 5) Productivity (AFK + Pomodoro)
- Deep-work profile management with configurable work/break cycles.
- `/pomodoro status` returns live session state, remaining/focused time, and latest update timestamps.

### 6) Security & Reliability
- Password-gated sensitive actions.
- Telemetry-backed exception logging.
- API safety wrappers and resilient fallback behavior.

### 7) Ticket System
- Full support ticket workflow with admin-only setup commands.
- Dynamic categories with up to 7 custom form questions per category.
- Public panel embed with category dropdown and modal forms.
- Claim / unclaim flow with role-aware channel permissions.
- HTML, JSON, and TXT transcript export on close/delete.
- Ticket logs channel, auto-close on inactivity, priority, tags, and staff notes.
- Post-close rating via DM.

---

## Quick Start (Local Setup)

### Prerequisites
- Python **3.10+**
- MongoDB connection URI
- FFmpeg installed on host
- Discord bot application + token

### Install & Run

```bash
cd /path/to/Discord-Top-User
pip install -r requirements.txt
python main.py
```

---

## Deployment Guide (Render with `Docerfile`)

> This repository intentionally uses `Docerfile` (spelling preserved in project).

### Render Web Service Steps

1. Push repository to GitHub.
2. In Render, create a **New Web Service** from this repository.
3. Configure service to build using Docker and point to **`Docerfile`**.
4. Set environment variables from the list below.
5. Deploy and monitor logs (Docker now starts both `main.py` and `website.py` together).

### API runtime compatibility

- Render runtime uses `PORT` automatically for the Flask API server.
- `api/index.py` is kept as the serverless-compatible API entrypoint (`from keep_alive import app`).
- API endpoints, request shapes, and response formats remain unchanged.

### Optional local Docker check

```bash
docker build -f Docerfile -t discord-top-user .
docker run --env-file .env discord-top-user
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `MONGO_URI` | Yes | MongoDB connection URI |
| `PASSWORD` | Yes | Shared secure password for admin actions/modal gates |
| `SPACE_PASSWORD` | Yes (music upload workflows) | Credential used for CDN upload operations |

Optional deployment environment tuning can be added by your host/provider.

---

## Repository Structure

```text
cogs/
  game_commands.py
  music_commands.py
  productivity_commands.py
  proxy.py
  setup_commands.py
  ticket_commands.py      # Ticket system cog (NEW)
  utility_commands.py
utils/
  audio_manager.py
  branding_view.py
  discord_resilience.py
data/
  transcripts/            # Local transcript storage (auto-created, gitignored)
main.py
keep_alive.py
database.py
telemetry.py
Docerfile
.github/
```

---

## Ticket System Setup

All setup commands require **Administrator** permission.

### 1) Configure base settings

```text
/ticket config set ticket_category:#Tickets logs_channel:#ticket-logs auto_close_hours:72
```

### 2) Add categories

```text
/ticket category add key:support name:General Support description:Help with general issues emoji:🎫 staff_roles:@Support @Mod
```

Add form questions (up to 7) when editing a category:

```text
/ticket category edit key:support question_json:[{"title":"Issue","description":"Describe your problem","placeholder":"I need help with...","required":true,"style":"short"},{"title":"Details","description":"Extra context","placeholder":"...","required":false,"style":"paragraph"}]
```

### 3) Create the public panel

```text
/ticket panel create channel:#open-ticket title:Support Center description:Select a category below to open a ticket.
```

Use `/ticket panel update` after changing categories or panel text.

### 4) Ticket channel commands (inside a ticket)

| Command | Who | Description |
|---|---|---|
| `/ticket claim` | Staff | Claim ticket (only claimant + owner can talk) |
| `/ticket unclaim` | Claimant/Admin | Release claim back to all staff |
| `/ticket close` | Owner/Staff | Close + generate transcript |
| `/ticket delete` | Staff/Admin | Delete channel (transcript first if open) |
| `/ticket reopen` | Staff | Reopen a closed ticket |
| `/ticket adduser` | Owner/Staff | Add a member to the ticket |
| `/ticket removeuser` | Staff | Remove a member |
| `/ticket lock` / `unlock` | Staff | Lock/unlock sending |
| `/ticket rename` | Owner/Staff | Rename ticket channel |
| `/ticket transcript` | Owner/Staff | Export HTML/JSON/TXT |
| `/ticket priority` | Staff | Set low/normal/high/urgent |
| `/ticket tag` | Staff | Add/remove tags |
| `/ticket note` | Staff | Internal staff note |

Ticket channels are named: `{username}-{ticket_id}-{category}` (sanitized for Discord limits).

Transcripts are saved locally under `data/transcripts/{guild_id}/` and only metadata/paths are stored in MongoDB.

---

## Community & Maintenance

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security Policy](SECURITY.md)
- [License](LICENSE)
- [Issue Templates](.github/ISSUE_TEMPLATE)

---

## Branding

Built and maintained by **[Deep Dey](https://deepdey.vercel.app/)**.

- Portfolio: https://deepdey.vercel.app/
- Instagram: https://deepdey.vercel.app/insta
- Contact: https://deepdey.vercel.app/contact
