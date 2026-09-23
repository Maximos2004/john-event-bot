import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import json

BASE_URL = "https://www.dutchgamesindustry.nl"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}

async def fetch_html(session, url):
    async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status == 200:
            return await resp.text()
        return None

def extract_price(text, full_html=""):
    """
    Extracts price information:
    - Checks for free/gratis
    - Checks for € amounts
    - Checks for paid / ticket indicators
    """
    combined = f"{text}\n{full_html}"
    
    # 1. Search for euro amounts like €15, € 25,00, 15 euro, 25 EUR
    euro_patterns = [
        r'€\s*(\d+(?:[.,]\d{2})?)',
        r'(\d+(?:[.,]\d{2})?)\s*(?:euro|eur)\b',
    ]
    for pattern in euro_patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            amount = match.group(1).replace(',', '.')
            try:
                num = float(amount)
                if num == 0:
                    return "Free / Gratis"
                return f"€{amount}"
            except ValueError:
                return f"€{amount}"

    # 2. Check for explicit Free / Gratis indicators
    free_patterns = [
        r'\b(?:free\s+entrance|free\s+admission|free\s+entry|free\s+to\s+attend|gratis\s+toegang|gratis)\b',
        r'\bfree\b',
    ]
    for pattern in free_patterns:
        if re.search(pattern, combined, re.IGNORECASE):
            return "Free / Gratis"

    # 3. Check for ticket / paid mentions
    if re.search(r'\b(?:tickets?|ticketed|admission|eventbrite)\b', combined, re.IGNORECASE):
        # If there is a ticket link or mention
        return "Paid (Check tickets)"

    return "Free / Check event page"

async def parse_event_details(session, event_url, initial_title, card_date, categories):
    html = await fetch_html(session, event_url)
    if not html:
        return {
            "title": initial_title,
            "url": event_url,
            "date_time": card_date or "Check event page",
            "location": "Netherlands",
            "price": "Check event page",
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

    # JSON-LD extraction
    json_ld_data = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "Event":
                json_ld_data = data
                break
        except Exception:
            pass

    # Date and Time
    date_val = None
    start_time = None
    end_time = None
    day_val = None

    # Parse sidebar rows
    for row in soup.find_all("div", class_="sidebar-data-row"):
        label_el = row.find("span", class_="sidebar-data-label")
        val_el = row.find("span", class_="sidebar-data-value")
        if not label_el or not val_el:
            continue
        label = label_el.text.strip().lower()
        val = val_el.text.strip()
        if "date" in label:
            date_val = val
        elif "day" in label:
            day_val = val
        elif "start time" in label:
            start_time = val
        elif "end time" in label:
            end_time = val

    # Combine date & time
    date_time_str = ""
    if day_val and date_val:
        date_time_str = f"{day_val}, {date_val}"
    elif date_val:
        date_time_str = date_val
    elif card_date:
        date_time_str = card_date

    if start_time:
        if end_time:
            date_time_str += f" • {start_time} - {end_time}"
        else:
            date_time_str += f" • {start_time}"

    if not date_time_str and json_ld_data:
        date_time_str = json_ld_data.get("startDate", "Check event page")

    # Location
    location_str = "Netherlands"
    # Check sidebar address
    for row in soup.find_all("div", class_="sidebar-data-row"):
        label_el = row.find("span", class_="sidebar-data-label")
        val_el = row.find("span", class_="sidebar-data-value")
        if label_el and "address" in label_el.text.strip().lower() and val_el:
            location_str = " ".join(val_el.text.split())
            break

    if location_str == "Netherlands" and json_ld_data:
        loc = json_ld_data.get("location")
        if isinstance(loc, dict):
            location_str = loc.get("name") or loc.get("address", {}).get("streetAddress", "Netherlands")
        elif isinstance(loc, str):
            location_str = loc

    # Check for Online indicator
    if any(c.lower() in ["webinar", "online"] for c in categories) or "online" in location_str.lower():
        location_str = "Online"

    # Price
    # Inspect sidebar / action buttons / description
    sidebar_text = soup.find("aside", class_="event-sidebar-content")
    sidebar_content = sidebar_text.text if sidebar_text else ""
    price = extract_price(description, sidebar_content)

    # Image
    image = None
    if json_ld_data and json_ld_data.get("image"):
        image = json_ld_data.get("image")
    else:
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            image = og_img["content"]

    return {
        "title": title,
        "url": event_url,
        "date_time": date_time_str or "Check event page",
        "location": location_str,
        "price": price,
        "image": image,
        "description": description[:280] + "..." if len(description) > 280 else description
    }

async def scrape_events():
    calendar_url = "https://www.dutchgamesindustry.nl/calendar"
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, calendar_url)
        if not html:
            print("Failed to fetch calendar HTML")
            return []

        soup = BeautifulSoup(html, "html.parser")
        entries = soup.find_all("a", class_="calendar-item-entry")
        print(f"Found {len(entries)} calendar entries")

        events = []
        for entry in entries:
            href = entry.get("href", "")
            if not href:
                continue

            full_url = f"{BASE_URL}{href}" if href.startswith("/") else href
            
            # Extract basic info from card
            title_el = entry.find(class_="title")
            title = title_el.text.strip() if title_el else "Unknown Event"
            
            date_el = entry.find(class_="event-date")
            card_date = date_el.text.strip() if date_el else ""

            categories = []
            badge_els = entry.find_all(class_="event-category-badge")
            for b in badge_els:
                categories.append(b.text.strip())

            # Fetch rich details
            details = await parse_event_details(session, full_url, title, card_date, categories)
            events.append(details)

        return events

async def main():
    events = await scrape_events()
    print(f"Scraped {len(events)} events.")
    for i, ev in enumerate(events[:5]):
        print(f"\n--- Event {i+1} ---")
        print(f"Title: {ev['title']}")
        print(f"URL: {ev['url']}")
        print(f"Date & Time: {ev['date_time']}")
        print(f"Location: {ev['location']}")
        print(f"Price: {ev['price']}")
        print(f"Image: {ev['image']}")
        print(f"Description: {ev['description'][:100]}...")

if __name__ == "__main__":
    asyncio.run(main())
