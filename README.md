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

### 2. Create the Discord Bot
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Open the **Bot** tab and click **Reset Token** to get your bot token. Keep it secret; it goes in `.env` (step 4).
3. On the same tab, under **Privileged Gateway Intents**, enable **Message Content Intent** and save.
   Without it the bot fails to start with `PrivilegedIntentsRequired`.

### 3. Clone & Setup Virtual Environment
```bash
git clone https://github.com/Maximos2004/john-event-bot.git
cd john-event-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Configuration
Copy `.env.example` to `.env` and fill in your bot token:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
DISCORD_TOKEN=your_bot_token_here
CHANNEL_ID=
CHECK_INTERVAL_HOURS=4
```
`CHANNEL_ID` is optional; leave it empty and use `/setup` instead.

### 5. Run Locally
```bash
python bot.py
```
On startup the bot logs `Logged in as ...`. If it is not in any server yet, it also logs an invite link.

### 6. Invite the Bot to Your Server
Open this link, replacing `YOUR_CLIENT_ID` with the **Application ID** from the portal's **General Information** tab:
```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=277025507328&scope=bot%20applications.commands
```
Then, in Discord, run `/setup channel:#your-channel` in each server. Slash commands can take a moment to appear; reload Discord (Ctrl+R) if they don't show up.

---

## 24/7 Deployment with systemd

A systemd service unit template is included in `johnevent.service`.

1. Edit `johnevent.service` and replace `youruser` and `/home/youruser/john-event-bot` with the Linux user and folder the bot runs from.
2. Copy the unit file to `/etc/systemd/system/`:
   ```bash
   sudo cp johnevent.service /etc/systemd/system/johnevent.service
   ```
3. Reload systemd, enable, and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable johnevent.service
   sudo systemctl start johnevent.service
   ```
4. Check status and logs:
   ```bash
   sudo systemctl status johnevent.service
   sudo journalctl -u johnevent.service -f
   ```

---

## Discord Bot Permissions
The invite link above requests only these permissions:
- **Send Messages** / **Send Messages in Threads**
- **Embed Links**
- **Attach Files**
- **Read Message History**
- **Use Application Commands**

In the channel picked with `/setup`, the bot needs View Channel, Send Messages, Embed Links and Read Message History; `/setup` checks these and tells you what is missing.

---

## License
MIT License.
