import React, { useId, useState } from 'react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ArrowDownToLine, ArrowUpRight, BarChart3, Bot, Calculator, Check, CheckCircle2, ChevronDown, CircleAlert, FileText, Loader2, Table2, TrendingUp } from 'lucide-react';
import { CHART_COLORS, CHART_THEME } from '../theme';
import MarkdownRenderer from './MarkdownRenderer';

const number = value => Number.isFinite(Number(value)) ? new Intl.NumberFormat('uz-UZ', { maximumFractionDigits: 2 }).format(Number(value)) : '—';
const compact = value => {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '—';
  if (Math.abs(amount) >= 1e12) return `${number(amount / 1e12)} trln`;
  if (Math.abs(amount) >= 1e9) return `${number(amount / 1e9)} mlrd`;
  if (Math.abs(amount) >= 1e6) return `${number(amount / 1e6)} mln`;
  if (Math.abs(amount) >= 1e3) return `${number(amount / 1e3)} ming`;
  return number(amount);
};
const scalar = value => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const finite = value => (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value));
const list = value => Array.isArray(value) ? value : [];
const period = filters => filters?.start_date && filters?.end_date ? `${filters.start_date} — ${filters.end_date}` : '';
const TITLES = { dashboard: 'Dashboard', report: 'Hisobot', agent: 'Agent tahlili', calculation: 'Hisob-kitob', analysis: 'Ma’lumot tahlili' };
const ICONS = { dashboard: BarChart3, report: FileText, agent: Bot, calculation: Calculator, analysis: TrendingUp };

function Card({ type, title, subtitle, children }) {
  const Icon = ICONS[type] || BarChart3;
  return <section className={`ca-card ca-${type}`} aria-label={title || TITLES[type]}>
    <header className="ca-card-head"><span className="ca-icon"><Icon size={19} /></span><div className="ca-heading"><h3>{title || TITLES[type] || 'Natija'}</h3>{subtitle && <p className="ca-period">{subtitle}</p>}</div><span className="ca-tag">{TITLES[type] || 'Natija'}</span></header>
    <div className="ca-body">{children}</div>
  </section>;
}

function Kpis({ items, definitions = {}, generic = false }) {
  const values = list(items).slice(0, 6);
  if (!values.length) return null;
  return <div className="ca-kpis">{values.map((item, index) => <div className="ca-kpi" key={item.key || item.id || index} title={item.description || definitions[item.key] || ''}>
    <span className="ca-kpi-label">{item.label || item.title || item.key}</span>
    <strong className="ca-kpi-value" title={scalar(item.value)}>{item.value == null ? '—' : !finite(item.value) ? scalar(item.value) : item.format === 'currency' ? compact(item.value) : number(item.value)}<small>{generic ? item.unit || '' : item.format === 'currency' ? 'so‘m' : item.format === 'percent' ? '%' : ''}</small></strong>
    {finite(item.change_pct) && <span className={`ca-change ${Number(item.change_pct) < 0 ? 'is-negative' : ''}`}>{Number(item.change_pct) > 0 ? '+' : ''}{number(item.change_pct)}% <span>oldingi davrga nisbatan</span></span>}
    {generic && item.delta && <span className={`ca-change ${item.delta_type === 'negative' ? 'is-negative' : ''}`}>{scalar(item.delta)}</span>}
  </div>)}</div>;
}

