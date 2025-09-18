#!/usr/bin/env python3
import sys
import time

from operators.list_ops                  import harvestLeadList
from operators.brockandscott_ops         import harvestBrockSales
from operators.aldridgepite_ops          import harvestAldridgePites
from operators.foreclosurehotline_ops    import harvestForeclosureHotline
from operators.servicelink_auction_ops   import harvestServiceLinkAuction
from operators.auction_com_ops           import harvestAuctionCom
from operators.zome_com_ops              import harvestZomeCom
from operators.logs_powerbi_ops          import harvestLogsPowerBI
from operators.realestate_ops            import fetchPropStreamEstimates
from operators.sheet_ops                 import appendToSheet, getLastProcessedFileNumberBySource

def run_lead_processing(source_keys):
    mapping = {
        "reuben_lublin":      {"name": "Reuben Lublin",       "scrape": lambda: harvestLeadList(
            {
                "name": "Reuben Lublin",
                "listUrl": "https://rlselaw.com/property-listing/georgia-property-listings/",
                "selectors": {
                    "row":        "table tbody tr",
                    "detailLink": "td.bid a",
                    "date":       "td.date",
                    "case":       "td.case",
                    "property":   "td.property",
                    "city":       "td.city",
                    "zip":        "td.zip",
                    "county":     "td.county",
                    "bid":        "td.bid"
                }
            },
            "Clayton"
        )},
        "brock_and_scott":     {"name": "Brock & Scott",       "scrape": harvestBrockSales},
        "aldridge_pites":      {"name": "Aldridge Pites",      "scrape": harvestAldridgePites},
        "foreclosure_hotline": {"name": "Foreclosure Hotline", "scrape": harvestForeclosureHotline},
        "servicelink_auction": {"name": "ServiceLink Auction", "scrape": harvestServiceLinkAuction},
        "auction_com":         {"name": "Auction.com",         "scrape": harvestAuctionCom},
        "zome_com":            {"name": "Xome.com",            "scrape": harvestZomeCom},
        "logs_powerbi":        {"name": "Logs PowerBI Report","scrape": harvestLogsPowerBI},
    }

    for key in source_keys:
        if key not in mapping:
            print(f"⚠️ Unknown source: {key}")
            continue

        name   = mapping[key]["name"]
        scrape = mapping[key]["scrape"]

        print(f"\n=== Processing {name} ===")
        try:
            last = getLastProcessedFileNumberBySource(name)
            print(f"[RUN] Last processed File # for {name}: {last}")
        except Exception as e:
            print(f"⚠️ Could not fetch last file for {name}: {e}")
            last = None

        leads = scrape()
        print(f"[RUN] Fetched {len(leads)} leads from {name}")

        if last:
            try:
                idx = next(i for i, l in enumerate(leads) if l["fileNumber"] == last)
                leads = leads[idx+1:]
                print(f"[RUN] Resuming {name} at index {idx+1}")
            except StopIteration:
                print(f"[RUN] {last} not found; processing all")

        total = len(leads)
        print(f"[RUN] {total} new {name} leads to process")

        prev_rec = None

        for i, lead in enumerate(leads, start=1):
            addr = lead["property"]
            print(f"\n[RUN] {name} Lead {i}/{total}: {addr}")

            rec = {
                "saleDate":   lead["saleDate"],
                "fileNumber": lead["fileNumber"],
                "property":   {"address": addr},
                "city":       lead["city"],
                "zip":        lead["zip"],
                "county":     lead["county"],
                "bid":        lead["bid"],
                "source":     name,
                "firstName":  "",
                "lastName":   "",
                "error":      ""
            }

            # equity & owner lookup
            try:
                equity, _, firstName, lastName = fetchPropStreamEstimates(addr, lead["city"], lead["zip"])
                print(f"  [RUN] Equity lookup returned ${equity:,.0f}, owner: {firstName} {lastName}")
                lookup_failed = False
            except Exception as e:
                equity, firstName, lastName = 0.0, "", ""
                lookup_failed = True
                rec["error"] = "Try again"
                print(f"  ⚠️ Equity lookup failed for {addr}: {e}")

            # attach results to previous record
            if prev_rec is not None:
                prev_rec["estValue"] = equity
                prev_rec["firstName"] = firstName
                prev_rec["lastName"]  = lastName
                if lookup_failed:
                    prev_rec["error"] = "Try again"

                # always append if there was an error
                if prev_rec["error"]:
                    appendToSheet(prev_rec)
                    print(f"  Processed {prev_rec['property']['address']} (File # {prev_rec['fileNumber']}) with ERROR")
                # otherwise only if equity ≥ 100k
                elif prev_rec["estValue"] >= 100_000:
                    appendToSheet(prev_rec)
                    print(f"  Processed {prev_rec['property']['address']} (File # {prev_rec['fileNumber']})")
                else:
                    print(f"  Skipping {prev_rec['property']['address']} (equity < $100k)")

            prev_rec = rec

        # note: the final buffered record is dropped

    print("\n✅ All sources complete.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(
            "Usage: python main.py "
            "[reuben_lublin] [brock_and_scott] [aldridge_pites] "
            "[foreclosure_hotline] [servicelink_auction] [auction_com] "
            "[zome_com] [logs_powerbi]"
        )
        sys.exit(1)
    run_lead_processing(args)