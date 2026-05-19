import fs from 'fs';
import https from 'https';
import { chromium } from 'playwright';
import { createWorker } from 'tesseract.js';
import { createCanvas, loadImage } from '@napi-rs/canvas';

// --- PROFESSIONAL CONFIGURATION ---
const ANTI_CAPTCHA_API_KEY = ""; // PASTE YOUR KEY HERE (from anti-captcha.com)

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
  if (!exists) stream.write(columns.join(',') + '\n');
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

async function firstVisible(page, selectors, label) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    try {
      await locator.waitFor({ state: 'visible', timeout: 5000 });
      return locator;
    } catch {}
  }
  return null;
}

// --- CAPTCHA SOLVERS ---

async function solveWithAntiCaptcha(imageBuffer) {
  if (!ANTI_CAPTCHA_API_KEY) return null;
  const base64 = imageBuffer.toString('base64');
  
  const createTask = () => new Promise((resolve, reject) => {
    const data = JSON.stringify({
      clientKey: ANTI_CAPTCHA_API_KEY,
      task: { type: "ImageToTextTask", body: base64 }
    });
    const req = https.request({
      hostname: 'api.anti-captcha.com',
      path: '/createTask',
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': data.length }
    }, res => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => resolve(JSON.parse(body)));
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });

  const getResult = (taskId) => new Promise((resolve, reject) => {
    const data = JSON.stringify({ clientKey: ANTI_CAPTCHA_API_KEY, taskId });
    const req = https.request({
      hostname: 'api.anti-captcha.com',
      path: '/getTaskResult',
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': data.length }
    }, res => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => resolve(JSON.parse(body)));
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });

  try {
    const task = await createTask();
    if (task.errorId > 0) throw new Error(task.errorDescription);
    const taskId = task.taskId;
    
    for (let i = 0; i < 30; i++) { // Poll for 60s
      await new Promise(r => setTimeout(r, 2000));
      const res = await getResult(taskId);
      if (res.status === 'ready') return res.solution.text;
    }
  } catch (e) {
    console.warn(`  Anti-Captcha Error: ${e.message}`);
  }
  return null;
}

