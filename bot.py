import os
import re
import sys
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands, tasks

from api_client import fetch_upcoming_events, end_of_day_ts

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("john_event")

# Load environment configuration
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_STR = os.getenv("CHANNEL_ID", "")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "4"))
POSTED_EVENTS_FILE = BASE_DIR / "posted_events.json"
GUILD_CONFIG_FILE = BASE_DIR / "guild_config.json"

if not DISCORD_TOKEN:
    logger.critical("DISCORD_TOKEN is missing! Please set it in .env")
    sys.exit(1)

# Optional legacy/static channel IDs from .env (comma-separated).
# The preferred way is the /setup slash command, which is stored per server.
try:
    ENV_CHANNEL_IDS = [int(c.strip()) for c in CHANNEL_ID_STR.split(",") if c.strip()]
except ValueError as e:
    logger.critical(f"Invalid CHANNEL_ID format in .env ({CHANNEL_ID_STR}): {e}")
    sys.exit(1)

if ENV_CHANNEL_IDS:
    logger.info(f"Static channel IDs from .env: {ENV_CHANNEL_IDS}")

# ==============================================================================
# Per-Server Configuration (set via /setup)
# ==============================================================================
def load_guild_config() -> dict[str, dict]:
    """Loads per-guild config. Format: { "guild_id": { "channel_id": int } }"""
    if not GUILD_CONFIG_FILE.exists():
        return {}
    try:
        with open(GUILD_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"Error loading {GUILD_CONFIG_FILE}: {e}")
        return {}

def save_guild_config(data: dict[str, dict]):
    """Saves per-guild config atomically."""
    temp_file = GUILD_CONFIG_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(GUILD_CONFIG_FILE)
    except Exception as e:
        logger.error(f"Error saving {GUILD_CONFIG_FILE}: {e}")

def get_guild_channel_id(guild_id: int) -> int | None:
    """Returns the configured announcement channel for a guild, if any."""
    entry = load_guild_config().get(str(guild_id)) or {}
    ch = entry.get("channel_id")
    return int(ch) if ch else None

def set_guild_channel_id(guild_id: int, channel_id: int | None):
    """Sets (or clears, when None) the announcement channel for a guild."""
    data = load_guild_config()
    if channel_id is None:
        data.pop(str(guild_id), None)
    else:
        data[str(guild_id)] = {"channel_id": channel_id}
    save_guild_config(data)

def get_all_target_channel_ids() -> list[int]:
    """All channels to post to: /setup-configured channels plus any static .env channels."""
    ids = [int(e["channel_id"]) for e in load_guild_config().values() if e.get("channel_id")]
    for ch in ENV_CHANNEL_IDS:
        if ch not in ids:
            ids.append(ch)
    return ids

# ==============================================================================
# Self-Cleaning Persistence Layer
# ==============================================================================
# Format:
# { "channel_id": { "event_id": {
#       "start": int | None,      # event start timestamp
#       "expiry": int,            # event end timestamp
#       "message_id": int | None, # Discord message holding the embed
#       "status": "upcoming" | "live" | "concluded"
# } } }
# Records are kept for a week after the event ends (so the post can be marked
# Concluded), then pruned. The concluded post itself stays in the channel forever.
PRUNE_GRACE_SECONDS = 7 * 24 * 3600

def _normalize_record(value) -> dict | None:
    """Converts legacy record formats (bare expiry int) into the current dict format."""
    if isinstance(value, (int, float)):
        return {"start": None, "expiry": int(value), "message_id": None, "status": "upcoming"}
    if isinstance(value, dict) and isinstance(value.get("expiry"), (int, float)):
        return {
            "start": value.get("start"),
            "expiry": int(value["expiry"]),
            "message_id": value.get("message_id"),
            "status": value.get("status") or "upcoming",
        }
    return None

