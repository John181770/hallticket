import fs from 'fs';
import { chromium } from 'playwright';
import { createWorker } from 'tesseract.js';
import { createCanvas, loadImage } from '@napi-rs/canvas';

// --- CONFIGURATION ---
const OUTPUT_COLUMNS = [
  'hall_ticket_no',
  'name',
  'course',
  'total_marks',
  'overall_result',
  'overall_class',
  'subject_name',
  'paper_type',
  'min_marks',
  'max_marks',
  'marks_obtained',
  'res'
];

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, '').replace('T', '_').slice(0, 15);
}

function parseCsv(content) {
  const lines = content.split('\n').map(l => l.trim()).filter(Boolean);
  if (lines.length === 0) return [];
  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
  return lines.slice(1).map(line => {
    const values = line.split(',').map(v => v.trim().replace(/^"|"$/g, ''));
    const obj = {};
    headers.forEach((h, i) => obj[h] = values[i]);
    return obj;
  });
}

function appendCsvRows(path, rows, columns) {
  const exists = fs.existsSync(path);
  const stream = fs.createWriteStream(path, { flags: 'a' });
  if (!exists) {
    stream.write(columns.join(',') + '\n');
  }
  rows.forEach(row => {
    const line = columns.map(col => {
      let val = row[col] === undefined || row[col] === null ? '' : String(row[col]);
      if (val.includes(',') || val.includes('"') || val.includes('\n')) {
        val = `"${val.replace(/"/g, '""')}"`;
      }
      return val;
    }).join(',');
    stream.write(line + '\n');
  });
  stream.end();
}

function existingTickets(path) {
  if (!fs.existsSync(path)) return new Set();
  const content = fs.readFileSync(path, 'utf8');
  const rows = parseCsv(content);
  return new Set(rows.map(r => r.hall_ticket_no).filter(Boolean));
}

function parseArgs(args) {
  const options = {
    input: 'hall_tickets.csv',
    url: 'http://results.uhsap.in/main_result?result=kKJFRFKdGMyT%2b6%2fe4faAQQ%3d%3d',
    output: '',
    errorOutput: '',
    limit: Infinity,
    delayMs: 2000,
    timeoutMs: 30000,
    maxAttempts: 3,
    headful: false,
    resume: true,
    browserChannel: 'msedge'
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--input') options.input = args[++i];
    if (args[i] === '--url') options.url = args[++i];
    if (args[i] === '--output') options.output = args[++i];
    if (args[i] === '--limit') options.limit = parseInt(args[++i], 10);
    if (args[i] === '--headful') options.headful = true;
    if (args[i] === '--no-resume') options.resume = false;
  }
  return options;
}

async function firstVisible(page, selectors, label) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    try {
      await locator.waitFor({ state: 'visible', timeout: 5000 });
      return locator;
    } catch {
      // Try next
    }
  }
  throw new Error(`Could not find visible ${label}`);
}

