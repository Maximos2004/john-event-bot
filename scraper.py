import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import json
import logging
from urllib.parse import urljoin, quote_plus
from datetime import datetime, timezone

logger = logging.getLogger("john_event.scraper")

BASE_URL = "https://www.dutchgamesindustry.nl"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}

async def fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    """Fetch HTML content with timeout and standard browser headers."""
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.text()
            logger.warning(f"HTTP {resp.status} when fetching {url}")
            return ""
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return ""

async def extract_weticket_prices(session: aiohttp.ClientSession, weticket_url: str) -> str | None:
    """Extracts exact ticket prices from WeTicket storefront API."""
    try:
        m = re.search(r'https://([a-zA-Z0-9.-]+\.weticket\.io)/([a-zA-Z0-9_-]+)', weticket_url)
        if not m:
            return None
        domain, slug = m.group(1), m.group(2)
        shop_url = f"https://{domain}/event/{slug}/shop"
        html = await fetch_html(session, shop_url)
        match = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            return None
        d = json.loads(match.group(1))

        def find_key(obj, target):
            if isinstance(obj, dict):
                if target in obj:
                    return obj[target]
                for v in obj.values():
                    res = find_key(v, target)
                    if res:
                        return res
            elif isinstance(obj, list):
                for v in obj:
                    res = find_key(v, target)
                    if res:
                        return res
            return None

        org = find_key(d, "organisation")
        org_uuid = org.get("uuid") if isinstance(org, dict) else None
        first_ts = find_key(d, "first_timeslot")
        shop_uuid = first_ts.get("shop_uuid") if isinstance(first_ts, dict) else None
        ts_uuid = first_ts.get("uuid") if isinstance(first_ts, dict) else None

        if org_uuid and shop_uuid:
            api_url = f"https://ticketing-api.weticket.io/v2/api/visitor/organisations/{org_uuid}/shops/{shop_uuid}/product-types"
            if ts_uuid:
                api_url += f"?selectedTimeslotUuid={ts_uuid}"
            api_resp = await fetch_html(session, api_url)
            if api_resp:
                api_data = json.loads(api_resp)
                tickets = api_data.get("tickets", [])
                active_tickets = [t for t in tickets if t.get("status") == "active" and t.get("price") is not None]
                if active_tickets:
                    prices = [t["price"] / 100 for t in active_tickets]
                    min_p, max_p = min(prices), max(prices)
                    if min_p == 0 and max_p == 0:
                        return "FREE"
                    if min_p == max_p:
                        return f"€{min_p:.2f}"
                    return f"€{min_p:.2f} - €{max_p:.2f}"
    except Exception as e:
        logger.debug(f"WeTicket parse error: {e}")
    return None

async def extract_eventbrite_price(session: aiohttp.ClientSession, eb_url: str) -> str | None:
    """Extracts exact ticket prices or free status from Eventbrite."""
    try:
        html = await fetch_html(session, eb_url)
        for s in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(s)
                def find_offers(obj):
                    if isinstance(obj, dict):
                        if "offers" in obj:
                            return obj["offers"]
                        for v in obj.values():
                            res = find_offers(v)
                            if res:
                                return res
                    elif isinstance(obj, list):
                        for v in obj:
                            res = find_offers(v)
                            if res:
                                return res
                    return None

                offers = find_offers(d)
                if offers:
                    if isinstance(offers, list):
                        prices = [float(off["price"]) for off in offers if "price" in off]
                        if prices:
                            min_p, max_p = min(prices), max(prices)
                            if min_p == 0 and max_p == 0:
                                return "FREE"
                            if min_p == max_p:
                                return f"€{min_p:.2f}"
                            return f"€{min_p:.2f} - €{max_p:.2f}"
                    elif isinstance(offers, dict):
                        low = offers.get("lowPrice")
                        high = offers.get("highPrice")
                        price = offers.get("price")
                        if low is not None and high is not None:
                            l, h = float(low), float(high)
                            if l == 0 and h == 0:
                                return "FREE"
                            if l == h:
                                return f"€{l:.2f}"
                            return f"€{l:.2f} - €{h:.2f}"
                        elif price is not None:
                            p = float(price)
                            return "FREE" if p == 0 else f"€{p:.2f}"
                if d.get("isFree") is True:
                    return "FREE"
            except Exception:
                pass

        if '"isFree":true' in html.replace(" ", ""):
            return "FREE"

        m = re.search(r'"lowPrice":"(\d+(?:\.\d+)?)"', html)
        if m:
            val = float(m.group(1))
            return "FREE" if val == 0 else f"€{val:.2f}"
    except Exception as e:
        logger.debug(f"Eventbrite parse error: {e}")
    return None

