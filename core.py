# core.py

import os
import time
import random
from datetime import date, timedelta
import logging
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import parse_qs
from anticaptchaofficial.recaptchav3proxyless import recaptchaV3Proxyless
from parser import parse_dmv_response_and_save
from dotenv import load_dotenv
load_dotenv()

# ─── 2.1 Configure Logging ─────────────────────────────────────────────────────
# We’ll log to a file named 'scraper_debug.log' inside each run’s directory.
def configure_logger(run_dir: str):
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, "scraper_debug.log")

    # Create or overwrite the log file for this run
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode="w", encoding="utf-8"),
            logging.StreamHandler()  # also print to console
        ]
    )
    logging.info(f"Logging initialized. Writing to {log_path}")


# ─── 2.2 Helper Functions for Timed HTTP ────────────────────────────────────────
def timed_get(session: requests.Session, url: str, run_dir: str) -> requests.Response:
    """
    Perform a GET and log status, timing, and a snippet of the response.
    """
    start = time.time()
    logging.info(f"→ GET {url}")
    try:
        resp = session.get(url, timeout=60)
        elapsed = (time.time() - start) * 1000
        logging.info(f"← {resp.status_code} {url} ({elapsed:.0f} ms)")
        # Log a small snippet of HTML for debugging (first 500 chars)
        snippet = resp.text.replace("\n", " ").strip()[:500]
        logging.debug(f"   HTML snippet: {snippet + ('…[truncated]' if len(resp.text) > 500 else '')}")
        return resp
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logging.error(f"✖ GET {url} failed after {elapsed:.0f} ms: {e}")
        raise


def timed_post(session: requests.Session, url: str, data: dict, run_dir: str) -> requests.Response:
    """
    Perform a POST and log status, timing, and a snippet of the response or error.
    """
    start = time.time()
    logging.info(f"→ POST {url} (payload keys: {list(data.keys())})")
    try:
        resp = session.post(url, data=data, timeout=60)
        elapsed = (time.time() - start) * 1000
        logging.info(f"← {resp.status_code} {url} ({elapsed:.0f} ms)")
        # Log small snippet of HTML (first 500 chars)
        snippet = resp.text.replace("\n", " ").strip()[:500]
        logging.debug(f"   HTML snippet: {snippet + ('…[truncated]' if len(resp.text) > 500 else '')}")
        return resp
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logging.error(f"✖ POST {url} failed after {elapsed:.0f} ms: {e}")
        raise


