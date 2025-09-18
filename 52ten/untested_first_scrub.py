import time
import sys
import os
import pandas as pd
import traceback

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException,
    ElementClickInterceptedException
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
# REMOVE NAV & OVERLAYS
###############################################################################
def remove_nav_and_overlays(driver, spinner):
    safe_spinner_write(spinner, "\n========== Removing nav + dropdowns + container-fluid... ==========\n")
    try:
        driver.execute_script("""
const nb = document.getElementById('navbarCollapse');
if(nb){ nb.remove(); }
document.querySelectorAll('[data-toggle="dropdown"]').forEach(e => e.remove());
document.querySelectorAll('.container-fluid').forEach(cf => cf.style.display='none');
""")
        safe_spinner_write(spinner, "[DEBUG] Removed nav & overlays.\n")
    except Exception as e:
        safe_spinner_write(spinner, f"[DEBUG] Could not remove nav/overlays: {e}\n")

###############################################################################
# SAFE CLICK
###############################################################################
def safe_click(driver, element, spinner, description="element"):
    safe_spinner_write(spinner, f"[SAFE CLICK] Attempt normal click on {description}...\n")
    try:
        element.click()
        return True
    except ElementClickInterceptedException as e:
        safe_spinner_write(spinner, f"[ERROR] {description} click intercepted: {e}\nTrying JS fallback.\n")
        remove_nav_and_overlays(driver, spinner)
        try:
            driver.execute_script("arguments[0].click();", element)
            safe_spinner_write(spinner, "[SAFE CLICK] JS fallback succeeded.\n")
            return True
        except Exception as ex:
            safe_spinner_write(spinner, f"[ERROR] JS fallback also failed: {ex}\n")
            # final approach => parent <a> direct nav
            try:
                parent_a = element.find_element(By.XPATH, "..")
                if parent_a.tag_name.lower() == "a":
                    href = parent_a.get_attribute("href")
                    if href:
                        safe_spinner_write(spinner, "[SAFE CLICK] Direct nav via parent <a>.\n")
                        driver.get(href)
                        return True
            except Exception as ex2:
                safe_spinner_write(spinner, f"[ERROR] direct nav parent <a> also failed: {ex2}\n")
            return False
    except Exception as e:
        safe_spinner_write(spinner, f"[ERROR] Normal click on {description} failed: {e}\nTrying JS fallback.\n")
        remove_nav_and_overlays(driver, spinner)
        try:
            driver.execute_script("arguments[0].click();", element)
            safe_spinner_write(spinner, "[SAFE CLICK] JS fallback used.\n")
            return True
        except Exception as ex:
            safe_spinner_write(spinner, f"[ERROR] Could not click {description} with JS fallback: {ex}\n")
            return False

###############################################################################
# DISMISS COOKIE BANNER
###############################################################################
def dismiss_cookie_banner_once(driver, spinner):
    safe_spinner_write(spinner, "\n\n========== Checking for cookie banner... ==========\n")
    try:
        wait = WebDriverWait(driver, 5)
        cookie_btn = wait.until(EC.element_to_be_clickable((By.ID, "c-p-bn")))
        cookie_btn.click()
        safe_spinner_write(spinner, "[DEBUG] Cookie banner closed.\n")
    except Exception as e:
        safe_spinner_write(spinner, f"[DEBUG] No cookie banner or couldn't close: {e}\n")
    time.sleep(1)