function PlotTooltip({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null;
  return <div className="ca-tooltip"><b>{scalar(label)}</b>{payload.map((item, index) => <div key={index}><i style={{ background: item.color || CHART_THEME.primary }} /><span>{item.name}</span><strong>{number(item.value)}{unit === 'UZS' ? ' so‘m' : unit ? ` ${unit}` : ''}</strong></div>)}</div>;
}

function Trend({ data }) {
  const id = useId().replace(/:/g, '');
  const rows = list(data);
  if (!rows.length) return <p className="ca-note">Tanlangan davr uchun sotuv ma’lumotlari topilmadi.</p>;
  return <div className="ca-plot"><div className="ca-plot-head"><h4>Sotuv dinamikasi</h4><div className="ca-legend"><span><i style={{ background: CHART_THEME.primary }} />Sotuv</span><span><i style={{ background: CHART_THEME.secondary }} />Yalpi foyda</span></div></div>
    <ResponsiveContainer width="100%" height={235}><AreaChart data={rows} margin={{ top: 12, right: 12, left: 0, bottom: 4 }}><defs><linearGradient id={`ca-fill-${id}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={CHART_THEME.primary} stopOpacity={0.19} /><stop offset="100%" stopColor={CHART_THEME.primary} stopOpacity={0.015} /></linearGradient></defs><CartesianGrid stroke={CHART_THEME.grid} vertical={false} strokeDasharray="3 5" /><XAxis dataKey="month" axisLine={false} tickLine={false} minTickGap={24} tick={{ fontSize: 11, fill: CHART_THEME.tick }} tickFormatter={value => scalar(value).slice(5)} dy={7} /><YAxis axisLine={false} tickLine={false} tickFormatter={compact} tick={{ fontSize: 10, fill: CHART_THEME.tick }} width={70} /><Tooltip content={<PlotTooltip unit="UZS" />} /><Area type="monotone" dataKey="revenue" name="Sotuv" stroke={CHART_THEME.primary} strokeWidth={2.5} dot={rows.length === 1 ? { r: 5, fill: CHART_THEME.primary, strokeWidth: 2, stroke: 'var(--surface)' } : false} fill={`url(#ca-fill-${id})`} /><Area type="monotone" dataKey="gross_profit" name="Yalpi foyda" stroke={CHART_THEME.secondary} strokeWidth={2} strokeDasharray="4 4" dot={rows.length === 1 ? { r: 5, fill: CHART_THEME.secondary, strokeWidth: 2, stroke: 'var(--surface)' } : false} fill="transparent" /></AreaChart></ResponsiveContainer>
  </div>;
}

function DataTable({ columns, rows, label = 'Ma’lumotlar jadvali', truncated = false, open = false }) {
  const data = list(rows);
  const defs = list(columns).length ? list(columns) : data[0] && !Array.isArray(data[0]) && typeof data[0] === 'object' ? Object.keys(data[0]) : [];
  const headers = defs.map((column, index) => typeof column === 'string' ? { key: column, label: column } : { key: column.key || column.name || String(index), label: column.label || column.name || column.key || String(index + 1) });
  if (!headers.length) return data.length ? <p className="ca-note">{data.map(scalar).join(', ')}</p> : null;
  return <details className="ca-data" open={open || undefined}><summary><Table2 size={15} /><span>{label}</span><small>{number(data.length)} qator</small><ChevronDown size={15} /></summary><div className="ca-table-wrap"><table><thead><tr>{headers.map((column, index) => <th key={`${column.key}-${index}`}>{column.label}</th>)}</tr></thead><tbody>{data.slice(0, 100).map((row, index) => <tr key={index}>{headers.map((column, cell) => <td key={`${column.key}-${cell}`}>{scalar(Array.isArray(row) ? row[cell] : row?.[column.key])}</td>)}</tr>)}</tbody></table>{!data.length && <p className="ca-note">Tanlangan shartlarga mos yozuv topilmadi.</p>}</div>{(truncated || data.length > 100) && <p className="ca-note">Natija chegaralangan. Barcha kerakli yozuvlar uchun savolni aniqroq filtrlang.</p>}</details>;
}

function SeriesPlot({ chart, fallbackRows = [] }) {
  const source = list(chart?.data).length ? chart.data : list(fallbackRows);
  if (!source.length) return null;
  const keys = source[0] && typeof source[0] === 'object' ? Object.keys(source[0]) : [];
  const xKey = chart?.x_key || chart?.x_axis || (keys.includes('label') ? 'label' : keys[0]);
  const yKey = chart?.y_key || chart?.y_axis || (keys.includes('value') ? 'value' : keys.find(key => key !== xKey && source.some(row => finite(row[key]))));
  if (!xKey || !yKey) return null;
  const series = [...new Set([yKey, chart?.secondary_y_axis, ...list(chart?.data_keys)])].filter(key => key && key !== xKey && source.some(row => finite(row[key])));
  if (!series.length) return null;
  const type = chart?.type || chart?.chart_type || 'bar';
  const isPie = type === 'pie' || type === 'donut';
  if (series.length > 6 || (isPie && series.length > 1)) return <p className="ca-note">{chart?.title || 'Natija'}: {series.length} ta ko‘rsatkich to‘liq jadvalda ko‘rsatilgan.</p>;
  const horizontal = type === 'horizontal_bar';
  if (!['horizontal_bar', 'bar', 'line', 'area', 'pie', 'donut'].includes(type)) return type === 'table' ? null : <p className="ca-note">{chart?.title || 'Grafik'}: bu grafik turi uchun natijalar jadvalda ko‘rsatilgan.</p>;
  const rows = source.map(row => ({ ...row, ...Object.fromEntries(series.map(key => [key, finite(row[key]) ? Number(row[key]) : null])) }));
  if (isPie && rows.some(row => row[yKey] < 0)) return <p className="ca-note">{chart?.title || 'Natija'}: manfiy qiymatlar jadvalda ko‘rsatilgan.</p>;
  const timeSeries = ['line', 'area'].includes(type);
  const label = chart?.title || 'Tahlil natijasi';
  const color = index => CHART_COLORS[index % CHART_COLORS.length];
  return <div className="ca-plot" data-chart-type={type}><div className="ca-plot-head"><h4>{label}</h4>{chart?.unit && <span>{chart.unit === 'UZS' ? 'so‘m' : chart.unit}</span>}</div>{series.length > 1 && <div className="ca-series-legend">{series.map((key, index) => <span key={key}><i style={{ background: color(index) }} />{key}</span>)}</div>}<ResponsiveContainer width="100%" height={225}>
    {isPie ? <PieChart><Pie data={rows} nameKey={xKey} dataKey={yKey} innerRadius={type === 'donut' ? 50 : 0} outerRadius={85} paddingAngle={2} stroke="none">{rows.map((row, index) => <Cell key={index} fill={color(index)} />)}</Pie><Tooltip formatter={value => number(value)} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, color: 'var(--text-primary)' }} /></PieChart> : timeSeries ? <AreaChart data={rows} margin={{ top: 10, right: 12, bottom: 8, left: 0 }}><CartesianGrid stroke={CHART_THEME.grid} vertical={false} strokeDasharray="3 5" /><XAxis dataKey={xKey} axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: CHART_THEME.tick }} minTickGap={26} /><YAxis width={66} tickFormatter={compact} axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: CHART_THEME.tick }} /><Tooltip content={<PlotTooltip unit={chart?.unit} />} />{series.map((key, index) => <Area key={key} type="monotone" dataKey={key} name={series.length > 1 ? key : label} stroke={color(index)} fill={color(index)} fillOpacity={type === 'line' ? 0 : 0.07} strokeWidth={2.5} dot={rows.length === 1 ? { r: 5, fill: color(index), stroke: 'var(--aw-panel)', strokeWidth: 2 } : false} connectNulls={false} />)}</AreaChart> : <BarChart data={rows} layout={horizontal ? 'vertical' : 'horizontal'} margin={{ top: 10, right: 12, bottom: 8, left: 0 }}><CartesianGrid stroke={CHART_THEME.grid} vertical={false} strokeDasharray="3 5" /><XAxis type={horizontal ? 'number' : 'category'} dataKey={horizontal ? undefined : xKey} tickFormatter={horizontal ? compact : value => scalar(value).length > 18 ? `${scalar(value).slice(0, 17)}…` : scalar(value)} axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: CHART_THEME.tick }} minTickGap={26} />
      <YAxis type={horizontal ? 'category' : 'number'} dataKey={horizontal ? xKey : undefined} width={horizontal ? 110 : 66} tickFormatter={horizontal ? undefined : compact} axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: CHART_THEME.tick }} /><Tooltip cursor={{ fill: CHART_THEME.hover }} content={<PlotTooltip unit={chart?.unit} />} />{series.map((key, index) => <Bar key={key} dataKey={key} name={series.length > 1 ? key : label} fill={color(index)} radius={[5, 5, 0, 0]} maxBarSize={38} />)}</BarChart>}
  </ResponsiveContainer></div>;
}

