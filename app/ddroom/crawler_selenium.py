"""Scrape rental listings from a user-provided DDROOM search URL."""

import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from libs.utils import render_images, render_link, use_selenium, write_file

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"), override=True)


def page_url(search_url: str, page: int) -> str:
    parts = urlsplit(search_url)
    # DDROOM does not accept the previously generated open-ended ping=10,+.
    # The >=10 ping constraint is enforced by parse_listings instead.
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != "page" and not (key == "ping" and value == "10,+")]
    query.append(("page", str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def parse_listings(soup: BeautifulSoup, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    listings = []
    seen = set()
    for card in soup.select("div.md\\:flex.md\\:py-6"):
        title_node = card.select_one("h3 a[href*='/object/']")
        if not title_node:
            continue
        href = title_node.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)
        price_node = card.select_one("span.text-xl.font-semibold")
        location_node = card.select_one(".flex.items-center.text-gray-500")
        metadata_node = card.select_one("div.mt-2.text-gray-500")
        metadata = metadata_node.get_text(" ", strip=True) if metadata_node else ""
        area_match = re.search(r"([\d.]+\s*坪)", metadata)
        area_ping = float(re.search(r"([\d.]+)", area_match.group(1)).group(1)) if area_match else None
        if area_ping is None or area_ping < 10:
            continue
        address = location_node.get_text(" ", strip=True) if location_node else ""
        price = price_node.get_text(" ", strip=True) if price_node else ""
        price_match = re.search(r"[\d,]+", price)
        rent = int(price_match.group().replace(',', '')) if price_match else None
        rooms = re.search(r"(?:^|[^\d])(\d+)\s*房", metadata)
        tags = [node.get_text(strip=True) for node in card.select('span.bg-gray-100')]
        pets = [tag for tag in tags if tag in {'可養貓', '可養狗', '可養其他寵物'}]
        if filters.get('city') and not address.startswith(filters['city']):
            continue
        if filters.get('area') and not any(address.startswith(filters.get('city', '') + area) for area in filters['area'].split(',')):
            continue
        if filters.get('min_rent') and (rent is None or rent < int(filters['min_rent'])):
            continue
        if filters.get('max_rent') and (rent is None or rent > int(filters['max_rent'])):
            continue
        if filters.get('bedroom') and (not rooms or int(rooms.group(1)) != int(filters['bedroom'])):
            continue
        if filters.get('space') == 'whole' and '整層' not in metadata:
            continue
        if filters.get('require_pet') and not pets:
            continue
        image = card.select_one("img[src*='static.dd-room.com']")
        listings.append(
            {
                "title": title_node.get_text(" ", strip=True),
                "price": price,
                "address": address,
                "pets": pets,
                "floor": metadata,
                "area": area_match.group(1) if area_match else "",
                "images": [image.get("src")] if image and image.get("src", "").startswith("http") else [],
                "link": href if href.startswith("http") else f"https://www.dd-room.com{href}",
            }
        )
    return listings


def main() -> None:
    search_url = os.getenv("DDROOM_FILTER_URL", "").strip()
    if not search_url:
        print("Please set DDROOM_FILTER_URL in .env")
        print("Open https://www.dd-room.com/search, set your filters, then copy the resulting URL.")
        sys.exit(1)
    if urlsplit(search_url).hostname not in {"dd-room.com", "www.dd-room.com"}:
        print("DDROOM_FILTER_URL must be a www.dd-room.com or dd-room.com URL")
        sys.exit(1)
    try:
        max_pages = max(1, int(os.getenv("DDROOM_MAX_PAGES", "20")))
        page_delay = max(1.0, float(os.getenv("DDROOM_PAGE_DELAY_SECONDS", "2")))
    except ValueError:
        print("DDROOM_MAX_PAGES must be an integer and DDROOM_PAGE_DELAY_SECONDS must be a number")
        sys.exit(1)

    driver = use_selenium()
    filters = dict(parse_qsl(urlsplit(search_url).query))
    filters['require_pet'] = os.getenv('DDROOM_REQUIRE_PET', '0') == '1'
    data: list[dict] = []
    seen_links: set[str] = set()
    pages_scraped = 0
    try:
        for page in range(1, max_pages + 1):
            print(f"Getting DDROOM page {page}/{max_pages}")
            driver.get(page_url(search_url, page))
            try:
                WebDriverWait(driver, 30).until(
                    lambda browser: browser.find_elements(By.CSS_SELECTOR, "div.md\\:flex.md\\:py-6 h3 a[href*='/object/']")
                    or '這些條件目前沒有符合的物件' in browser.find_element(By.TAG_NAME, 'body').text
                )
            except TimeoutException as error:
                raise RuntimeError(
                    f"第 {page} 頁未載入可辨識的房源列表，無法確認是否真的沒有結果；保留上次資料。"
                ) from error
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            card_count = len(soup.select("div.md\\:flex.md\\:py-6 h3 a[href*='/object/']"))
            if not card_count:
                print('網站確認這些條件目前沒有符合的物件。', flush=True)
                break
            page_data = parse_listings(soup, filters)
            print(f"第 {page} 頁：讀取 {card_count} 筆房源，{len(page_data)} 筆符合地區、租金、房數、寵物與 10 坪以上條件", flush=True)
            data.extend(item for item in page_data if item['link'] not in seen_links)
            seen_links.update(item['link'] for item in page_data)
            pages_scraped = page
            if card_count < 20:
                break
            time.sleep(page_delay)
    finally:
        driver.quit()

    columns = ["title", "price", "address", "floor", "area", "images", "link", "pets"]
    df = pd.DataFrame(data, columns=columns)
    write_file(df.to_json(orient="records", force_ascii=False), "ddroom.json")
    df["images"] = df["images"].apply(render_images)
    df["link"] = [render_link(row['link'], row['title']) for row in data]
    write_file(df.to_html(escape=False, index=False), "ddroom.html")
    print(f"Saved {len(df)} DDROOM listings from {pages_scraped} page(s) to static/ddroom.html and static/ddroom.json")


if __name__ == "__main__":
    main()
