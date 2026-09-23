# John Event 🇳🇱 • NL Game Dev Radar

A lightweight, 24/7 Discord bot that tracks upcoming Dutch game development events (conferences, awards, meetups, lectures, network lunches) from [Dutch Games Industry](https://www.dutchgamesindustry.nl/calendar) and announces them to Discord with rich embeds.

---

## Features

- **Automated Event Tracking**: Scrapes upcoming Dutch game development events every 4 hours using `aiohttp` and `BeautifulSoup`.
- **Strict Event Filtering**: Only tracks real industry events (conferences, meetups, awards, lunches), automatically filtering out standalone game release listings.
- **Dynamic Price Extraction**:
  - Direct integration with **WeTicket Storefront API** (e.g. LevelUp Groningen ticket tiers).
  - Schema.org / JSON-LD parsing for **Eventbrite** events (e.g. Dutch Game Awards, GSNI Lectures).
  - Regular expression parsing for direct `€` amounts and free community indicators.
- **Rich Dutch Orange Discord Embeds (`#EB5E28`)**:
  - Event title linked to official source page.
  - Native **Discord Timestamps** `<t:timestamp:F> (<t:timestamp:R>)` with localized time and dynamic relative countdown.
  - Clickable **Google Maps** links for physical venue addresses.
  - Bold exact ticket prices (`**FREE**` or `**€XX.XX**`).
- **Duplicate Prevention**: State is persisted per channel in `posted_events.json` to prevent re-posting across reboots.
- **Multi-Channel & Instant Setup**:
  - Supports multiple target channels.
  - Listens for `on_guild_join` to immediately publish the events calendar when added to a new server.
  - Admin command `!checkevents` and slash command `/checkevents` guarded by `Manage Messages` permission.

---

## Installation & Setup

### 1. Prerequisites
- Python 3.11+
- Virtual environment (`venv`)

### 2. Clone & Setup Virtual Environment
```bash
git clone https://github.com/<your-username>/john-event-bot.git
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
CHANNEL_ID=your_discord_channel_id_here
CALENDAR_URL=https://www.dutchgamesindustry.nl/calendar
CHECK_INTERVAL_HOURS=4
```

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
