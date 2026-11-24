import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { activateConnectedSource, commandFilters, connectionKey } from '../src/services/chatContext.js';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' });
let checks = 0;
function check(name, fn) { fn(); checks++; console.log(`PASS ${name}`); }
try {
  const [{ default: Artifacts }, { default: Understanding }, { default: Workspace }] = await Promise.all([
    server.ssrLoadModule('/src/components/CommandArtifacts.jsx'),
    server.ssrLoadModule('/src/components/DatabaseUnderstanding.jsx'),
    server.ssrLoadModule('/src/components/AssistantWorkspace.jsx'),
  ]);
  const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
  const noop = () => {};
  const connection = { database_name: 'operations', dialect: 'postgresql', connection_fingerprint: 'server-a' };
  const initial = { active: 'new', sessions: [{ id: 'new', source: 'demo', messages: [] }] };
  check('Reload selects the connected database only for an unused default chat', () => {
    const state = activateConnectedSource(initial, connection);
    assert.equal(state.sessions[0].source, 'external');
    assert.equal(state.sessions[0].connection, 'server-a');
    assert.equal(initial.sessions[0].source, 'demo');
    for (const session of [
      { ...initial.sessions[0], sourceExplicit: true },
      { ...initial.sessions[0], messages: [{ role: 'user', content: 'Demo savol' }] },
      { ...initial.sessions[0], source: 'external', connection: 'server-b' },
    ]) {
      const saved = { active: 'new', sessions: [session] };
      assert.equal(activateConnectedSource(saved, connection), saved);
    }
    assert.notEqual(connectionKey(connection), connectionKey({ ...connection, connection_fingerprint: 'server-b' }));
  });
  check('External requests omit demo defaults and retain explicit date follow-ups', () => {
    const defaults = { start_date: '2026-01-01', end_date: '2026-11-12', branch_id: 'demo-branch', category: 'car' };
    assert.deepEqual(commandFilters([], defaults, 'external'), {});
    const previous = [{ role: 'assistant', filters: { start_date: '2025-01-01', end_date: '2025-12-31', branch_id: 'demo-branch', category: 'car' } }];
    assert.deepEqual(commandFilters(previous, defaults, 'external'), { start_date: '2025-01-01', end_date: '2025-12-31' });
    assert.equal(commandFilters(previous, defaults, 'demo').start_date, '2025-01-01');
  });
  const profile = { status: 'ready', profile_complete: true, database_name: 'operations', dialect: 'postgresql', table_count: 2, column_count: 43, relationship_count: 1, read_only: true, tables: [{ name: 'shipments', column_count: 40, columns: [{ name: 'shipment_id', data_type: 'integer' }], columns_truncated: true, row_count_estimate: 512 }], suggested_questions: ['shipments jadvalida nechta yozuv bor?'], warnings: [] };
  const understandingProps = { profile, sourceName: 'operations', onRefresh: noop, onReconnect: noop, onQuestion: noop };
  check('Schema card shows actual counts, table details, truncation and source questions', () => {
    const html = render(Understanding, understandingProps);
    for (const value of ['Baza o‘rganildi', '<b>43</b>', '40 ustun', 'shipment_id', 'Dastlabki 1 ustun', 'shipments jadvalida nechta yozuv bor?', 'Faqat o‘qish']) assert.ok(html.includes(value), value);
  });
  check('Loading, partial and error states never claim ready', () => {
    const pending = render(Understanding, { ...understandingProps, loading: true });
    assert.ok(pending.includes('Baza o‘rganilmoqda'));
    assert.ok(!pending.includes('Baza o‘rganildi'));
    const partial = render(Understanding, { ...understandingProps, profile: { ...profile, status: 'partial', profile_complete: false, warnings: ['Profiling chegaralangan'] }, compact: true });
    assert.ok(partial.includes('Baza qisman o‘rganildi'));
    assert.ok(partial.includes('hisobot yaratish ishlamaydi'));
    assert.ok(partial.includes('Profiling chegaralangan'));
    assert.ok(render(Understanding, { ...understandingProps, profile: null, error: 'Ulanish o‘zgardi' }).includes('Ulanish o‘zgardi'));
  });
  const rows = [{ region: 'North', delivery_count: 12, late_count: null }];
  const analysis = { columns: ['region', 'delivery_count', 'late_count'], rows, charts: [], summary_text: '12 ta yetkazib berish.', sql_query: 'SELECT region, delivery_count FROM deliveries', success: true };
  const artifact = data => render(Artifacts, { artifacts: [data] });
  check('External report shows actual query rows, nulls and real export links without demo sales', () => {
    const html = artifact({ type: 'report', data: { kind: 'external', id: 'report-check', title: 'Yetkazib berish hisoboti', analysis, source: { name: 'operations' }, exports: { csv: '/api/platform/reports/report-check/export?format=csv', html: '/api/platform/reports/report-check/export?format=html' } } });
    for (const value of ['Yetkazib berish hisoboti', 'delivery_count', 'North', '>12<', '>—<', 'format=csv', 'format=html', 'SELECT region']) assert.ok(html.includes(value), value);
    for (const value of ['Yalpi foyda', 'so‘m sotuv', 'Yetakchi modellar']) assert.ok(!html.includes(value), value);
  });
  check('Report links cannot introduce arbitrary remote or script URLs', () => {
    const html = artifact({ type: 'report', data: { kind: 'external', analysis, exports: { csv: 'javascript:alert(1)', html: 'https://example.test/fake' } } });
    assert.ok(!html.includes('javascript:'));
    assert.ok(!html.includes('example.test'));
  });
  check('Generic dashboard preserves KPI labels, units and full multiple-series results', () => {
    const html = artifact({ type: 'dashboard', data: { kind: 'external', dashboard: { kpis: [{ id: 'delay', title: 'Kechikish', value: '3.2', unit: 'kun' }, { id: 'state', title: 'Holat', value: 'Faol' }], charts: [{ id: 'regions', title: 'Hududlar', chart_type: 'bar', x_axis: 'region', y_axis: 'delivery_count', secondary_y_axis: 'late_count', data_keys: ['delivery_count', 'late_count'], data: [{ ...rows[0], late_count: 2 }] }] } } });
    for (const value of ['Kechikish', 'kun', 'Faol', 'ca-series-legend', 'delivery_count', 'late_count', '>2<']) assert.ok(html.includes(value), value);
    assert.ok(!html.includes('Yalpi foyda'));
  });
  check('Question-specific dashboard uses actual analysis and quality agent exposes audit scope', () => {
    const dashboard = artifact({ type: 'dashboard', data: { kind: 'external', analysis } });
    assert.ok(dashboard.includes('12 ta yetkazib berish.'));
    const quality = artifact({ type: 'agent', data: { kind: 'external', summary: 'Tekshiruv tugadi', findings: [], audit: { tables_checked: 1, relationships_checked: 0, tables: [{ table: 'shipments', sampled_rows: 120, duplicate_key_groups: null, issues: ['Bo‘sh qiymat topildi'] }], relationships: [], warnings: ['Namuna asosida'] } } });
    for (const value of ['Tekshirilgan qatorlar', '>120<', 'Bo‘sh qiymat topildi', 'Namuna asosida']) assert.ok(quality.includes(value), value);
  });
  check('Partial schema disables data shortcuts while allowing arithmetic; voice remains present', () => {
    const html = render(Workspace, { session: { id: 'external', source: 'external', connection: 'server-a', messages: [] }, filters: {}, sourceName: 'operations', connected: true, understanding: { ...profile, status: 'partial', profile_complete: false }, onRefresh: noop, onConnect: noop, onMessagesChange: noop });
    const prompts = [...html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].map(match => match[0]).filter(button => ['Dashboard yaratish', 'Hisobot tayyorlash', 'Data sifatini tekshirish', 'Hisob-kitob qilish'].some(title => button.includes(`<b>${title}</b>`)));
    assert.equal(prompts.length, 4);
    assert.equal(prompts.filter(button => button.includes('disabled=""')).length, 3);
    assert.ok(html.includes('Ovozli buyruq berish'));
    assert.ok(html.includes('Hisob-kitob qilish'));
  });
  check('Iterative analysis keeps every executed result and exposes only actual operation trace', () => {
    const html = artifact({ type: 'analysis', data: { agent_status: 'completed', agent_trace: [
      { id: 's1', tool: 'inspect_tables', label: 'Jadvallar tekshirildi', status: 'completed', reasoning: 'PRIVATE_REASONING', arguments: { hidden: 'PRIVATE_ARGUMENT' } },
      { id: 's2', tool: 'query_data', label: 'Birinchi so‘rov', status: 'failed' },
      { id: 's3', tool: 'query_data', label: 'Tuzatilgan so‘rov', status: 'completed', result_id: 'q1', execution_time_ms: 125 },
    ], query_results: [
      { id: 'q1', title: 'Oylar', sql_query: 'SELECT month, amount FROM monthly', columns: ['month', 'amount'], rows: [{ month: '2024-01', amount: 0 }], returned_count: 1, is_truncated: false },
      { id: 'q2', title: 'Mahsulotlar', sql_query: 'SELECT product, amount FROM products', columns: ['product', 'amount'], rows: [{ product: 'Keyboard', amount: 550 }], returned_count: 1, is_truncated: true },
    ], rows: [{ fake_legacy_row: 999 }], columns: ['fake_legacy_row'], charts: [] } });
    for (const value of ['3 bosqich · 2 bajarildi', 'Bajarilmadi', 'Tuzatilgan so‘rov', 'q1', 'q2', 'Oylar', 'Mahsulotlar', 'Keyboard', '>550<', '>0<', 'SELECT product', 'natija cheklangan']) assert.ok(html.includes(value), value);
    for (const value of ['PRIVATE_REASONING', 'PRIVATE_ARGUMENT', 'fake_legacy_row']) assert.ok(!html.includes(value), value);
    assert.ok(!/<details class="ca-agent-trace" open/.test(html));
  });
  check('Partial analysis preserves evidence and does not claim completion', () => {
    const data = { agent_status: 'partial', success: true, error: 'agent_budget_exhausted', explanation: 'Davomiy tahlil kerak.', validation_warnings: ['Vaqt chegarasi tugadi.'], query_results: [{ id: 'q1', title: 'Bajarilgan hisob', columns: ['total'], rows: [{ total: 42 }], sql_query: 'SELECT SUM(amount) AS total FROM orders' }] };
    const html = artifact({ type: 'dashboard', data: { kind: 'external', analysis: data } });
    for (const value of ['Tahlil qisman yakunlandi', '>42<', 'Vaqt chegarasi tugadi.', 'Davomiy tahlil kerak.']) assert.ok(html.includes(value), value);
    assert.ok(!html.includes('mos ma’lumot topilmadi'));
    const message = render(Workspace, { session: { id: 'partial', source: 'demo', messages: [{ role: 'assistant', id: 'answer', content: 'Qisman tayyor.', steps: [{ id: 'step-1', label: 'Tahlil', status: 'partial' }], artifacts: [] }] }, filters: {}, sourceName: 'demo', connected: true, onMessagesChange: noop });
    assert.ok(message.includes('Qisman bajarildi'));
    assert.ok(message.includes('0/1 amal bajarildi'));
  });
  check('Agent report offers four safe export formats and cannot follow a remote file link', () => {
    const html = artifact({ type: 'report', data: { kind: 'external', id: 'iterative-1', analysis, exports: { xlsx: '/api/platform/reports/iterative-1/export?format=xlsx', json: '/api/platform/reports/iterative-1/export?format=json' } } });
    for (const value of ['format=xlsx', 'format=json', 'format=csv', 'format=html', 'Excel', 'JSON']) assert.ok(html.includes(value), value);
    const unsafe = artifact({ type: 'report', data: { kind: 'external', analysis, exports: { xlsx: '//example.test/report.xlsx', json: '/api/platform/reports/check/export?format=json&redirect=https://example.test' } } });
    assert.ok(!unsafe.includes('example.test'));
  });
  check('An unknown chart evidence ID is shown as missing rather than using unrelated rows', () => {
    const html = artifact({ type: 'analysis', data: { agent_status: 'completed', query_results: [{ id: 'q1', title: 'Asosiy hisob', columns: ['amount'], rows: [{ amount: 10 }] }], charts: [{ id: 'bad', query_id: 'missing', title: 'Mos bo‘lmagan grafik', x_axis: 'x', y_axis: 'y', data: [{ x: 'invented-category', y: 123 }] }] } });
    assert.ok(html.includes('hisoblash dalili topilmadi'));
    assert.ok(!html.includes('invented-category'));
    assert.ok(html.includes('>10<'));
  });
  check('Chat uses one selected chart and keeps the evidence table for non-chart results', () => {
    const chart = { title: 'Tanlangan reyting', chart_type: 'horizontal_bar', x_axis: 'region', y_axis: 'count', data: [{ region: 'A', count: 2 }, { region: 'B', count: 0 }] };
    const data = { chart, charts: [chart, { ...chart, title: 'Oldingi ko‘rinish' }], rows: chart.data };
    const html = artifact({ type: 'analysis', data });
    assert.equal((html.match(/data-chart-type=/g) || []).length, 1);
    assert.match(html, /data-chart-type="horizontal_bar"/);
    assert.ok(!html.includes('Oldingi ko‘rinish'));
    const noChart = artifact({ type: 'analysis', data: { ...data, chart: null } });
    assert.ok(!noChart.includes('data-chart-type='));
    assert.ok(noChart.includes('>0<'));
  });
  console.log(`${checks} connected workflow checks passed.`);
} finally { await server.close(); }