function Dashboard({ data }) {
  if (data.kind === 'external') return <ExternalDashboard data={data} />;
  return <><Kpis items={data.kpis} definitions={data.metadata?.definitions} /><Trend data={data.trend} />{list(data.top_models).length > 0 && <div className="ca-mini-list"><h4>Yetakchi modellar</h4>{data.top_models.slice(0, 4).map((model, index) => <div className="ca-mini-row" key={`${model.brand}-${model.model}`}><span className="ca-rank">{String(index + 1).padStart(2, '0')}</span><b>{model.brand} {model.model}</b><span>{number(model.units)} ta</span><strong>{compact(model.revenue)} <small>so‘m</small></strong></div>)}</div>}<DataTable columns={[{ key: 'name', label: 'Filial' }, { key: 'units', label: 'Sotuv soni' }, { key: 'revenue', label: 'Sotuv, so‘m' }, { key: 'gross_profit', label: 'Yalpi foyda, so‘m' }]} rows={data.branches} label="Filiallar kesimida" /><p className="ca-note">Yalpi foyda: sotuv summasi minus tannarx; operatsion xarajatlar ayirilmagan.{data.metadata?.as_of ? ` Ombor zaxirasi: ${data.metadata.as_of} holatiga.` : ''}{data.metadata?.synthetic ? ' Sun’iy namuna ma’lumotlari.' : ''}</p></>;
}