def load_all_posted_events() -> dict[str, dict[str, dict]]:
    """
    Loads all posted events indexed by channel ID, migrating legacy formats
    and pruning records whose event ended more than PRUNE_GRACE_SECONDS ago.
    """
    if not POSTED_EVENTS_FILE.exists():
        return {}

    now_ts = int(datetime.now(timezone.utc).timestamp())
    try:
        with open(POSTED_EVENTS_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        cleaned_data: dict[str, dict[str, dict]] = {}
        pruned_count = 0

        if isinstance(raw_data, dict):
            for ch_key, items in raw_data.items():
                cleaned_data[ch_key] = {}
                if isinstance(items, dict):
                    for ev_id, value in items.items():
                        rec = _normalize_record(value)
                        if rec and rec["expiry"] + PRUNE_GRACE_SECONDS > now_ts:
                            cleaned_data[ch_key][ev_id] = rec
                        else:
                            pruned_count += 1
                elif isinstance(items, list):
                    # Migrate legacy flat list (give default 48h expiry)
                    default_expiry = now_ts + (48 * 3600)
                    for item_id in items:
                        cleaned_data[ch_key][str(item_id)] = _normalize_record(default_expiry)

        if pruned_count > 0:
            logger.info(f"Self-cleaning: pruned {pruned_count} old event record(s) from persistence file.")
            save_all_posted_events(cleaned_data)

        return cleaned_data

    except Exception as e:
        logger.error(f"Error loading {POSTED_EVENTS_FILE}: {e}")
        return {}

def save_all_posted_events(data: dict[str, dict[str, dict]]):
    """Saves posted events dictionary to disk atomically."""
    temp_file = POSTED_EVENTS_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(POSTED_EVENTS_FILE)
    except Exception as e:
        logger.error(f"Error saving {POSTED_EVENTS_FILE}: {e}")

def load_posted_events_for_channel(channel_id: int) -> dict[str, dict]:
    """Loads posted event records {event_id: record} for a specific channel."""
    return load_all_posted_events().get(str(channel_id), {})

def upsert_event_record(channel_id: int, event_id: str, **fields):
    """Creates or updates one event record for a channel and persists atomically."""
    all_data = load_all_posted_events()
    ch_records = all_data.setdefault(str(channel_id), {})
    rec = ch_records.get(event_id) or {"start": None, "expiry": 0, "message_id": None, "status": "upcoming"}
    rec.update(fields)
    ch_records[event_id] = rec
    save_all_posted_events(all_data)

# ==============================================================================
# Discord Embed Construction
# ==============================================================================
FOOTER_TEXT = "John Event • Dutch Games Industry Radar"
DATE_FIELD_NAME = "📅 Date & Time"

def create_event_embed(event: dict) -> discord.Embed:
    """Builds a rich Dutch orange embed for a Dutch game dev event."""
    embed = discord.Embed(
        title=event.get("title", "Upcoming Game Dev Event"),
        url=event.get("url"),
        description=event.get("description") or None,
        color=STATUS_COLORS["upcoming"]
    )

    # 1. Date & Time with native Discord localized Timestamp and Relative countdown
    start_ts = event.get("start_timestamp")
    if start_ts:
        date_time_str = f"<t:{start_ts}:F> (<t:{start_ts}:R>)"
    else:
        date_time_str = "Check event page"
    embed.add_field(name=DATE_FIELD_NAME, value=date_time_str, inline=False)

    # 2. Location (Clickable Google Maps link when address available)
    loc_name = event.get("address") or "Netherlands"
    map_url = event.get("map_url")
    if map_url:
        loc_str = f"[{loc_name}]({map_url})"
    elif loc_name.lower().startswith("online"):
        loc_str = "🌐 Online"
    else:
        loc_str = loc_name
    embed.add_field(name="📍 Location", value=loc_str, inline=True)

    # 3. Categories / Tags
    categories = event.get("categories") or []
    if categories:
        cat_str = " • ".join(categories)
        embed.add_field(name="🏷️ Categories", value=cat_str, inline=True)

    # 4. Direct link to official event page
    event_url = event.get("url")
    if event_url:
        embed.add_field(name="🔗 Official Link", value=f"[Event & Registration Page]({event_url})", inline=False)

    # Thumbnail image from DGI official media
    if event.get("image"):
        embed.set_thumbnail(url=event["image"])

    embed.set_footer(text=FOOTER_TEXT)
    return embed

# ==============================================================================
# Event Status (Upcoming → Happening now → Concluded)
# ==============================================================================
STATUS_COLORS = {
    "upcoming": 0x57F287,   # Green
    "live": 0xEB5E28,       # Dutch Orange
    "concluded": 0xED4245,  # Red
}
TITLE_PREFIXES = {
    "live": "🟠 Happening now · ",
    "concluded": "🔴 Concluded · ",
}
TIMESTAMP_RE = re.compile(r"<t:(\d+):F>")

def event_status(start_ts: int | None, expiry_ts: int, now_ts: int | None = None) -> str:
    """Works out whether an event is upcoming, live or concluded."""
    now_ts = now_ts if now_ts is not None else int(datetime.now(timezone.utc).timestamp())
    if now_ts >= expiry_ts:
        return "concluded"
    if start_ts and now_ts >= start_ts:
        return "live"
    return "upcoming"

def _strip_title_prefix(title: str) -> str:
    for prefix in TITLE_PREFIXES.values():
        if title.startswith(prefix):
            return title[len(prefix):]
    return title

def detect_embed_status(embed: discord.Embed) -> str:
    """Reads the status an existing embed is currently showing, from its title prefix."""
    title = embed.title or ""
    for status, prefix in TITLE_PREFIXES.items():
        if title.startswith(prefix):
            return status
    return "upcoming"

def format_date_value(status: str, start_ts: int | None, expiry_ts: int) -> str:
    if not start_ts:
        return f"Ended <t:{expiry_ts}:R>" if status == "concluded" else "Check event page"
    if status == "live":
        return f"<t:{start_ts}:F>\n🟠 Started <t:{start_ts}:R> · ends <t:{expiry_ts}:R>"
    if status == "concluded":
        return f"~~<t:{start_ts}:F>~~\nEnded <t:{expiry_ts}:R>"
    return f"<t:{start_ts}:F> (<t:{start_ts}:R>)"

def apply_status(embed: discord.Embed, status: str, start_ts: int | None, expiry_ts: int) -> discord.Embed:
    """Restyles an event embed (new or already posted) for the given status, in place."""
    title = TITLE_PREFIXES.get(status, "") + _strip_title_prefix(embed.title or "Game Dev Event")
    embed.title = title[:256]
    embed.colour = STATUS_COLORS[status]
    for i, field in enumerate(embed.fields):
        if field.name == DATE_FIELD_NAME:
            embed.set_field_at(i, name=DATE_FIELD_NAME, value=format_date_value(status, start_ts, expiry_ts), inline=field.inline)
            break
    return embed

# ==============================================================================
# Discord Bot Setup & Event Loops
# ==============================================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
api_lock = asyncio.Lock()

async def check_and_post_events(target_channel_id: int | None = None) -> dict[int, int]:
    """
    Fetches events from the official DGI API, filters out already posted ones per channel,
    publishes embeds to Discord, and records persistence with expiry dates.
    Returns a dict mapping channel_id to posted count.
    """
    async with api_lock:
        target_ids = [target_channel_id] if target_channel_id else get_all_target_channel_ids()
        events = await fetch_upcoming_events()
        posted_counts = {}

        for ch_id in target_ids:
            channel = bot.get_channel(ch_id)
            if not channel:
                try:
                    channel = await bot.fetch_channel(ch_id)
                except Exception as e:
                    invite_url = f"https://discord.com/oauth2/authorize?client_id={bot.user.id}&permissions=277025507328&scope=bot%20applications.commands"
                    logger.warning(f"Could not access Discord channel {ch_id}: {e}. Ensure bot is invited to server: {invite_url}")
                    continue

            channel_posted = load_posted_events_for_channel(ch_id)
            new_events = [ev for ev in events if ev["id"] not in channel_posted]
            logger.info(f"Channel #{channel.name} ({ch_id}): {len(events)} active events, {len(new_events)} new.")

            # If DGI changed the time of an already-posted event, store the new times and
            # flag the post for a re-render by the status loop.
            for ev in events:
                rec = channel_posted.get(ev["id"])
                if rec and (rec.get("start"), rec.get("expiry")) != (ev["start_timestamp"], ev["expiry_timestamp"]):
                    upsert_event_record(ch_id, ev["id"], start=ev["start_timestamp"],
                                        expiry=ev["expiry_timestamp"], status="refresh")

            count = 0
            for ev in new_events:
                try:
                    status = event_status(ev["start_timestamp"], ev["expiry_timestamp"])
                    embed = apply_status(create_event_embed(ev), status, ev["start_timestamp"], ev["expiry_timestamp"])
                    msg = await channel.send(embed=embed)
                    upsert_event_record(ch_id, ev["id"], start=ev["start_timestamp"], expiry=ev["expiry_timestamp"],
                                        message_id=msg.id, status=status)
                    count += 1
                    logger.info(f"Posted to #{channel.name}: '{ev['title']}' ({ev['id']})")
                    await asyncio.sleep(1.2) # Rate limit protection
                except Exception as e:
                    logger.error(f"Failed to post '{ev['title']}' to #{channel.name}: {e}")
            posted_counts[ch_id] = count

        return posted_counts

# Scheduled 4-Hour Background Task
@tasks.loop(hours=CHECK_INTERVAL_HOURS)
async def scheduled_event_checker():
    logger.info("Executing scheduled DGI API event check...")
    try:
        results = await check_and_post_events()
        total = sum(results.values())
        logger.info(f"Scheduled check complete. {total} new event(s) posted across {len(results)} channel(s).")
    except Exception as e:
        logger.error(f"Error during scheduled event check: {e}", exc_info=True)

@scheduled_event_checker.before_loop
async def before_event_checker():
    await bot.wait_until_ready()
    logger.info("Bot is ready. Starting event checker background loop.")

async def _get_channel(ch_id: int):
    channel = bot.get_channel(ch_id)
    if channel:
        return channel
    try:
        return await bot.fetch_channel(ch_id)
    except Exception as e:
        logger.warning(f"Could not access Discord channel {ch_id}: {e}")
        return None

# Status Loop: restyles posts as events start and end. Uses only saved data
# (no DGI API calls), so it can run often.
@tasks.loop(minutes=10)
async def refresh_event_statuses():
    now_ts = int(datetime.now(timezone.utc).timestamp())
    channels: dict[int, object] = {}
    updated = 0

    for ch_key, records in load_all_posted_events().items():
        for ev_id, rec in records.items():
            msg_id = rec.get("message_id")
            if not msg_id:
                continue
            desired = event_status(rec.get("start"), rec["expiry"], now_ts)
            if desired == rec.get("status"):
                continue

            ch_id = int(ch_key)
            if ch_id not in channels:
                channels[ch_id] = await _get_channel(ch_id)
            channel = channels[ch_id]
            if not channel:
                continue

            try:
                msg = await channel.fetch_message(msg_id)
                if not msg.embeds:
                    upsert_event_record(ch_id, ev_id, message_id=None)
                    continue
                embed = apply_status(msg.embeds[0], desired, rec.get("start"), rec["expiry"])
                await msg.edit(embed=embed)
                upsert_event_record(ch_id, ev_id, status=desired)
                updated += 1
                logger.info(f"Marked event {ev_id} in #{channel.name} as '{desired}'.")
                await asyncio.sleep(1.0)  # Rate limit protection
            except discord.NotFound:
                # Post was deleted: stop tracking the message, keep the record so it isn't re-posted
                upsert_event_record(ch_id, ev_id, message_id=None)
            except discord.Forbidden as e:
                logger.warning(f"No permission to edit message {msg_id} in channel {ch_id}: {e}")
            except Exception as e:
                logger.error(f"Failed to update status of event {ev_id} in channel {ch_id}: {e}")

    if updated:
        logger.info(f"Status refresh complete. {updated} post(s) updated.")

@refresh_event_statuses.before_loop
async def before_status_refresh():
    await bot.wait_until_ready()

# One-time Backfill: finds posts made before message IDs were tracked
# (by reading the bot's own messages through the Discord API) so they can be
# marked Concluded too.
BACKFILL_HISTORY_LIMIT = 200
_backfill_done = False

async def backfill_existing_posts():
    known_ids = {
        rec.get("message_id")
        for records in load_all_posted_events().values()
        for rec in records.values()
        if rec.get("message_id")
    }
    events_by_url = {ev["url"]: ev for ev in await fetch_upcoming_events()}
    if not events_by_url:
        logger.info("Backfill skipped: no events from DGI API (will retry on next start).")
        return False

    now_ts = int(datetime.now(timezone.utc).timestamp())
    found = 0
    for ch_id in get_all_target_channel_ids():
        channel = await _get_channel(ch_id)
        if not channel:
            continue
        try:
            async for msg in channel.history(limit=BACKFILL_HISTORY_LIMIT):
                if msg.author.id != bot.user.id or msg.id in known_ids or not msg.embeds:
                    continue
                embed = msg.embeds[0]
                if not embed.footer or embed.footer.text != FOOTER_TEXT:
                    continue

                ev = events_by_url.get(embed.url)
                if ev:
                    ev_key, start_ts, expiry_ts = ev["id"], ev["start_timestamp"], ev["expiry_timestamp"]
                else:
                    # Event no longer listed by DGI (already over): read the start time from the embed
                    date_field = next((f for f in embed.fields if f.name == DATE_FIELD_NAME), None)
                    match = TIMESTAMP_RE.search(date_field.value) if date_field else None
                    if not match:
                        continue
                    start_ts = int(match.group(1))
                    ev_key, expiry_ts = f"msg:{msg.id}", end_of_day_ts(start_ts)

                desired = event_status(start_ts, expiry_ts, now_ts)
                if desired != detect_embed_status(embed):
                    await msg.edit(embed=apply_status(embed, desired, start_ts, expiry_ts))
                    await asyncio.sleep(1.0)
                if expiry_ts + PRUNE_GRACE_SECONDS > now_ts:
                    upsert_event_record(ch_id, ev_key, start=start_ts, expiry=expiry_ts,
                                        message_id=msg.id, status=desired)
                found += 1
        except discord.Forbidden as e:
            logger.warning(f"Backfill: no access to history of #{channel.name}: {e}")
        except Exception as e:
            logger.error(f"Backfill failed for channel {ch_id}: {e}", exc_info=True)

    logger.info(f"Backfill complete. Linked {found} existing post(s).")
    return True

# Bot Events
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}#{bot.user.discriminator} (ID: {bot.user.id})")
    if not bot.guilds:
        invite_url = f"https://discord.com/oauth2/authorize?client_id={bot.user.id}&permissions=277025507328&scope=bot%20applications.commands"
        logger.warning(f"John Event is currently in 0 servers. Server invite link: {invite_url}")
    else:
        for g in bot.guilds:
            logger.info(f"Connected to guild: '{g.name}' (ID: {g.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} application commands globally.")
    except Exception as e:
        logger.warning(f"Failed to sync slash commands: {e}")

    global _backfill_done
    if not _backfill_done:
        _backfill_done = True  # on_ready can fire again on reconnects; only try once per run
        try:
            await backfill_existing_posts()
        except Exception as e:
            logger.error(f"Backfill of existing posts failed: {e}", exc_info=True)

    if not scheduled_event_checker.is_running():
        scheduled_event_checker.start()
    if not refresh_event_statuses.is_running():
        refresh_event_statuses.start()