async function extractResultPayload(page, hallTicketNo) {
  return page.evaluate(({ ticket, columns }) => {
    const norm = (value) => (value || '').replace(/\s+/g, ' ').trim();
    const keyify = (value) => norm(value).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    const readRows = (table) => [...table.querySelectorAll('tr')].map((tr) => [...tr.children].map((cell) => norm(cell.innerText || cell.textContent)));
    const pageText = norm(document.body.innerText);

    const pickFromText = (patterns) => {
      for (const pattern of patterns) {
        const match = pageText.match(pattern);
        if (match?.[1]) return norm(match[1]);
      }
      return '';
    };

    const meta = {
      name: '',
      course: '',
      overall_result: '',
      overall_class: '',
      total_marks: '',
    };
    
    // 1. Precise selectors (best if fixed IDs)
    const nameEl = document.querySelector('#ContentPlaceHolder1_Label_name');
    const courseEl = document.querySelector('#ContentPlaceHolder1_Label_course');
    const resultEl = document.querySelector('#ContentPlaceHolder1_Label_result');
    const classEl = document.querySelector('#ContentPlaceHolder1_Label_class');
    const totalMarksEl = document.querySelector('#ContentPlaceHolder1_Label_total_marks') || document.querySelector('#ContentPlaceHolder1_Label_totalmarks');
    
    if (nameEl) meta.name = norm(nameEl.innerText);
    if (courseEl) meta.course = norm(courseEl.innerText);
    if (resultEl) meta.overall_result = norm(resultEl.innerText).toUpperCase().replace(/ED$/, '');
    if (classEl) meta.overall_class = norm(classEl.innerText);
    if (totalMarksEl) meta.total_marks = norm(totalMarksEl.innerText);

    // 2. Table-based scan (as seen in user image)
    const allTds = [...document.querySelectorAll('td')];
    for (let i = 0; i < allTds.length - 1; i++) {
      const k = keyify(allTds[i].innerText);
      const v = norm(allTds[i+1].innerText);
      if (!meta.name && (k === 'name' || k === 'student name')) meta.name = v;
      if (!meta.course && (k === 'course')) meta.course = v;
      if (!meta.total_marks && (k === 'total marks' || k === 'grand total')) meta.total_marks = v;
      if (!meta.overall_result && (k === 'result' || k === 'overall result')) meta.overall_result = v.toUpperCase().replace(/ED$/, '');
      if (!meta.overall_class && (k === 'class' || k === 'overall class')) meta.overall_class = v;
    }

    // 3. Fallback regex
    if (!meta.name) meta.name = pickFromText([/Name\s*[:\-]\s*([A-Za-z .']+?)(?:\s{2,}| Course\s*[:\-]| Hall| Reg| Result|$)/i]);
    if (!meta.course) meta.course = pickFromText([/Course\s*[:\-]\s*([^:]+?)(?:\s{2,}| Name\s*[:\-]| Hall| Reg| Result|$)/i]);
    if (!meta.overall_result) meta.overall_result = pickFromText([/(?:Overall\s*)?Result\s*[:\-]\s*(PASS|FAIL|PASSED|FAILED)/i]).replace(/ED$/i, '').toUpperCase();
    if (!meta.overall_class) meta.overall_class = pickFromText([/(?:Overall\s*)?Class\s*[:\-]\s*([^:]+?)(?:\s{2,}| Result\s*[:\-]|$)/i]);
    if (!meta.total_marks) meta.total_marks = pickFromText([/Total\s*Marks\s*[:\-]\s*(\d+)/i]);

    const tables = [...document.querySelectorAll('table')];
    const output = [];
    const seen = new Set();

    const mapHeader = (header) => {
      const key = keyify(header);
      if (/subject/.test(key) && !/total/.test(key)) return 'subject_name';
      if (/(paper|component|exam|type|assessment|practical|theory|mcq)/.test(key) && !/result/.test(key)) return 'paper_type';
      if (/min\s*\/\s*max/.test(key)) return 'min_max';
      if (/min/.test(key)) return 'min_marks';
      if (/max/.test(key) || /total marks/.test(key)) return 'max_marks';
      if (/(obtained|secured|marks scored|marks$)/.test(key) && !/total/.test(key)) return 'marks_obtained';
      if (/status|result/.test(key)) return 'res';
      return '';
    };

    for (const table of tables) {
      const rows = readRows(table).filter((row) => row.some(Boolean));
      if (rows.length < 2) continue;

      let headerIndex = rows.findIndex((row) => {
        const mapped = row.map(mapHeader).filter(Boolean);
        return mapped.includes('subject_name') && (mapped.includes('marks_obtained') || mapped.includes('res') || mapped.includes('min_max'));
      });
      if (headerIndex < 0) continue;

      const headers = rows[headerIndex].map(mapHeader);
      let currentSubject = '';
      for (const row of rows.slice(headerIndex + 1)) {
        if (row.every((cell) => !cell)) continue;

        const item = Object.fromEntries(columns.map((column) => [column, '']));
        item.hall_ticket_no = ticket;
        item.name = meta.name;
        item.course = meta.course;
        item.total_marks = meta.total_marks;
        item.overall_result = meta.overall_result;
        item.overall_class = meta.overall_class;

        row.forEach((cell, index) => {
          const mapped = headers[index];
          if (!mapped) return;
          if (mapped === 'min_max') {
            const split = cell.split(/\s*\/\s*/);
            item.min_marks = split[0] || '';
            item.max_marks = split[1] || '';
          } else {
            item[mapped] = cell;
          }
        });

        const rowText = row.join(' ');
        if (/grand\s+total|overall\s+total/i.test(rowText)) continue;
        if (/min\s*\/\s*max/i.test(rowText) || /marks\s+obtained/i.test(rowText)) continue;

        if (item.subject_name) currentSubject = item.subject_name;
        else item.subject_name = currentSubject;

        if (!item.paper_type && /theory|mcq|practical|internal|assessment|viva|paper/i.test(rowText)) {
          item.paper_type = row.find((cell) => /theory|mcq|practical|internal|assessment|viva|paper/i.test(cell)) || '';
        }

        // If it's a summary row, paper_type might be empty or "Total"
        if (!item.paper_type && item.marks_obtained && !item.res) {
           // check if result is in another column
           item.res = row.find(c => /PASS|FAIL/i.test(c)) || '';
        }

        // Final cleanup: if paper_type is too long, it's probably junk
        if (item.paper_type.length > 50) item.paper_type = '';
        if (item.marks_obtained.length > 20) item.marks_obtained = '';

        if (!item.paper_type && !item.marks_obtained && !item.res) continue;
        
        const key = [item.hall_ticket_no, item.subject_name, item.paper_type, item.min_marks, item.max_marks, item.marks_obtained, item.res].join('|');
        if (!seen.has(key)) {
          seen.add(key);
          output.push(item);
        }
      }
    }

    return { rows: output, pageText: pageText.slice(0, 1000) };
  }, { ticket: hallTicketNo, columns: OUTPUT_COLUMNS });
}

async function solveCaptcha(worker, imageBuffer) {
  const img = await loadImage(imageBuffer);
  const scale = 6;
  const canvas = createCanvas(img.width * scale, img.height * scale);
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 0, 0, img.width * scale, img.height * scale);
  
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;
  const threshold = 160; 
  for (let i = 0; i < data.length; i += 4) {
    const avg = (data[i] + data[i + 1] + data[i + 2]) / 3;
    const v = avg > threshold ? 0 : 255;
    data[i] = data[i + 1] = data[i + 2] = v;
  }
  ctx.putImageData(imageData, 0, 0);
  
  const processedBuffer = await canvas.toBuffer('image/png');
  const { data: { text } } = await worker.recognize(processedBuffer, {
    tessedit_char_whitelist: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
    tessedit_pageseg_mode: '7'
  });
  return text.trim().replace(/[^A-Z0-9]/g, '');
}

async function scrapeTicket(page, url, hallTicketNo, args, worker) {
  let captchaAttempts = 0;
  const maxCaptchaAttempts = 6;

  while (captchaAttempts < maxCaptchaAttempts) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: args.timeoutMs });
      
      const htInput = await firstVisible(page, ['#ContentPlaceHolder1_TextBox_htno', 'input[name$="TextBox_htno"]'], 'hall ticket input');
      await htInput.fill(String(hallTicketNo));

      const captchaImg = await firstVisible(page, ['#ContentPlaceHolder1_ImageButton1', 'input[type="image"]'], 'captcha image');
      const screenshot = await captchaImg.screenshot();
      
      const solvedCaptcha = await solveCaptcha(worker, screenshot);
      console.log(`  Captcha attempt ${captchaAttempts + 1}: ${solvedCaptcha}`);

      const captchaInput = await firstVisible(page, ['#ContentPlaceHolder1_TextBox_captcha', 'input[name$="TextBox_captcha"]'], 'captcha input');
      await captchaInput.fill(solvedCaptcha);

      let isInvalidCaptcha = false;
      const handleDialog = async dialog => {
        if (dialog.message().toLowerCase().includes('captcha')) {
           isInvalidCaptcha = true;
           await dialog.dismiss();
        } else {
           await dialog.accept();
        }
      };
      
      page.on('dialog', handleDialog);
      const submitBtn = await firstVisible(page, ['#ContentPlaceHolder1_Button_result', 'input[type="submit"]'], 'submit button');
      await submitBtn.click();

      // Sharp wait for dialog or text update
      await page.waitForTimeout(1200);
      page.off('dialog', handleDialog);

      if (isInvalidCaptcha) {
        console.warn('  Invalid captcha detected via alert, retrying...');
        captchaAttempts++;
        continue;
      }

      const pageText = await page.innerText('body');
      if (/invalid\s+captcha/i.test(pageText)) {
        console.warn('  Invalid captcha detected in text, retrying...');
        captchaAttempts++;
        continue;
      }

      if (/not\s+available|not\s+found|no\s+records|results\s+for\s+hall\s+ticket\s+no\s+is\s+not\s+available/i.test(pageText)) {
        return { rows: [], status: 'not_available', message: 'Result not available' };
      }

      try {
        await page.waitForFunction(() => document.querySelectorAll('table').length > 5 || /Name|Course|Result|Hall/i.test(document.body.innerText), { timeout: 4000 });
      } catch (e) {
        // Maybe it's a silent fail or incomplete load
        captchaAttempts++;
        continue;
      }
      
      const result = await extractResultPayload(page, hallTicketNo);
      if (result.rows.length > 0) {
        return { rows: result.rows, status: 'ok' };
      } else {
         console.warn('  No rows found, check if captcha is correct.');
         captchaAttempts++;
      }
    } catch (e) {
      console.warn(`  Attempt failed: ${e.message}`);
      captchaAttempts++;
    }
  }
  throw new Error(`Failed after ${maxCaptchaAttempts} captcha attempts`);
}

