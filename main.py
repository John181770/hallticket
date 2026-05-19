from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import re
import time

import pandas as pd
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageStat
from playwright.sync_api import Page, sync_playwright

# custom tesseract module
from extract_txt import read_captcha_text

URL = "http://results.uhsap.in/main_result?result=kKJFRFKdGMyT%2b6%2fe4faAQQ%3d%3d"
CAPTCHA_SOURCE_XPATH = "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[2]/td[2]/input[1]"
CAPTCHA_TARGET_XPATH = "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[2]/td[2]/input[2]"

DEFAULT_HALL_TICKET_XPATH = "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[1]/td[2]/input"
DEFAULT_SUBMIT_XPATH = "/html/body/form/div[5]/div[4]/input"
DEFAULT_RESULT_TABLE_XPATH = "/html/body/form/div[5]/div[5]/div/div[1]/div/div/table"

DEFAULT_CAPTCHA_DIR = Path("captcha_images")
DEFAULT_OUTPUT_DIR = Path("output/results_excel")
DEFAULT_LOG_FILE = Path("ocr_attempts_log.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load hall ticket numbers from a CSV and solve UHSAP captchas per ticket."
    )
    parser.add_argument("--csv", required=True, help="Path to the CSV file containing hall ticket numbers.")
    parser.add_argument(
        "--column",
        default="hall_ticket_no",
        help="CSV column name containing the hall ticket numbers (default: hall_ticket_no).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium in headless mode.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="Playwright timeout in milliseconds for page interactions.",
    )
    parser.add_argument(
        "--captcha-dir",
        default=str(DEFAULT_CAPTCHA_DIR),
        help="Directory to save captcha image captures.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to save results Excel files.",
    )
    return parser.parse_args()


def load_hall_tickets(csv_path: Path, column: str) -> list[str]:
    df = pd.read_csv(csv_path, dtype=str)
    if column not in df.columns:
        raise ValueError(f'CSV column "{column}" not found. Available columns: {list(df.columns)}')
    values = (
        df[column].astype(str)
        .fillna("")
        .map(str.strip)
        .loc[lambda s: s != ""]
        .tolist()
    )
    if not values:
        raise ValueError(f'No non-empty values found in CSV column "{column}".')
    return values


def is_mostly_white(img: Image.Image) -> bool:
    gray = ImageOps.grayscale(img)
    stat = ImageStat.Stat(gray)
    mean = float(stat.mean[0])
    std = float(stat.stddev[0])
    return mean >= 247.0 and std <= 3.5


