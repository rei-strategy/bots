# operators/auction_com_ops.py

from playwright.sync_api import sync_playwright, TimeoutError
import re
import time

URL = "https://www.auction.com/residential/GA/"

def harvestAuctionCom():
    print("[RUN] Harvesting Auction.com…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, timeout=60000)

        # 1) wait for at least one card
        try:
            page.wait_for_selector("div[data-elm-id$='_root']", timeout=30000)
        except TimeoutError:
            print("⚠️ No listings found on Auction.com")
            browser.close()
            return []

        # 2) figure out how many we expect
        total_count = None
        total_el = page.query_selector("span[data-elm-id='asset-list_totals_in_count']")
        if total_el:
            txt = total_el.inner_text().strip()
            try:
                total_count = int(re.sub(r"\D", "", txt))
                print(f"[RUN] Expecting {total_count} listings")
            except ValueError:
                print(f"⚠️ Couldn't parse total count from '{txt}'")

        # 3) scroll the last card into view until we've loaded them all
        prev_len   = 0
        stable     = 0
        max_stable = 15   # allow 15 rounds of no-growth
        rounds     = 0
        max_rounds = 100  # absolute cap

        while rounds < max_rounds:
            cards = page.query_selector_all("div[data-elm-id$='_root']")
            curr_len = len(cards)
            print(f"[RUN] Currently have {curr_len} listings")

            # stop if we've got them all
            if total_count and curr_len >= total_count:
                print("[RUN] Reached expected count; stopping scroll")
                break

            # track stability
            if curr_len == prev_len:
                stable += 1
                if stable >= max_stable:
                    print("[RUN] No new listings for a while; stopping scroll")
                    break
            else:
                stable = 0

            prev_len = curr_len
            rounds += 1

            if not cards:
                break

            # scroll the last card into view
            try:
                cards[-1].scroll_into_view_if_needed(timeout=5000)
            except TimeoutError:
                pass

            # give Auction.com time to load the next batch
            page.wait_for_timeout(3000)

        # 4) scrape everything you found
        cards = page.query_selector_all("div[data-elm-id$='_root']")
        print(f"[RUN] Found {len(cards)} listings")
        leads = []

        for idx, root in enumerate(cards, start=1):
            # — Sale Date —
            info_div = root.query_selector("div.listing-card-asset-info div")
            txt = info_div.inner_text().strip() if info_div else ""
            dates = re.findall(r"[A-Za-z]{3} \d{1,2}", txt)
            sale_date = dates[0] if dates else ""

            # — Street address —
            street_el = root.query_selector("h3[data-elm-id^='address_line_1']")
            street = street_el.inner_text().strip() if street_el else ""

            # — City / Zip / County —
            loc_el = root.query_selector("h3[data-elm-id^='address_line_2']")
            loc_text = loc_el.inner_text().strip() if loc_el else ""
            parts = [p.strip() for p in loc_text.split(",")]
            city     = parts[0] if parts else ""
            zip_code = parts[1].split()[-1] if len(parts) > 1 else ""
            county   = parts[2].replace(" County", "").strip() if len(parts) > 2 else ""

            # — Bid —
            bid_el = root.query_selector("div[title^='$']")
            bid = bid_el.inner_text().strip() if bid_el else ""

            print(f"[RUN] Auction.com Lead {idx}: {street}, {city} ({sale_date})")
            leads.append({
                "saleDate":   sale_date,
                "fileNumber": "",  # none on site
                "property":   street,
                "city":       city,
                "zip":        zip_code,
                "county":     county,
                "bid":        bid,
            })

        browser.close()

    print(f"[RUN] Fetched {len(leads)} leads from Auction.com")
    return leads