function ExternalDashboard({ data }) {
  if (data.analysis) return <Analysis data={data.analysis} showSummary />;
  const dashboard = data.dashboard || data;
  const charts = list(dashboard.charts);
  return <>{dashboard.executive_summary && <div className="ca-summary"><MarkdownRenderer content={dashboard.executive_summary} /></div>}<Kpis items={dashboard.kpis || dashboard.kpi_cards} generic />{charts.map((chart, index) => <div className="ca-analysis-block" key={chart.id || index}><SeriesPlot chart={chart} />{chart.description && <p className="ca-note">{chart.description}</p>}<DataTable rows={chart.data} label={chart.title || 'Grafik ma’lumotlari'} open={chart.chart_type === 'table' || list(chart.data).length <= 1} />{chart.insight && <p className="ca-note">{chart.insight}</p>}</div>)}{!charts.length && !list(dashboard.kpis || dashboard.kpi_cards).length && <p className="ca-note">{dashboard.message || 'Dashboard uchun ko‘rsatkichlar topilmadi.'}</p>}{dashboard.schema_summary && <p className="ca-note">{dashboard.schema_summary}</p>}</>;
}

function reportUrl(data, format) {
  if (!['csv', 'html', 'xlsx', 'json'].includes(format)) return null;
  const supplied = data.exports?.[format];
  const rule = /^\/api\/platform\/reports\/[A-Za-z0-9_-]+\/export\?format=(csv|html|xlsx|json)$/;
  if (typeof supplied === 'string' && rule.test(supplied) && supplied.endsWith(`=${format}`)) return supplied;
  if (typeof data.id === 'string' && /^[A-Za-z0-9_-]+$/.test(data.id)) return `/api/platform/reports/${data.id}/export?format=${format}`;
  return null;
}

function Report({ data }) {
  const [showOverview, setShowOverview] = useState(false);
  const csv = reportUrl(data, 'csv'), html = reportUrl(data, 'html');
  const summary = data.summary || {};
  if (data.kind === 'external') return <><div className="ca-report-summary"><span><CheckCircle2 size={17} />{data.id ? 'Hisobot saqlandi' : 'Hisobot natijasi'}</span>{data.source?.name && <p>{data.source.name}</p>}</div><ReportDownloads data={data} /><Analysis data={data.analysis || { message: 'Hisobot ma’lumotlari mavjud emas.' }} showSummary /></>;
  return <><div className="ca-report-summary"><span><CheckCircle2 size={17} /> {data.id ? 'Hisobot saqlandi' : 'Hisobot natijasi'}</span>{finite(summary.revenue) && <strong>{compact(summary.revenue)} <small>so‘m sotuv</small></strong>}{finite(summary.units) && <p>{number(summary.units)} ta yakunlangan sotuv</p>}</div><div className="ca-downloads">{csv && <a className="ca-download" href={csv} download><ArrowDownToLine size={16} /> CSV yuklash</a>}{html && <a className="ca-download" href={html} target="_blank" rel="noopener noreferrer"><FileText size={16} /> Hisobotni ochish <ArrowUpRight size={14} /></a>}</div>{data.overview && <details className="ca-data" onToggle={event => setShowOverview(event.currentTarget.open)}><summary><BarChart3 size={15} /><span>Hisobot tafsilotlari</span><ChevronDown size={15} /></summary>{showOverview && <div className="ca-report-overview"><Dashboard data={data.overview} /></div>}</details>}</>;
}