@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f"🎉 Joined new guild: '{guild.name}' (ID: {guild.id}).")
    configured = get_guild_channel_id(guild.id)
    if configured:
        # Re-joined a server that was set up before: resume posting right away
        try:
            await asyncio.sleep(2)
            count_dict = await check_and_post_events(target_channel_id=configured)
            logger.info(f"Posted {sum(count_dict.values())} event(s) to configured channel in '{guild.name}'.")
        except Exception as e:
            logger.error(f"Error checking events upon joining guild '{guild.name}': {e}", exc_info=True)
        return

    # Not set up yet: leave a hint in the server's system channel if we can talk there
    ch = guild.system_channel
    if ch and ch.permissions_for(guild.me).send_messages:
        try:
            await ch.send(
                "👋 Hi! I'm **John Event** — I post upcoming Dutch games industry events.\n"
                "An admin can pick the channel I should post in with `/setup channel:#your-channel`."
            )
        except Exception as e:
            logger.warning(f"Could not send welcome message in '{guild.name}': {e}")

def _resolve_checkevents_target(guild: discord.Guild | None, channel_id: int) -> int | None:
    """Picks the channel /checkevents should post to for the server it was used in."""
    if guild:
        configured = get_guild_channel_id(guild.id)
        if configured:
            return configured
    if channel_id in ENV_CHANNEL_IDS:
        return channel_id
    return None

