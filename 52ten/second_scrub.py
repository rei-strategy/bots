#!/usr/bin/env python3
import sys
import os
import time

import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    from yaspin import yaspin
    YASPIN_AVAILABLE = True
except ImportError:
    yaspin = None

###############################################################################
# SPINNER UTILS
###############################################################################
def safe_spinner_write(spinner, message):
    if spinner:
        try:
            spinner.write(message)
        except ValueError:
            print(message)
    else:
        print(message)

def safe_spinner_ok(spinner, message):
    if spinner:
        try:
            spinner.ok(message)
        except ValueError:
            print(message)
    else:
        print(message)

def safe_spinner_fail(spinner, message):
    if spinner:
        try:
            spinner.fail(message)
        except ValueError:
            print(message)
    else:
        print(message)

###############################################################################
# CSV HELPER
###############################################################################
def append_row_to_csv(row_dict, field_order, csv_filename):
    file_exists = os.path.isfile(csv_filename)
    mode = 'a' if file_exists else 'w'
    df = pd.DataFrame([row_dict], columns=field_order)
    df.to_csv(csv_filename, mode=mode, header=not file_exists, index=False)

###############################################################################
# SCRAPE PARK DETAIL (AS EXAMPLE)
###############################################################################
def scrape_park_detail(driver, spinner):
    time.sleep(2)
    try:
        wait = WebDriverWait(driver, 5)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1")))
    except:
        pass

    data = {}
    def safe_find_text(css, fallback="N/A"):
        try:
            return driver.find_element(By.CSS_SELECTOR, css).text.strip()
        except:
            return fallback

    data["title"]            = safe_find_text("h1")
    data["price"]            = safe_find_text(".listing-price")
    data["street_address"]   = safe_find_text("ui-street-address-widget")
    data["city_state_zip"]   = safe_find_text("ui-city-state-zip-widget")
    data["bed_bath"]         = safe_find_text(".listing-bed-bath")
    data["homes_for_sale"]   = safe_find_text("#forSaleCount")
    data["homes_for_rent"]   = safe_find_text("#forRentCount")
    data["vacant_sites"]     = safe_find_text("#vacantCount")

    # example reading a <strong> block
    def read_ul_after_strong(strong_text):
        try:
            ul_el = driver.find_element(By.XPATH, f"//strong[contains(text(),'{strong_text}')]/following-sibling::ul")
            return ul_el.text.strip()
        except:
            return "N/A"

    data["average_monthly_rent"] = read_ul_after_strong("Average Monthly Rent")
    data["pet_policies"]         = read_ul_after_strong("Pet Policies")
    data["additional_details"]   = read_ul_after_strong("Additional Details")
    data["amenities"]            = read_ul_after_strong("Amenities")
    data["other_info"]           = read_ul_after_strong("Other")

    def safe_xpath_strong(txt):
        try:
            e = driver.find_element(By.XPATH, f"//strong[contains(text(),'{txt}')]")
            return e.text.strip()
        except NoSuchElementException:
            return "N/A"

    data["on_site_sales_office"] = safe_xpath_strong("On Site Sales Office")
    data["contact_info_line"]    = safe_xpath_strong("Contact:")

    safe_spinner_write(spinner, f"[PARSE] detail => {data}")
    return data

