# parser.py

import os
import logging
import pandas as pd
from bs4 import BeautifulSoup


def parse_dmv_response_and_save(html: str, summary_csv_path: str, detail_csv_path: str, run_dir: str):
    """
    Parse the DMV response HTML for summary and detailed fee tables.
    If parsing fails or yields no results, dump the HTML to failed_parse.html.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        # ─── 3.1 Extract Summary Fees ─────────────────────────────────────────────
        summary_list = _extract_summary(soup)
        if summary_list is None or not summary_list:
            logging.warning("[Parser] No summary fees extracted.")
        else:
            df_summary = pd.DataFrame(summary_list)
            df_summary.to_csv(summary_csv_path, index=False)
            logging.info(f"[Parser] Wrote summary CSV to {summary_csv_path}")

        # ─── 3.2 Extract Detailed Fees ───────────────────────────────────────────
        detail_list = _extract_detail(soup)
        if detail_list is None or not detail_list:
            logging.warning("[Parser] No detailed fees extracted.")
        else:
            df_detail = pd.DataFrame(detail_list)
            df_detail.to_csv(detail_csv_path, index=False)
            logging.info(f"[Parser] Wrote detail CSV to {detail_csv_path}")

        # If both lists are empty, treat as a parse failure
        if (not summary_list) and (not detail_list):
            raise RuntimeError("No fees found in HTML (both summary and detail empty)")

    except Exception as e:
        logging.exception(f"[Parser] Exception while parsing response. {e}")
        # Dump the full HTML to a file for offline inspection
        failed_path = os.path.join(run_dir, "failed_parse.html")
        try:
            with open(failed_path, "w", encoding="utf-8") as f:
                f.write(html)
            logging.info(f"[Parser] Saved full response HTML to {failed_path}")
        except Exception as write_err:
            logging.error(f"[Parser] Failed to write failed_parse.html: {write_err}")
        # Re‐raise so run_scrape knows parsing completely failed
        raise


def _extract_summary(soup: BeautifulSoup):
    """
    Returns a list of dicts [{"Item": <dt>, "Fee": <dd>}, …] or None.
    """
    fieldset = None
    for legend in soup.find_all("legend"):
        if legend.get_text(strip=True) == "Fees":
            fieldset = legend.find_parent("fieldset")
            break
    if not fieldset:
        logging.warning("[Parser] <legend>Fees</legend> not found.")
        return None

    items = []
    dt_tags = fieldset.find_all("dt")
    dd_tags = fieldset.find_all("dd")
    if len(dt_tags) != len(dd_tags):
        logging.warning(f"[Parser] Mismatched <dt> ({len(dt_tags)}) vs <dd> ({len(dd_tags)}) counts.")
    for i, dt in enumerate(dt_tags):
        fee_text = dd_tags[i].get_text(strip=True) if i < len(dd_tags) else ""
        items.append({"Item": dt.get_text(strip=True), "Fee": fee_text})
    return items


def _extract_detail(soup: BeautifulSoup):
    """
    Returns a list of dicts [{"Description": <col1>, "Fee": <col2>}, …] or None.
    """
    table = soup.find("table", attrs={"class": "table--secondary"})
    if not table:
        logging.warning("[Parser] <table class='table--secondary'> not found.")
        return None

    items = []
    for row in table.select("tbody tr"):
        tds = row.find_all("td")
        if len(tds) >= 2:
            desc = tds[0].get_text(strip=True)
            fee = tds[1].get_text(strip=True)
            items.append({"Description": desc, "Fee": fee})
        else:
            logging.debug(f"[Parser] Skipped a <tr> with {len(tds)} <td> cells.")
    return items
