# NTRUHS MBBS Result Extractor

This workspace contains OCR tooling for the uploaded DR. N.T.R University of Health Sciences PDF plus a Playwright-based scraper for the results portal.

## Input

Create or place a CSV with this column:

```csv
hall_ticket_no
1234567890
1234567891
```

## Setup

```powershell
npm install
npx playwright install chromium
```

If you do not want to download Playwright Chromium, use `--browser-channel msedge` when running the extractor.

## Extract Hall Tickets From PDF

Render the scanned PDF into OCR-ready images:

```powershell
npm run render-pdf -- "C:\Users\pope1\Downloads\Third Professional Part - I - MBBS - NS - R21 - JANUARY - 2026.pdf" rendered_pages_wasm 2.5
```

Run OCR and create `hall_tickets.csv`:

```powershell
npm run ocr-tickets -- rendered_pages_wasm hall_tickets.csv ocr_text
```

If OCR text already exists and you only need to rebuild the CSV:

```powershell
npm run rebuild-tickets -- ocr_text hall_tickets.csv
```

## Run

Use the exact results portal URL for the specific exam result page:

```powershell
npm run extract -- --input .\hall_tickets.csv --url "http://results.uhsap.in/main_result?result=kKJFRFKdGMyT%2b6%2fe4faAQQ%3d%3d" --headful --browser-channel msedge
```

Or run the preconfigured command for this workspace:

```powershell
npm run run-results
```

The script writes:

- `exam_results_[timestamp].csv`
- `exam_results_errors_[timestamp].csv`

The preconfigured command writes incrementally to:

- `exam_results_live.csv`
- `exam_results_errors_live.csv`

## Output Columns

The output CSV uses one row per paper/component:

```text
hall_ticket_no,name,course,overall_result,overall_class,subject_name,paper_type,min_marks,max_marks,marks_obtained,result_status,subject_total_marks,subject_total_obtained
```

## Notes

- The script retries each hall ticket up to three total attempts, which is the first try plus two retries.
- The script pauses for a human-entered CAPTCHA for each hall ticket.
- If the portal layout changes, run with `--headful` to watch the browser and adjust selectors if needed:

```powershell
npm run extract -- --input .\hall_tickets.csv --url "http://results.uhsap.in/main_result?result=kKJFRFKdGMyT%2b6%2fe4faAQQ%3d%3d" --headful --browser-channel msedge
```
