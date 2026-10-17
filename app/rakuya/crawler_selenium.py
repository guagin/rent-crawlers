"""Scrape rental listings from a user-provided Rakuya search URL."""

import os
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from libs.utils import get_page_content, render_images, render_link, use_selenium, write_file

load_dotenv()


def page_url(search_url: str, page: int) -> str:
    """Keep the user's search filters while changing only the page number."""
    parts = urlsplit(search_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if page == 1:
        query = [(key, value) for key, value in query if key != "page"]
    else:
        query = [(key, value) for key, value in query if key != "page"]
        query.append(("page", str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def parse_listings(soup: BeautifulSoup) -> list[dict]:
    listings = []
    for card in soup.select("section.grid-item.search-obj"):
        anchor = card.select_one("a[href]")
        title_node = card.select_one(".card__head h2")
        if not anchor or not title_node:
            continue

        district = card.select_one(".info__geo--area")
        community = card.select_one(".info__geo--community")
        floor = card.select_one(".info__floor")
        area = card.select_one(".info__space--main")
        price = card.select_one(".info__price--total")
        image = card.select_one(".card__photo img")
        listings.append(
            {
                "title": title_node.get_text(" ", strip=True),
                "price": price.get_text(" ", strip=True) if price else "",
                # Rakuya's result cards expose the district/community, not a full street address.
                "address": " ".join(
                    node.get_text(" ", strip=True)
                    for node in (district, community)
                    if node is not None and node.get_text(strip=True)
                ),
                "floor": floor.get_text(" ", strip=True) if floor else "",
                "area": area.get_text(" ", strip=True) if area else "",
                "images": [image.get("src") or image.get("data-src")] if image and (image.get("src") or image.get("data-src")) else [],
                "link": anchor.get("href", ""),
            }
        )
    return listings


def is_challenge_page(driver, soup: BeautifulSoup) -> bool:
    title = (driver.title or "").lower()
    body = soup.get_text(" ", strip=True).lower()
    markers = (
        "cloudflare",
        "checking your browser",
        "verify you are human",
        "just a moment",
        "請稍候",
        "驗證您是真人",
        "人機驗證",
    )
    return any(marker in title or marker in body for marker in markers)


def main() -> None:
    search_url = os.getenv("RAKUYA_FILTER_URL", "").strip()
    if not search_url:
        print("Please set RAKUYA_FILTER_URL in .env")
        print("Open https://rent.rakuya.com.tw/, set your filters, then copy the resulting URL.")
        sys.exit(1)
    if urlsplit(search_url).hostname not in {"rent.rakuya.com.tw", "www.rent.rakuya.com.tw"}:
        print("RAKUYA_FILTER_URL must be a rent.rakuya.com.tw URL")
        sys.exit(1)

    try:
        max_pages = max(1, int(os.getenv("RAKUYA_MAX_PAGES", "20")))
    except ValueError:
        print("RAKUYA_MAX_PAGES must be a positive integer")
        sys.exit(1)
    try:
        page_delay = max(2.0, float(os.getenv("RAKUYA_PAGE_DELAY_SECONDS", "3")))
    except ValueError:
        print("RAKUYA_PAGE_DELAY_SECONDS must be a number")
        sys.exit(1)

    driver = use_selenium()
    data: list[dict] = []
    seen_links: set[str] = set()
    pages_scraped = 0
    try:
        for page in range(1, max_pages + 1):
            print(f"Getting Rakuya page {page}/{max_pages}")
            soup = get_page_content(driver, page_url(search_url, page))
            # The listing cards can hydrate after navigation, so wait on the
            # actual cards instead of assuming the page is ready after a delay.
            if page > 1 and not soup.select("section.grid-item.search-obj"):
                try:
                    WebDriverWait(driver, 10).until(
                        lambda browser: browser.find_elements(
                            By.CSS_SELECTOR, "section.grid-item.search-obj"
                        )
                    )
                except TimeoutException:
                    pass
                soup = BeautifulSoup(driver.page_source, "html.parser")
            page_data = parse_listings(soup)
            print(f"Found {len(page_data)} listing cards on page {page}")
            if not page_data and is_challenge_page(driver, soup):
                print("A Cloudflare/human-verification page was returned.")
                if not sys.stdin.isatty():
                    print("No interactive terminal is available; rerun this script from PowerShell to complete the check manually.")
                    break
                try:
                    input("Complete the check manually in the Chrome window, wait for listings, then press Enter here. ")
                except EOFError:
                    print("No interactive terminal is available; stopping with the listings already collected.")
                    break
                try:
                    WebDriverWait(driver, 30).until(
                        lambda browser: browser.find_elements(
                            By.CSS_SELECTOR, "section.grid-item.search-obj"
                        )
                    )
                except TimeoutException:
                    pass
                soup = BeautifulSoup(driver.page_source, "html.parser")
                page_data = parse_listings(soup)
                print(f"Found {len(page_data)} listing cards after manual verification")
            if not page_data:
                if page > 1:
                    print("No listing cards were returned; stopping pagination (the page may not have loaded or may be restricted).")
                break
            new_items = [item for item in page_data if item["link"] not in seen_links]
            print(f"Added {len(new_items)} new listings")
            if not new_items:
                break
            data.extend(new_items)
            seen_links.update(item["link"] for item in new_items)
            pages_scraped = page
            time.sleep(page_delay)
    finally:
        driver.quit()

    columns = ["title", "price", "address", "floor", "area", "images", "link"]
    df = pd.DataFrame(data, columns=columns)
    write_file(df.to_json(orient="records", force_ascii=False), "rakuya.json")
    df["images"] = df["images"].apply(render_images)
    df["link"] = df.apply(lambda row: render_link(row["link"], row["title"]), axis=1)
    write_file(df.to_html(escape=False, index=False), "rakuya.html")
    print(f"Saved {len(df)} Rakuya listings from {pages_scraped} page(s) to static/rakuya.html and static/rakuya.json")


if __name__ == "__main__":
    main()