###############################################################################
# LOAD MORE + SCROLL
###############################################################################
def load_all_pages_for_view(driver, spinner):
    while True:
        time.sleep(1)
        remove_nav_and_overlays(driver, spinner)
        try:
            load_more_btn = driver.find_element(By.XPATH, "//a[contains(text(),'Load More')]")
            safe_spinner_write(spinner, "\n[LOAD MORE] Attempting click...\n")
            driver.execute_script("arguments[0].scrollIntoView(true);", load_more_btn)
            time.sleep(1)
            if not safe_click(driver, load_more_btn, spinner, description="Load More link"):
                safe_spinner_write(spinner, "[LOAD MORE] Could not proceed. Breaking.\n")
                break
            time.sleep(3)
        except NoSuchElementException:
            safe_spinner_write(spinner, "[LOAD MORE] No more 'Load More' links.\n")
            break

    wait = WebDriverWait(driver, 30)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.entity-card-content-layer")))
    except TimeoutException:
        safe_spinner_write(spinner, "[ERROR] No .entity-card-content-layer => Possibly no data.\n")
        return

    safe_spinner_write(spinner, "[SCROLL] Attempting infinite scroll...\n")
    consecutive_no_growth = 0
    while True:
        remove_nav_and_overlays(driver, spinner)
        cards_before = driver.find_elements(By.CSS_SELECTOR, "div.entity-card-content-layer")
        count_before = len(cards_before)

        safe_spinner_write(spinner, f"[SCROLL] {count_before} cards. Scrolling further...\n")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

        cards_after = driver.find_elements(By.CSS_SELECTOR, "div.entity-card-content-layer")
        count_after = len(cards_after)
        if count_after > count_before:
            safe_spinner_write(spinner, f"[SCROLL] Card count grew from {count_before} -> {count_after}\n")
            consecutive_no_growth = 0
        else:
            safe_spinner_write(spinner, "[SCROLL] No new cards this iteration.\n")
            consecutive_no_growth += 1

        if consecutive_no_growth >= 2:
            break

###############################################################################
# SCRAPE PARK DETAIL
###############################################################################
def scrape_park_detail(driver, spinner):
    time.sleep(2)
    safe_spinner_write(spinner, "\n\n========== Parsing park detail page... ==========\n")

    def safe_find_text(sel, fallback="N/A"):
        try:
            return driver.find_element(By.CSS_SELECTOR, sel).text.strip()
        except NoSuchElementException:
            return fallback

    title            = safe_find_text("h1")
    price            = safe_find_text(".listing-price")
    address_fallback = safe_find_text(".listing-address")
    bed_bath         = safe_find_text(".listing-bed-bath")

    street_address = safe_find_text("ui-street-address-widget")
    if street_address == "N/A":
        street_address = address_fallback

    city_state_zip   = safe_find_text("ui-city-state-zip-widget")

    homes_for_sale = safe_find_text("#forSaleCount")
    homes_for_rent = safe_find_text("#forRentCount")
    vacant_sites   = safe_find_text("#vacantCount")

    def read_ul_after_strong(strong_text):
        try:
            ul_elem = driver.find_element(
                By.XPATH, f"//strong[contains(text(),'{strong_text}')]/following-sibling::ul"
            )
            return ul_elem.text.strip()
        except NoSuchElementException:
            return "N/A"

    pet_policies_block       = read_ul_after_strong("Pet Policies")
    additional_details_block = read_ul_after_strong("Additional Details")
    amenities_block          = read_ul_after_strong("Amenities")
    other_block              = read_ul_after_strong("Other")
    avg_rent_block           = read_ul_after_strong("Average Monthly Rent")

    def safe_xpath_strong(txt):
        try:
            e = driver.find_element(By.XPATH, f"//strong[contains(text(),'{txt}')]")
            return e.text.strip()
        except NoSuchElementException:
            return "N/A"

    on_site_sales_office = safe_xpath_strong("On Site Sales Office")
    contact_info_line    = safe_xpath_strong("Contact:")

    data = {
        "title": title,
        "price": price,
        "street_address": street_address,
        "city_state_zip": city_state_zip,
        "bed_bath": bed_bath,
        "homes_for_sale": homes_for_sale,
        "homes_for_rent": homes_for_rent,
        "vacant_sites": vacant_sites,
        "average_monthly_rent": avg_rent_block,
        "pet_policies": pet_policies_block,
        "additional_details": additional_details_block,
        "amenities": amenities_block,
        "other_info": other_block,
        "on_site_sales_office": on_site_sales_office,
        "contact_info_line": contact_info_line,
    }

    safe_spinner_write(spinner, f"[PARSE] Park detail extracted:\n{data}\n")
    return data