async function main() {
  const args = parseArgs(process.argv);
  const inputRows = parseCsv(fs.readFileSync(args.input, 'utf8'));
  const tickets = [...new Set(inputRows.map(r => r.hall_ticket_no).filter(Boolean))];

  const stamp = timestamp();
  const outPath = args.output || `exam_results_${stamp}.csv`;
  const errorPath = args.errorOutput || `exam_results_errors_${stamp}.csv`;

  const doneTickets = args.resume ? existingTickets(outPath) : new Set();
  
  const worker = await createWorker('eng');
  
  const browser = await chromium.launch({ 
    headless: !args.headful,
    channel: args.browserChannel 
  });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    for (let i = 0; i < tickets.length; i++) {
      const ticket = tickets[i];
      if (i >= args.limit) break;
      if (doneTickets.has(ticket)) {
        console.log(`[${i+1}/${tickets.length}] ${ticket} already done.`);
        continue;
      }
      console.log(`[${i+1}/${tickets.length}] Processing ${ticket}...`);
      
      let success = false;
      for (let attempt = 1; attempt <= args.maxAttempts; attempt++) {
        try {
          const result = await scrapeTicket(page, args.url, ticket, args, worker);
          if (result.status === 'ok') {
            appendCsvRows(outPath, result.rows, OUTPUT_COLUMNS);
            console.log(`  Success: ${result.rows.length} rows.`);
          } else {
            appendCsvRows(errorPath, [{ hall_ticket_no: ticket, status: result.status, message: result.message }], ['hall_ticket_no', 'status', 'message']);
            console.log(`  ${result.message}`);
          }
          success = true;
          break;
        } catch (e) {
          console.error(`  Error on attempt ${attempt}: ${e.message}`);
          if (attempt === args.maxAttempts) {
             appendCsvRows(errorPath, [{ hall_ticket_no: ticket, status: 'failed', message: e.message }], ['hall_ticket_no', 'status', 'message']);
          }
        }
      }
    }
  } finally {
    await worker.terminate();
    await browser.close();
  }
}

main().catch(console.error);
