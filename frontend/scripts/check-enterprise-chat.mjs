import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

// Exercise the scripts shipped in the enterprise page without CDN dependencies.
const [html, publicPage, backendPage] = await Promise.all(
  ['../index.html', '../public/enterprise.html', '../../enterprise_dashboard.html']
    .map(path => readFile(new URL(path, import.meta.url), 'utf8'))
);
assert.equal(html, publicPage);
assert.equal(html, backendPage);
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', innerHTML: '', listeners: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    insertAdjacentHTML(_, markup) { this.innerHTML += markup; }
  });
  return elements.get(id);
}

let respond;
const requests = [];
let presets = [];
let profile = { status: 'ready', database_name: 'warehouse', dialect: 'sqlite', table_count: 1, column_count: 3, tables: [{ name: 'stock', column_count: 3 }] };
const saved = new Map();
const storage = {
  get length() { return saved.size; }, key: index => [...saved.keys()][index],
  getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value),
};
const context = vm.createContext({
  document: { getElementById: element, querySelector: element, querySelectorAll: () => [], addEventListener() {} },
  console, AbortController, localStorage: storage, clearTimeout, setTimeout: () => 0,
  fetch(path, options) {
    if (path.startsWith('/api/dashboard/')) return new Promise(() => {});
    if (path === '/api/query/presets') return Promise.resolve({ ok: true, json: async () => ({ questions: presets }) });
    if (path === '/api/database/understanding') return Promise.resolve({ ok: Boolean(profile), json: async () => profile });
    assert.equal(path, '/api/query/chat');
    requests.push(JSON.parse(options.body));
    return respond();
  },
});
context.window = context;
for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) vm.runInContext(script[1], context);
const messages = () => element('chatMessages').innerHTML;
const send = prompt => vm.runInContext(`submitUserMessage(${JSON.stringify(prompt)})`, context);
const replyWith = payload => { respond = async () => ({ ok: true, json: async () => payload }); };
const rows = Array.from({ length: 9 }, (_, index) => ({
  id: index, period: `2026-0${index + 1}`, count: index === 3 ? 0 : 1_000_000 - index,
}));
const chart = { chart_type: 'line', title: 'Oylar', x_axis: 'period', y_axis: 'count', data: rows };
replyWith({ success: true, explanation: 'Javob <tekshirildi>', chart, charts: [chart, chart],
  query_results: [{ columns: ['id', 'period', 'count'], rows: [...rows].reverse(), sql_query: 'SELECT count FROM totals' }] });
await send('Oylar bo‘yicha nechta?');
assert.equal(requests[0].history.length, 0);
assert.equal((messages().match(/data-chart-type=/g) || []).length, 1);
assert.match(messages(), /Javob &lt;tekshirildi&gt;/);
assert.match(messages(), /SELECT count FROM totals/);
const marks = [...messages().matchAll(/<circle[^>]*><title>([^<]+)<\/title>/g)].map(match => match[1]);
assert.deepEqual(marks, rows.map(row => `${row.period} · count = ${row.count}`));
assert.doesNotMatch(messages(), /Mln UZS/);
for (const type of ['bar', 'horizontal_bar', 'area', 'line', 'donut']) {
  context.chartFixture = { ...chart, chart_type: type };
  const rendered = vm.runInContext('renderChatChart(chartFixture)', context);
  assert.ok(rendered.includes(`data-chart-type="${type}"`), type);
  assert.ok(rendered.includes('role="img"'), type);

  context.chartFixture.data = [rows[0], { ...rows[1], count: null }, rows[2]];
  const withGap = vm.runInContext('renderChatChart(chartFixture)', context);
  if (type === 'donut') {
    assert.equal(withGap, '');
  } else {
    assert.match(withGap, /2026-02/);
    assert.doesNotMatch(withGap, /NaN|2026-02 · count =/);
    assert.equal((withGap.match(/<title>/g) || []).length, 2);
    if (type === 'line' || type === 'area') assert.equal((withGap.match(/stroke-width="2.5"/g) || []).length, 2);
  }
}
replyWith({ success: false, error: 'clarification_required', explanation: 'Qaysi hudud bo‘yicha?', chart: null });
await send('Faqat bitta hududda');
assert.deepEqual(requests[1].history, [
  { role: 'user', content: 'Oylar bo‘yicha nechta?' }, { role: 'assistant', content: 'Javob <tekshirildi>' },
]);
assert.match(messages(), /Qaysi hudud bo‘yicha\?/);
assert.match(messages(), /Aniqlashtirish kerak/);
assert.equal((messages().match(/data-chart-type=/g) || []).length, 1);
replyWith({ success: true, chart: null, explanation: 'Toshkentda jami 42.', rows: [{ total: 42 }], columns: ['total'] });
await send('Toshkent');
assert.deepEqual(requests.at(-1).history.slice(-2), [
  { role: 'user', content: 'Faqat bitta hududda' }, { role: 'assistant', content: 'Qaysi hudud bo‘yicha?', clarification: true }
]);
assert.match(messages(), /Toshkentda jami 42/);
assert.equal((messages().match(/data-chart-type=/g) || []).length, 1);
respond = async () => ({ ok: false, status: 503 });
await send('Yana tekshir');
assert.match(messages(), /HTTP 503/);
assert.equal((messages().match(/data-chart-type=/g) || []).length, 1);
replyWith({ success: true, chart: { ...chart, query_id: 'missing' }, explanation: 'Hisoblandi.', query_results: [
  { id: 'q1', columns: ['count'], rows: [{ count: 9 }], sql_query: 'SELECT COUNT(*) FROM sales' },
] });
await send('Yana taqqosla');
assert.ok(!requests.at(-1).history.some(message => message.content.includes('Yana tekshir')));
assert.match(messages(), /Diagrammaning hisoblash manbasi topilmadi/);
assert.match(messages(), /SELECT COUNT\(\*\) FROM sales/);
assert.equal((messages().match(/data-chart-type=/g) || []).length, 1);

