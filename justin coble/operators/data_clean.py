#!/usr/bin/env python3
import time
import sys

from operators.realestate_ops import fetchPropStreamEstimates
from operators.list_ops                  import harvestLeadList
from operators.brockandscott_ops         import harvestBrockSales
from operators.aldridgepite_ops          import harvestAldridgePites
from operators.foreclosurehotline_ops    import harvestForeclosureHotline
from operators.servicelink_auction_ops   import harvestServiceLinkAuction
from operators.auction_com_ops           import harvestAuctionCom
from operators.zome_com_ops              import harvestZomeCom
from operators.logs_powerbi_ops          import harvestLogsPowerBI
from operators.sheet_ops                 import _service, SPREADSHEET_ID, appendToSheet

# mapping of source-keys → scraper functions
SOURCES = {
    "reuben_lublin":      harvestLeadList,
    "brock_and_scott":     harvestBrockSales,
    "aldridge_pites":      harvestAldridgePites,
    "foreclosure_hotline": harvestForeclosureHotline,
    "servicelink_auction": harvestServiceLinkAuction,
    "auction_com":         harvestAuctionCom,
    "zome_com":            harvestZomeCom,
    "logs_powerbi":        harvestLogsPowerBI,
}

def _get_all_rows():
    """Fetch all rows A2:M."""
    resp = _service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Sheet1!A2:M"
    ).execute()
    return resp.get("values", [])

def _update_cell(row: int, col: str, val: str):
    """Update a single cell like 'J5' or 'L12'."""
    _service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Sheet1!{col}{row}",
        valueInputOption="RAW",
        body={"values": [[val]]}
    ).execute()
    time.sleep(0.5)

def error_recheck(passes=2):
    """
    1) Look at every row; if Error (col L) is non-empty,
       or Equity (col J) is blank or zero, re-run PropStream.
    2) If we now get equity>0, write it into col J and clear col L;
       otherwise set "Try again" in col L.
    Repeat 'passes' times.
    """
    for _ in range(passes):
        rows = _get_all_rows()
        for idx, row in enumerate(rows, start=2):
            equity_str = row[9] if len(row) > 9 else ""
            error_str  = row[11] if len(row) > 11 else ""
            # parse equity numeric
            try:
                equity_val = float(equity_str.replace("$", "").replace(",", "")) if equity_str.strip() else 0.0
            except ValueError:
                equity_val = 0.0

            if error_str.strip() or equity_val == 0.0:
                address = row[4]
                city    = row[5]
                zipc    = row[6]
                try:
                    equity, _, _, _ = fetchPropStreamEstimates(address, city, zipc)
                except Exception:
                    equity = 0.0

                if equity > 0:
                    _update_cell(idx, "J", f"${equity:,.0f}")
                    _update_cell(idx, "L", "")  # clear error
                else:
                    if not error_str.strip():
                        _update_cell(idx, "L", "Try again")
        time.sleep(1)

def add_missing_records():
    """
    3) For each source, re-scrape its site and append any leads
       we don’t already have (equity≥100k only).
    """
    rows = _get_all_rows()
    have = set()
    for row in rows:
        if len(row) > 10:
            fn = row[3]; src = row[10]
            have.add((fn, src))

    for key, scraper in SOURCES.items():
        print(f"\n=== Scanning for new {key} leads ===")
        # harvest raw leads
        if key == "reuben_lublin":
            leads = scraper(
                {
                  "name": "Reuben Lublin",
                  "listUrl": "https://rlselaw.com/property-listing/georgia-property-listings/",
                  "selectors": {
                    "row":"table tbody tr",
                    "detailLink":"td.bid a","date":"td.date",
                    "case":"td.case","property":"td.property",
                    "city":"td.city","zip":"td.zip",
                    "county":"td.county","bid":"td.bid"
                  }},
                "Clayton"
            )
        else:
            leads = scraper()

        for lead in leads:
            keypair = (lead["fileNumber"], key)
            if keypair in have:
                continue
            try:
                equity, _, firstName, lastName = fetchPropStreamEstimates(
                    lead["property"], lead["city"], lead["zip"]
                )
            except Exception:
                equity, firstName, lastName = 0.0, "", ""

            if equity >= 100_000:
                rec = {
                    "saleDate":   lead["saleDate"],
                    "fileNumber": lead["fileNumber"],
                    "property":   {"address": lead["property"]},
                    "city":       lead["city"],
                    "zip":        lead["zip"],
                    "county":     lead["county"],
                    "bid":        lead["bid"],
                    "source":     key,
                    "firstName":  firstName,
                    "lastName":   lastName,
                    "error":      ""
                }
                appendToSheet(rec)
                print(f"  ➕ Added new: {rec['property']['address']} ({rec['fileNumber']})")
        time.sleep(1)

def mark_removed():
    """
    4) For each site, scrape all current fileNumbers,
       then for every row in Sheet1 where source==site but fileNumber
       is no longer on the site, write “Removed” in col M.
    """
    rows = _get_all_rows()
    sheet_by_src = {}
    for idx, row in enumerate(rows, start=2):
        if len(row) > 10:
            fn, src = row[3], row[10]
            sheet_by_src.setdefault(src, []).append((idx, fn))

    for src, scraper in SOURCES.items():
        print(f"\n=== Checking removed for {src} ===")
        if src == "reuben_lublin":
            raw = scraper(
                {
                  "name": "Reuben Lublin",
                  "listUrl": "https://rlselaw.com/property-listing/georgia-property-listings/",
                  "selectors": {
                    "row":"table tbody tr",
                    "detailLink":"td.bid a","date":"td.date",
                    "case":"td.case","property":"td.property",
                    "city":"td.city","zip":"td.zip",
                    "county":"td.county","bid":"td.bid"
                  }},
                "Clayton"
            )
        else:
            raw = scraper()
        present = {l["fileNumber"] for l in raw}

        for row_idx, fn in sheet_by_src.get(src, []):
            if fn not in present:
                _update_cell(row_idx, "M", "Removed")
                print(f"  🗑️ Marked removed: {fn}")
        time.sleep(1)

if __name__ == "__main__":
    print("→ Rechecking errors…")
    error_recheck()
    print("→ One more pass on errors…")
    error_recheck()
    print("→ Adding any missing new records…")
    add_missing_records()
    print("→ Marking removed records…")
    mark_removed()
    print("\n✅ data_clean complete.")