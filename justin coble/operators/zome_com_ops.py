# operators/zome_com_ops.py
from playwright.sync_api import sync_playwright, TimeoutError
import os
import time
from typing import List, Dict, Tuple

URL_HOME = "https://www.xome.com/"

HEADLESS = os.getenv("XOME_HEADLESS", "1").lower() not in ("0", "false", "no")
SCROLL_WAIT_SEC = float(os.getenv("XOME_SCROLL_WAIT_SEC", "0.8"))
NAV_TIMEOUT_MS = int(os.getenv("XOME_NAV_TIMEOUT_MS", "60000"))
LIST_TIMEOUT_MS = int(os.getenv("XOME_LIST_TIMEOUT_MS", "30000"))

# Enrichment (optional)
ENRICH_SLEEP_SEC = float(os.getenv("XOME_ENRICH_SLEEP_SEC", "0.5"))
ENRICH_EVERY_N = int(os.getenv("XOME_ENRICH_EVERY_N", "1"))  # 1=enrich every row

# Import PropStream enrichment if available
try:
    from operators.realestate_ops import fetchPropStreamEstimates
    _HAVE_PROPSTREAM = True
except Exception:
    _HAVE_PROPSTREAM = False


def _wait_idle(page, ms: int = 1500):
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass


def _safe_text(el) -> str:
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def _enrich_with_propstream(street: str, city: str, zip5: str) -> Tuple[float, float, str, str]:
    if not _HAVE_PROPSTREAM:
        return 0.0, 0.0, "", ""
    try:
        eq, est, first, last = fetchPropStreamEstimates(street, city, zip5)
        if ENRICH_SLEEP_SEC > 0:
            time.sleep(ENRICH_SLEEP_SEC)
        return float(eq or 0.0), float(est or 0.0), first or "", last or ""
    except Exception as e:
        print(f"  [XOME] PropStream enrichment failed for {street}, {city} {zip5}: {e}")
        return 0.0, 0.0, "", ""


def harvestZomeCom() -> List[Dict]:
    print("[RUN] Harvesting Xome.com…")
    leads: List[Dict] = []  # <- we will ALWAYS return this

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            page = browser.new_page()

            # 1) Go to home and search Georgia
            page.goto(URL_HOME, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            _wait_idle(page, 2000)
            try:
                page.wait_for_selector("input#location-auctions", timeout=LIST_TIMEOUT_MS)
                page.click("input#location-auctions")
                page.fill("input#location-auctions", "")
                page.type("input#location-auctions", "Georgia", delay=80)
                # pick first suggestion
                try:
                    page.wait_for_selector("ul.ac_results li", timeout=5000)
                    page.click("ul.ac_results li")
                except TimeoutError:
                    page.keyboard.press("ArrowDown")
                    page.keyboard.press("Enter")
                _wait_idle(page, 2500)
            except TimeoutError:
                print("⚠️ Search box not found on Xome.com")
                try:
                    browser.close()
                finally:
                    print(f"[RUN] Returning {len(leads)} leads (early).")
                    return leads  # empty list

            # 2) read expected total (best-effort)
            total = None
            try:
                page.wait_for_selector("span#totalPropertiesCount", timeout=LIST_TIMEOUT_MS)
                raw_total = _safe_text(page.locator("span#totalPropertiesCount").first)
                total = int(raw_total) if raw_total.isdigit() else None
                if total:
                    print(f"[RUN] Expecting {total} listings")
            except Exception as e:
                print(f"⚠️ Could not get total count: {e}")

            # 3) wait for first result
            try:
                page.wait_for_selector("p.auction-date", timeout=LIST_TIMEOUT_MS)
            except TimeoutError:
                print("⚠️ No auction-date elements found on Xome.com")
                try:
                    browser.close()
                finally:
                    print(f"[RUN] Returning {len(leads)} leads (no results).")
                    return leads

            # 4) scroll until loaded or stalls
            stable_cycles = 0
            prev_len = 0
            max_scrolls = 400  # safety
            scrolls = 0
            while scrolls < max_scrolls:
                items = page.query_selector_all("p.auction-date")
                curr_len = len(items)

                if total and curr_len >= total:
                    print(f"[RUN] Loaded {curr_len} of {total} listings (reached total)")
                    break

                if curr_len == prev_len:
                    stable_cycles += 1
                else:
                    stable_cycles = 0

                if stable_cycles >= 3:  # 3 consecutive stalls = done
                    if total:
                        print(f"[RUN] Loaded {curr_len} of {total} listings (stalled)")
                    else:
                        print(f"[RUN] Loaded {curr_len} listings (no total; stalled)")
                    break

                prev_len = curr_len
                try:
                    page.keyboard.press("End")
                except Exception:
                    pass
                try:
                    page.mouse.wheel(0, 3000)
                except Exception:
                    pass

                time.sleep(SCROLL_WAIT_SEC)
                scrolls += 1

            # 5) scrape from the list
            dates = page.query_selector_all("p.auction-date")
            links = page.query_selector_all("address a.address-linktext")
            bids  = page.query_selector_all("div.property-bidding span")

            count = min(len(dates), len(links), len(bids))
            print(f"[RUN] Scraping {count} cards from list view")

            for i in range(count):
                try:
                    # date
                    full = _safe_text(dates[i])
                    sale_date = full.split(" -", 1)[0].replace("Auction Begins:", "").strip()

                    # two outer spans (street + "City, ST ZIP")
                    outer_spans = links[i].query_selector_all(":scope > span")
                    if len(outer_spans) >= 2:
                        street = _safe_text(outer_spans[0])
                        loc_text = _safe_text(outer_spans[1])
                    else:
                        # fallback: split by newlines
                        txt = _safe_text(links[i])
                        parts_all = [t.strip() for t in txt.split("\n") if t.strip()]
                        street = parts_all[0] if parts_all else ""
                        loc_text = parts_all[1] if len(parts_all) > 1 else ""

                    city = ""
                    zip5 = ""
                    if "," in loc_text:
                        city = loc_text.split(",", 1)[0].strip()
                        right = loc_text.split(",", 1)[1].strip()  # "GA 30087"
                        right_parts = right.split()
                        if right_parts:
                            zip5 = right_parts[-1]

                    bid = _safe_text(bids[i])

                    lead = {
                        "saleDate":   sale_date,
                        "fileNumber": "",
                        "property":   street,
                        "city":       city,
                        "zip":        zip5,
                        "county":     "",
                        "bid":        bid,
                        "firstName":  "",
                        "lastName":   "",
                        "equity":     0.0,
                    }

                    # Optional enrichment for names/equity
                    if _HAVE_PROPSTREAM and (ENRICH_EVERY_N <= 1 or (i % ENRICH_EVERY_N) == 0):
                        eq, _est, first, last = _enrich_with_propstream(street, city, zip5)
                        lead["firstName"] = first
                        lead["lastName"]  = last
                        lead["equity"]    = eq

                    leads.append(lead)

                except Exception as e:
                    print(f"⚠️ Card {i+1}/{count} parse failed: {e}")
                    continue

            try:
                browser.close()
            except Exception:
                pass

    except Exception as e:
        # Any unexpected error -> return whatever we collected so far, not None
        print(f"⚠️ Xome run failed unexpectedly: {e}")

    if leads is None:
        # Just in case someone edits the file later and accidentally sets it to None
        leads = []

    print(f"[RUN] Fetched {len(leads)} leads from Xome.com")
    return leads
