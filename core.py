import os
import json
import logging
from datetime import datetime
from typing import Tuple, Dict, Any
import random

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from anticaptchaofficial.recaptchav3proxyless import recaptchaV3Proxyless

from parser import parse_dmv_response_and_save

# Configuration
PAGE_URL = os.getenv("PAGE_URL", "https://www.dmv.ca.gov/wasapp/FeeCalculatorWeb/newResidentForm.do")
SUBMIT_URL = os.getenv("SUBMIT_URL", "https://www.dmv.ca.gov/wasapp/FeeCalculatorWeb/newResidentFees.do")
API_KEY = os.getenv("ANTICAPTCHA_KEY")
if not API_KEY:
    raise RuntimeError("Please set the ANTICAPTCHA_KEY environment variable")

CAPTCHA_TIMEOUT = int(os.getenv("CAPTCHA_TIMEOUT", 60))  # seconds

# Logging Setup
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def extract_recaptcha_config(html: str) -> Tuple[str, str]:
    """
    Extract ReCAPTCHA v3 sitekey and action from HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", src=lambda s: s and "recaptchav3.js" in s)
    if not tag:
        raise RuntimeError("Could not find ReCAPTCHA v3 loader script in page HTML.")
    qs = parse_qs(urlparse(tag["src"]).query)
    sitekey = qs.get("sitekey", [""])[0]
    action = qs.get("action", [""])[0]
    if not sitekey or not action:
        raise RuntimeError("Empty ReCAPTCHA config: sitekey=%r, action=%r" % (sitekey, action))
    return sitekey, action


def solve_captcha(session: requests.Session, html: str) -> str:
    """
    Solve ReCAPTCHA v3 and return token.
    """
    sitekey, action = extract_recaptcha_config(html)
    solver = recaptchaV3Proxyless()
    solver.set_verbose(1)
    solver.set_key(API_KEY)
    solver.set_website_url(PAGE_URL)
    solver.set_website_key(sitekey)
    solver.set_page_action(action)
    solver.set_min_score(0.3)

    logger.info(f"Solving ReCAPTCHA V3 (sitekey={sitekey}, action={action})")
    token = solver.solve_and_return_solution()
    if not token:
        logger.error("ReCAPTCHA v3 solve failed: %s", solver.error_code)
        raise RuntimeError(f"CAPTCHA solve failed: {solver.error_code}")
    logger.info("ReCAPTCHA solved successfully.")
    return token


def save_json(data: Dict[str, Any], path: str) -> None:
    """
    Save dictionary as JSON file, creating directories if needed.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_scrape(idx: int, output_dir: str) -> None:
    """
    Main scraping workflow: fetch form, populate payload, solve captcha, submit form, parse response.
    """
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/136.0.0.0 Safari/537.36",
        "Referer": PAGE_URL,
        # add other headers as needed
    }
    result_dir = os.path.join(output_dir, str(idx))
    os.makedirs(result_dir, exist_ok=True)

    # STEP 1: GET form page
    logger.info("[%d] Fetching form page", idx)
    response = session.get(PAGE_URL, headers=headers)
    response.raise_for_status()
    with open(os.path.join(result_dir, 'form.html'), 'w', encoding='utf-8') as f:
        f.write(response.text)

    # STEP 2: Extract hidden fields
    hidden_fields = extract_hidden_fields(response.text)

    # STEP 3: Generate payload
    payload = generate_random_payload(idx)
    form_data = {**hidden_fields, **payload}

    # STEP 4: Solve captcha with timeout handling
    token = _get_token_with_refresh(session, response.text, payload, idx)
    form_data['g-recaptcha-response'] = token

    # Save payload for debugging
    save_json(form_data, os.path.join(result_dir, 'payload.json'))

    # STEP 5: Submit POST
    logger.info("[%d] Submitting form", idx)
    post_resp = session.post(SUBMIT_URL, data=form_data, headers={**headers, "Content-Type": "application/x-www-form-urlencoded"})
    post_resp.raise_for_status()
    with open(os.path.join(result_dir, 'response.html'), 'w', encoding='utf-8') as f:
        f.write(post_resp.text)

    # STEP 6: Parse and save CSVs
    summary_csv = os.path.join(result_dir, 'summary.csv')
    detail_csv = os.path.join(result_dir, 'detailed.csv')
    parse_dmv_response_and_save(post_resp.text, summary_csv, detail_csv)


