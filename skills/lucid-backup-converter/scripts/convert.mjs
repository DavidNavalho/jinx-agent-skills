#!/usr/bin/env node
// Converts extracted Lucid documents (.lcd) to .drawio, PDF and per-page PNG.
//
// How: draw.io's own open-source LucidImporter (in js/extensions.min.js) turns Lucid's JSON
// into draw.io XML, and draw.io's export page (export3.html, the same page its desktop app
// and export server use) renders it in headless Chrome via Playwright.
//
// Usage:
//   node convert.mjs --tools <tools-dir> --in <extracted-dir> --out <output-dir> [options]
//
// Options:
//   --concurrency N     documents converted in parallel (default 3)
//   --only TEXT         only documents whose path contains TEXT (case-insensitive)
//   --limit N           stop after N documents (useful for a test run)
//   --formats LIST      any of drawio,pdf,png (default all three)
//   --scale N           PNG pixel density (default 2)
//   --force             redo documents that already converted (default: skip them, so reruns resume)
//   --browser NAME      chrome (installed Google Chrome) or chromium (Playwright's); default: try chrome, then chromium
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

// ---------- arguments ----------
const args = { concurrency: 3, formats: 'drawio,pdf,png', scale: 2 };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const key = argv[i].replace(/^--/, '');
  if (key === 'force') args.force = true;
  else args[key] = argv[++i];
}
if (!args.tools || !args.in || !args.out) {
  console.error('usage: node convert.mjs --tools <tools-dir> --in <extracted-dir> --out <output-dir> ' +
    '[--concurrency N] [--only TEXT] [--limit N] [--formats drawio,pdf,png] [--scale N] [--force] [--browser chrome|chromium]');
  process.exit(2);
}
const TOOLS = path.resolve(args.tools);
const IN = path.resolve(args.in);
const OUT = path.resolve(args.out);
const FORMATS = new Set(args.formats.split(',').map((s) => s.trim()));
const WEBAPP = path.join(TOOLS, 'drawio/src/main/webapp');
if (!fs.existsSync(path.join(WEBAPP, 'export3.html')) || !fs.existsSync(path.join(WEBAPP, 'js/extensions.min.js'))) {
  console.error(`draw.io webapp not found in ${WEBAPP}. Run setup.sh first.`);
  process.exit(2);
}
// Playwright is installed in the tools dir by setup.sh, not next to this script.
const { chromium } = createRequire(path.join(TOOLS, 'package.json'))('playwright');

// ---------- find documents ----------
function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(path.join(dir, e.name)) : e.name.endsWith('.lcd') ? [path.join(dir, e.name)] : []);
}
let files = walk(IN).sort();
if (args.only) files = files.filter((f) => path.relative(IN, f).toLowerCase().includes(args.only.toLowerCase()));
if (args.limit) files = files.slice(0, parseInt(args.limit, 10));
if (files.length === 0) {
  console.error(`No .lcd files found in ${IN}${args.only ? ` matching "${args.only}"` : ''}`);
  process.exit(1);
}

// ---------- browser ----------
async function launch() {
  const tries = args.browser ? [args.browser] : ['chrome', 'chromium'];
  for (const name of tries) {
    try {
      return await chromium.launch(name === 'chrome' ? { channel: 'chrome' } : {});
    } catch (e) {
      if (name === tries.at(-1)) throw new Error(`Could not start ${name}: ${e.message.split('\n')[0]}. ` +
        `Install Google Chrome, or run "npx playwright install chromium" in ${TOOLS}.`);
    }
  }
}
const browser = await launch();
const DPR = parseFloat(args.scale) || 2;
const MAX_PNG_PX = 16000;
const MAX_PNG_AREA = 100e6;
const context = await browser.newContext({
  deviceScaleFactor: DPR,
  viewport: { width: 1600, height: 1200 },
});

// Serve the draw.io webapp from disk under a fake origin. Other requests (e.g. images that
// diagrams reference on Lucid's CDN) go to the network as normal.
const ORIGIN = 'http://drawio.local';
const LUCID_ORIGIN = 'https://lucid.app';
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.gif': 'image/gif', '.jpg': 'image/jpeg',
  '.json': 'application/json', '.xml': 'application/xml', '.txt': 'text/plain',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };
