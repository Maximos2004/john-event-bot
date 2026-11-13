# John Event 🇳🇱 • Dutch Games Industry Radar

A lightweight, 24/7 Discord bot that tracks upcoming Dutch game development events (conferences, awards, meetups, lectures, network lunches) using the official [Dutch Games Industry REST API](https://www.dutchgamesindustry.nl/api/docs.html) and announces them to Discord with rich embeds.

---

## Features

- **Official REST API (Zero Scraping)**: Uses the official Dutch Games Industry API endpoint (`/api/events`) for reliable, compliant, and structured event retrieval.
- **Rich Colour-Coded Discord Embeds** (green upcoming, orange happening now, red concluded):
  - Event title and official registration/event link.
  - Native **Discord Timestamps** `<t:timestamp:F> (<t:timestamp:R>)` with localized time and dynamic relative countdown.
  - Clickable **Google Maps** links for physical venue addresses.
  - Official event image thumbnails from DGI media storage.
  - Categorization badges (e.g. `Meetup`, `Networking`, `Conference`, `Showcase`).
- **Live Event Status**: Posts update themselves as events progress (checked every 10 minutes, using saved data only — no extra API calls):
  - 🟢 **Upcoming** — green, date with countdown.
  - 🟠 **Happening now** — Dutch orange, "started X ago · ends in Y".
  - 🔴 **Concluded** — red, struck-through date, "ended X ago". Concluded posts stay in the channel as history.
  - Events without an end time count as concluded at 23:59 (Amsterdam) on their start day.
  - On startup, the bot finds its own earlier posts in each configured channel (via Discord message history) so posts made before this feature also get marked.
- **Self-Cleaning Persistence**: Tracks posted events per channel (start/end time, message ID, status) in `posted_events.json`. Records are pruned a week after the event ends so the state file never expands infinitely.
- **Per-Server Setup**:
  - `/setup channel:#channel` (requires `Manage Server`) picks where the bot posts in that server, checks the bot can post there, and immediately publishes upcoming events. Saved in `guild_config.json`.
  - `/unsetup` stops posting in that server.
  - When added to a new server, the bot greets the system channel with setup instructions.
  - Admin command `!checkevents` and slash command `/checkevents` (requires `Manage Messages`) post any new events to the server's configured channel.
  - Optional static channel IDs via `CHANNEL_ID` in `.env` still work as a fallback.

---

## Installation & Setup

### 1. Prerequisites
- Python 3.11+
- Virtual environment (`venv`)

### 2. Clone & Setup Virtual Environment
```bash
git clone https://github.com/Maximos2004/john-event-bot.git
cd john-event-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your Discord credentials:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
DISCORD_TOKEN=your_bot_token_here
CHANNEL_ID=
CHECK_INTERVAL_HOURS=4
```
Then, in Discord, run `/setup channel:#your-channel` in each server. (`CHANNEL_ID` is optional.)

### 4. Run Locally
```bash
python bot.py
```

---

## 24/7 Deployment with systemd

A systemd service unit template is included in `johnevent.service`.

1. Copy the unit file to `/etc/systemd/system/`:
   ```bash
   sudo cp johnevent.service /etc/systemd/system/johnevent.service
   ```
2. Reload systemd, enable, and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable johnevent.service
   sudo systemctl start johnevent.service
   ```
3. Check status and logs:
   ```bash
   sudo systemctl status johnevent.service
   sudo journalctl -u johnevent.service -f
   ```

---

## Discord Bot Permissions
When inviting the bot, it only requires minimal permissions scoped to the target channel:
- **Send Messages**
- **Embed Links**
- **Read Message History**
- **Use Application Commands**

---

## License
MIT License.