def _get_token_with_refresh(session: requests.Session, html: str, payload: Dict[str, Any], idx: int) -> str:
    """
    Solve captcha, and refresh session if timeout exceeded.
    """
    start = datetime.now()
    try:
        return solve_captcha(session, html)
    except RuntimeError as e:
        elapsed = (datetime.now() - start).total_seconds()
        if elapsed > CAPTCHA_TIMEOUT:
            logger.warning("[%d] Captcha solve took %.1fs > %ds, refreshing session", idx, elapsed, CAPTCHA_TIMEOUT)
            response = session.get(PAGE_URL)
            response.raise_for_status()
            return solve_captcha(session, response.text)
        raise e

def save_payload(payload: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def extract_hidden_fields(html: str) -> Dict[str, str]:
    """
    Extract hidden input fields from the form.
    """
    soup = BeautifulSoup(html, 'html.parser')
    return {inp['name']: inp.get('value', '') for inp in soup.select('form#FeeRequestForm input[type=hidden]') if inp.get('name')}


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run DMV fee scraper')
    parser.add_argument('-n', '--num', type=int, default=1, help='Number of scrapes')
    parser.add_argument('-o', '--output', default='results', help='Output directory')
    args = parser.parse_args()
    for i in range(args.num):
        run_scrape(i, args.output)



def generate_random_payload(idx: int) -> dict:
    """
    Generates a fully-populated payload for the DMV new resident fee calculator,
    combining all original option sets with deterministic weight-range logic
    and valid county→city→zip mapping.
    """
    current_year = datetime.today().year

    # ── Original option tables ────────────────────────────────────────────────
    vehicle_types = {
        "Automobile": "11", "Motorcycle": "21", "Commercial": "31",
        "Trailer": "40", "Off Highway Vehicle": "F0", "Vessel": "V1"
    }
    motive_powers = {
        "Gas": "G", "Hybrid": "Q", "Diesel": "D",
        "Electric": "E", "Other": "O"
    }
    secondary_motive_powers = {
        "Butane": "B", "Methanol": "M", "Natural Gas": "N",
        "Propane": "P", "Flex Fuel": "F", "Hydrogen": "R",
        "Diesel-Hybrid": "Y"
    }
    axle_options = {"Two": "2", "More than Two": "3"}
    weight_types = {
        "Unladen": "U", "Gross Vehicle": "G",
        "Combined Gross Vehicle": "C"
    }
    electric_types = {
        "under 6,000": "1000", "6,000 - 9,999": "6000",
        "10,000 and over": "10000"
    }
    unladen_ranges_two_axles = {
        "under 3,000": "1000", "3,000 - 4,000": "3000",
        "4,001 - 5,000": "4001", "5,001 - 6,000": "5001",
        "6,001 - 7,000": "6001", "7,001 - 8,000": "7001",
        "8,001 - 9,000": "8001", "9,001 - 10,000": "9001",
        "above 10,000": "10001"
    }
    gross_ranges = {
        "10,001 - 15,000": "A", "15,001 - 20,000": "B",
        "20,001 - 26,000": "C", "26,001 - 30,000": "D",
        "30,001 - 35,000": "E", "35,001 - 40,000": "F",
        "40,001 - 45,000": "G", "45,001 - 50,000": "H",
        "50,001 - 54,999": "I", "55,000 - 60,000": "J",
        "60,001 - 65,000": "K", "65,001 - 70,000": "L",
        "70,001 - 75,000": "M", "75,001 - 80,000": "N"
    }
    acquired_from_options = {
        "California Dealer": "D", "Out of State Dealer": "O",
        "Private Party": "P", "Family Transfer": "F",
        "Vehicle was a Gift": "G"
    }
    counties = {
        "Alameda": "1", "Alpine": "2", "Amador": "3", "Butte": "4",
        "Calaveras": "5", "Colusa": "6", "Contra Costa": "7",
        "Del Norte": "8", "El Dorado": "9", "Fresno": "10",
        "Glenn": "11", "Humboldt": "12", "Imperial": "13",
        "Inyo": "14", "Kern": "15", "Kings": "16", "Lake": "17",
        "Lassen": "18", "Los Angeles": "19", "Madera": "20",
        "Marin": "21", "Mariposa": "22", "Mendocino": "23",
        "Merced": "24", "Modoc": "25", "Mono": "26",
        "Monterey": "27", "Napa": "28", "Nevada": "29",
        "Orange": "30", "Placer": "31", "Plumas": "32",
        "Riverside": "33", "Sacramento": "34", "San Benito": "35",
        "San Bernardino": "36", "San Diego": "37",
        "San Francisco": "38", "San Joaquin": "39",
        "San Luis Obispo": "40", "San Mateo": "41",
        "Santa Barbara": "42", "Santa Clara": "43",
        "Santa Cruz": "44", "Shasta": "45", "Sierra": "46",
        "Siskiyou": "47", "Solano": "48", "Sonoma": "49",
        "Stanislaus": "50", "Sutter": "51", "Tehama": "52",
        "Trinity": "53", "Tulare": "54", "Tuolumne": "55",
        "Ventura": "56", "Yolo": "57", "Yuba": "58"
    }

    # ── County → City → ZIP mapping ────────────────────────────────────────────
    county_cities = {
        "Alameda":     ("Oakland",     "OAKLAND",     "94607"),
        "Butte":       ("Chico",       "CHICO",       "95926"),
        "Los Angeles": ("Los Angeles", "LOSANGELES",  "90001"),
        "Orange":      ("Anaheim",     "ANAHEIM",     "92801"),
        "San Diego":   ("San Diego",   "SANDIEGO",    "92101"),
    }

    # ── Random Dates ───────────────────────────────────────────────────────────
    operated = datetime(
        year=random.randint(current_year-1, current_year),
        month=random.randint(1, 12),
        day=random.randint(1, 28)
    )
    purchased = datetime(
        year=random.randint(current_year-1, current_year),
        month=random.randint(1, 12),
        day=random.randint(1, 28)
    )

    # ── Random Selections ───────────────────────────────────────────────────────
    vt = random.choice(list(vehicle_types.values()))
    mp = random.choice(list(motive_powers.values()))
    # secondary motive power unused by form; left here for completeness
    smp = random.choice(list(secondary_motive_powers.values()))
    ax = random.choice(list(axle_options.values()))
    county_name = random.choice(list(county_cities.keys()))
    county_code = counties[county_name]
    city_label, city_val, zip_code = county_cities[county_name]
    acq = random.choice(list(acquired_from_options.values()))

    # ── Build Base Payload ─────────────────────────────────────────────────────
    payload = {
        "typeLicense":            vt,
        "yearModel":              str(random.randint(1990, current_year)),
        "motive-power":           mp,
        "motivePower":            mp,
        # secondary motive not sent — preserved in payload for debugging
        "secondaryMotivePower":   smp,
        "numberOfAxles":          ax,
        "electricType":           random.choice(list(electric_types.values())) if mp == "E" else "",
        "operatedMonth":          f"{operated.month:02d}",
        "operatedDay":            f"{operated.day:02d}",
        "operatedYear":           str(operated.year),
        "purchaseMonth":          f"{purchased.month:02d}",
        "purchaseDay":            f"{purchased.day:02d}",
        "purchaseYear":           str(purchased.year),
        "acquiredFrom":           acq,
        "purchasePrice":          str(random.randint(1000, 100000)),
        "useTaxCredit":           str(random.randint(0, 5000)),
        "countyCode":             county_code,
        "countyNameLabel":        county_name,
        "cityNameLabel":          city_label,
        "cityName":               city_val,
        "zipCode":                zip_code,
    }

    # ── WeightType + Matching Range ───────────────────────────────────────────
    wt = random.choice(list(weight_types.values()))
    payload["weightType"] = wt
    if wt == "U":
        # unladen for two-axle vs multi-axle
        if ax == "2":
            payload["unladenRangeTwoAxles"] = random.choice(list(unladen_ranges_two_axles.values()))
            payload["unladenRangeMoreThanTwoAxles"] = ""
        else:
            payload["unladenRangeMoreThanTwoAxles"] = random.choice(list(unladen_ranges_two_axles.values()))
            payload["unladenRangeTwoAxles"] = ""
        payload["grossRange"] = ""
    else:
        payload["grossRange"] = random.choice(list(gross_ranges.values()))
        payload["unladenRangeTwoAxles"] = ""
        payload["unladenRangeMoreThanTwoAxles"] = ""

    # ── Persist for Debug ───────────────────────────────────────────────────────
    save_payload(payload, f"results/{idx}/payload.json")

    return payload