###############################################################################
# GATHER LINKS
###############################################################################
def gather_all_links(driver, spinner):
    remove_nav_and_overlays(driver, spinner)
    anchors = driver.find_elements(By.CSS_SELECTOR, "div.entity-card-content-layer a[href*='/parks/']")
    links = [a.get_attribute("href") for a in anchors]
    safe_spinner_write(spinner, f"[LINKS] Found {len(links)} listing links on final loaded page.\n")
    return links

###############################################################################
# CSV + DEDUP
###############################################################################
def append_row_to_csv(csv_filename, row_dict, field_order):
    file_exists = os.path.isfile(csv_filename)
    mode = 'a' if file_exists else 'w'
    df = pd.DataFrame([row_dict], columns=field_order)
    df.to_csv(csv_filename, mode=mode, header=not file_exists, index=False)

def dedup_csv(csv_filename):
    if not os.path.isfile(csv_filename):
        return
    df = pd.read_csv(csv_filename, dtype=str)
    if "park_url" not in df.columns:
        return
    before = df.shape[0]
    df.drop_duplicates(subset=["park_url", "city"], inplace=True)
    after = df.shape[0]
    if after < before:
        print(f"Deduplicated CSV from {before} -> {after} rows.")
    df.to_csv(csv_filename, index=False)

def count_csv_entries_for_city(csv_filename, city_name):
    if not os.path.isfile(csv_filename):
        return 0
    df = pd.read_csv(csv_filename, dtype=str)
    if "city" not in df.columns:
        return 0
    return df[df["city"] == city_name].shape[0]

###############################################################################
# GET LAST CITY FROM CSV
###############################################################################
def get_last_city_in_csv(csv_filename):
    if not os.path.isfile(csv_filename):
        return None
    df = pd.read_csv(csv_filename, dtype=str)
    if df.empty or "city" not in df.columns:
        return None
    return df.iloc[-1]["city"]

###############################################################################
# CLICK HOME LINK
###############################################################################
def safe_click_home_link(driver, spinner):
    safe_spinner_write(spinner, "[HOME FALLBACK] Attempting anchor parent for MHVillage logo...\n")
    try:
        anchor_home = driver.find_element(By.CSS_SELECTOR, "a.navbar-brand")
        if not safe_click(driver, anchor_home, spinner, description="Home anchor link"):
            safe_spinner_write(spinner, "[HOME FALLBACK] Could not click anchor. Doing direct nav.\n")
            driver.get("https://www.mhvillage.com/")
            time.sleep(3)
        else:
            time.sleep(3)
        return True
    except Exception as e:
        safe_spinner_write(spinner, f"[HOME FALLBACK] anchor parent not found: {e}\nDirect nav.\n")
        try:
            driver.get("https://www.mhvillage.com/")
            time.sleep(3)
            return True
        except Exception as ex:
            safe_spinner_write(spinner, f"[HOME FALLBACK] direct nav also failed: {ex}\n")
            return False

