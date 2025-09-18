# operators/servicelink_auction_ops.py

from playwright.sync_api import sync_playwright, TimeoutError
import os
from typing import List, Dict, Optional, Set

URL = "https://www.servicelinkauction.com/georgia"

# Pagination / waits
MAX_PAGES = int(os.getenv("SL_MAX_PAGES", "50"))
NAV_TIMEOUT_MS = int(os.getenv("SL_NAV_TIMEOUT_MS", "60000"))
LIST_TIMEOUT_MS = int(os.getenv("SL_LIST_TIMEOUT_MS", "30000"))
IDLE_WAIT_MS = int(os.getenv("SL_IDLE_WAIT_MS", "1500"))

# ---- Google Sheets resume config ----
# Spreadsheet/tab that already contains your previously-added ServiceLink rows.
SL_SHEET_ID   = os.getenv("SL_SHEET_ID", "").strip()
SL_WORKSHEET  = os.getenv("SL_WORKSHEET", "ServiceLink Auction").strip()
SL_RESUME_COL = os.getenv("SL_RESUME_COL", "Property").strip()  # header name to match

# Stop scraping after we hit this many **consecutive** already-present rows
SL_STOP_AFTER_CONSEC_SEEN = int(os.getenv("SL_STOP_AFTER_CONSEC_SEEN", "30"))

# Optional composite-key mode if street alone isn’t unique enough:
#   "property" (default) or "prop_city_zip"
SL_RESUME_MODE = os.getenv("SL_RESUME_MODE", "property").strip().lower()

# Try to use ADC for Sheets (optional)
_gspread = None
_google_auth_default = None
try:
    import gspread
    import google.auth
    _gspread = gspread
    _google_auth_default = google.auth.default
except Exception:
    _gspread = None
    _google_auth_default = None


def _wait_idle(page, ms: int = IDLE_WAIT_MS):
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass


def _collect_listings_on_current_page(page) -> List[Dict]:
    """
    Scrape ONE page using your original, working selectors.
    Returns a list of dicts with keys: saleDate, fileNumber, property, city, zip, county, bid
    """
    try:
        page.wait_for_selector("div.address-line-1.ng-star-inserted", timeout=LIST_TIMEOUT_MS)
    except TimeoutError:
        return []

    address_els  = page.query_selector_all("div.address-line-1.ng-star-inserted")
    location_els = page.query_selector_all("div.address-line-1.ng-star-inserted + div")
    bid_els      = page.query_selector_all("div.propertyValue")
    date_els     = page.query_selector_all("div.bottom-label")

    count = min(len(address_els), len(location_els), len(bid_els), len(date_els))
    leads: List[Dict] = []

    for i in range(count):
        raw_date = (date_els[i].inner_text() or "").strip()
        sale_date = raw_date.replace("Auction Begins:", "").strip()

        street = (address_els[i].inner_text() or "").strip()

        loc_raw = (location_els[i].inner_text() or "").strip()
        parts = [p.strip() for p in loc_raw.split("⋅")]
        city     = parts[0] if len(parts) > 0 else ""
        zip_code = parts[2] if len(parts) > 2 else ""
        county   = parts[3] if len(parts) > 3 else ""
        county   = county.replace("County", "").strip()

        raw_bid = (bid_els[i].inner_text() or "").strip()
        bid = raw_bid.replace("Starting bid:", "").strip()

        leads.append({
            "saleDate":   sale_date,
            "fileNumber": "",       # none on site
            "property":   street,   # resume key (alone or part of composite)
            "city":       city,
            "zip":        zip_code,
            "county":     county,
            "bid":        bid,
        })

    return leads


def _find_next_numeric_page_link(page, current_page_index: int):
    """
    Find <a class="page-link page-item">N</a> where N = current_page_index + 1.
    Returns locator or None.
    """
    next_index = current_page_index + 1
    loc = page.locator("a.page-link.page-item", has_text=str(next_index))
    return loc if loc.count() > 0 else None


# ---------------- Sheets resume helpers ----------------

def _normalize(s: str) -> str:
    return (s or "").strip().upper()


def _key_for(lead: Dict) -> str:
    if SL_RESUME_MODE == "prop_city_zip":
        return _normalize(f"{lead.get('property','')}|{lead.get('city','')}|{lead.get('zip','')}")
    # default: property only
    return _normalize(lead.get("property", ""))