// export3.html expects a server that has the stencil libraries (AWS, Azure, Kubernetes, ...) as
// stencils/*.xml, but the repository ships them bundled in js/stencils.min.js, with the coded shapes
// in js/shapes-*.min.js. The editor loads those bundles; the export page doesn't, so shapes from
// those libraries fail to draw. Inject both bundles into the export page, the way the editor does
// (App.js): with allowEval off, since some bundled libraries carry scripts that would otherwise run
// and wipe the page ("Execution context was destroyed"); shapes-*.min.js provides those shapes instead.
const SHAPE_BUNDLES = '<script>mxStencilRegistry.allowEval = false;</script>' +
  fs.readdirSync(path.join(WEBAPP, 'js'))
    .filter((f) => /^shapes-.*\.min\.js$/.test(f) || f === 'stencils.min.js').sort()
    .map((f) => `<script src="js/${f}"></script>`).join('');
const EXPORT_PAGE = fs.readFileSync(path.join(WEBAPP, 'export3.html'), 'utf8')
  .replace('<script src="js/extensions.min.js"></script>', `${SHAPE_BUNDLES}<script src="js/extensions.min.js"></script>`);
if (!EXPORT_PAGE.includes('stencils.min.js')) {
  console.error('Could not add the stencil bundles to export3.html; this draw.io version may be laid out differently.');
  process.exit(2);
}

