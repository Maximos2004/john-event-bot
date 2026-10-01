import logging
import re
import html
from datetime import datetime, timezone
from urllib.parse import quote_plus
import zoneinfo
import aiohttp

logger = logging.getLogger("john_event.api")

DGI_EVENTS_API = "https://www.dutchgamesindustry.nl/api/events"
AMS_TZ = zoneinfo.ZoneInfo("Europe/Amsterdam")

def clean_html_text(raw_html: str) -> str:
    """Cleans raw HTML description text into readable plaintext/markdown."""
    if not raw_html:
        return ""
    # Convert line breaks and paragraph closings to newlines
    text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.I)
    text = re.sub(r'</p>', '\n\n', text, flags=re.I)
    # Strip remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities (&amp;, &#8217;, etc.)
    text = html.unescape(text)
    # Collapse 3+ consecutive newlines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def parse_dgi_event(item: dict) -> dict | None:
    """Parses a raw DGI API event item into a structured event dict."""
    event_id = item.get("id")
    title = item.get("name")
    start_date_str = item.get("startDate")

    if not event_id or not title or not start_date_str:
        return None

    start_time_str = item.get("startTime") or "09:00"
    end_date_str = item.get("endDate")
    end_time_str = item.get("endTime")

    # Parse start datetime in Europe/Amsterdam timezone
    try:
        start_parts = [int(p) for p in start_date_str.split("-")]
        time_parts = [int(p) for p in start_time_str.split(":")[:2]]
        start_dt = datetime(start_parts[0], start_parts[1], start_parts[2], time_parts[0], time_parts[1], tzinfo=AMS_TZ)
        start_ts = int(start_dt.timestamp())
    except Exception as e:
        logger.warning(f"Error parsing start date/time '{start_date_str} {start_time_str}' for event '{title}': {e}")
        return None

    # Parse end datetime or calculate expiry
    if end_date_str:
        try:
            end_parts = [int(p) for p in end_date_str.split("-")]
            e_time = [int(p) for p in (end_time_str or "23:59").split(":")[:2]]
            end_dt = datetime(end_parts[0], end_parts[1], end_parts[2], e_time[0], e_time[1], tzinfo=AMS_TZ)
            expiry_ts = int(end_dt.timestamp())
        except Exception:
            expiry_ts = start_ts + (24 * 3600)
    else:
        # Default expiry: 24h after start time
        expiry_ts = start_ts + (24 * 3600)

    # Address & Maps
    address = (item.get("address") or "").strip()
    map_url = None
    if address and address.lower() != "none":
        map_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"
    else:
        address = "🌐 Online / See event page"

    # Image URL
    img_filename = item.get("image")
    image_url = None
    if img_filename and img_filename.lower() != "none":
        image_url = f"https://www.dutchgamesindustry.nl/images/uploaded/{img_filename}"

    # Official URL
    event_url = item.get("eventURL") or f"https://www.dutchgamesindustry.nl/event/{event_id}"

    # Categories
    categories = item.get("categories") or []

    # Clean description (truncate to ~300 chars for sleek embed)
    desc = clean_html_text(item.get("description", ""))
    if len(desc) > 300:
        desc = desc[:297].rstrip() + "..."

    return {
        "id": event_id,
        "title": title,
        "start_timestamp": start_ts,
        "expiry_timestamp": expiry_ts,
        "has_exact_time": bool(item.get("startTime")),
        "address": address,
        "map_url": map_url,
        "url": event_url,
        "image": image_url,
        "categories": categories,
        "description": desc,
    }

async def fetch_upcoming_events() -> list[dict]:
    """
    Fetches events from the official Dutch Games Industry REST API.
    Filters out past events and sorts upcoming events chronologically.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JohnEventBot/2.0",
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=15)
    now_ts = int(datetime.now(timezone.utc).timestamp())

    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(DGI_EVENTS_API) as resp:
                if resp.status != 200:
                    logger.error(f"DGI API returned status {resp.status}")
                    return []
                raw_events = await resp.json()

        today_date = datetime.now(AMS_TZ).date()
        events = []
        for item in raw_events:
            ev = parse_dgi_event(item)
            if not ev:
                continue
            ev_start_date = datetime.fromtimestamp(ev["start_timestamp"], tz=AMS_TZ).date()
            if ev_start_date >= today_date and ev["expiry_timestamp"] >= now_ts:
                events.append(ev)

        # Sort chronologically
        events.sort(key=lambda x: x["start_timestamp"])
        logger.info(f"Successfully fetched {len(events)} active upcoming event(s) from DGI API.")
        return events

    except Exception as e:
        logger.error(f"Failed to fetch events from DGI API: {e}", exc_info=True)
        return []
