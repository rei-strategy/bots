# operators/aldridgepite_ops.py

from playwright.sync_api import sync_playwright, TimeoutError

def harvestAldridgePites() -> list[dict]:
    """
    1) Navigate to the GA listings URL.
    2) If redirected to the Disclaimer, click the “Agree” link by targeting the anchor.
    3) Wait for the URL to switch back to the listings path.
    4) Wait for the DataTable rows under <table class="posts-data-table">.
    5) Extract File #, Address, City, Zip, County, Sale Date, Current Bid.
    """
    list_url = "https://aldridgepite.com/sale-day-listings-selection/foreclosure-listings-georgia/"
    results  = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()

        # 1) First visit
        page.goto(list_url, timeout=60000)
        page.wait_for_load_state("networkidle", timeout=60000)

        # 2) If disclaimer page showed up, click that Agree anchor exactly
        if "/disclaimer-georgia" in page.url:
            print("  [RUN] Hit disclaimer, clicking Agree link…")
            # specifically target the <a> whose text is Agree
            page.click("a:has-text('Agree')", timeout=10000)
            # wait for it to navigate back to the listing URL
            page.wait_for_url("**/sale-day-listings-selection/foreclosure-listings-georgia/**", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=60000)

        # 3) Now wait for the rows of the table to be present
        selector = "table.posts-data-table tbody tr"
        try:
            page.wait_for_selector(selector, timeout=30000)
        except TimeoutError:
            print("⚠️ No Aldridge Pites rows found after Agree; skipping source.")
            browser.close()
            return []

        rows = page.query_selector_all(selector)
        print(f"[RUN] Found {len(rows)} Aldridge Pites rows")

        # 4) Extract each row
        for row in rows:
            try:
                file_num  = row.query_selector("td.col-title").inner_text().strip()
                address   = row.query_selector("td.col-Address").inner_text().strip()
                city      = row.query_selector("td.col-City").inner_text().strip()
                zip_code  = row.query_selector("td.col-Zip").inner_text().strip()
                county    = row.query_selector("td.col-County").inner_text().strip()
                sale_date = row.query_selector("td.col-Date_Listed").inner_text().strip()
                bid       = row.query_selector("td.col-Current_Bid").inner_text().strip()
            except Exception as e:
                print(f"⚠️ Error parsing Aldridge row: {e}")
                continue

            results.append({
                "saleDate":   sale_date,
                "fileNumber": file_num,
                "property":   address,
                "city":       city,
                "zip":        zip_code,
                "county":     county,
                "bid":        bid
            })

        browser.close()
    return results