async def resolve_price(session: aiohttp.ClientSession, title: str, description: str, page_html: str, page_soup: BeautifulSoup) -> str:
    """
    Determines exact price:
    - Queries external ticketing providers (WeTicket, Eventbrite)
    - Checks official event website for ticketing platforms
    - Parses direct € amounts and Free indicators
    - Returns 'FREE' or '€XX.XX' (or range '€XX.XX - €YY.YY')
    """
    container = page_soup.find("div", class_="event-view-container") or page_soup.find("main") or page_soup

    # 1. Collect external links strictly from the event content (ignoring site-wide header/footer)
    ext_links = []
    for a in container.find_all("a", href=True):
        h = a["href"].strip()
        if h.startswith("http") and "dutchgamesindustry.nl" not in h and not any(skip in h for skip in ["google.com", "facebook.com", "x.com", "twitter.com", "whatsapp.com"]):
            ext_links.append(h)

    # 2. Check direct ticketing URLs from the event container
    for link in ext_links:
        if "weticket.io" in link:
            wt_price = await extract_weticket_prices(session, link)
            if wt_price:
                return wt_price
        elif "eventbrite" in link:
            eb_price = await extract_eventbrite_price(session, link)
            if eb_price:
                return eb_price

    # 3. Check official external website if it links to a ticketing platform
    for link in ext_links:
        if not any(skip in link for skip in ["linkedin.com", "meetup.com", "instagram.com"]):
            site_html = await fetch_html(session, link)
            if site_html:
                wt_match = re.search(r'https://[a-zA-Z0-9.-]+\.weticket\.io/[a-zA-Z0-9/_-]+', site_html)
                if wt_match:
                    wt_price = await extract_weticket_prices(session, wt_match.group(0))
                    if wt_price:
                        return wt_price
                eb_match = re.search(r'https://www\.eventbrite\.[a-z.]+/e/[a-zA-Z0-9/_-]+', site_html)
                if eb_match:
                    eb_price = await extract_eventbrite_price(session, eb_match.group(0))
                    if eb_price:
                        return eb_price

    # 4. Search text for exact € amounts: e.g. €25.00, € 15, 25 euro
    container_text = container.text if container else f"{title}\n{description}"
    euro_patterns = [
        r'€\s*(\d+(?:[.,]\d{2})?)',
        r'(\d+(?:[.,]\d{2})?)\s*(?:euro|eur)\b',
    ]
    for pattern in euro_patterns:
        match = re.search(pattern, container_text, re.IGNORECASE)
        if match:
            amount = match.group(1).replace(',', '.')
            try:
                num = float(amount)
                return "FREE" if num == 0 else f"€{num:.2f}"
            except ValueError:
                return f"€{amount}"

    # 5. Default to FREE for community meetups / lunches / open days / talks
    return "FREE"

