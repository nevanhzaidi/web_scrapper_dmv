import logging
from typing import Optional
import pandas as pd
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def parse_dmv_response_and_save(html: str,
                                summary_csv_path: str,
                                detail_csv_path: str) -> None:
    """
    Parse DMV fee calculator response HTML and save summary and detail CSVs.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Summary Fees
    summary = _extract_summary(soup)
    if summary:
        pd.DataFrame(summary).to_csv(summary_csv_path, index=False)
        logger.info("Saved summary fees to %s", summary_csv_path)
    else:
        logger.warning("No summary fees found.")

    # Detailed Fees
    detail = _extract_detail(soup)
    if detail:
        pd.DataFrame(detail).to_csv(detail_csv_path, index=False)
        logger.info("Saved detailed fees to %s", detail_csv_path)
    else:
        logger.warning("No detailed fees found.")


def _extract_summary(soup: BeautifulSoup) -> Optional[list]:
    legend = soup.find('legend', string=lambda s: s and s.strip() == 'Fees')
    if not legend:
        return None
    fieldset = legend.find_parent('fieldset')
    if not fieldset:
        return None
    items = []
    for dt, dd in zip(fieldset.find_all('dt'), fieldset.find_all('dd')):
        items.append({'Item': dt.get_text(strip=True), 'Fee': dd.get_text(strip=True)})
    return items


def _extract_detail(soup: BeautifulSoup) -> Optional[list]:
    table = soup.find('table', class_='table--secondary')
    if not table:
        return None
    rows = []
    for tr in table.select('tbody > tr'):
        cols = tr.find_all('td')
        if len(cols) >= 2:
            rows.append({'Description': cols[0].get_text(strip=True), 'Fee': cols[1].get_text(strip=True)})
    return rows