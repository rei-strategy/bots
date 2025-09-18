from playwright.sync_api import sync_playwright, TimeoutError
import re

def harvestBrockSales() -> list[dict]:
    """
    Scrapes Brock & Scott GA foreclosure sales, paginating through all pages.
    Returns a list of records with keys:
      saleDate, fileNumber, property, city, zip, county, bid
    """
    base_url = "https://www.brockandscott.com/foreclosure-sales/?_sft_foreclosure_state=ga"
    all_records = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base_url, timeout=60000)

        while True:
            # Wait for rows
            page.wait_for_selector("div.record", timeout=30000)
            rows = page.query_selector_all("div.record")
            for row in rows:
                # Extract each field
                county = row.query_selector("div.forecol:has-text('County:') p:nth-of-type(2)").inner_text().strip()
                sale_date = row.query_selector("div.forecol:has-text('Sale Date:') p:nth-of-type(2)").inner_text().strip()
                case_num = row.query_selector("div.forecol:has-text('Case #:') p:nth-of-type(2)").inner_text().strip()
                address_full = row.query_selector("div.forecol:has-text('Address:') p:nth-of-type(2)").inner_text().strip()
                bid = row.query_selector("div.forecol:has-text('Opening Bid Amount:') p:nth-of-type(2)").inner_text().strip()

                # Parse address_full into property, city, zip
                # e.g. "268 Sabrina Ct   Woodstock, Georgia 30188"
                parts = address_full.split(",")
                addr_city = parts[0].strip()  # "268 Sabrina Ct   Woodstock"
                state_zip = parts[1].strip() if len(parts) > 1 else ""
                # split addr_city on 2+ spaces
                pc = re.split(r"\s{2,}", addr_city)
                if len(pc) >= 2:
                    prop_addr, city = pc[0].strip(), pc[1].strip()
                else:
                    tokens = addr_city.rsplit(" ", 1)
                    prop_addr = tokens[0].strip()
                    city = tokens[1].strip() if len(tokens) > 1 else ""

                zip_code = state_zip.split()[-1] if state_zip else ""

                all_records.append({
                    "saleDate":   sale_date,
                    "fileNumber": case_num,
                    "property":   prop_addr,
                    "city":       city,
                    "zip":        zip_code,
                    "county":     county,
                    "bid":        bid
                })

            # Pagination: click "Next >" if present
            next_btn = page.query_selector("a:has-text('Next')")
            if next_btn:
                next_btn.click()
                page.wait_for_load_state("networkidle", timeout=60000)
            else:
                break

        browser.close()

    return all_records