###############################################################################
# RE-RUN STATE SEARCH
###############################################################################
def run_state_search_flow(driver, spinner, state):
    wait = WebDriverWait(driver, 30)
    safe_spinner_write(spinner, f"[HOME FALLBACK] Re-running state search for '{state}'...\n")
    try:
        dismiss_cookie_banner_once(driver, spinner)

        search_input = wait.until(EC.presence_of_element_located((By.ID, "autocomplete-input")))
        search_input.clear()
        search_input.send_keys(state)
        time.sleep(1)

        try:
            suggestions = wait.until(
                EC.visibility_of_all_elements_located((By.CSS_SELECTOR, "a.dropdown-item.cursor-pointer"))
            )
        except TimeoutException:
            page_src = driver.page_source
            print("\n\n[DEBUG] Page Source after typing state (fallback run_state_search_flow):\n", page_src, "\n\n")
            raise

        if not suggestions:
            page_src = driver.page_source
            print("\n\n[DEBUG] No suggestions found for that state input (fallback). Page Source:\n", page_src, "\n\n")
            raise Exception("No suggestions found for that state input (fallback).")

        if not safe_click(driver, suggestions[0], spinner, description="first suggestion fallback"):
            raise Exception("Could not click first suggestion fallback in run_state_search_flow.")

        time.sleep(2)
        safe_spinner_write(spinner, "[HOME FALLBACK] Clicking 'Search'...\n")
        search_btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[@translate='app.search' and contains(text(), 'Search')]"))
        )
        if not safe_click(driver, search_btn, spinner, description="Search button fallback"):
            raise Exception("Could not click 'Search' fallback in run_state_search_flow.")

        time.sleep(5)
        safe_spinner_write(spinner, "[HOME FALLBACK] Selecting 'Parks' tab...\n")
        parks_tab = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, '//span[@class="w-100 text-center" and contains(text(), "Parks")]')
            )
        )
        if not safe_click(driver, parks_tab, spinner, description="'Parks' tab fallback"):
            raise Exception("Could not open 'Parks' tab fallback.")
        time.sleep(5)

        safe_spinner_write(spinner, "[HOME FALLBACK] Re-search flow done. City list should appear.\n")
        return True
    except Exception as e:
        safe_spinner_write(spinner, f"[HOME FALLBACK] run_state_search_flow failed: {e}\n")
        return False

###############################################################################
# SAFE RETURN TO CITY LIST
###############################################################################
def safe_return_to_city_list(driver, spinner, city_name, state):
    safe_spinner_write(spinner, f"[{city_name}] 1st back => city listing.\n")
    driver.back()
    time.sleep(2)

    safe_spinner_write(spinner, f"[{city_name}] 2nd back => statewide city list.\n")
    driver.back()
    time.sleep(3)

    wait = WebDriverWait(driver, 30)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "strong.result-location")))
        safe_spinner_write(spinner, "[FALLBACK CHECK] City list re-located normally.\n")
        return True
    except TimeoutException:
        safe_spinner_write(spinner, "[FALLBACK CHECK] Timed out re-locating city <strong>. Attempt home fallback...\n")
        if not safe_click_home_link(driver, spinner):
            safe_spinner_write(spinner, "[FALLBACK CHECK] Could not go home. Breaking.\n")
            return False

        if not run_state_search_flow(driver, spinner, state):
            safe_spinner_write(spinner, "[FALLBACK CHECK] re-run state search also failed.\n")
            return False
        return True

###############################################################################
# PARSE CITY PARKS (Full City)
###############################################################################
def parse_city_parks(driver, spinner, city_name, csv_filename):
    load_all_pages_for_view(driver, spinner)
    city_links = gather_all_links(driver, spinner)
    site_count = len(city_links)
    csv_count = count_csv_entries_for_city(csv_filename, city_name)

    if csv_count >= site_count:
        safe_spinner_write(spinner, f"[{city_name}] CSV has {csv_count} >= site_count {site_count}. Skipping city.\n")
        return []

    safe_spinner_write(spinner, f"[{city_name}] CSV={csv_count}, site={site_count} => parse entire city.\n")

    field_order = [
        "park_url", "city",
        "title", "price", "street_address", "city_state_zip", "bed_bath",
        "homes_for_sale", "homes_for_rent", "vacant_sites",
        "average_monthly_rent", "pet_policies", "additional_details", "amenities",
        "other_info", "on_site_sales_office", "contact_info_line"
    ]

    city_data = []
    for idx, link_url in enumerate(city_links, start=1):
        remove_nav_and_overlays(driver, spinner)
        safe_spinner_write(spinner, f"[{city_name}] Opening listing #{idx}: {link_url}\n")

        try:
            driver.get(link_url)
            time.sleep(2)
        except Exception as e:
            safe_spinner_write(spinner, f"[ERROR] direct nav to {link_url} failed: {e}\n")
            continue

        detail = scrape_park_detail(driver, spinner)
        detail["park_url"] = link_url
        detail["city"] = city_name
        city_data.append(detail)

        # partial save
        append_row_to_csv(csv_filename, detail, field_order)

        safe_spinner_write(spinner, f"[{city_name}] Returning after detail.\n")
        driver.back()
        time.sleep(2)

    return city_data

