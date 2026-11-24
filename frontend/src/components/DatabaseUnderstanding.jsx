import React from 'react';
import { ArrowUpRight, CheckCircle2, ChevronDown, CircleAlert, Database, Link2, Loader2, RefreshCw, Table2 } from 'lucide-react';

const count = value => Number.isFinite(value) ? new Intl.NumberFormat('uz-UZ').format(value) : '—';
const array = value => Array.isArray(value) ? value : [];

export default function DatabaseUnderstanding({ profile, loading, error, sourceName, onRefresh, onReconnect, onQuestion, disabled = false, compact = false }) {
  if (loading) return <div className="aw-understanding-pending" role="status"><Loader2 size={19} className="spin" /><div><b>Baza o‘rganilmoqda</b></div></div>;
  if (error || !profile) return <div className="aw-understanding-pending is-warning" role="status"><CircleAlert size={19} /><div><b>Baza tuzilishi yuklanmadi</b>{error && <p>{error}</p>}</div><button type="button" onClick={onRefresh}><RefreshCw size={14} />Qayta urinish</button></div>;
  const ready = profile.status === 'ready' && profile.profile_complete !== false;
  const empty = profile.status === 'empty';
  const title = ready ? 'Baza o‘rganildi' : empty ? 'Bazada jadvallar topilmadi' : 'Baza qisman o‘rganildi';
  const tables = array(profile.tables);
  const questions = array(profile.suggested_questions).filter(question => typeof question === 'string' && question.trim()).slice(0, 3);
  return <section className={`aw-understanding ${ready ? 'is-ready' : 'is-partial'} ${compact ? 'is-compact' : ''}`} aria-label="Ulangan baza haqida">
    <div className="aw-understanding-head"><span className="aw-understanding-icon">{ready ? <CheckCircle2 size={21} /> : <CircleAlert size={21} />}</span><div><h2>{title}</h2><p>{sourceName}<span>·</span>{profile.dialect || 'Ma’lumot bazasi'}</p></div>{profile.read_only && <span className="aw-understanding-access">Faqat o‘qish</span>}</div>
    {!ready && <p className="aw-understanding-intro">{empty ? 'Jadvallar va o‘qish ruxsatini tekshiring.' : 'Baza to‘liq o‘rganilmaguncha tahlil, dashboard va hisobot yaratish ishlamaydi.'}</p>}
    <div className="aw-understanding-counts"><span><Table2 size={14} /><b>{count(profile.table_count)}</b> jadval</span><span><Database size={14} /><b>{count(profile.column_count)}</b> ustun</span><span><Link2 size={14} /><b>{count(profile.relationship_count)}</b> bog‘lanish</span></div>
    {array(profile.warnings).length > 0 && <ul className="aw-understanding-warnings">{profile.warnings.map((warning, index) => <li key={index}>{typeof warning === 'string' ? warning : warning?.message || 'Baza profili bo‘yicha cheklov mavjud.'}</li>)}</ul>}
    {!ready && onReconnect && <button type="button" className="aw-understanding-reconnect" onClick={onReconnect}><RefreshCw size={14} />Qayta ulash</button>}
    {tables.length > 0 && <details className="aw-schema"><summary><Table2 size={14} /><span>Baza tuzilishini ko‘rish</span><ChevronDown size={14} /></summary><div className="aw-schema-tables">{tables.map((table, index) => <details key={`${table.name}-${index}`} className="aw-schema-table"><summary><span>{table.name}</span><small>{count(table.column_count ?? array(table.columns).length)} ustun{Number.isFinite(table.row_count_estimate) ? ` · taxm. ${count(table.row_count_estimate)} qator` : ''}</small><ChevronDown size={13} /></summary>{table.description && <p>{table.description}</p>}<div className="aw-schema-columns">{array(table.columns).map((column, columnIndex) => <span key={`${column.name}-${columnIndex}`}><b>{column.name}</b><small>{column.data_type}</small></span>)}</div>{table.columns_truncated && <p>Dastlabki {array(table.columns).length} ustun ko‘rsatilgan.</p>}</details>)}</div>{profile.tables_truncated && <p className="aw-schema-limit">Dastlabki {tables.length} jadval ko‘rsatilgan.</p>}</details>}
    {!compact && questions.length > 0 && <div className="aw-schema-questions">{questions.map((question, index) => <button type="button" key={index} disabled={disabled} onClick={() => onQuestion(question)}>{question}<ArrowUpRight size={14} /></button>)}</div>}
  </section>;
}