###############################################################################
# SCRAPE A SINGLE CITY BY DIRECT SEARCH
###############################################################################
def scrape_city_by_direct_search(driver, spinner, city_name, state_name):
    """
    1) Go home
    2) Type => city_name + ' ' + state_name
    3) pick first suggestion
    4) click Search
    5) click Parks
    6) gather card listing
    7) For each card => parse
    """
    # We can do a new tab or just go to homepage again
    driver.get("https://www.mhvillage.com/")
    time.sleep(2)

    # Quick cookie dismiss
    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "c-p-bn"))
        ).click()
        time.sleep(1)
    except:
        pass

    full_search = f"{city_name}, {state_name}"
    safe_spinner_write(spinner, f"[SCRAPE] City='{city_name}'...")

    wait = WebDriverWait(driver, 10)
    search_input = wait.until(EC.presence_of_element_located((By.ID, "autocomplete-input")))
    search_input.clear()
    search_input.send_keys(full_search)
    time.sleep(1)

    # Grab suggestions
    try:
        suggestions = wait.until(
            EC.visibility_of_all_elements_located((By.CSS_SELECTOR, "a.dropdown-item.cursor-pointer"))
        )
        if not suggestions:
            safe_spinner_write(spinner, f"[{city_name}] No suggestions => skip.\n")
            return
        suggestions[0].click()
    except:
        safe_spinner_write(spinner, f"[{city_name}] Could not find suggestion => skip.\n")
        return

    # click 'Search'
    time.sleep(1)
    try:
        search_btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//span[@translate='app.search' and contains(text(),'Search')]")
        ))
        search_btn.click()
    except:
        safe_spinner_write(spinner, f"[{city_name}] Search button not clickable => skip.\n")
        return
    time.sleep(3)

    # click Parks tab
    try:
        parks_tab = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//span[@class='w-100 text-center' and contains(text(),'Parks')]")
        ))
        parks_tab.click()
    except:
        safe_spinner_write(spinner, f"[{city_name}] Could not open Parks tab => skip.\n")
        return
    time.sleep(3)

    # gather card
    card_sel = "div.entity-card-content-layer"
    cards = driver.find_elements(By.CSS_SELECTOR, card_sel)
    if not cards:
        safe_spinner_write(spinner, f"[{city_name}] Found 0 park cards => skip.\n")
        return

    safe_spinner_write(spinner, f"[{city_name}] Found {len(cards)} park card(s).")

    # We'll define the columns
    field_order = [
        "park_url", "city",
        "title", "price", "street_address", "city_state_zip", "bed_bath",
        "homes_for_sale", "homes_for_rent", "vacant_sites",
        "average_monthly_rent", "pet_policies", "additional_details", "amenities",
        "other_info", "on_site_sales_office", "contact_info_line"
    ]
    csv_filename = f"{state_name.lower()}_parks_listings.csv"

    for idx, card in enumerate(cards, start=1):
        # We must re-find the card each time to avoid stale references
        # So let's do a re-locate or a safer approach. 
        try:
            re_cards = driver.find_elements(By.CSS_SELECTOR, card_sel)
            if idx-1 >= len(re_cards):
                break
            card_el = re_cards[idx-1]
            link_el = card_el.find_element(By.CSS_SELECTOR, 'a[href*="/parks/"]')
            park_url = link_el.get_attribute("href")
            safe_spinner_write(spinner, f"[{city_name}] Card #{idx} => visiting {park_url}")
        except StaleElementReferenceException as se:
            safe_spinner_write(spinner, f"[{city_name}] Card #{idx} => no link: {se}\n")
            continue
        except NoSuchElementException as ne:
            safe_spinner_write(spinner, f"[{city_name}] Card #{idx} => no link: {ne}\n")
            continue
        except Exception as ex:
            safe_spinner_write(spinner, f"[{city_name}] Card #{idx} => unexpected error: {ex}\n")
            continue

        # Visit listing
        try:
            driver.get(park_url)
            time.sleep(2)
        except Exception as e:
            safe_spinner_write(spinner, f"[{city_name}] Could not load {park_url}: {e}\n")
            continue

        # parse
        detail = scrape_park_detail(driver, spinner)
        detail["park_url"] = park_url
        detail["city"] = city_name

        # partial save
        append_row_to_csv(detail, field_order, csv_filename)

        # Go back 
        driver.back()
        time.sleep(2)


def main():
    if yaspin:
        spinner = yaspin(text="Starting second_scrub.py...", color="cyan")
        spinner.start()
    else:
        spinner = None

    driver = None
    try:
        # Argv => [scriptname, state_name, city1, city2, city3...]
        if len(sys.argv) < 3:
            raise ValueError("Usage: second_scrub.py <StateName> <City1> <City2> ...")

        state_name = sys.argv[1]
        city_list  = sys.argv[2:]  # all remaining are city names

        # Start driver
        service = Service("/Users/georgesmacbook/Downloads/dist/chromedriver")
        options = webdriver.ChromeOptions()
        options.page_load_strategy = "eager"
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_window_size(1920, 1080)

        safe_spinner_write(spinner, f"[STEP] Searching entire state => {state_name}\n")

        for city in city_list:
            scrape_city_by_direct_search(driver, spinner, city, state_name)

        safe_spinner_write(spinner, "\nAll done => second_scrub.py finished.")
        safe_spinner_ok(spinner, "Done scraping these missing cities.")

    except Exception as e:
        safe_spinner_fail(spinner, f"ERROR: {e}")
        input("[Press ENTER to exit second_scrub.py]")
    finally:
        if driver:
            driver.quit()
        if spinner:
            spinner.stop()

if __name__ == "__main__":
    main()