###############################################################################
# PARSE CITY PARKS AFTER RESUME (Partial)
###############################################################################
def parse_city_parks_after_resume(driver, spinner, city_name, csv_filename):
    load_all_pages_for_view(driver, spinner)
    city_links = gather_all_links(driver, spinner)
    site_count = len(city_links)
    csv_count = count_csv_entries_for_city(csv_filename, city_name)

    field_order = [
        "park_url", "city",
        "title", "price", "street_address", "city_state_zip", "bed_bath",
        "homes_for_sale", "homes_for_rent", "vacant_sites",
        "average_monthly_rent", "pet_policies", "additional_details", "amenities",
        "other_info", "on_site_sales_office", "contact_info_line"
    ]

    # Gather existing park_urls in CSV for this city
    existing_urls = set()
    if os.path.isfile(csv_filename):
        df = pd.read_csv(csv_filename, dtype=str)
        df_city = df[df["city"] == city_name]
        existing_urls = set(df_city["park_url"].tolist())

    safe_spinner_write(spinner, f"[{city_name}] (Resume partial) Found {len(existing_urls)} in CSV, site_count={site_count}.\n")

    city_data = []
    for idx, link_url in enumerate(city_links, start=1):
        if link_url in existing_urls:
            safe_spinner_write(spinner, f"[{city_name}] Already in CSV => skipping {link_url}\n")
            continue

        remove_nav_and_overlays(driver, spinner)
        safe_spinner_write(spinner, f"[{city_name}] Partial resume => listing #{idx}: {link_url}\n")

        try:
            driver.get(link_url)
            time.sleep(2)
        except Exception as e:
            safe_spinner_write(spinner, f"[ERROR] direct nav to {link_url} failed: {e}\n")
            continue

        detail = scrape_park_detail(driver, spinner)
        detail["park_url"] = link_url
        detail["city"] = city_name
        city_data.append(detail)

        append_row_to_csv(csv_filename, detail, field_order)

        safe_spinner_write(spinner, f"[{city_name}] Returning after partial detail.\n")
        driver.back()
        time.sleep(2)

    return city_data

###############################################################################
# SINGLE LIST FALLBACK
###############################################################################
def single_list_parks(driver, spinner, csv_filename):
    load_all_pages_for_view(driver, spinner)
    all_links = gather_all_links(driver, spinner)

    field_order = [
        "park_url", "city",
        "title", "price", "street_address", "city_state_zip", "bed_bath",
        "homes_for_sale", "homes_for_rent", "vacant_sites",
        "average_monthly_rent", "pet_policies", "additional_details", "amenities",
        "other_info", "on_site_sales_office", "contact_info_line"
    ]

    results = []
    for idx, link_url in enumerate(all_links, start=1):
        remove_nav_and_overlays(driver, spinner)
        safe_spinner_write(spinner, f"[SINGLE] Opening listing #{idx}: {link_url}\n")

        try:
            driver.get(link_url)
            time.sleep(2)
        except Exception as e:
            safe_spinner_write(spinner, f"[ERROR] direct nav to {link_url} failed: {e}\n")
            continue

        detail = scrape_park_detail(driver, spinner)
        detail["park_url"] = link_url
        detail["city"] = "N/A"
        results.append(detail)

        append_row_to_csv(csv_filename, detail, field_order)

        safe_spinner_write(spinner, "[SINGLE] Returning after parse.\n")
        driver.back()
        time.sleep(2)

    return results