function ReportDownloads({ data }) {
  const formats = [{ key: 'xlsx', label: 'Excel' }, { key: 'csv', label: 'CSV' }, { key: 'html', label: 'Hisobot' }, { key: 'json', label: 'JSON' }];
  return <div className="ca-downloads" aria-label="Hisobotni yuklab olish">{formats.map(format => {
    const url = reportUrl(data, format.key);
    if (!url) return null;
    return format.key === 'html'
      ? <a key={format.key} className="ca-download" href={url} target="_blank" rel="noopener noreferrer"><FileText size={15} />{format.label}<ArrowUpRight size={13} /></a>
      : <a key={format.key} className="ca-download" href={url} download><ArrowDownToLine size={15} />{format.label}</a>;
  })}</div>;
}

const TOOL_LABELS = {
  search_schema: 'Baza tuzilishini qidirish', inspect_tables: 'Jadvallarni tekshirish',
  inspect_table: 'Jadvalni tekshirish', inspect_schema: 'Baza tuzilishini tekshirish',
  query_data: 'Ma’lumotlarni hisoblash', run_query: 'Ma’lumotlarni hisoblash',
  calculate: 'Hisob-kitob', finish: 'Natijani yakunlash', finalize_analysis: 'Natijani yakunlash',
};
const STEP_STATUSES = { completed: 'Bajarildi', success: 'Bajarildi', partial: 'Qisman bajarildi', failed: 'Bajarilmadi', error: 'Bajarilmadi', running: 'Bajarilmoqda', unavailable: 'Mavjud emas', clarification: 'Aniqlashtirish kerak', skipped: 'O‘tkazib yuborildi' };
const completedStep = step => ['completed', 'success'].includes(step.status);

function AgentTrace({ steps }) {
  const items = list(steps).filter(step => step && typeof step === 'object');
  if (!items.length) return null;
  const completed = items.filter(completedStep).length;
  return <details className="ca-agent-trace"><summary>{completed === items.length ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}<span>{items.length} bosqich · {completed} bajarildi</span><ChevronDown size={14} /></summary><ol>{items.map((step, index) => {
    const complete = completedStep(step);
    const status = STEP_STATUSES[step.status] || 'Holat noma’lum';
    return <li className={complete ? 'is-complete' : ['failed', 'error'].includes(step.status) ? 'is-failed' : ''} key={step.id || index}><span className="ca-agent-step-icon">{complete ? <Check size={12} /> : step.status === 'running' ? <Loader2 size={12} className="spin" /> : <CircleAlert size={12} />}</span><div><b>{typeof step.label === 'string' && step.label ? step.label : TOOL_LABELS[step.tool] || 'Amal'}</b><small>{status}{finite(step.execution_time_ms) ? ` · ${number(Number(step.execution_time_ms) / 1000)} s` : ''}{step.result_id || step.query_id ? ` · ${scalar(step.result_id || step.query_id)}` : ''}</small></div></li>;
  })}</ol></details>;
}

function AgentStatus({ data }) {
  const evidenceNote = list(data.query_results).length ? ' Bajarilgan hisoblar quyida.' : '';
  const notices = {
    limit_reached: `Tahlil limiti tugadi.${evidenceNote}`,
    partial: `Tahlil qisman yakunlandi.${evidenceNote}`,
    failed: 'Tahlil yakunlanmadi.',
    needs_clarification: 'Tahlilni davom ettirish uchun aniqlashtirish kerak.',
    clarification: 'Tahlilni davom ettirish uchun aniqlashtirish kerak.',
  };
  const message = notices[data.agent_status];
  if (!message) return null;
  return <p className="ca-agent-status" role="status"><CircleAlert size={15} /><span>{message}</span></p>;
}