async function solveWithInternalOCR(worker, imageBuffer) {
  const img = await loadImage(imageBuffer);
  const scale = 6;
  const canvas = createCanvas(img.width * scale, img.height * scale);
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 0, 0, img.width * scale, img.height * scale);
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;
  for (let i = 0; i < data.length; i += 4) {
    const avg = (data[i] + data[i + 1] + data[i + 2]) / 3;
    const v = avg > 160 ? 0 : 255;
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

async function extractResultPayload(page, ticket, columns) {
  return page.evaluate(({ ticket, columns }) => {
    const norm = (v) => (v || '').replace(/\s+/g, ' ').trim();
    const keyify = (v) => norm(v).toLowerCase().replace(/[^a-z0-9]+/g, ' ');
    const readRows = (t) => [...t.querySelectorAll('tr')].map(tr => [...tr.children].map(c => norm(c.innerText)));
    
    const meta = { name: '', course: '', overall_result: '', overall_class: '', total_marks: '' };
    const tds = [...document.querySelectorAll('td')];
    for (let i = 0; i < tds.length - 1; i++) {
       const k = keyify(tds[i].innerText);
       const v = norm(tds[i+1].innerText);
       if (!meta.name && k === 'name') meta.name = v;
       if (!meta.course && k === 'course') meta.course = v;
       if (!meta.total_marks && (k === 'total marks' || k === 'grand total')) meta.total_marks = v;
       if (!meta.overall_result && k === 'result') meta.overall_result = v.toUpperCase().replace(/ED$/, '');
       if (!meta.overall_class && k === 'class') meta.overall_class = v;
    }

    const output = [];
    const seen = new Set();
    const mapHeader = (h) => {
      const k = keyify(h);
      if (/subject/.test(k) && !/total/.test(k)) return 'subject_name';
      if (/paper|component|exam|internal|practical|theory/i.test(k)) return 'paper_type';
      if (/min\s*\/\s*max/.test(k)) return 'min_max';
      if (/marks\s*obtained|secured/.test(k)) return 'marks_obtained';
      if (/result|status/.test(k)) return 'res';
      return '';
    };

    const tables = [...document.querySelectorAll('table')];
    for (const table of tables) {
      const rows = readRows(table).filter(r => r.some(Boolean));
      let headIdx = rows.findIndex(r => r.map(mapHeader).filter(Boolean).length >= 2);
      if (headIdx < 0) continue;
      const headers = rows[headIdx].map(mapHeader);
      let curSub = '';
      for (const row of rows.slice(headIdx + 1)) {
        const rowText = row.join(' ');
        if (/total|grand/i.test(rowText) || /min\s*\/\s*max/i.test(rowText)) continue;
        const item = Object.fromEntries(columns.map(c => [c, '']));
        item.hall_ticket_no = ticket;
        Object.assign(item, meta);
        row.forEach((cell, idx) => {
          const m = headers[idx];
          if (m === 'min_max') {
             const s = cell.split(/\s*\/\s*/);
             item.min_marks = s[0] || ''; item.max_marks = s[1] || '';
          } else if (m) item[m] = cell;
        });
        if (item.subject_name) curSub = item.subject_name; else item.subject_name = curSub;
        if (!item.paper_type && /theory|mcq|practical|assessment|internal/i.test(rowText)) {
           item.paper_type = row.find(c => /theory|mcq|practical|assessment|internal/i.test(c)) || '';
        }
        if (!item.paper_type && !item.marks_obtained && !item.res) {
          item.res = row.find(c => /PASS|FAIL/i.test(c)) || '';
        }
        if (item.paper_type.length > 50) item.paper_type = '';
        if (!item.paper_type && !item.marks_obtained && !item.res) continue;
        const pk = [item.hall_ticket_no, item.subject_name, item.paper_type, item.marks_obtained].join('|');
        if (!seen.has(pk)) { seen.add(pk); output.push(item); }
      }
    }
    return { rows: output };
  }, { ticket, columns });
}

async function scrapeTicket(page, url, ticket, worker) {
  let attempts = 0;
  while (attempts < 6) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.fill('#ContentPlaceHolder1_TextBox_htno', String(ticket));
      
      const captchaImg = await page.locator('#ContentPlaceHolder1_ImageButton1').screenshot();
      let solved = await solveWithAntiCaptcha(captchaImg);
      if (!solved) solved = await solveWithInternalOCR(worker, captchaImg);
      
      console.log(`  Captcha attempt ${attempts+1}: ${solved}`);
      await page.fill('#ContentPlaceHolder1_TextBox_captcha', solved || '');

      let winAlert = false;
      const onDialog = d => { if (d.message().toLowerCase().includes('captcha')) winAlert = true; d.dismiss(); };
      page.on('dialog', onDialog);
      await page.click('#ContentPlaceHolder1_Button_result');
      await page.waitForTimeout(1500);
      page.off('dialog', onDialog);

      if (winAlert) { attempts++; continue; }
      
      try {
        await page.waitForFunction(() => document.querySelectorAll('table').length > 5 || /Name|Result|not\s+found/i.test(document.body.innerText), { timeout: 5000 });
      } catch { attempts++; continue; }

      if (/not\s+found|no\s+records/i.test(await page.innerText('body'))) return { status: 'not_found' };

      const res = await extractResultPayload(page, ticket, OUTPUT_COLUMNS);
      if (res.rows.length > 0) return { status: 'ok', rows: res.rows };
      attempts++;
    } catch (e) { console.warn(`  Fail: ${e.message}`); attempts++; }
  }
  throw new Error('All attempts failed');
}

async function main() {
  const input = parseCsv(fs.readFileSync('hall_tickets.csv', 'utf8'));
  const tickets = [...new Set(input.map(r => r.hall_ticket_no).filter(Boolean))];
  const outPath = 'exam_results_pro.csv';
  const done = existingTickets(outPath);
  const worker = await createWorker('eng');
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage();

  try {
    for (let i = 0; i < tickets.length; i++) {
      if (done.has(tickets[i])) continue;
      console.log(`[${i+1}/${tickets.length}] ${tickets[i]}...`);
      try {
        const result = await scrapeTicket(page, 'http://results.uhsap.in/main_result?result=kKJFRFKdGMyT%2b6%2fe4faAQQ%3d%3d', tickets[i], worker);
        if (result.status === 'ok') {
          appendCsvRows(outPath, result.rows, OUTPUT_COLUMNS);
          console.log(`  Success: ${result.rows.length} rows.`);
        } else console.log(`  Not found.`);
      } catch (e) { console.error(`  Final Error: ${e.message}`); }
    }
  } finally { await worker.terminate(); await browser.close(); }
}

main().catch(console.error);
