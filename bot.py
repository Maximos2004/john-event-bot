import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv

import discord
from discord.ext import commands, tasks

from scraper import fetch_upcoming_events

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
CALENDAR_URL = os.getenv("CALENDAR_URL", "https://www.dutchgamesindustry.nl/calendar")
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

# Per-channel persistence
def load_all_posted_events() -> dict[str, list[str]]:
    """Loads all posted events indexed by channel ID."""
    if not POSTED_EVENTS_FILE.exists():
        return {}
    try:
        with open(POSTED_EVENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            elif isinstance(data, list):
                # Legacy flat list migration
                default_key = str(CHANNEL_IDS[0]) if CHANNEL_IDS else "default"
                return {default_key: data}
    except Exception as e:
        logger.error(f"Error loading {POSTED_EVENTS_FILE}: {e}")
    return {}

def load_posted_events_for_channel(channel_id: int) -> set[str]:
    """Loads the set of posted event IDs/URLs for a specific channel."""
    data = load_all_posted_events()
    return set(data.get(str(channel_id), []))

def save_posted_events_for_channel(channel_id: int, posted: set[str]):
    """Persists updated posted events for a channel to disk atomically."""
    data = load_all_posted_events()
    data[str(channel_id)] = sorted(list(posted))
    temp_file = POSTED_EVENTS_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(POSTED_EVENTS_FILE)
    except Exception as e:
        logger.error(f"Error saving {POSTED_EVENTS_FILE}: {e}")

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
scrape_lock = asyncio.Lock()

def create_event_embed(event: dict) -> discord.Embed:
    """Builds a rich Dutch orange embed for a Dutch game dev event."""
    embed = discord.Embed(
        title=event.get("title", "Upcoming Game Dev Event"),
        url=event.get("url", CALENDAR_URL),
        description=event.get("description") or None,
        color=0xEB5E28 # Dutch Orange
    )
    
    # 1. Date & Time with Discord Timestamp and Relative Timestamp in parentheses
    if event.get("timestamp"):
        ts = event["timestamp"]
        date_time_str = f"<t:{ts}:F> (<t:{ts}:R>)"
    else:
        date_time_str = "Check event page"
    embed.add_field(name="📅 Date & Time", value=date_time_str, inline=False)
    
    # 2. Location (Clickable Google Maps link when available)
    loc_name = event.get("location_name") or "Netherlands"
    map_url = event.get("map_url")
    if map_url:
        loc_str = f"[{loc_name}]({map_url})"
    elif loc_name.lower() == "online":
        loc_str = "🌐 Online"
    else:
        loc_str = loc_name
    embed.add_field(name="📍 Location", value=loc_str, inline=True)
    
    # 3. Exact Price in Bold (e.g. **FREE** or **€35.00**)
    price = event.get("price") or "FREE"
    embed.add_field(name="🏷️ Price", value=f"**{price}**", inline=True)
    
    if event.get("image"):
        embed.set_thumbnail(url=event["image"])
    
    embed.set_footer(text="John Event • NL Game Dev Radar")
    return embed

async def check_and_post_events(target_channel_id: int | None = None) -> dict[int, int]:
    """
    Scrapes events, filters out already posted ones per channel, publishes embeds to Discord,
    and updates persistence. Returns a dict mapping channel_id to posted count.
    """
    async with scrape_lock:
        target_ids = [target_channel_id] if target_channel_id else CHANNEL_IDS
        events = await fetch_upcoming_events(CALENDAR_URL)
        posted_counts = {}

        for ch_id in target_ids:
            channel = bot.get_channel(ch_id)
            if not channel:
                try:
                    channel = await bot.fetch_channel(ch_id)
                except Exception as e:
                    invite_url = f"https://discord.com/oauth2/authorize?client_id={bot.user.id}&permissions=277025507328&scope=bot%20applications.commands"
                    logger.warning(f"Could not access Discord channel {ch_id}: {e}. Ensure bot is in the server: {invite_url}")
                    continue

            posted = load_posted_events_for_channel(ch_id)
            new_events = [ev for ev in events if (ev.get("url") or ev.get("id")) not in posted]
            logger.info(f"Channel #{channel.name} ({ch_id}): {len(events)} total upcoming, {len(new_events)} new.")

            count = 0
            for ev in new_events:
                ev_key = ev.get("url") or ev.get("id")
                try:
                    embed = create_event_embed(ev)
                    await channel.send(embed=embed)
                    posted.add(ev_key)
                    save_posted_events_for_channel(ch_id, posted)
                    count += 1
                    logger.info(f"Posted to #{channel.name}: '{ev['title']}' ({ev['url']})")
                    await asyncio.sleep(1.2) # Rate limit protection
                except Exception as e:
                    logger.error(f"Failed to post to #{channel.name}: {e}")
            posted_counts[ch_id] = count

        return posted_counts

# Scheduled 4-Hour Background Task
@tasks.loop(hours=CHECK_INTERVAL_HOURS)
async def scheduled_event_checker():
    logger.info("Executing scheduled event check...")
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
        await asyncio.sleep(2) # Give Gateway state a moment to populate channels
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
    status_msg = await ctx.send("🔍 Checking for new game development events...")
    try:
        target_id = ctx.channel.id if ctx.channel.id in CHANNEL_IDS else None
        results = await check_and_post_events(target_channel_id=target_id)
        total_posted = sum(results.values())
        if total_posted > 0:
            ch_summary = ", ".join(f"<#{cid}> ({cnt})" for cid, cnt in results.items() if cnt > 0)
            await status_msg.edit(content=f"✅ Check complete! **{total_posted}** new event(s) posted to {ch_summary}.")
        else:
            await status_msg.edit(content="✅ Check complete! No new events found on the calendar.")
    except Exception as e:
        logger.error(f"Error in !checkevents command: {e}")
        await status_msg.edit(content=f"❌ Error while checking events: `{e}`")

@cmd_checkevents.error
async def cmd_checkevents_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ You do not have permission to run this command (`Manage Messages` required).")
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(f"⚠️ An error occurred: `{error}`")

@bot.tree.command(name="checkevents", description="Check Dutch Games Industry calendar for new events")
@discord.app_commands.default_permissions(manage_messages=True)
async def slash_checkevents(interaction: discord.Interaction):
    """Slash command equivalent for checking events."""
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message(
            "⛔ You do not have permission to run this command (`Manage Messages` required).",
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=False)
    try:
        target_id = interaction.channel_id if interaction.channel_id in CHANNEL_IDS else None
        results = await check_and_post_events(target_channel_id=target_id)
        total_posted = sum(results.values())
        if total_posted > 0:
            ch_summary = ", ".join(f"<#{cid}> ({cnt})" for cid, cnt in results.items() if cnt > 0)
            await interaction.followup.send(f"✅ Check complete! **{total_posted}** new event(s) posted to {ch_summary}.")
        else:
            await interaction.followup.send("✅ Check complete! No new events found on the calendar.")
    except Exception as e:
        logger.error(f"Error in /checkevents command: {e}")
        await interaction.followup.send(f"❌ Error while checking events: `{e}`")

def main():
    logger.info("Starting John Event Discord Bot...")
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
