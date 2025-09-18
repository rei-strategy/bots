from playwright.sync_api import sync_playwright

def harvestLeadList(source: dict, county: str):
    """
    Scrape the attorney list table and return one dict per row,
    capturing all seven columns plus an optional detail URL.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(source["listUrl"])

        # If you ever need a dropdown filter, uncomment:
        # page.select_option(source["selectors"].get("countySelect", ""), county)
        # page.click(source["selectors"].get("searchBtn", ""))

        rows = page.query_selector_all(source["selectors"]["row"])
        leads = []
        for row in rows:
            # Capture each table cell
            sale_date  = row.query_selector("td.date").inner_text().strip()
            file_num   = row.query_selector("td.case").inner_text().strip()
            prop       = row.query_selector("td.property").inner_text().strip()
            city       = row.query_selector("td.city").inner_text().strip()
            zipc       = row.query_selector("td.zip").inner_text().strip()
            county_val = row.query_selector("td.county").inner_text().strip()
            bid_txt    = row.query_selector("td.bid").inner_text().strip()

            # Try to capture a detail-page link if present
            link_el = row.query_selector(source["selectors"].get("detailLink", ""))
            detail_url = link_el.get_attribute("href") if link_el else None

            leads.append({
                "saleDate":   sale_date,
                "fileNumber": file_num,
                "property":   prop,
                "city":       city,
                "zip":        zipc,
                "county":     county_val,
                "bid":        bid_txt,
                "detailUrl":  detail_url
            })

        browser.close()
        return leads