function QueryEvidence({ results }) {
  const queries = list(results).filter(result => result && typeof result === 'object');
  if (!queries.length) return null;
  return <div className="ca-evidence" aria-label="Hisoblash dalillari">{queries.map((query, index) => <section className="ca-evidence-item" key={query.id || index} aria-label={query.title || query.purpose || `So‘rov ${index + 1}`}><header><span className="ca-evidence-id">{scalar(query.id || `Q${index + 1}`)}</span><h4>{query.title || query.purpose || `So‘rov ${index + 1}`}</h4></header>{query.purpose && query.title && query.purpose !== query.title && <p className="ca-note">{query.purpose}</p>}<DataTable columns={query.columns} rows={query.rows} truncated={query.is_truncated} label="Hisoblangan ma’lumotlar" open={queries.length === 1 && list(query.rows).length <= 5} />{query.is_truncated && <p className="ca-note">{number(list(query.rows).length)} qator qaytarildi; natija cheklangan.</p>}{query.sql_query && <details className="ca-query"><summary>SQL so‘rovi{finite(query.execution_time_ms) ? ` · ${number(Number(query.execution_time_ms) / 1000)} s` : ''}</summary><pre><code>{query.sql_query}</code></pre></details>}</section>)}</div>;
}

function Agent({ data }) {
  const recommendations = list(data.recommendations);
  return <>{data.analysis && <p className="ca-agent-analysis">{scalar(data.analysis)}</p>}<div className="ca-findings">{list(data.findings).map((finding, index) => <div className={`ca-finding ${['warning', 'critical', 'success'].includes(finding.severity) ? finding.severity : ''}`} key={index}><span className="ca-finding-dot" /><div><h4>{finding.title || 'Tekshiruv natijasi'}</h4><p>{scalar(finding.description)}</p></div>{finite(finding.value) && <strong>{compact(finding.value)}</strong>}</div>)}</div>{recommendations.length > 0 && <div className="ca-recommendations"><h4>Keyingi qadamlar</h4><ol>{recommendations.map((item, index) => <li key={index}>{typeof item === 'string' ? item : scalar(item?.title || item?.action || item?.recommendation || item)}</li>)}</ol></div>}{data.analysis_error && <p className="ca-note">AI izohi: {scalar(data.analysis_error)} Hisoblangan tekshiruv natijalari yuqorida.</p>}{!list(data.findings).length && !data.analysis && <p className="ca-note">{data.summary || 'Agent natijasi mavjud emas.'}</p>}{data.kind === 'external' && data.audit && <QualityAudit audit={data.audit} />}</>;
}

function QualityAudit({ audit }) {
  return <><p className="ca-note">{number(audit.tables_checked)} ta jadval va {number(audit.relationships_checked)} ta bog‘lanish tekshirildi. Natija tekshirilgan yozuvlar doirasiga tegishli.</p><DataTable label="Jadvallar tekshiruvi" columns={[{ key: 'table', label: 'Jadval' }, { key: 'sampled_rows', label: 'Tekshirilgan qatorlar' }, { key: 'duplicate_key_groups', label: 'Takror kalit guruhlari' }, { key: 'issues', label: 'Aniqlangan muammolar' }]} rows={list(audit.tables).map(table => ({ ...table, issues: list(table.issues).join('; ') || 'Qayd etilmadi' }))} /><DataTable label="Bog‘lanishlar tekshiruvi" columns={[{ key: 'relationship', label: 'Bog‘lanish' }, { key: 'checked_rows', label: 'Tekshirilgan qatorlar' }, { key: 'orphan_count_sample', label: 'Mos yozuvi yo‘q' }, { key: 'issue', label: 'Izoh' }]} rows={list(audit.relationships).map(link => ({ ...link, relationship: `${link.from_table}.${link.from_column} → ${link.to_table}.${link.to_column}` }))} />{list(audit.warnings).map((warning, index) => <p className="ca-note" key={index}>{scalar(warning)}</p>)}</>;
}