###############################################################################
# MASTER CITY LIST
###############################################################################
def city_list_parks(driver, spinner, csv_filename, state):
    wait = WebDriverWait(driver, 30)
    safe_spinner_write(spinner, "\n\n[CHECK] Looking for city <strong>...\n\n")

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "strong.result-location")))
    except TimeoutException:
        safe_spinner_write(spinner, "[WARN] No city <strong> => single-list fallback.\n")
        return single_list_parks(driver, spinner, csv_filename)

    city_elems = driver.find_elements(By.CSS_SELECTOR, "strong.result-location")
    total_cities = len(city_elems)
    safe_spinner_write(spinner, f"[DEBUG] Found {total_cities} city <strong>.\n")

    if total_cities == 0:
        safe_spinner_write(spinner, "[WARN] 0 city => single-list approach.\n")
        return single_list_parks(driver, spinner, csv_filename)

    last_city_done = get_last_city_in_csv(csv_filename)
    safe_spinner_write(spinner, f"[RESUME] Last city from CSV = '{last_city_done}'\n")

    skip_mode = True if last_city_done else False
    found_last_city = False

    all_data = []
    for idx in range(total_cities):
        remove_nav_and_overlays(driver, spinner)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "strong.result-location")))
        except TimeoutException:
            safe_spinner_write(spinner, "[ERROR] Timed out re-locating city <strong>. Breaking.\n")
            break

        updated_cities = driver.find_elements(By.CSS_SELECTOR, "strong.result-location")
        if idx >= len(updated_cities):
            break

        city_elem = updated_cities[idx]
        city_name = city_elem.text.strip()
        safe_spinner_write(spinner, f"\n[CITY #{idx+1}] => '{city_name}'\n")

        # RESUME LOGIC
        if skip_mode:
            if not found_last_city:
                if city_name == last_city_done:
                    # Found partially done city
                    safe_spinner_write(spinner, f"[RESUME] Found last city '{city_name}'. Checking CSV vs site.\n")
                    # We still need to open it
                    driver.execute_script("arguments[0].scrollIntoView(true);", city_elem)
                    time.sleep(1)
                    if not safe_click(driver, city_elem, spinner, description=f"Resume city '{city_name}'"):
                        safe_spinner_write(spinner, f"[ERROR] Could not open city '{city_name}'. Skipping.\n")
                        found_last_city = True
                        continue

                    time.sleep(2)
                    try:
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.entity-card-content-layer")))
                    except TimeoutException:
                        safe_spinner_write(spinner, f"[ERROR] Timed out waiting for city '{city_name}' => skip.\n")
                        driver.back()
                        found_last_city = True
                        continue

                    # partial check
                    load_all_pages_for_view(driver, spinner)
                    city_links = gather_all_links(driver, spinner)
                    site_count = len(city_links)
                    csv_count = count_csv_entries_for_city(csv_filename, city_name)
                    safe_spinner_write(spinner, f"[{city_name}] site_count={site_count}, csv_count={csv_count}\n")

                    if csv_count < site_count:
                        safe_spinner_write(spinner, f"[{city_name}] Doing partial scraping for remainder.\n")
                        city_data = parse_city_parks_after_resume(driver, spinner, city_name, csv_filename)
                        all_data.extend(city_data)
                    else:
                        safe_spinner_write(spinner, f"[{city_name}] Already matched or exceeded. Skipping.\n")

                    if not safe_return_to_city_list(driver, spinner, city_name, state):
                        break

                    found_last_city = True
                else:
                    safe_spinner_write(spinner, f"[RESUME] Skipping '{city_name}' until last city found.\n")
                continue
            else:
                skip_mode = False

        # Normal city parse (full)
        driver.execute_script("arguments[0].scrollIntoView(true);", city_elem)
        time.sleep(1)

        if not safe_click(driver, city_elem, spinner, description=f"City '{city_name}'"):
            safe_spinner_write(spinner, f"[ERROR] Could not open city '{city_name}'. Skipping.\n")
            continue

        time.sleep(2)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.entity-card-content-layer")))
        except TimeoutException:
            safe_spinner_write(spinner, f"[ERROR] Timed out waiting for city '{city_name}' => skip.\n")
            driver.back()
            continue

        city_data = parse_city_parks(driver, spinner, city_name, csv_filename)
        all_data.extend(city_data)

        if not safe_return_to_city_list(driver, spinner, city_name, state):
            break

    return all_data

