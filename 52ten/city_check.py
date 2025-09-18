#!/usr/bin/env python3
import sys
import os
import time
import subprocess
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementNotInteractableException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    from yaspin import yaspin
except ImportError:
    yaspin = None


###############################################################################
# SPINNER UTILS
###############################################################################
def safe_spinner_write(spinner, message):
    """Write message with spinner OR fallback to print."""
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
# 1) READ CSV / GET UNIQUE CITIES
###############################################################################
def read_csv_cities(csv_path):
    """Return a set of city names from the CSV file's 'city' column."""
    if not os.path.isfile(csv_path):
        return set()
    df = pd.read_csv(csv_path, dtype=str)
    if "city" not in df.columns:
        return set()
    city_series = df["city"].dropna().str.strip()
    return set(city_series.to_list())


###############################################################################
# 2) LOAD ALL STATEWIDE CITY NAMES
###############################################################################
def load_all_statewide_cities(driver, spinner, state):
    wait = WebDriverWait(driver, 15)

    safe_spinner_write(spinner, "[STEP] Loading MHVillage homepage...")
    driver.get("https://www.mhvillage.com/")
    time.sleep(2)

    # Dismiss any cookie banner
    try:
        cookie_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "c-p-bn"))
        )
        cookie_btn.click()
        time.sleep(1)
    except:
        pass

    # Search for state
    safe_spinner_write(spinner, f"[STEP] Searching for state => {state}")
    search_input = wait.until(
        EC.presence_of_element_located((By.ID, "autocomplete-input"))
    )
    search_input.clear()
    search_input.send_keys(state)
    time.sleep(1)

    # click first suggestion
    suggestions = wait.until(
        EC.visibility_of_all_elements_located((By.CSS_SELECTOR, "a.dropdown-item.cursor-pointer"))
    )
    if suggestions:
        suggestions[0].click()
    else:
        raise Exception(f"No suggestions found for state '{state}'.")

    # Click search
    time.sleep(1)
    search_btn = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//span[@translate='app.search' and contains(text(), 'Search')]")
    ))
    search_btn.click()
    time.sleep(4)

    # Click 'Parks' tab
    try:
        parks_tab = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//span[@class='w-100 text-center' and contains(text(),'Parks')]")
        ))
        parks_tab.click()
    except:
        raise Exception("Could not open 'Parks' tab for statewide search")

    time.sleep(4)

    # Attempt to click 'Load More' until none remain
    while True:
        time.sleep(1)
        try:
            load_more_btn = driver.find_element(By.XPATH, "//a[contains(text(),'Load More')]")
            load_more_btn.click()
            time.sleep(2)
        except (NoSuchElementException, ElementNotInteractableException):
            safe_spinner_write(spinner, "[LOAD MORE] None found => done.")
            break

    # Now gather city elements
    city_elems = driver.find_elements(By.CSS_SELECTOR, "strong.result-location")
    city_names = set()
    for elem in city_elems:
        txt = elem.text.strip()
        if txt:
            city_names.add(txt)

    safe_spinner_write(spinner, f"[DONE] Found {len(city_names)} unique city names on the site.\n")
    return city_names


###############################################################################
# MAIN
###############################################################################
def main():
    ###########################################################################
    # 1) Prompt the user BEFORE starting spinner:
    ###########################################################################
    csv_path = input("Enter path to CSV (e.g. '/Users/.../arizona_parks_listings.csv'): ").strip()
    if not os.path.isfile(csv_path):
        print(f"ERROR: CSV not found => {csv_path}")
        sys.exit(1)

    state_name = input("Enter the same STATE you used in main scraping (e.g., 'Arizona'): ").strip()

    ###########################################################################
    # 2) Attempt to start spinner AFTER input is read
    ###########################################################################
    spinner = None
    if yaspin:
        try:
            spinner = yaspin(text="Starting Validation...", color="cyan")
            spinner.start()
        except ValueError:
            print("[INFO] Terminal too small for spinner. Falling back to prints.\n")

    driver = None
    try:
        #######################################################################
        # 3) Start driver
        #######################################################################
        service = Service("/Users/georgesmacbook/Downloads/dist/chromedriver")  # your path
        options = webdriver.ChromeOptions()
        options.page_load_strategy = "eager"
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_window_size(1920, 1080)

        #######################################################################
        # 4) Gather site cities
        #######################################################################
        site_cities = load_all_statewide_cities(driver, spinner, state_name)

        #######################################################################
        # 5) Gather CSV cities
        #######################################################################
        csv_cities = read_csv_cities(csv_path)

        #######################################################################
        # 6) Compare => new_cities = site_cities - csv_cities
        #######################################################################
        new_cities = site_cities.difference(csv_cities)

        safe_spinner_write(spinner, "\n=== CROSS-CHECK: Which site cities are NOT in the CSV? ===\n")
        if not new_cities:
            safe_spinner_write(spinner, "No new site-cities found => nothing to pass to second_scrub.\n")
        else:
            for city in sorted(new_cities):
                safe_spinner_write(spinner, f"NEW city => '{city}'")

            # Launch second_scrub.py
            safe_spinner_write(spinner, "\nNow launching 'second_scrub.py' to scrape these new cities...\n")
            cmd_list = [
                sys.executable,  # same python interpreter
                os.path.join(os.path.dirname(__file__), "second_scrub.py"),
                state_name
            ] + sorted(new_cities)

            # Fire off second_scrub
            subprocess.run(cmd_list)

        safe_spinner_ok(spinner, "Validation Done. Exiting city_checker.py now.")

    except Exception as e:
        safe_spinner_fail(spinner, f"ERROR: {e}")
        input("Press ENTER to exit.")
    finally:
        if driver:
            driver.quit()
        if spinner:
            spinner.stop()


if __name__ == "__main__":
    main()
