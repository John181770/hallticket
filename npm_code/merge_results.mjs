import fs from 'fs';
import path from 'path';

const OUTPUT_FILE = 'merged_mbbs_results.csv';
const DIR = './';

function parseCsv(content) {
  const lines = content.split('\n').map(l => l.trim()).filter(Boolean);
  if (lines.length === 0) return { headers: [], rows: [] };
  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
  const rows = lines.slice(1).map(line => {
    // Basic CSV parser that handles quotes
    const values = [];
    let current = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const char = line[i];
      if (char === '"') inQuotes = !inQuotes;
      else if (char === ',' && !inQuotes) {
        values.push(current.trim().replace(/^"|"$/g, ''));
        current = '';
      } else {
        current += char;
      }
    }
    values.push(current.trim().replace(/^"|"$/g, ''));
    
    const obj = {};
    headers.forEach((h, i) => obj[h] = values[i]);
    return obj;
  });
  return { headers, rows };
}

function stringifyRow(row, columns) {
  return columns.map(col => {
    let val = row[col] === undefined || row[col] === null ? '' : String(row[col]);
    if (val.includes(',') || val.includes('"') || val.includes('\n')) {
      val = `"${val.replace(/"/g, '""')}"`;
    }
    return val;
  }).join(',');
}

async function main() {
  const files = fs.readdirSync(DIR).filter(f => f.startsWith('exam_results_') && f.endsWith('.csv') && !f.includes('error'));
  console.log(`Found ${files.length} result files to merge.`);

  const allRows = [];
  const seen = new Set();
  let finalHeaders = [];

  for (const file of files) {
    console.log(`Processing ${file}...`);
    const content = fs.readFileSync(path.join(DIR, file), 'utf8');
    const { headers, rows } = parseCsv(content);
    
    // Union of all headers
    headers.forEach(h => {
      if (!finalHeaders.includes(h)) finalHeaders.push(h);
    });

    for (const row of rows) {
      // Create a unique key for deduplication
      const key = [row.hall_ticket_no, row.subject_name, row.paper_type, row.marks_obtained].join('|');
      if (!seen.has(key)) {
        seen.add(key);
        allRows.push(row);
      }
    }
  }

  // Ensure 'res' is mapped if it was 'result_status' in older files
  allRows.forEach(row => {
    if (row.result_status && !row.res) row.res = row.result_status;
    if (row.subject_total_obtained && !row.marks_obtained) row.marks_obtained = row.subject_total_obtained;
  });

  // Simplified header list if user wants it "simple"
  const preferredHeaders = [
    'hall_ticket_no', 'name', 'course', 'total_marks', 'overall_result', 'overall_class',
    'subject_name', 'paper_type', 'min_marks', 'max_marks', 'marks_obtained', 'res'
  ];

  const stream = fs.createWriteStream(OUTPUT_FILE);
  stream.write(preferredHeaders.join(',') + '\n');
  allRows.forEach(row => {
    stream.write(stringifyRow(row, preferredHeaders) + '\n');
  });
  stream.end();

  console.log(`Merged ${allRows.length} rows into ${OUTPUT_FILE}`);
}

main().catch(console.error);