# ─── 2.3 Extract ReCAPTCHA Config (unchanged) ─────────────────────────────────────
def extract_recaptcha_config(html: str):
    """
    Parse the page’s <script src="…recaptchav3.js?sitekey=…&selector=…&action=…">
    and return (sitekey, action).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the <script> tag whose `src` contains "recaptchav3.js"
    script_tag = soup.find("script", attrs={"src": lambda s: s and "recaptchav3.js" in s})
    if not script_tag:
        logging.error("[Captcha] Could not find ReCAPTCHA v3 loader script in page HTML.")
        raise RuntimeError("Could not find ReCAPTCHA v3 loader script in page HTML.")

    src = script_tag["src"]
    # Everything after the first "?" is the query string
    qs = src.split("?", 1)[1]
    params = parse_qs(qs)

    # CA DMV uses "sitekey" (not "render")
    sitekey = params.get("sitekey", [""])[0]
    action  = params.get("action",  [""])[0]

    if not sitekey:
        logging.error(f"[Captcha] Found recaptchav3.js tag, but sitekey was empty. Raw src: {src}")
        raise RuntimeError("Empty sitekey—cannot solve CAPTCHA.")

    logging.info(f"[Captcha] Extracted sitekey='{sitekey}', action='{action}'")
    return sitekey, action


# ─── 2.4 Solve CAPTCHA with Timing ───────────────────────────────────────────────
def solve_captcha(session: requests.Session, html: str, run_dir: str) -> str:
    sitekey, action = extract_recaptcha_config(html)

    solver = recaptchaV3Proxyless()
    solver.set_verbose(1)
    solver.set_key(os.getenv("ANTICAPTCHA_KEY") or "")
    solver.set_website_url(os.getenv("PAGE_URL") or "")
    solver.set_website_key(sitekey)
    solver.set_page_action(action)     # ← use set_page_action, not set_website_action
    solver.set_min_score(0.3)

    logging.info("[Captcha] Starting to solve ReCAPTCHA V3…")
    start = time.time()
    token = solver.solve_and_return_solution()
    elapsed = (time.time() - start) * 1000
    if not token:
        error_code = solver.error_code
        logging.error(f"[Captcha] No token returned; solver.error_code = {error_code} (in {elapsed:.0f} ms)")
        raise RuntimeError(f"Captcha failed: {error_code}")
    logging.info(f"[Captcha] Received token (first 20 chars): {token[:20]}… (in {elapsed:.0f} ms)")
    return token


# ─── 2.5 Extract Hidden Fields ───────────────────────────────────────────────────
def extract_hidden_fields(html: str, run_dir: str) -> dict:
    """
    Find all <input type='hidden'> under form#FeeRequestForm and return name->value.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"id": "FeeRequestForm"})
    if not form:
        logging.warning("[HiddenFields] <form id='FeeRequestForm'> not found in HTML.")
        return {}

    hidden = {}
    for inp in form.find_all("input", type="hidden"):
        name = inp.get("name")
        value = inp.get("value", "")
        if name:
            hidden[name] = value

    logging.info(f"[HiddenFields] Extracted {len(hidden)} hidden fields: {list(hidden.keys())}")
    return hidden


