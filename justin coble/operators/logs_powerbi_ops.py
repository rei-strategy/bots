# operators/logs_powerbi_ops.py
from playwright.sync_api import sync_playwright, TimeoutError
import time

# Direct PowerBI embed URL (skips the wrapper page+iframe)
POWERBI_URL = (
    "https://app.powerbi.com/view?"
    "r=eyJrIjoiMzRjYjFlYjktODhlMS00ZDE3LTlmYzItMGNmMzgxYWJlNWM4IiwidCI6ImRmZmRlOTRmLTcyZmIt"
    "NDlhZS1hY2IyLTBiOTYxYWJkNWI0MSIsImMiOjN9"
)

def harvestLogsPowerBI():
    print("[RUN] Harvesting Logs PowerBI…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(POWERBI_URL, timeout=60000)

        # — wait for the grid headers to load —
        try:
            page.wait_for_selector('div[role="columnheader"]', timeout=60000)
        except TimeoutError:
            print("⚠️ PowerBI grid header not found")
            browser.close()
            return []

        # — map header text → column-index attr —
        col_map = {}
        for hdr in page.query_selector_all('div[role="columnheader"]'):
            text = hdr.inner_text().split("\n", 1)[0].strip()
            idx  = hdr.get_attribute("column-index")
            if text and idx:
                col_map[text] = idx

        needed = ["Sale Date", "Case #", "Property Address", "Property County"]
        missing = [c for c in needed if c not in col_map]
        if missing:
            print(f"⚠️ Missing columns in PowerBI: {missing}")
            browser.close()
            return []

        # — scroll to bottom so all rows load —
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        # — grab all gridcells for each column —
        sale_cells   = page.query_selector_all(
            f'div[role="gridcell"][column-index="{col_map["Sale Date"]}"]'
        )
        case_cells   = page.query_selector_all(
            f'div[role="gridcell"][column-index="{col_map["Case #"]}"]'
        )
        prop_cells   = page.query_selector_all(
            f'div[role="gridcell"][column-index="{col_map["Property Address"]}"]'
        )
        county_cells = page.query_selector_all(
            f'div[role="gridcell"][column-index="{col_map["Property County"]}"]'
        )

        count = min(
            len(sale_cells),
            len(case_cells),
            len(prop_cells),
            len(county_cells)
        )
        leads = []

        for i in range(count):
            sale_txt  = sale_cells[i].inner_text().strip()
            case_txt  = case_cells[i].inner_text().strip()
            full_addr = prop_cells[i].inner_text().strip()
            county    = county_cells[i].inner_text().strip()

            # split "981 Santa Fe Trail, Macon, Georgia 31220"
            parts = [p.strip() for p in full_addr.split(",")]
            street   = parts[0] if len(parts) > 0 else ""
            city     = parts[1] if len(parts) > 1 else ""
            zip_code = ""
            if len(parts) > 2:
                zip_code = parts[2].split()[-1]

            leads.append({
                "saleDate":   sale_txt,
                "fileNumber": case_txt,
                "property":   street,
                "city":       city,
                "zip":        zip_code,
                "county":     county,
                "bid":        ""  # not listed
            })

        browser.close()

    print(f"[RUN] Fetched {len(leads)} leads from Logs PowerBI")
    return leads