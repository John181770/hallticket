# UHSAP Result Automation (Playwright + Tesseract OCR)

## Files
- `scrape_uhsap_results.py`: main automation script
- `requirements.txt`: Python dependencies
- `sample_hall_tickets.csv`: sample input CSV format

## Input CSV format
CSV must contain a column named `hall_ticket_no`.

Example:
```csv
hall_ticket_no
HT12345678
HT87654321
```

## Install
```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

## Run (macOS/Linux)
```bash
python3 scrape_uhsap_results.py --csv sample_hall_tickets.csv
```

## Run (Windows with explicit Tesseract path)
```bash
python scrape_uhsap_results.py ^
  --csv sample_hall_tickets.csv ^
  --tesseract-path "C:\Users\name\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
```
## Run in Mac
```
python3 scrape_uhsap_results.py \
  --csv sample_hall_tickets.csv \
  --tesseract-path "$(which tesseract)"
```
## Output
- Excel files: `output/results_excel/<hall_ticket_no>.xlsx`
- Captcha images used for OCR/debug: `output/captcha_images/*.png`

## XPath overrides
If the page structure changes, override any XPath:
```bash
python3 scrape_uhsap_results.py \
  --csv sample_hall_tickets.csv \
  --hall-ticket-xpath "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[1]/td[2]/input" \
  --captcha-source-xpath "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[2]/td[2]/input[1]" \
  --captcha-target-xpath "/html/body/form/div[5]/div[3]/div[2]/table/tbody/tr[2]/td[2]/input[2]" \
  --submit-xpath "/html/body/form/div[5]/div[4]/input" \
  --result-table-xpath "/html/body/form/div[5]/div[5]/div/div[1]/div/div/table"
```