# ─── 2.6 Save JSON Utility (unchanged) ────────────────────────────────────────────
def save_json(data: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logging.info(f"[I/O] Saved JSON to {path}")


# ─── 2.7 Generate Random Payload (unchanged, aside from logging) ───────────────────

def generate_random_payload(idx: int, run_dir: str) -> dict:
    """
    Generates a fully-populated payload for the DMV new resident fee calculator,
    ensuring all dates are valid (≤ today, and purchase ≥ operated), and selecting
    appropriate weight ranges and county→city→ZIP mappings. If the vehicle type
    is “Trailer” (code "40"), also supplies a valid trailerType. Any field whose
    value is an empty string is omitted from the final payload.
    """
    today = date.today()
    current_year = today.year

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

    # ── Generate Valid Dates ────────────────────────────────────────────────────
    # 1) "First Operated in CA" must be ≤ today and at least within the past year
    earliest_operated = today - timedelta(days=365)
    operated_date = earliest_operated + timedelta(
        days=random.randint(0, (today - earliest_operated).days)
    )

    # 2) "Purchase Date" must be ≥ operated_date and ≤ today
    purchase_start = operated_date
    purchase_date = purchase_start + timedelta(
        days=random.randint(0, (today - purchase_start).days)
    )

    # ── Random Selections ───────────────────────────────────────────────────────
    vt = random.choice(list(vehicle_types.values()))
    mp = random.choice(list(motive_powers.values()))
    smp = random.choice(list(secondary_motive_powers.values()))
    ax = random.choice(list(axle_options.values()))

    county_name = random.choice(list(county_cities.keys()))
    county_code = counties[county_name]
    city_label, city_val, zip_code = county_cities[county_name]

    acq = random.choice(list(acquired_from_options.values()))

    # ── Build Base Payload ─────────────────────────────────────────────────────
    payload = {
        "typeLicense":          vt,
        "yearModel":            str(random.randint(1990, current_year)),
        "motivePower":          mp,
        "secondaryMotivePower": smp,
        "numberOfAxles":        ax,
        "operatedMonth":        f"{operated_date.month:02d}",
        "operatedDay":          f"{operated_date.day:02d}",
        "operatedYear":         str(operated_date.year),
        "purchaseMonth":        f"{purchase_date.month:02d}",
        "purchaseDay":          f"{purchase_date.day:02d}",
        "purchaseYear":         str(purchase_date.year),
        "acquiredFrom":         acq,
        "purchasePrice":        str(random.randint(1000, 100000)),
        "useTaxCredit":         str(random.randint(0, 5000)),
        "countyCode":           county_code,
        "countyNameLabel":      county_name,
        "cityNameLabel":        city_label,
        "cityName":             city_val,
        "zipCode":              zip_code,
    }

    # If electric, pick an electric subcategory; otherwise omit from payload
    if mp == "E":
        payload["electricType"] = random.choice(list(electric_types.values()))

    # ── WeightType + Matching Range ───────────────────────────────────────────
    wt = random.choice(list(weight_types.values()))
    payload["weightType"] = wt
    if wt == "U":
        if ax == "2":
            payload["unladenRangeTwoAxles"] = random.choice(list(unladen_ranges_two_axles.values()))
        else:
            payload["unladenRangeMoreThanTwoAxles"] = random.choice(list(unladen_ranges_two_axles.values()))
    else:
        payload["grossRange"] = random.choice(list(gross_ranges.values()))

    # ── If Trailer (vt == "40"), supply a valid trailerType; otherwise omit
    if vt == "40":
        trailer_types = ["PTI", "CCH", "CCHPT"]
        payload["trailerType"] = random.choice(trailer_types)

    # ── Remove any keys whose value is an empty string ────────────────────────
    filtered_payload = {k: v for k, v in payload.items() if v != ""}

    # ── Save and Log ────────────────────────────────────────────────────────────
    save_json(filtered_payload, os.path.join(run_dir, "payload.json"))
    logging.info(f"[Payload] Generated payload (keys: {list(filtered_payload.keys())}) saved to payload.json")

    return filtered_payload


# ─── 2.8 The Main Scrape Workflow (modified run_scrape) ───────────────────────────
# core.py (excerpt)
# (Keep your existing imports, e.g. solve_captcha, parse_dmv_response_and_save, etc.)

def run_scrape(idx: int, output_dir: str):
    run_dir = os.path.join(output_dir, str(idx))
    os.makedirs(run_dir, exist_ok=True)
    configure_logger(run_dir)  # writes to results/{idx}/scraper_debug.log

    # ─── 1) Prepare a single Session (preserve cookies) ──────────────────────
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.0.0 Safari/537.36"
        ),
        "Referer": os.getenv("PAGE_URL", "")
    })

    # ─── 2) GET the form page ────────────────────────────────────────────────
    try:
        form_resp = timed_get(session, os.getenv("PAGE_URL"), run_dir)
    except Exception as e:
        logging.exception(f"[run_scrape#{idx}] Failed to GET form page; aborting run. {e}")
        return

    form_html = form_resp.text
    with open(os.path.join(run_dir, "form.html"), "w", encoding="utf-8") as f:
        f.write(form_html)
    logging.info("[I/O] Wrote form.html")

    # ─── 3) Extract ALL hidden fields, save to hidden_fields.json ────────────
    soup = BeautifulSoup(form_html, "html.parser")
    hidden_fields = {}
    for inp in soup.select("form#FeeRequestForm input[type='hidden']"):
        name = inp.get("name")
        if name:
            hidden_fields[name] = inp.get("value", "")
    with open(os.path.join(run_dir, "hidden_fields.json"), "w", encoding="utf-8") as f:
        json.dump(hidden_fields, f, indent=2)
    logging.info(f"[HiddenFields] Extracted {len(hidden_fields)} keys and saved to hidden_fields.json")

    # ─── 4) Generate random payload and merge with hidden_fields ─────────────
    payload = generate_random_payload(idx, run_dir)
    form_data = {**hidden_fields, **payload}

    # ─── 5) Solve CAPTCHA (same session), with one retry if needed ───────────
    start_captcha = time.time()
    try:
        captcha_token = solve_captcha(session, form_html, run_dir)
    except Exception as e:
        elapsed = (time.time() - start_captcha) * 1000
        logging.warning(f"[Captcha] First solve attempt failed after {elapsed:.0f} ms: {e}")
        if elapsed > int(os.getenv("CAPTCHA_TIMEOUT", "60000")):
            logging.info("[Captcha] Retrying: re-fetching form page…")
            try:
                retry_resp = timed_get(session, os.getenv("PAGE_URL"), run_dir)
                new_html = retry_resp.text
                captcha_token = solve_captcha(session, new_html, run_dir)
            except Exception as e2:
                logging.exception(f"[Captcha] Retry also failed! Aborting run. {e2}")
                return
        else:
            logging.exception("[Captcha] Solve failed (not timeout). Aborting run.")
            return

    form_data["g-recaptcha-response"] = captcha_token

    # ─── 6) Save form_data.json ───────────────────────────────────────────────
    form_data_path = os.path.join(run_dir, "form_data.json")
    save_json(form_data, form_data_path)
    logging.info(f"[Payload] Saved combined form_data to {form_data_path}")

    # ─── 7) BEFORE POST: capture request info (headers, cookies, body) ──────
    request_info = {
        "url": os.getenv("SUBMIT_URL"),
        "method": "POST",
        "request_headers": dict(session.headers),
        "cookies": session.cookies.get_dict(),
        "body_form_data": form_data
    }
    with open(os.path.join(run_dir, "request_info.json"), "w", encoding="utf-8") as f:
        json.dump(request_info, f, indent=2)
    logging.info(f"[DEBUG] Wrote request_info.json (headers, cookies, form_data)")

    # ─── 8) Submit the form with the same session ────────────────────────────
    try:
        submit_resp = timed_post(session, os.getenv("SUBMIT_URL"), form_data, run_dir)
    except Exception as e:
        logging.exception(f"[run_scrape#{idx}] Failed to POST form; aborting run. {e}")
        return

    # ─── 9) Write response.html ──────────────────────────────────────────────
    resp_html = submit_resp.text
    resp_path = os.path.join(run_dir, "response.html")
    with open(resp_path, "w", encoding="utf-8") as f:
        f.write(resp_html)
    logging.info("[I/O] Wrote response.html")

    # ─── 10) Save response_info.json (status code + response headers) ───────
    response_info = {
        "status_code": submit_resp.status_code,
        "response_headers": dict(submit_resp.headers)
    }
    with open(os.path.join(run_dir, "response_info.json"), "w", encoding="utf-8") as f:
        json.dump(response_info, f, indent=2)
    logging.info(f"[DEBUG] Wrote response_info.json (status_code, response_headers)")

    # ─── 11) Pre-parse sanity checks ─────────────────────────────────────────
    html_lower = resp_html.lower()
    if "session not verified" in html_lower:
        logging.error(f"[run_scrape#{idx}] DMV returned “Session Not Verified”. Aborting parse.")
        return

    if '<div class="alert alert--error"' in resp_html and "<legend>calculate new resident fees</legend>" in html_lower:
        logging.error(f"[run_scrape#{idx}] DMV re-rendered form with validation errors. Aborting parse.")
        return

    # ─── 12) Parse and save CSVs ──────────────────────────────────────────────
    summary_csv = os.path.join(run_dir, "summary.csv")
    detail_csv = os.path.join(run_dir, "detailed.csv")
    try:
        parse_dmv_response_and_save(
            html=resp_html,
            summary_csv_path=summary_csv,
            detail_csv_path=detail_csv,
            run_dir=run_dir
        )
        logging.info(f"[run_scrape#{idx}] Parsing succeeded, CSVs written.")
    except Exception as e:
        logging.exception(f"[run_scrape#{idx}] Parsing failed. {e}")
        return

    logging.info(f"[run_scrape#{idx}] Completed successfully.")