function Analysis({ data, showSummary = false }) {
  const chart = Object.hasOwn(data, 'chart') ? data.chart : list(data.charts)[0];
  const summary = showSummary && (data.summary_text || data.explanation);
  const warnings = list(data.validation_warnings).map((warning, index) => <p className="ca-note" key={index}>{scalar(warning)}</p>);
  if (list(data.query_results).length || list(data.agent_trace).length || data.agent_status) {
    const queries = list(data.query_results);
    const queryId = chart?.query_id || chart?.result_id;
    const query = queryId && queries.find(item => item.id === queryId);
    return <>
      <AgentStatus data={data} /><AgentTrace steps={data.agent_trace} />
      {summary && <div className="ca-summary"><MarkdownRenderer content={summary} /></div>}
      {chart && (queryId && !query
        ? <p className="ca-note">{chart.title || 'Grafik'}: hisoblash dalili topilmadi.</p>
        : <section className="ca-evidence-chart">
          <SeriesPlot chart={chart} fallbackRows={query?.rows} />
          {queryId && <p className="ca-chart-source">Manba: {scalar(queryId)}</p>}
          {!queryId && list(chart.data).length > 0 && <DataTable rows={chart.data} label={chart.title || 'Grafik ma’lumotlari'} open={chart.chart_type === 'table'} />}
        </section>)}
      <QueryEvidence results={queries} />{warnings}
    </>;
  }
  if (list(data.analyses).length) return <>{chart && <SeriesPlot chart={chart} />}{data.analyses.map((analysis, index) => {
    const grouped = analysis.group_by && analysis.group_by !== 'none';
    const rows = list(analysis.rows);
    const metric = analysis.metric_label || analysis.operation || 'Natija';
    return <div className="ca-analysis-block" key={index}><h4>{metric}{analysis.unit && <small> · {analysis.unit === 'UZS' ? 'so‘m' : analysis.unit}</small>}</h4>{period(analysis.filters) && <p className="ca-period">{period(analysis.filters)}{analysis.filters?.branch_city ? ` · ${analysis.filters.branch_city}` : ''}{analysis.filters?.brand ? ` · ${analysis.filters.brand}` : ''}{analysis.filters?.model ? ` ${analysis.filters.model}` : ''}</p>}{!grouped && rows.length === 1 && rows[0]?.value != null ? <strong className="ca-single-value">{number(rows[0].value)}</strong> : null}<DataTable columns={analysis.columns} rows={rows} truncated={analysis.truncated} label="Hisoblangan ma’lumotlar" open={rows.length > 1 && rows.length <= 5} />{analysis.definition && <p className="ca-note">{analysis.definition}</p>}</div>;
  })}</>;
  const table = data.table || data;
  return <>
    {summary && <div className="ca-summary"><MarkdownRenderer content={summary} /></div>}
    {chart && <><SeriesPlot chart={chart} fallbackRows={table.rows} />
      {!list(table.rows).length && list(chart.data).length > 0 && <DataTable rows={chart.data} label={chart.title || 'Grafik ma’lumotlari'} open={chart.chart_type === 'table'} />}
    </>}
    <DataTable columns={table.columns} rows={table.rows} truncated={table.truncated || data.is_truncated} open={list(table.rows).length <= 5} />
    {!list(table.rows).length && !chart && <p className="ca-note">{data.message || data.error || 'Tanlangan shartlarga mos ma’lumot topilmadi.'}</p>}
    {warnings}
    {data.sql_query && <details className="ca-query"><summary>Hisoblash so‘rovini ko‘rish</summary><pre><code>{data.sql_query}</code></pre></details>}
  </>;
}

export default function CommandArtifacts({ artifacts = [] }) {
  const items = list(artifacts);
  if (!items.length) return null;
  return <div className="ca-stack">{items.map((artifact, index) => {
    const data = artifact.data && typeof artifact.data === 'object' ? artifact.data : {};
    const title = artifact.title || data.title || (artifact.type === 'agent' ? data.agent_name : '') || TITLES[artifact.type];
    const subtitle = data.metadata?.period_label || period(data.filters);
    let content;
    if (artifact.type === 'dashboard') content = <Dashboard data={data} />;
    else if (artifact.type === 'report') content = <Report data={data} />;
    else if (artifact.type === 'agent') content = <Agent data={data} />;
    else if (artifact.type === 'analysis') content = <Analysis data={data} />;
    else if (artifact.type === 'calculation') content = <div className="ca-calculation"><code className="ca-expression">{scalar(data.expression)}</code><strong className="ca-calculation-value">{data.formatted != null ? scalar(data.formatted) : data.value != null ? number(data.value) : '—'}</strong><span>Hisoblangan natija</span></div>;
    else content = <p className="ca-note">{data.message || 'Natija tayyor.'}</p>;
    return <Card key={artifact.id || `${artifact.type}-${index}`} type={artifact.type || 'analysis'} title={title} subtitle={subtitle}>{content}</Card>;
  })}</div>;
}