NOT_SETUP_MSG = "⚠️ This server has no events channel yet. An admin can set one with `/setup channel:#your-channel`."

# Setup Commands
@bot.tree.command(name="setup", description="Choose the channel where John Event posts upcoming events")
@app_commands.describe(channel="The text channel to post events in")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def slash_setup(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need the 'Manage Server' permission to use this command.", ephemeral=True)
        return

    perms = channel.permissions_for(interaction.guild.me)
    missing = [name for name, ok in (
        ("View Channel", perms.view_channel),
        ("Send Messages", perms.send_messages),
        ("Embed Links", perms.embed_links),
        ("Read Message History", perms.read_message_history),
    ) if not ok]
    if missing:
        await interaction.response.send_message(
            f"❌ I can't post in {channel.mention}. Missing permission(s): **{', '.join(missing)}**.",
            ephemeral=True,
        )
        return

    set_guild_channel_id(interaction.guild.id, channel.id)
    logger.info(f"Guild '{interaction.guild.name}' ({interaction.guild.id}) set events channel to #{channel.name} ({channel.id}).")

    await interaction.response.send_message(
        f"✅ Events will now be posted in {channel.mention}. Fetching upcoming events…", ephemeral=True
    )
    try:
        results = await check_and_post_events(target_channel_id=channel.id)
        total = sum(results.values())
        await interaction.followup.send(f"📬 Posted **{total}** upcoming event(s) to {channel.mention}.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error posting events after /setup: {e}", exc_info=True)
        await interaction.followup.send(f"⚠️ Channel saved, but the first event check failed: {e}", ephemeral=True)

@bot.tree.command(name="unsetup", description="Stop John Event from posting events in this server")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def slash_unsetup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need the 'Manage Server' permission to use this command.", ephemeral=True)
        return
    if not get_guild_channel_id(interaction.guild.id):
        await interaction.response.send_message("ℹ️ This server has no events channel configured.", ephemeral=True)
        return
    set_guild_channel_id(interaction.guild.id, None)
    logger.info(f"Guild '{interaction.guild.name}' ({interaction.guild.id}) removed its events channel.")
    await interaction.response.send_message("🛑 I'll stop posting events in this server. Use `/setup` to turn it back on.", ephemeral=True)

# Bot Commands
@bot.command(name="checkevents")
@commands.has_permissions(manage_messages=True)
async def cmd_checkevents(ctx: commands.Context):
    """Admin command to manually trigger an immediate calendar check."""
    target_id = _resolve_checkevents_target(ctx.guild, ctx.channel.id)
    if not target_id:
        await ctx.send(NOT_SETUP_MSG)
        return
    status_msg = await ctx.send("🔍 Checking DGI API for upcoming game development events...")
    try:
        results = await check_and_post_events(target_channel_id=target_id)
        total = sum(results.values())
        if total > 0:
            await status_msg.edit(content=f"✅ Check complete! Posted **{total}** new event(s).")
        else:
            await status_msg.edit(content="✨ All events are already up to date!")
    except Exception as e:
        logger.error(f"Error in !checkevents command: {e}")
        await status_msg.edit(content=f"❌ Failed to check events: {e}")

@bot.tree.command(name="checkevents", description="Check for new Dutch game development events now")
async def slash_checkevents(interaction: discord.Interaction):
    """Slash command to manually trigger an immediate calendar check."""
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You need the 'Manage Messages' permission to use this command.", ephemeral=True)
        return

    target_id = _resolve_checkevents_target(interaction.guild, interaction.channel.id)
    if not target_id:
        await interaction.response.send_message(NOT_SETUP_MSG, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)
    try:
        results = await check_and_post_events(target_channel_id=target_id)
        total = sum(results.values())
        if total > 0:
            await interaction.followup.send(f"✅ Check complete! Posted **{total}** new event(s).")
        else:
            await interaction.followup.send("✨ All events are already up to date!")
    except Exception as e:
        logger.error(f"Error in /checkevents slash command: {e}")
        await interaction.followup.send(f"❌ Failed to check events: {e}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