###############################################################################
# MAIN
###############################################################################
def main():
    # Prompt for state BEFORE spinner
    state = input("Enter the state name (e.g., 'Arizona'): ")

    spin = None
    try:
        spin = yaspin(text="Starting up...", color="cyan")
        spin.start()
    except ValueError:
        print("Terminal too small for spinner fallback.\n")
        spin = None

    driver = None
    try:
        csv_filename = f"{state.lower()}_parks_listings.csv"

        # Adjust your path to chromedriver if needed
        service = Service("/Users/georgesmacbook/Downloads/dist/chromedriver")
        options = webdriver.ChromeOptions()
        options.page_load_strategy = "eager"

        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(300)
        driver.set_window_size(1920, 1080)

        safe_spinner_write(spin, "[DEBUG] Loading MHVillage homepage...\n")
        driver.get("https://www.mhvillage.com/")
        time.sleep(2)

        dismiss_cookie_banner_once(driver, spin)

        wait = WebDriverWait(driver, 30)

        # Type state in the search
        safe_spinner_write(spin, f"\nSearching for state: {state}\n")
        search_input = wait.until(EC.presence_of_element_located((By.ID, "autocomplete-input")))
        search_input.clear()
        search_input.send_keys(state)
        time.sleep(1)

        try:
            suggestions = wait.until(
                EC.visibility_of_all_elements_located((By.CSS_SELECTOR, "a.dropdown-item.cursor-pointer"))
            )
        except TimeoutException:
            page_src = driver.page_source
            print("\n\n[DEBUG] Page Source after typing state:\n", page_src, "\n\n")
            raise

        if not suggestions:
            page_src = driver.page_source
            print("\n\n[DEBUG] No suggestions found for that state input. Page Source:\n", page_src, "\n\n")
            raise Exception("No suggestions found for that state input.")

        safe_spinner_write(spin, f"Found {len(suggestions)} suggestions. Clicking first.\n")
        if not safe_click(driver, suggestions[0], spin, description="first suggestion"):
            raise Exception("Could not click first suggestion fallback.")

        time.sleep(2)
        safe_spinner_write(spin, "Clicking 'Search' button...\n")
        search_btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[@translate='app.search' and contains(text(), 'Search')]"))
        )
        if not safe_click(driver, search_btn, spin, description="Search button"):
            raise Exception("Could not click 'Search' fallback.")

        time.sleep(5)
        safe_spinner_write(spin, "Selecting 'Parks' tab...\n")
        parks_tab = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, '//span[@class="w-100 text-center" and contains(text(), "Parks")]')
            )
        )
        if not safe_click(driver, parks_tab, spin, description="'Parks' tab"):
            safe_spinner_write(spin, "[ERROR] Could not open 'Parks' tab.\n")
            return

        time.sleep(5)
        safe_spinner_write(spin, f"Now scraping 'Parks' for state: {state}\n")

        all_data = city_list_parks(driver, spin, csv_filename, state)
        safe_spinner_write(spin, f"\nScrape attempt done for '{state}'. Collected {len(all_data)} new rows.\n")

        dedup_csv(csv_filename)

        if spin:
            safe_spinner_ok(spin, "Done scraping 'Parks'!")
        else:
            print("Done scraping 'Parks'!\n")

    except Exception as ex:
        if spin:
            safe_spinner_fail(spin, f"ERROR: {ex}\n{traceback.format_exc()}")
        else:
            print(f"ERROR: {ex}\n{traceback.format_exc()}")
        input("[Press ENTER to exit]")
    finally:
        if driver:
            driver.quit()
        if spin:
            try:
                spin.stop()
            except:
                pass

    #
    # === Prompt user to run validation ===
    #
    ans = input("\nWould you like to run a validation check now? (Y/N): ")
    if ans.strip().lower().startswith('y'):
        # You can either instruct them to run or spawn a process
        # Here, we demonstrate spawning the validation script:
        print("\nRunning validation check...\n")
        os.system(f'python mhv_validate.py "{csv_filename}"')
        print("\nValidation check completed. Exiting...\n")

if __name__ == "__main__":
    main()