def _get_existing_keys_from_sheet() -> Optional[Set[str]]:
    """
    Reads the configured sheet and returns a set of normalized keys from the
    target column (by header name SL_RESUME_COL), or from a composite if requested.
    If gspread/ADC isn’t available or SL_SHEET_ID missing, returns None.
    """
    if not SL_SHEET_ID or _gspread is None or _google_auth_default is None:
        print("[RUN] Resume: Sheets not configured; full harvest (no de-dup on write).")
        return None

    try:
        creds, _ = _google_auth_default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
        gc = _gspread.authorize(creds)
        sh = gc.open_by_key(SL_SHEET_ID)
        ws = sh.worksheet(SL_WORKSHEET)
    except Exception as e:
        print(f"[RUN] Resume: Could not open sheet '{SL_WORKSHEET}' ({e}); full harvest.")
        return None

    try:
        values = ws.get_all_values()
        if not values or len(values) < 2:
            print("[RUN] Resume: Sheet empty or headers only; full harvest.")
            return set()

        headers = values[0]
        rows = values[1:]

        # Locate target column by header name (case-insensitive)
        try:
            col_idx = next(i for i, h in enumerate(headers) if h.strip().lower() == SL_RESUME_COL.strip().lower())
        except StopIteration:
            print(f"[RUN] Resume: Header '{SL_RESUME_COL}' not found; defaulting to column A.")
            col_idx = 0

        existing: Set[str] = set()

        # If composite mode, we need multiple columns; find indices
        prop_idx = col_idx
        city_idx = None
        zip_idx  = None

        if SL_RESUME_MODE == "prop_city_zip":
            def idx_for(name: str, fallback: Optional[int] = None):
                low = name.lower()
                for i, h in enumerate(headers):
                    if h.strip().lower() == low:
                        return i
                return fallback
            city_idx = idx_for("City")
            zip_idx  = idx_for("Zip")

        for row in rows:
            if SL_RESUME_MODE == "prop_city_zip":
                prop = row[prop_idx] if prop_idx < len(row) else ""
                city = row[city_idx] if (city_idx is not None and city_idx < len(row)) else ""
                zipc = row[zip_idx]  if (zip_idx  is not None and zip_idx  < len(row)) else ""
                key = _normalize(f"{prop}|{city}|{zipc}")
            else:
                key = _normalize(row[prop_idx] if prop_idx < len(row) else "")
            if key:
                existing.add(key)

        print(f"[RUN] Resume: Loaded {len(existing)} existing keys from '{SL_WORKSHEET}' (col '{SL_RESUME_COL}', mode '{SL_RESUME_MODE}').")
        return existing
    except Exception as e:
        print(f"[RUN] Resume: Failed reading sheet values ({e}); full harvest.")
        return None


def harvestServiceLinkAuction():
    print("[RUN] Harvesting ServiceLink Auction…")

    existing_keys = _get_existing_keys_from_sheet()  # None means "unknown"; set() means "no rows yet"
    have_existing = existing_keys is not None

    all_new: List[Dict] = []
    consec_seen = 0  # count of consecutive rows we already have (for early stop)

    with sync_playwright() as pw:
        # launch and ignore HTTPS errors via the context (as your original code did)
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        # Load the state page
        page.goto(URL, timeout=NAV_TIMEOUT_MS)
        _wait_idle(page)

        # Scrape page 1
        page_index = 1
        print(f"[RUN] Scraping page {page_index}…")
        page_leads = _collect_listings_on_current_page(page)
        print(f"[RUN] Found {len(page_leads)} listings on page {page_index}")

        # Filter & maybe early-stop
        for lead in page_leads:
            key = _key_for(lead)
            if have_existing and key in existing_keys:
                consec_seen += 1
                if consec_seen >= SL_STOP_AFTER_CONSEC_SEEN:
                    print(f"[RUN] Hit {consec_seen} consecutive existing rows; stopping early.")
                    browser.close()
                    print(f"[RUN] Fetched {len(all_new)} new leads from ServiceLink Auction")
                    return all_new
                continue
            else:
                consec_seen = 0
                all_new.append(lead)

        # Paginate: click numeric page-link anchors (2, 3, …)
        while page_index < MAX_PAGES:
            next_link = _find_next_numeric_page_link(page, page_index)
            if not next_link:
                break

            # Snapshot of the first visible address to detect content change after the click
            try:
                first_addr_before = page.locator("div.address-line-1.ng-star-inserted").first.inner_text().strip()
            except Exception:
                first_addr_before = ""

            # Click the next page link
            try:
                next_link.first.click()
            except Exception:
                try:
                    next_link.first.scroll_into_view_if_needed()
                    next_link.first.click()
                except Exception:
                    print(f"[RUN] Could not click page link for page {page_index + 1}; stopping pagination.")
                    break

            # Wait for listings to appear (and ideally change)
            _wait_idle(page)
            try:
                page.wait_for_selector("div.address-line-1.ng-star-inserted", timeout=LIST_TIMEOUT_MS)
                _wait_idle(page)
                try:
                    first_addr_after = page.locator("div.address-line-1.ng-star-inserted").first.inner_text().strip()
                    if first_addr_after == first_addr_before:
                        print(f"[RUN] Warning: page {page_index + 1} content may be unchanged.")
                except Exception:
                    pass
            except TimeoutError:
                print(f"[RUN] No listings after clicking page {page_index + 1}; stopping pagination.")
                break

            page_index += 1
            print(f"[RUN] Scraping page {page_index}…")
            page_leads = _collect_listings_on_current_page(page)
            print(f"[RUN] Found {len(page_leads)} listings on page {page_index}")
            if not page_leads:
                break

            for lead in page_leads:
                key = _key_for(lead)
                if have_existing and key in existing_keys:
                    consec_seen += 1
                    if consec_seen >= SL_STOP_AFTER_CONSEC_SEEN:
                        print(f"[RUN] Hit {consec_seen} consecutive existing rows; stopping early.")
                        browser.close()
                        print(f"[RUN] Fetched {len(all_new)} new leads from ServiceLink Auction")
                        return all_new
                    continue
                else:
                    consec_seen = 0
                    all_new.append(lead)

        browser.close()

    print(f"[RUN] Fetched {len(all_new)} new leads from ServiceLink Auction")
    return all_new
