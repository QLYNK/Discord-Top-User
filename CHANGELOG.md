# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- **Ticket system cog** (`cogs/ticket_commands.py`):
  - Admin-only category/panel/config commands
  - Category dropdown panel with modal forms (up to 7 questions, multi-step for 6–7)
  - Claim/unclaim permission flow
  - Close/delete/reopen/lock/unlock/rename/adduser/removeuser/transcript commands
  - HTML, JSON, and TXT transcript generation to `data/transcripts/`
  - Ticket logs channel, auto-close on inactivity, priority, tags, staff notes, post-close rating
- Ticket database collections and helpers in `database.py` (`TicketConfig`, `Tickets`)
- Cog registration in `main.py`
- README ticket setup guide
- `.gitignore` entry for local transcript storage
- Repository documentation set:
  - `README.md`
  - `SECURITY.md`
  - `CONTRIBUTING.md`
  - `CODE_OF_CONDUCT.md`
  - `CHANGELOG.md`
- GitHub community templates and CI files under `.github/`
