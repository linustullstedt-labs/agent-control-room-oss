/** Capture the unchanged app UI with fictional data. No real agent APIs are used.
 * Run: node tools/capture-screenshots.mjs
 * Requires playwright (or PLAYWRIGHT_MODULE pointing to its entry point).
 */
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = resolve(import.meta.dirname, '..');
const now = Date.now();
const session = (id, repo, vendor, state, subtitle, line, extra = {}) => ({
  key: `${vendor}:${id}`, sessionId: `sample-${id}`, repo, name: repo, vendor, state, subtitle,
  client: vendor === 'codex' ? 'desktop' : 'vscode', cwd: `/Users/demo/projects/${repo}`,
  since: now - 120000, startedAt: now - 1800000, line, full: line,
  task: 'Implement and verify the requested improvements.', source: 'Fictional screenshot fixture',
  canReply: state === 'finished' || state === 'working', ...extra,
});
const sessions = [
  session('checkout', 'storefront', 'claude', 'needs-permission', 'Checkout validation', 'Wants to run: npm test -- checkout', {
    approvalId: 'sample-approval', full: 'npm test -- checkout\n\nRun the checkout tests to verify discount codes, shipping totals, and payment validation.', since: now - 180000,
  }),
  session('design', 'design-system', 'claude', 'needs-answer', 'Accessible components', 'Asks: Should the new focus style apply to every button variant?', {
    full: 'The focus indicator is ready. Should I apply it to **all button variants**, including icon-only buttons, or keep the change limited to primary actions?', since: now - 60000,
  }),
  session('api', 'customer-api', 'codex', 'working', 'Search endpoint', 'Adding pagination and request validation to customer search.'),
  session('tests', 'customer-api', 'claude', 'working', 'Integration tests', 'Checking search filters, empty results, and pagination boundaries.'),
  session('docs', 'developer-docs', 'claude', 'working', 'Quickstart guide', 'Updating setup examples and verifying the quickstart commands.', {client: 'terminal', since: now - 300000}),
  session('analytics', 'analytics-dashboard', 'codex', 'finished', 'Weekly reporting', 'Added weekly trends, date filters, and CSV export.', {
    full: '## Weekly reporting is ready\n\nAdded **weekly revenue trends**, a date-range filter, and CSV export to the dashboard.\n\n- Charts and exports use the same selected date range.\n- Empty results show a clear, helpful message.\n- Keyboard navigation works across the filters.\n\n**Verification:** all 24 tests passed, including date boundaries and CSV formatting.', since: now - 240000,
  }),
  session('mobile', 'mobile-companion', 'claude', 'finished', 'Navigation polish', 'Improved navigation labels and empty states.', {since: now - 480000}),
];
const snapshot = { sessions, errors: [], approvals: {on: true, hookInstalled: true}, notifications: true, platform: 'darwin', version: '0.1.0', pageVersion: 'sample', voice: 'system' };
const places = {places: ['storefront', 'customer-api', 'analytics-dashboard', 'design-system'].map((name, i) => ({name, path: `/Users/demo/projects/${name}`, where: '~/projects', open: i < 2, used: now / 1000 - i * 100})), parents: [{path: '/Users/demo/projects', label: '~/projects'}], agents: [{vendor: 'claude', targets: ['window', 'new-window', 'terminal']}, {vendor: 'codex', targets: ['new-window', 'terminal', 'app']}]};
const server = createServer(async (req, res) => {
  if (req.method !== 'GET') { res.writeHead(405); res.end('Screenshot fixture is read-only'); return; }
  const pathname = new URL(req.url, 'http://localhost').pathname;
  if (pathname === '/api/sessions' || pathname === '/api/places') {
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(pathname.endsWith('sessions') ? snapshot : places)); return;
  }
  const file = pathname === '/' ? 'index.html' : pathname === '/icon.svg' ? 'icon.svg' : null;
  if (!file) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', file.endsWith('svg') ? 'image/svg+xml' : 'text/html');
  res.end(await readFile(resolve(root, 'agent_control_room/web', file)));
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
let browser;
try {
  browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {channel: 'chrome'})});
  const page = await browser.newPage({viewport: {width: 1000, height: 850}, deviceScaleFactor: 2});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator('.item').first().waitFor();
  await page.evaluate(() => document.fonts.ready);
  await mkdir(resolve(root, 'docs/screenshots'), {recursive: true});
  const capture = async name => {
    await page.screenshot({path: resolve(root, 'docs/screenshots', `${name}.png`), fullPage: true});
    console.log(`Captured ${name}`);
  };
  const row = repo => page.locator(`.item[data-repo="${repo}"] .row`);
  await capture('01-session-overview');
  await row('storefront').click();
  await page.getByRole('button', {name: 'Allow', exact: true}).waitFor();
  await capture('02-permission-approval');
  await row('analytics-dashboard').click();
  await page.locator('#reply-input').fill('Looks good. Add a monthly comparison using the same date filter.');
  await capture('03-review-and-reply');
  await row('customer-api').click();
  await page.getByRole('tab', {name: /Integration tests/}).click();
  await capture('04-multiple-agents');
  await row('customer-api').click();
  await page.getByRole('button', {name: 'New agent', exact: true}).click();
  await page.locator('#l-prompt').fill('Add saved carts with clear empty states and checkout tests.');
  await capture('05-new-agent');
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('Verified: five screenshots captured; no browser JavaScript errors.');
} finally {
  await browser?.close();
  await new Promise(r => server.close(r));
}