profile = { ...profile, connection_fingerprint: 'other-connection' };
await vm.runInContext('loadSourceProfile()', context);
assert.equal(element('chatSourceLabel').textContent, 'warehouse');
await send('Endi oktabrda-chi?');
assert.deepEqual(requests.at(-1).history, []);

let finish;
respond = () => new Promise(resolve => { finish = resolve; });
const pending = send('Kutayotgan savol');
element('clearChatBtn').listeners.click();
finish({ ok: true, json: async () => ({ success: true, explanation: 'Eski javob' }) });
await pending;
assert.doesNotMatch(messages(), /Eski javob|Kutayotgan savol|data-chart-type/);
presets = ['Ombordagi qoldiq qancha?'];
await vm.runInContext('loadPresetPrompts()', context);
assert.ok(element('presetPrompts').innerHTML.includes(presets[0]));
assert.ok(element('aiInsightsList').innerHTML.includes(presets[0]));
element('reportTitleInput').value = presets[0];
element('reportCategoryInput').value = 'Savdo';
element('reportPeriodInput').value = 'Barcha davrlar';
replyWith({ success: false, explanation: 'Qaysi sana uchun?' });
await element('newReportForm').listeners.submit({ preventDefault() {} });
assert.equal(saved.size, 0);
assert.match(element('reportStatus').textContent, /Qaysi sana/);
replyWith({ success: true, explanation: 'Jami 42 dona.', chart: null, sql_query: 'SELECT SUM(stock) AS qty FROM inventory', rows: [{ qty: 42 }], columns: ['qty'] });
await element('newReportForm').listeners.submit({ preventDefault() {} });
assert.equal(requests.at(-1).prompt, presets[0]);
assert.equal(saved.size, 1);
const report = JSON.parse([...saved.values()][0]);
assert.equal(report.summary, 'Jami 42 dona.');
assert.deepEqual(report.results[0].rows, [{ qty: 42 }]);
assert.match(element('previewBody').innerHTML, /Jami 42 dona/);
assert.doesNotMatch(element('previewBody').innerHTML, /previewReportChart|96.8%/);
vm.runInContext('state.reports = []; restoreReports(); renderReports();', context);
assert.ok(element('reportsList').innerHTML.includes(presets[0]));
presets = [];
await vm.runInContext('loadPresetPrompts()', context);
assert.ok(!element('presetPrompts').innerHTML.includes(report.title));
await vm.runInContext('loadSourceProfile()', context);
assert.match(element('sourcesGrid').innerHTML, /warehouse/);
assert.match(element('sourcesGrid').innerHTML, /stock · 3 ustun/);
profile = null;
await vm.runInContext('loadSourceProfile()', context);
assert.doesNotMatch(element('sourcesGrid').innerHTML, /warehouse/);
assert.match(element('chatSourceLabel').textContent, /Ulanishni tekshiring/);
assert.match(element('sourcesGrid').innerHTML, /Ulanishni tekshiring/);
assert.equal(vm.runInContext("formatStockUnits({ metr: 5, dona: 3 })", context), '3 dona · 5 metr');
console.log('Enterprise checks passed: five chart types, source questions, evidence, context, clarification, scalar, errors, cancellation, report creation and persistence.');