async def parse_event_details(session: aiohttp.ClientSession, event_url: str, initial_title: str, card_date: str, card_ts: int | None, sem: asyncio.Semaphore) -> dict:
    """Fetches and parses rich information from an individual event page."""
    event_id = event_url.rstrip("/").split("/")[-1]
    
    async with sem:
        html = await fetch_html(session, event_url)

    if not html:
        return {
            "id": event_id,
            "title": initial_title,
            "url": event_url,
            "timestamp": card_ts,
            "location_name": "Netherlands",
            "map_url": None,
            "price": "FREE",
            "image": None,
            "description": ""
        }

    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = initial_title
    h1 = soup.find("h1", class_="event-name-h1") or soup.find("h1")
    if h1 and h1.text.strip():
        title = h1.text.strip()

    # Description
    desc_el = soup.find("div", class_="event-description-text")
    description = desc_el.text.strip() if desc_el else ""

    # JSON-LD parsing
    json_ld_data = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") in ["Event", "EducationEvent"]:
                json_ld_data = data
                break
        except Exception:
            pass

    # Timestamp extraction (Discord timestamp requirement)
    unix_timestamp = card_ts
    if json_ld_data and json_ld_data.get("startDate"):
        try:
            dt = datetime.fromisoformat(json_ld_data["startDate"])
            unix_timestamp = int(dt.timestamp())
        except Exception:
            pass

    if not unix_timestamp:
        cd_elem = soup.find(class_="event-countdown")
        if cd_elem and cd_elem.get("data-timestamp"):
            try:
                unix_timestamp = int(cd_elem["data-timestamp"])
            except Exception:
                pass

    # Location & Google Maps Link
    location_name = "Netherlands"
    map_url = None

    address_a = soup.find("a", href=lambda h: h and "google.com/maps" in h)
    if address_a:
        map_url = address_a.get("href")
        location_name = " ".join(address_a.text.split())
    else:
        for row in soup.find_all("div", class_="sidebar-data-row"):
            label_el = row.find("span", class_="sidebar-data-label")
            val_el = row.find("span", class_="sidebar-data-value")
            if label_el and "address" in label_el.text.strip().lower() and val_el:
                location_name = " ".join(val_el.text.split())
                break

    if location_name == "Netherlands" and json_ld_data:
        loc = json_ld_data.get("location")
        if isinstance(loc, dict):
            location_name = loc.get("name") or loc.get("address", {}).get("streetAddress", "Netherlands")
        elif isinstance(loc, str):
            location_name = loc

    if "online" in location_name.lower():
        location_name = "Online"
        map_url = None
    elif not map_url and location_name not in ["Netherlands", "Online", "TBD"]:
        map_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(location_name)}"

    # Price resolution
    price = await resolve_price(session, title, description, html, soup)

    # Image
    image = None
    if json_ld_data and json_ld_data.get("image"):
        image = json_ld_data.get("image")
    else:
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            image = og_img["content"]

    return {
        "id": event_id,
        "title": title,
        "url": event_url,
        "timestamp": unix_timestamp,
        "location_name": location_name,
        "map_url": map_url,
        "price": price,
        "image": image,
        "description": description[:260] + "..." if len(description) > 260 else description
    }

async def fetch_upcoming_events(calendar_url: str = "https://www.dutchgamesindustry.nl/calendar") -> list[dict]:
    """
    Scrapes Dutch Games Industry calendar strictly for actual EVENTS (ignoring game releases).
    """
    logger.info(f"Fetching upcoming events from {calendar_url}...")
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, calendar_url)
        if not html:
            logger.error("Failed to retrieve calendar HTML")
            return []

        soup = BeautifulSoup(html, "html.parser")
        entries = soup.find_all("a", class_="calendar-item-entry")
        
        # STRICT FILTER: Only include real events (/event/...), exclude game releases (/game/...)
        event_entries = [e for e in entries if e.get("href", "").startswith("/event/")]
        logger.info(f"Found {len(event_entries)} upcoming events (filtered from {len(entries)} total entries)")

        sem = asyncio.Semaphore(5)
        tasks = []

        for entry in event_entries:
            href = entry.get("href", "")
            full_url = urljoin(BASE_URL, href)
            
            title_el = entry.find(class_="title")
            title = title_el.text.strip() if title_el else "Event"
            
            date_el = entry.find(class_="event-date")
            card_date = date_el.text.strip() if date_el else ""

            ts_elem = entry.find(class_="card-countdown-timer")
            card_ts = int(ts_elem["data-timestamp"]) if ts_elem and ts_elem.get("data-timestamp") else None

            tasks.append(parse_event_details(session, full_url, title, card_date, card_ts, sem))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        events = []
        for r in results:
            if isinstance(r, dict):
                events.append(r)
            else:
                logger.error(f"Error parsing event: {r}")

        logger.info(f"Successfully processed {len(events)} events")
        return events