await context.route(`${ORIGIN}/**`, async (route) => {
  const url = new URL(route.request().url());
  const rel = decodeURIComponent(url.pathname);
  if (rel === '/export3.html') return route.fulfill({ status: 200, body: EXPORT_PAGE, contentType: 'text/html' });
  // Images uploaded to Lucid are referenced by relative path (/imageBlocks/image/<id>), which only
  // resolves on Lucid's site. They're served publicly from lucid.app, so fetch them from there.
  if (rel.startsWith('/imageBlocks/')) {
    try {
      return await route.fulfill({ response: await route.fetch({ url: `${LUCID_ORIGIN}${url.pathname}${url.search}` }) });
    } catch {
      return route.fulfill({ status: 502, body: '' });
    }
  }
  const file = path.join(WEBAPP, rel);
  if (!file.startsWith(WEBAPP + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    return route.fulfill({ status: 404, body: '' });
  }
  return route.fulfill({ status: 200, body: fs.readFileSync(file),
    contentType: MIME[path.extname(file).toLowerCase()] || 'application/octet-stream' });
});

// Collects problems seen while rendering: JavaScript errors and resources that failed to load
// (a missing draw.io file, or an image Lucid hosts that couldn't be fetched).
function watch(page, warnings) {
  page.on('pageerror', (e) => warnings.push(`error: ${e.message}`));
  page.on('response', (r) => { if (r.status() >= 400) warnings.push(`HTTP ${r.status()}: ${r.url().slice(0, 200)}`); });
  page.on('requestfailed', (r) => warnings.push(`failed to load: ${r.url().slice(0, 200)}`));
}

async function openExportPage(viewport, warnings) {
  const page = await context.newPage();
  try {
    if (warnings) watch(page, warnings);
    if (viewport) await page.setViewportSize(viewport);
    await page.goto(`${ORIGIN}/export3.html`);
    await page.waitForFunction(() => typeof window.render === 'function' && typeof window.LucidImporter !== 'undefined');
    return page;
  } catch (e) {
    await page.close().catch(() => {});
    throw e;
  }
}

// render() is started from a timer rather than inside page.evaluate: for some documents, calling it
// synchronously makes Playwright lose the evaluation ("Execution context was destroyed") even
// though the page renders fine. Completion is signalled by export.js adding #LoadingComplete.
async function renderOn(page, data) {
  await page.evaluate((d) => {
    window.__renderError = null;
    setTimeout(() => {
      try { window.render(d); } catch (e) { window.__renderError = String((e && e.stack) || e); }
    }, 0);
  }, data);
  await page.waitForFunction(() => document.getElementById('LoadingComplete') || window.__renderError,
    null, { timeout: 180000 });
  const error = await page.evaluate(() => window.__renderError);
  if (error) throw new Error(`draw.io render failed: ${error.split('\n')[0]}`);
  const bounds = await page.evaluate(() => document.getElementById('LoadingComplete').getAttribute('bounds'));
  return { bounds: JSON.parse(bounds) };
}

// ---------- fixes applied before import ----------
// Newer Lucid documents leave block styles "unset" and point at a theme ShapeStylePreset in
// state.Elements. LucidImporter doesn't resolve these and draws such shapes solid black,
// so copy the concrete values from the preset (or the document's default preset) first.
const PRESET_PROPS = { FillColor: 'BlockFillColor', LineColor: 'BlockLineColor',
  LineWidth: 'BlockLineWidth', StrokeStyle: 'BlockStrokeStyle' };

// A Lucid line can start or end on another line ("Endpoint.Line" + "LineP", the fraction along it).
// Lucid then stores a placeholder position of about (-1000000, -1000000), which LucidImporter uses
// as-is, so the connector shoots off the page and the page becomes ~1.5 million px wide (PDF
// printing fails). Replace the placeholder with the real point along the target line.
function resolveLineToLineEndpoints(state) {
  let changed = 0;
  for (const pg of [state, ...Object.values(state.Pages || {})]) {
    const lines = pg.Lines || {};
    const isPlaceholder = (ep) => ep && (Math.abs(ep.x) >= 100000 || Math.abs(ep.y) >= 100000);
    const pointOn = (id, frac, depth) => {
      const props = lines[id]?.Properties ?? lines[id];
      if (!props || depth > 5) return null;
      const a = endpoint(props.Endpoint1, depth + 1), b = endpoint(props.Endpoint2, depth + 1);
      if (!a || !b) return null;
      return { x: a.x + (b.x - a.x) * frac, y: a.y + (b.y - a.y) * frac };
    };
    const endpoint = (ep, depth) => {
      if (!ep) return null;
      if (!isPlaceholder(ep)) return { x: ep.x, y: ep.y };
      return ep.Line ? pointOn(ep.Line, ep.LineP ?? 0.5, depth) : null;
    };
    for (const line of Object.values(lines)) {
      const props = line.Properties ?? line;
      for (const key of ['Endpoint1', 'Endpoint2']) {
        const ep = props[key];
        if (!isPlaceholder(ep) || !ep.Line) continue;
        const p = pointOn(ep.Line, ep.LineP ?? 0.5, 0);
        if (p) { ep.x = p.x; ep.y = p.y; changed++; }
      }
    }
  }
  return changed;
}

function preprocess(doc) {
  const state = JSON.parse(doc.state);
  const changed = resolveStylePresets(state) + resolveLineToLineEndpoints(state);
  return changed ? { ...doc, state: JSON.stringify(state) } : doc;
}

function resolveStylePresets(state) {
  const elements = state.Elements || {};
  const defaultPreset = state.Properties?.StylePresetId;
  let changed = 0;
  for (const pg of Object.values(state.Pages || {})) {
    for (const block of Object.values(pg.Blocks || {})) {
      const props = block.Properties || {};
      const preset = elements[props.StylePresetId || defaultPreset];
      if (preset?.Type !== 'ShapeStylePreset') continue;
      for (const [prop, presetProp] of Object.entries(PRESET_PROPS)) {
        if (props[prop] === 'unset' && preset.Properties[presetProp] != null) {
          props[prop] = preset.Properties[presetProp];
          changed++;
        }
      }
    }
  }
  return changed;
}

const safe = (s) => (s || '').replace(/[\x00-\x1f/\\:*?"<>|]/g, '_').trim().slice(0, 80) || 'page';

// ---------- convert one document ----------
async function convert(file) {
  const rel = path.relative(IN, file);
  const base = path.basename(file, '.lcd');
  const dest = path.join(OUT, path.dirname(rel), base);
  // The marker holds this document's result (pages, warnings), so a resumed run can still report
  // what an earlier run found instead of losing it.
  const marker = path.join(dest, '.converted');
  if (!args.force && fs.existsSync(marker)) {
    let saved = {};
    try { saved = JSON.parse(fs.readFileSync(marker, 'utf8')); } catch { /* marker from an older version */ }
    return { ...saved, file: rel, status: 'skipped' };
  }

  fs.mkdirSync(dest, { recursive: true });
  const warnings = [];
  const pages = new Set();
  try {
    const result = await convertInto(file, rel, base, dest, warnings, async (viewport) => {
      const p = await openExportPage(viewport, warnings);
      pages.add(p);
      return p;
    });
    fs.writeFileSync(marker, JSON.stringify({ ...result, converted: new Date().toISOString() }, null, 2));
    return result;
  } finally {
    // A failed document must not leave loaded draw.io pages behind in the shared browser.
    await Promise.all([...pages].map((p) => p.close().catch(() => {})));
  }
}

async function convertInto(file, rel, base, dest, warnings, openPage) {
  const started = Date.now();
  const doc = preprocess(JSON.parse(fs.readFileSync(file, 'utf8')).Document);

  // 1. Lucid JSON -> draw.io XML
  let page = await openPage(null);
  const xml = await page.evaluate((d) => window.LucidImporter.importState(d), doc);
  if (!xml || !xml.includes('<diagram')) throw new Error('LucidImporter produced no diagram');
  const pageNames = await page.evaluate((x) =>
    Array.from(new DOMParser().parseFromString(x, 'text/xml').getElementsByTagName('diagram'))
      .map((d) => d.getAttribute('name') || ''), xml);
  if (FORMATS.has('drawio')) fs.writeFileSync(path.join(dest, `${base}.drawio`), xml);

  // 2. Whole document -> one PDF, one PDF page per diagram page, cropped to the content
  if (FORMATS.has('pdf')) {
    await renderOn(page, { xml, format: 'pdf', allPages: '1', crop: '1', border: 20 });
    await page.pdf({ path: path.join(dest, `${base}.pdf`), printBackground: true, preferCSSPageSize: true });
  }
  await page.close();

  // 3. Each diagram page -> PNG. Chrome tiles screenshots whose clip is bigger than the
  // viewport the page was rendered in, so measure first, then re-render at the content size.
  if (FORMATS.has('png')) {
    for (let i = 0; i < pageNames.length; i++) {
      const data = { xml, format: 'png', from: i, to: i, border: 20 };
      page = await openPage(null);
      let { bounds } = await renderOn(page, data);
      await page.close();
      // Chrome can't capture images wider or taller than ~16384 px, and very large captures time
      // out, so shrink pages that exceed either the edge or the total-pixel limit.
      const shrink = Math.min(1, MAX_PNG_PX / (Math.max(bounds.width, bounds.height) * DPR),
        Math.sqrt(MAX_PNG_AREA / (bounds.width * bounds.height * DPR * DPR)));
      if (shrink < 1) {
        data.scale = shrink;
        warnings.push(`note: page ${i + 1} scaled to ${Math.round(data.scale * 100)}% to fit the PNG size limit`);
        page = await openPage(null);
        ({ bounds } = await renderOn(page, data));
        await page.close();
      }
      const w = Math.max(Math.ceil(bounds.width), 1), h = Math.max(Math.ceil(bounds.height), 1);
      page = await openPage({ width: w, height: h });
      await renderOn(page, data);
      const name = `${String(i + 1).padStart(2, '0')} ${safe(pageNames[i])}.png`;
      await page.screenshot({ path: path.join(dest, name), clip: { x: 0, y: 0, width: w, height: h }, timeout: 180000 });
      await page.close();
    }
  }

  return { file: rel, status: 'ok', pages: pageNames.length, seconds: Math.round((Date.now() - started) / 1000),
    warnings: [...new Set(warnings)] };
}

// ---------- run with a small worker pool ----------
fs.mkdirSync(OUT, { recursive: true });
const results = [];
let next = 0, finished = 0;
async function worker() {
  while (next < files.length) {
    const file = files[next++];
    let r;
    try {
      r = await convert(file);
    } catch (e) {
      r = { file: path.relative(IN, file), status: 'failed', error: e.message.split('\n')[0] };
    }
    results.push(r);
    finished++;
    const detail = r.status === 'ok' ? `${r.pages} page(s), ${r.seconds}s${r.warnings.length ? `, ${r.warnings.length} warning(s)` : ''}`
      : r.status === 'failed' ? r.error : 'already converted';
    console.log(`[${finished}/${files.length}] ${r.status.padEnd(7)} ${r.file}  (${detail})`);
  }
}
await Promise.all(Array.from({ length: Math.max(1, parseInt(args.concurrency, 10) || 1) }, worker));
await browser.close();

const count = (s) => results.filter((r) => r.status === s).length;
const report = { finished: new Date().toISOString(), input: IN, output: OUT,
  ok: count('ok'), skipped: count('skipped'), failed: count('failed'),
  results: results.sort((a, b) => a.file.localeCompare(b.file)) };
fs.writeFileSync(path.join(OUT, 'conversion-report.json'), JSON.stringify(report, null, 2));
console.log(`\nDone: ${report.ok} converted, ${report.skipped} skipped, ${report.failed} failed. ` +
  `Report: ${path.join(OUT, 'conversion-report.json')}`);
process.exit(report.failed ? 1 : 0);
