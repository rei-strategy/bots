from playwright.sync_api import sync_playwright

def parseLeadDetail(detailUrl: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(detailUrl)
        # scrape fields…
        addr = page.query_selector("#address").inner_text().strip()
        val  = float(page.query_selector("#estValue").inner_text().replace(",", ""))
        bal  = float(page.query_selector("#estBalance").inner_text().replace(",", ""))
        fn, ln = page.query_selector("#ownerName").inner_text().split(" ", 1)
        mail = page.query_selector("#mailingAddr").inner_text().strip()
        date = page.query_selector("#auctionDate").inner_text().strip()
        atty = page.query_selector("#attorney").inner_text().strip()
        browser.close()
        return {
          "property": {"address": addr},
          "estValue": val,
          "estBalance": bal,
          "homeowner": {"firstName": fn, "lastName": ln, "mailingAddress": mail},
          "auctionDate": date,
          "attorney": atty,
          "county": county
        }

def filterByEquityRule(estValue: float, estBalance: float) -> bool:
    return (estValue - estBalance) >= 100_000
