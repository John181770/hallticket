#!/usr/bin/env python3
"""
Automate UHSAP result extraction with Playwright + Tesseract OCR.

Flow per hall ticket number:
1) Open page
2) Fill hall ticket number from CSV
3) Read captcha from image-like element via OCR
4) Fill captcha text
5) Submit and accept alert dialog
6) Extract results table
7) Save table to a per-ticket Excel file
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Iterable, Tuple

import pandas as pd
import pytesseract
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter, ImageOps
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

DEFAULT_URL = (
    "http://results.uhsap.in/main_result?result=kKJFRFKdGMyT%2b6%2fe4faAQQ%3d%3d"
)

# Default XPaths (can be overridden by CLI args)
DEFAULT_HALL_TICKET_XPATH = "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[1]/td[2]/input"
DEFAULT_CAPTCHA_SOURCE_XPATH = "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[2]/td[2]/input[1]"
DEFAULT_CAPTCHA_TARGET_XPATH = "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[2]/td[2]/input[2]"
DEFAULT_SUBMIT_XPATH = "/html/body/form/div[5]/div[4]/input"
DEFAULT_RESULT_TABLE_XPATH = "/html/body/form/div[5]/div[5]/div/div[1]/div/div/table"

OCR_CONFIG = r"--oem 3 --psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape UHSAP results from hall ticket numbers in CSV."
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        required=True,
        help='Path to CSV file that contains column "hall_ticket_no".',
    )
    parser.add_argument(
        "--hall-ticket-column",
        default="hall_ticket_no",
        help='CSV column name (default: "hall_ticket_no").',
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Result page URL.")
    parser.add_argument(
        "--output-dir",
        default="output/results_excel",
        help="Folder where per-ticket Excel files are written.",
    )
    parser.add_argument(
        "--captcha-image-dir",
        default="output/captcha_images",
        help="Folder to save captcha images for debugging.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium in headless mode (default: headed).",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=30000,
        help="Playwright action timeout in milliseconds.",
    )
    parser.add_argument(
        "--max-captcha-retries",
        type=int,
        default=4,
        help="Retries per hall ticket when OCR is unclear or result table does not load.",
    )
    parser.add_argument(
        "--tesseract-path",
        default="",
        help=(
            "Optional full path to tesseract executable "
            '(example Windows: "C:\\Users\\<user>\\AppData\\Local\\Programs\\Tesseract-OCR\\tesseract.exe").'
        ),
    )
    parser.add_argument(
        "--hall-ticket-xpath",
        default=DEFAULT_HALL_TICKET_XPATH,
        help="XPath for hall ticket input field.",
    )
    parser.add_argument(
        "--captcha-source-xpath",
        default=DEFAULT_CAPTCHA_SOURCE_XPATH,
        help="XPath for captcha source element (image-like element).",
    )
    parser.add_argument(
        "--captcha-target-xpath",
        default=DEFAULT_CAPTCHA_TARGET_XPATH,
        help="XPath for captcha text input field.",
    )
    parser.add_argument(
        "--submit-xpath",
        default=DEFAULT_SUBMIT_XPATH,
        help="XPath for submit button.",
    )
    parser.add_argument(
        "--result-table-xpath",
        default=DEFAULT_RESULT_TABLE_XPATH,
        help="XPath for final result table.",
    )
    return parser.parse_args()


def load_hall_tickets(csv_path: Path, column_name: str) -> list[str]:
    df = pd.read_csv(csv_path, dtype=str)
    if column_name not in df.columns:
        raise ValueError(
            f'CSV column "{column_name}" not found. Available columns: {list(df.columns)}'
        )
    values = (
        df[column_name]
        .fillna("")
        .astype(str)
        .map(str.strip)
        .loc[lambda s: s != ""]
        .tolist()
    )
    if not values:
        raise ValueError(f'No non-empty values found in column "{column_name}".')
    return values


def preprocess_for_ocr(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    enlarged = gray.resize((gray.width * 3, gray.height * 3))
    denoised = enlarged.filter(ImageFilter.MedianFilter(size=3))
    bw = denoised.point(lambda x: 255 if x > 140 else 0, mode="1")
    return bw


def extract_ocr_text(image: Image.Image) -> str:
    text = pytesseract.image_to_string(image, config=OCR_CONFIG)
    return re.sub(r"[^A-Za-z0-9]", "", text).upper().strip()


def image_from_locator(page: Page, xpath: str, timeout_ms: int) -> Image.Image:
    locator = page.locator(f"xpath={xpath}").first
    locator.wait_for(state="visible", timeout=timeout_ms)
    element_type = (locator.get_attribute("type") or "").lower()
    raw_value = (locator.get_attribute("value") or "").strip()

    # If captcha text is exposed directly in input value, use it as an image substitute.
    # This handles pages where the first input in the captcha row is a generated text field.
    if element_type in {"text", "hidden"} and re.fullmatch(r"[A-Za-z0-9]{3,10}", raw_value):
        img = Image.new("RGB", (300, 60), "white")
        return img

    data = locator.screenshot(type="png")
    return Image.open(BytesIO(data))


def read_captcha_text(page: Page, xpath: str, timeout_ms: int) -> Tuple[str, Image.Image]:
    locator = page.locator(f"xpath={xpath}").first
    locator.wait_for(state="visible", timeout=timeout_ms)

    element_type = (locator.get_attribute("type") or "").lower()
    raw_value = (locator.get_attribute("value") or "").strip()
    if element_type in {"text", "hidden"} and re.fullmatch(r"[A-Za-z0-9]{3,10}", raw_value):
        # Fallback: sometimes captcha appears as plain text in input value.
        synthetic = Image.new("RGB", (260, 60), "white")
        text = raw_value.upper()
        return text, synthetic

    image = image_from_locator(page, xpath, timeout_ms)
    processed = preprocess_for_ocr(image)
    text = extract_ocr_text(processed)
    return text, processed


def parse_table_html(table_html: str) -> pd.DataFrame:
    soup = BeautifulSoup(table_html, "html.parser")
    rows: list[list[str]] = []
    for tr in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if not rows:
        return pd.DataFrame()

    width = max(len(r) for r in rows)
    normalized = [r + [""] * (width - len(r)) for r in rows]
    header = normalized[0]
    data_rows = normalized[1:] if len(normalized) > 1 else []

    # Keep unique non-empty column names.
    if not any(h.strip() for h in header):
        header = [f"column_{i + 1}" for i in range(width)]
    else:
        deduped: list[str] = []
        seen: dict[str, int] = {}
        for i, col in enumerate(header):
            base = (col or f"column_{i + 1}").strip()
            seen[base] = seen.get(base, 0) + 1
            deduped.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
        header = deduped

    return pd.DataFrame(data_rows, columns=header)


def ensure_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or f"ticket_{int(time.time())}"


def wait_for_result_table(page: Page, table_xpath: str, timeout_ms: int) -> str:
    table = page.locator(f"xpath={table_xpath}").first
    table.wait_for(state="visible", timeout=timeout_ms)
    return table.evaluate("node => node.outerHTML")


def run_for_ticket(
    page: Page,
    args: argparse.Namespace,
    hall_ticket_no: str,
    captcha_dir: Path,
    results_dir: Path,
) -> bool:
    for attempt in range(1, args.max_captcha_retries + 1):
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)

            hall_ticket_input = page.locator(f"xpath={args.hall_ticket_xpath}").first
            hall_ticket_input.wait_for(state="visible", timeout=args.timeout_ms)
            hall_ticket_input.fill(hall_ticket_no)

            captcha_text, captcha_image = read_captcha_text(
                page=page,
                xpath=args.captcha_source_xpath,
                timeout_ms=args.timeout_ms,
            )
            captcha_img_path = captcha_dir / (
                f"{sanitize_filename(hall_ticket_no)}_attempt_{attempt}.png"
            )
            captcha_image.save(captcha_img_path)

            if len(captcha_text) < 3:
                print(
                    f"[{hall_ticket_no}] Attempt {attempt}: OCR text too short ({captcha_text!r}), retrying..."
                )
                continue

            captcha_input = page.locator(f"xpath={args.captcha_target_xpath}").first
            captcha_input.wait_for(state="visible", timeout=args.timeout_ms)
            captcha_input.fill(captcha_text)

            page.once("dialog", lambda dialog: dialog.accept())
            page.locator(f"xpath={args.submit_xpath}").first.click(timeout=args.timeout_ms)

            table_html = wait_for_result_table(
                page=page, table_xpath=args.result_table_xpath, timeout_ms=args.timeout_ms
            )

            df = parse_table_html(table_html)
            if df.empty:
                print(f"[{hall_ticket_no}] Attempt {attempt}: Result table parsed empty, retrying...")
                continue

            df.insert(0, "hall_ticket_no", hall_ticket_no)
            df.insert(1, "captcha_ocr_text", captcha_text)
            out_path = results_dir / f"{sanitize_filename(hall_ticket_no)}.xlsx"
            df.to_excel(out_path, index=False)

            print(f"[{hall_ticket_no}] Success -> {out_path}")
            return True

        except PlaywrightTimeoutError:
            print(f"[{hall_ticket_no}] Attempt {attempt}: timeout, retrying...")
        except Exception as exc:  # noqa: BLE001
            print(f"[{hall_ticket_no}] Attempt {attempt}: {exc}")

    print(f"[{hall_ticket_no}] Failed after {args.max_captcha_retries} attempts.")
    return False


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    captcha_dir = Path(args.captcha_image_dir).expanduser().resolve()
    ensure_dirs([output_dir, captcha_dir])

    if args.tesseract_path:
        # Equivalent to the sample style:
        # pytesseract.tesseract_cmd = r"C:\Users\...\tesseract.exe"
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_path

    hall_tickets = load_hall_tickets(csv_path=csv_path, column_name=args.hall_ticket_column)
    print(f"Loaded {len(hall_tickets)} hall ticket numbers from {csv_path}")

    success = 0
    failed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        for hall_ticket_no in hall_tickets:
            ok = run_for_ticket(
                page=page,
                args=args,
                hall_ticket_no=hall_ticket_no,
                captcha_dir=captcha_dir,
                results_dir=output_dir,
            )
            if ok:
                success += 1
            else:
                failed += 1

        context.close()
        browser.close()

    print(f"Completed. Success: {success}, Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