def parse_table_html(table_html: str) -> dict[str, pd.DataFrame]:
    """
    Parse result table HTML with nested structure:
    - Extract student header info
    - Extract subject rows with nested component tables
    - Create flattened records combining student + subject + component data
    - Return dict of DataFrames for multi-sheet export.
    """
    soup = BeautifulSoup(table_html, "html.parser")
    
    # =========================
    # EXTRACT STUDENT INFO
    # =========================
    student_info: dict[str, str] = {}
    
    # Try to find student info table (usually with class "bg-success" or similar)
    main_table = soup.find("table", class_=lambda x: x and ("bg-success" in x if x else False))
    
    if main_table:
        rows = main_table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            # Parse key-value pairs (assuming alternating key/value columns)
            for i in range(0, len(cols), 2):
                key = cols[i].get_text(strip=True)
                if i + 1 < len(cols):
                    value = cols[i + 1].get_text(strip=True)
                    student_info[key] = value
    
    # =========================
    # EXTRACT NESTED DATA
    # =========================
    final_records: list[dict] = []
    
    # Find all subject tables (usually with IDs containing "GridView_SUBJECT" or similar)
    subject_tables = soup.find_all("table", id=lambda x: x and "SUBJECT" in x.upper() if x else False)
    
    if not subject_tables:
        # Fallback: find all tables and heuristically identify subject tables
        all_tables = soup.find_all("table")
        subject_tables = [t for t in all_tables if t != main_table]
    
    for subject_table in subject_tables:
        # Skip if it looks like a header table
        if subject_table == main_table:
            continue
        
        subject_rows = subject_table.find_all("tr")[1:]  # Skip header
        
        for row in subject_rows:
            cols = row.find_all("td", recursive=False)
            
            if len(cols) < 2:
                continue
            
            subject_name = cols[0].get_text(strip=True)
            
            # Look for nested component table in the second column
            component_table = cols[1].find("table") if len(cols) > 1 else None
            
            if component_table:
                component_rows = component_table.find_all("tr")[1:]  # Skip header
                
                for crow in component_rows:
                    ccols = crow.find_all("td")
                    
                    if len(ccols) < 4:
                        continue
                    
                    component_name = ccols[0].get_text(strip=True)
                    minmax = ccols[1].get_text(" ", strip=True)
                    marks = ccols[2].get_text(strip=True)
                    result = ccols[3].get_text(strip=True)
                    
                    # Split min/max marks
                    min_marks = "0"
                    max_marks = ""
                    
                    if "/" in minmax:
                        parts = minmax.split("/")
                        min_marks = parts[0].strip()
                        max_marks = parts[1].strip() if len(parts) > 1 else ""
                    elif minmax.strip():
                        max_marks = minmax.strip()
                    
                    # Create flattened record
                    final_records.append({
                        "USN": student_info.get("USN", ""),
                        "NAME": student_info.get("NAME", ""),
                        "COURSE": student_info.get("COURSE", ""),
                        "SEMESTER": student_info.get("SEMESTER", ""),
                        "TOTAL_MARKS": student_info.get("TOTAL MARKS", ""),
                        "FINAL_RESULT": student_info.get("RESULT", ""),
                        "SUBJECT": subject_name,
                        "COMPONENT": component_name,
                        "MIN_MARKS": min_marks,
                        "MAX_MARKS": max_marks,
                        "MARKS": marks,
                        "RESULT": result,
                    })
            else:
                # Subject without nested components
                final_records.append({
                    "USN": student_info.get("USN", ""),
                    "NAME": student_info.get("NAME", ""),
                    "COURSE": student_info.get("COURSE", ""),
                    "SEMESTER": student_info.get("SEMESTER", ""),
                    "TOTAL_MARKS": student_info.get("TOTAL MARKS", ""),
                    "FINAL_RESULT": student_info.get("RESULT", ""),
                    "SUBJECT": subject_name,
                    "COMPONENT": "",
                    "MIN_MARKS": "",
                    "MAX_MARKS": "",
                    "MARKS": "",
                    "RESULT": "",
                })
    
    # =========================
    # CREATE DATAFRAMES
    # =========================
    sheets: dict[str, pd.DataFrame] = {}
    
    # Flattened records sheet (main data)
    if final_records:
        df_flat = pd.DataFrame(final_records)
        
        # Convert numeric columns
        numeric_cols = ["MIN_MARKS", "MAX_MARKS", "MARKS", "TOTAL_MARKS"]
        for col in numeric_cols:
            if col in df_flat.columns:
                df_flat[col] = pd.to_numeric(df_flat[col], errors="coerce")
        
        sheets["results"] = df_flat
        
        # Student info sheet
        df_student = pd.DataFrame([student_info])
        sheets["student_info"] = df_student
    else:
        # Fallback: basic parsing if nested structure not found
        rows: list[list[str]] = []
        for tr in soup.select("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        
        if rows:
            width = max(len(r) for r in rows)
            normalized = [r + [""] * (width - len(r)) for r in rows]
            header = normalized[0]
            data_rows = normalized[1:] if len(normalized) > 1 else []
            
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
            
            sheets["results"] = pd.DataFrame(data_rows, columns=header)
    
    return sheets


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or f"ticket_{int(time.time())}"


def log_attempt(
    log_file: Path,
    hall_ticket_no: str,
    attempt: int,
    captcha_image_path: Path | None,
    ocr_value: str,
    status: str,
    excel_file_path: Path | None = None,
) -> None:
    """Append attempt record to CSV log file."""
    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hall_ticket_no": hall_ticket_no,
        "attempt": attempt,
        "captcha_image_location": str(captcha_image_path) if captcha_image_path else "",
        "ocr_value": ocr_value,
        "status": status,
        "excel_file_location": str(excel_file_path) if excel_file_path else "",
    }
    
    df_record = pd.DataFrame([record])
    
    # Append to CSV, creating it if it doesn't exist
    if log_file.exists():
        df_log = pd.read_csv(log_file)
        df_log = pd.concat([df_log, df_record], ignore_index=True)
    else:
        df_log = df_record
    
    df_log.to_csv(log_file, index=False)


