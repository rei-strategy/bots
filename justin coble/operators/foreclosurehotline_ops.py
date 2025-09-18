# operators/foreclosurehotline_ops.py

from playwright.sync_api import sync_playwright

URL = "https://www.foreclosurehotline.net/Foreclosure.aspx"

def harvestForeclosureHotline():
    print("[RUN] Harvesting Foreclosure Hotline via Playwright…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        page.goto(URL, timeout=60000)

        # 1) pick Georgia
        page.wait_for_selector("select#ddlState")
        page.select_option("select#ddlState", "GA")

        # 2) wait for county dropdown to enable
        page.wait_for_selector("select#ddlCounty")
        page.wait_for_function(
            "() => !document.querySelector('select#ddlCounty').disabled"
        )

        # 3) select “All” counties and submit
        page.select_option("select#ddlCounty", "877")
        page.click("input#btnSelect")

        # 4) wait until the File Number cell is populated
        page.wait_for_function(
            "() => {\n"
            "  const td = document.querySelector('#tdFileNum');\n"
            "  return td && td.innerText.trim().length > 0;\n"
            "}",
            timeout=30_000
        )

        # 5) pull down each column in one go
        fn_html   = page.inner_html("td#tdFileNum")
        sd_html   = page.inner_html("td#tdSaleDate")
        addr_html = page.inner_html("td#tdAddress")
        cnt_text  = page.inner_text("td#tdCounty")
        st_text   = page.inner_text("td#tdState")
        bid_text  = page.inner_text("td#tdBid")

        browser.close()

    def split_html(html: str):
        # split on <br>, drop empty segments
        return [seg.strip() for seg in html.split("<br>") if seg.strip()]

    fns      = split_html(fn_html)
    sds      = split_html(sd_html)
    raw_addr = split_html(addr_html)
    cnts     = [c.strip() for c in cnt_text.split("\n") if c.strip()]
    sts      = [s.strip() for s in st_text.split("\n") if s.strip()]
    bids     = [b.strip() for b in bid_text.split("\n") if b.strip()]

    count = min(len(fns), len(sds), len(raw_addr)//2, len(cnts), len(sts))
    leads = []

    for i in range(count):
        fn        = fns[i]
        sd        = sds[i]
        street    = raw_addr[2*i]
        city_line = raw_addr[2*i + 1]
        cnt       = cnts[i]
        st        = sts[i]
        bid       = bids[i] if i < len(bids) else ""

        # parse "CityName STATE ZIP"
        parts = city_line.split()
        if len(parts) >= 3:
            city     = " ".join(parts[:-2])
            zip_code = parts[-1]
        elif len(parts) == 2:
            city, zip_code = parts
        else:
            city     = city_line
            zip_code = ""

        leads.append({
            "saleDate":   sd,
            "fileNumber": fn,
            "property":   street,
            "city":       city,
            "zip":        zip_code,
            "county":     cnt,
            "bid":        bid,
        })

    print(f"[RUN] Fetched {len(leads)} leads from Foreclosure Hotline")
    return leads