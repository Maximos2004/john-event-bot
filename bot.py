import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

import discord
from discord.ext import commands, tasks

from api_client import fetch_upcoming_events

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
CHANNEL_ID_STR = os.getenv("CHANNEL_ID", "1552326005652455524")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "4"))
POSTED_EVENTS_FILE = BASE_DIR / "posted_events.json"

if not DISCORD_TOKEN:
    logger.critical("DISCORD_TOKEN is missing! Please set it in .env")
    sys.exit(1)

# Support multiple comma-separated channel IDs
try:
    CHANNEL_IDS = [int(c.strip()) for c in CHANNEL_ID_STR.split(",") if c.strip()]
except ValueError as e:
    logger.critical(f"Invalid CHANNEL_ID format in .env ({CHANNEL_ID_STR}): {e}")
    sys.exit(1)

logger.info(f"Configured target channel IDs: {CHANNEL_IDS}")

# ==============================================================================
# Self-Cleaning Persistence Layer
# ==============================================================================
def load_all_posted_events() -> dict[str, dict[str, int]]:
    """
    Loads all posted events indexed by channel ID.
    Format: { "channel_id": { "event_id": expiry_timestamp, ... } }
    Automatically prunes events whose expiry_timestamp has passed.
    """
    if not POSTED_EVENTS_FILE.exists():
        return {}

    now_ts = int(datetime.now(timezone.utc).timestamp())
    try:
        with open(POSTED_EVENTS_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        cleaned_data: dict[str, dict[str, int]] = {}
        pruned_count = 0

        if isinstance(raw_data, dict):
            for ch_key, items in raw_data.items():
                cleaned_data[ch_key] = {}
                if isinstance(items, dict):
                    for ev_id, exp_ts in items.items():
                        if isinstance(exp_ts, (int, float)) and exp_ts > now_ts:
                            cleaned_data[ch_key][ev_id] = int(exp_ts)
                        else:
                            pruned_count += 1
                elif isinstance(items, list):
                    # Migrate legacy flat list to dict (give default 48h expiry)
                    default_expiry = now_ts + (48 * 3600)
                    for item_id in items:
                        cleaned_data[ch_key][str(item_id)] = default_expiry

        if pruned_count > 0:
            logger.info(f"Self-cleaning: pruned {pruned_count} expired event(s) from persistence file.")
            save_all_posted_events(cleaned_data)

        return cleaned_data

    except Exception as e:
        logger.error(f"Error loading {POSTED_EVENTS_FILE}: {e}")
        return {}

def save_all_posted_events(data: dict[str, dict[str, int]]):
    """Saves posted events dictionary to disk atomically."""
    temp_file = POSTED_EVENTS_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(POSTED_EVENTS_FILE)
    except Exception as e:
        logger.error(f"Error saving {POSTED_EVENTS_FILE}: {e}")

def load_posted_events_for_channel(channel_id: int) -> dict[str, int]:
    """Loads posted event dict {event_id: expiry_ts} for a specific channel."""
    all_data = load_all_posted_events()
    return all_data.get(str(channel_id), {})

def record_posted_event(channel_id: int, event_id: str, expiry_timestamp: int):
    """Records an event as posted for a specific channel and persists atomically."""
    all_data = load_all_posted_events()
    ch_key = str(channel_id)
    if ch_key not in all_data:
        all_data[ch_key] = {}
    all_data[ch_key][event_id] = expiry_timestamp
    save_all_posted_events(all_data)

# ==============================================================================
# Discord Embed Construction
# ==============================================================================
def create_event_embed(event: dict) -> discord.Embed:
    """Builds a rich Dutch orange embed for a Dutch game dev event."""
    embed = discord.Embed(
        title=event.get("title", "Upcoming Game Dev Event"),
        url=event.get("url"),
        description=event.get("description") or None,
        color=0xEB5E28 # Dutch Orange
    )

    # 1. Date & Time with native Discord localized Timestamp and Relative countdown
    start_ts = event.get("start_timestamp")
    if start_ts:
        date_time_str = f"<t:{start_ts}:F> (<t:{start_ts}:R>)"
    else:
        date_time_str = "Check event page"
    embed.add_field(name="📅 Date & Time", value=date_time_str, inline=False)

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

    embed.set_footer(text="John Event • Dutch Games Industry Radar")
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
        target_ids = [target_channel_id] if target_channel_id else CHANNEL_IDS
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

            count = 0
            for ev in new_events:
                try:
                    embed = create_event_embed(ev)
                    await channel.send(embed=embed)
                    record_posted_event(ch_id, ev["id"], ev["expiry_timestamp"])
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

    if not scheduled_event_checker.is_running():
        scheduled_event_checker.start()

@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f"🎉 Joined new guild: '{guild.name}' (ID: {guild.id})! Running immediate event check...")
    try:
        await asyncio.sleep(2)
        count_dict = await check_and_post_events()
        total = sum(count_dict.values())
        logger.info(f"Initial setup check on joining '{guild.name}' complete. {total} event(s) posted.")
    except Exception as e:
        logger.error(f"Error checking events upon joining guild '{guild.name}': {e}", exc_info=True)

# Bot Commands
@bot.command(name="checkevents")
@commands.has_permissions(manage_messages=True)
async def cmd_checkevents(ctx: commands.Context):
    """Admin command to manually trigger an immediate calendar check."""
    status_msg = await ctx.send("🔍 Checking DGI API for upcoming game development events...")
    try:
        target_id = ctx.channel.id if ctx.channel.id in CHANNEL_IDS else None
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

    await interaction.response.defer(ephemeral=False)
    try:
        target_id = interaction.channel.id if interaction.channel.id in CHANNEL_IDS else None
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