def capture_captcha_image(page: Page, timeout_ms: int) -> tuple[Image.Image | None, str]:
    primary = page.locator(f"xpath={CAPTCHA_SOURCE_XPATH}").first
    primary.wait_for(state="visible", timeout=timeout_ms)

    candidates = [
        page.locator(f"xpath={CAPTCHA_SOURCE_XPATH}/ancestor::td[1]//img").first,
        page.locator(f"xpath={CAPTCHA_SOURCE_XPATH}/ancestor::tr[1]//img").first,
        page.locator(f"xpath={CAPTCHA_SOURCE_XPATH}/ancestor::td[1]//canvas").first,
        page.locator(f"xpath={CAPTCHA_SOURCE_XPATH}/ancestor::tr[1]//canvas").first,
        primary,
    ]

    deadline = time.monotonic() + 15
    last_img = None
    found = False

    while time.monotonic() < deadline and not found:
        for loc in candidates:
            if loc.count() == 0:
                continue
            try:
                loc.wait_for(state="visible", timeout=1000)
                data = loc.screenshot(type="png")
            except Exception:
                continue
            img = Image.open(BytesIO(data)).convert("RGB")
            last_img = img
            if not is_mostly_white(img):
                return img, "captcha-captured"
        page.wait_for_timeout(500)

    return last_img, "captcha-fallback"


def solve_ticket(
    browser,
    ticket: str,
    captcha_dir: Path,
    output_dir: Path,
    log_file: Path,
    headless: bool,
    timeout_ms: int,
    max_retries: int = 3,
) -> bool:
    page = browser.new_page()
    page.set_default_timeout(timeout_ms)
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        hall_ticket_input = page.locator(f"xpath={DEFAULT_HALL_TICKET_XPATH}").first
        hall_ticket_input.wait_for(state="visible", timeout=timeout_ms)
        hall_ticket_input.fill(ticket)

        for attempt in range(1, max_retries + 1):
            captcha_img, status = capture_captcha_image(page=page, timeout_ms=timeout_ms)
            if captcha_img is None:
                print(f"[{ticket}] Attempt {attempt}: Could not capture captcha image.")
                log_attempt(log_file, ticket, attempt, None, "", "failed-no-image")
                if attempt == max_retries:
                    return False
                continue

            captcha_file = captcha_dir / f"captcha_{ticket}_attempt_{attempt}.png"
            captcha_img.save(captcha_file)
            print(f"[{ticket}] Attempt {attempt}: Captcha saved: {captcha_file} ({status})")

            text = read_captcha_text(str(captcha_file))
            print(f"[{ticket}] Attempt {attempt}: Extracted captcha text: {text}")

            captcha_input = page.locator(f"xpath={CAPTCHA_TARGET_XPATH}").first
            captcha_input.wait_for(state="visible", timeout=timeout_ms)

            dialog_message = {"content": None}

            def handle_dialog(dialog):
                dialog_message["content"] = dialog.message
                print(f"[{ticket}] Attempt {attempt}: Dialog shown: {dialog.type} | Message: {dialog.message}")
                dialog.accept()

            page.on("dialog", handle_dialog)
            captcha_input.fill(text)
            page.locator(f"xpath={DEFAULT_SUBMIT_XPATH}").first.click()

            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            # Check if dialog had the "Please Enter Captcha Correctly" message
            if dialog_message["content"] and "Please Enter Captcha Correctly" in dialog_message["content"]:
                print(f"[{ticket}] Attempt {attempt}: Incorrect captcha detected. Retrying...")
                log_attempt(log_file, ticket, attempt, captcha_file, text, "failed-incorrect-captcha")
                captcha_input.clear()
                page.wait_for_timeout(1000)
                continue

            # Check if results loaded successfully
            try:
                page.wait_for_selector(f"xpath={DEFAULT_RESULT_TABLE_XPATH}", timeout=10000)
                print(f"[{ticket}] Attempt {attempt}: Captcha solved and results loaded successfully.")

                # Extract and save results table
                try:
                    table_locator = page.locator(f"xpath={DEFAULT_RESULT_TABLE_XPATH}").first
                    table_html = table_locator.evaluate("node => node.outerHTML")
                    sheets_dict = parse_table_html(table_html)

                    # Check if any data was extracted
                    has_data = any(not df.empty for df in sheets_dict.values())
                    
                    if has_data:
                        output_file = output_dir / f"{sanitize_filename(ticket)}.xlsx"
                        
                        # Capture table screenshot for embedding
                        table_image_data = None
                        try:
                            table_image_bytes = table_locator.screenshot(type="png")
                            table_image_data = BytesIO(table_image_bytes)
                        except Exception as e:
                            print(f"[{ticket}] Warning: Could not capture table screenshot: {e}")
                        
                        # Write all sheets to Excel with image in sheet 1
                        from openpyxl import Workbook
                        from openpyxl.drawing.image import Image as XlImage
                        
                        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
                            # Get the workbook and remove default sheet
                            workbook = writer.book
                            if "Sheet" in workbook.sheetnames:
                                del workbook["Sheet"]
                            
                            # Create image sheet (sheet 1)
                            if table_image_data:
                                img_sheet = workbook.create_sheet("table_image", 0)
                                table_image_data.seek(0)
                                xl_img = XlImage(table_image_data)
                                xl_img.width = 1000
                                xl_img.height = 600
                                img_sheet.add_image(xl_img, "A1")
                                print(f"[{ticket}] Table image embedded in sheet 1")
                            
                            # Write data sheets (only results)
                            if "results" in sheets_dict:
                                df = sheets_dict["results"]
                                if not df.empty:
                                    df.to_excel(writer, sheet_name="results", index=False)
                        
                        print(f"[{ticket}] Results saved to: {output_file}")
                        print(f"[{ticket}] Sheets: table_image (sheet 1), results (sheet 2)")
                        log_attempt(log_file, ticket, attempt, captcha_file, text, "success", output_file)
                    else:
                        print(f"[{ticket}] Warning: Result table was empty, but saving attempt record.")
                        log_attempt(log_file, ticket, attempt, captcha_file, text, "failed-empty-table")
                except Exception as e:
                    print(f"[{ticket}] Error extracting/saving table: {e}")
                    log_attempt(log_file, ticket, attempt, captcha_file, text, "failed-table-extraction")

                return True
            except Exception:
                print(f"[{ticket}] Attempt {attempt}: Failed to load results after submitting captcha.")
                log_attempt(log_file, ticket, attempt, captcha_file, text, "failed-no-results-table")
                if attempt < max_retries:
                    captcha_input.clear()
                    page.wait_for_timeout(1000)
                    continue
                return False

        print(f"[{ticket}] Failed after {max_retries} attempts.")
        return False
    finally:
        page.close()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv).expanduser().resolve()
    captcha_dir = Path(args.captcha_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    log_file = Path(DEFAULT_LOG_FILE).expanduser().resolve()
    
    captcha_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    tickets = load_hall_tickets(csv_path, args.column)
    print(f"Loaded {len(tickets)} hall ticket numbers from {csv_path}")
    print(f"Log file: {log_file}")

    success = 0
    failure = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for ticket in tickets:
            if solve_ticket(
                browser=browser,
                ticket=ticket,
                captcha_dir=captcha_dir,
                output_dir=output_dir,
                log_file=log_file,
                headless=True,
                timeout_ms=args.timeout,
            ):
                success += 1
            else:
                failure += 1
        browser.close()

    print(f"Completed. Success: {success}, Failed: {failure}")
    return 0 if failure == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
