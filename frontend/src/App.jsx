import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, Database, Menu, MessageSquare, Plus, RefreshCw, Search, Sparkles, X } from 'lucide-react';
import { api, platformApi } from './services/api';
import ThemeToggle from './components/ThemeToggle';
import ConnectionModal from './components/ConnectionModal';
import AssistantWorkspace from './components/AssistantWorkspace';
import { activateConnectedSource, connectionKey } from './services/chatContext';
import './assistant.css';

const HISTORY_KEY = 'tabloai.conversations.v1';
const ACTIVE_KEY = 'tabloai.active-conversation.v1';
const DEFAULT_FILTERS = { start_date: '2026-01-01', end_date: '2026-11-24', branch_id: '', category: '' };
const makeId = () => globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const newConversation = (source = 'demo', connection = '', sourceExplicit = false, databaseName = '') => ({ id: makeId(), title: 'Yangi suhbat', source, connection, sourceExplicit, databaseName, createdAt: Date.now(), updatedAt: Date.now(), messages: [] });
const shortSource = value => String(value || '').split(/[\\/]/).filter(Boolean).pop() || 'Tashqi baza';
function readHistory() {
  try {
    const value = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    if (Array.isArray(value)) return value.filter(item => item && typeof item.id === 'string' && Array.isArray(item.messages)).slice(0, 20).map(item => ({
      ...item,
      title: typeof item.title === 'string' ? item.title.slice(0, 65) : 'Yangi suhbat',
      connection: typeof item.connection === 'string' ? item.connection : '',
      source: item.source === 'external' ? 'external' : 'demo',
      updatedAt: Number.isFinite(item.updatedAt) ? item.updatedAt : 0,
      messages: item.messages.filter(message => message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string').slice(-30).map(message => ({
        ...message,
        steps: Array.isArray(message.steps) ? message.steps : [],
        artifacts: Array.isArray(message.artifacts) ? message.artifacts : [],
        sources: Array.isArray(message.sources) ? message.sources : [],
        suggestions: Array.isArray(message.suggestions) ? message.suggestions : [],
      })),
    }));
  } catch { /* The current session remains usable if storage is blocked. */ }
  return [];
}
function initialState() {
  const saved = readHistory();
  const sessions = saved.length ? saved : [newConversation()];
  let active = sessions[0].id;
  try { const id = localStorage.getItem(ACTIVE_KEY); if (sessions.some(session => session.id === id)) active = id; } catch {}
  return { sessions, active };
}

export default function App() {
  const [state, setState] = useState(initialState);
  const [context, setContext] = useState(null);
  const [health, setHealth] = useState(null);
  const [contextError, setContextError] = useState('');
  const [connectOpen, setConnectOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [storageError, setStorageError] = useState(false);
  const [contextLoading, setContextLoading] = useState(true);
  const [understanding, setUnderstanding] = useState(null);
  const [understandingLoading, setUnderstandingLoading] = useState(false);
  const [understandingError, setUnderstandingError] = useState('');
  const contextRequest = useRef(0);
  const active = state.sessions.find(session => session.id === state.active) || state.sessions[0];
  const externalConnection = context?.active_connection && !context.active_connection.is_demo ? context.active_connection : null;
  const externalKey = connectionKey(externalConnection);
  const demoSource = context?.sources?.[0];
  const sourceName = active.source === 'external' ? shortSource(active.databaseName || (active.connection === externalKey ? externalConnection?.database_name : active.connection?.split(':').slice(1).join(':'))) : demoSource?.name || 'Atlas Auto Group';
  const sourceReady = active.source === 'demo' ? !!demoSource : !!externalConnection && active.connection === externalKey;
  const aiReady = !!health?.ai?.online && !!health?.ai?.model_ready;

  const refreshContext = useCallback(async () => {
    const id = ++contextRequest.current;
    setContextLoading(true);
    setUnderstandingLoading(true);
    setUnderstandingError('');
    setContextError('');
    const results = await Promise.allSettled([platformApi.sources(), api.getHealth()]);
    if (id !== contextRequest.current) return;
    if (results[0].status === 'fulfilled') {
      const next = results[0].value;
      setContext(next);
      const connection = next.active_connection && !next.active_connection.is_demo ? next.active_connection : null;
      if (connection) {
        setState(previous => activateConnectedSource(previous, connection));
        try {
          const profile = await api.getUnderstanding();
          if (id !== contextRequest.current) return;
          if (profile.database_name !== connection.database_name || (profile.connection_fingerprint && connection.connection_fingerprint && profile.connection_fingerprint !== connection.connection_fingerprint)) {
            setUnderstanding(null);
            setUnderstandingError('Ulanish o‘zgardi. Baza ma’lumotlarini qayta yangilang.');
          } else setUnderstanding(profile);
        } catch {
          if (id !== contextRequest.current) return;
          setUnderstanding(null);
          setUnderstandingError('Baza tuzilishini yuklab bo‘lmadi. Qayta urinib ko‘ring.');
        }
      } else setUnderstanding(null);
    }
    else { setContext(null); setUnderstanding(null); setContextError('Ma‘lumot manbasiga ulanib bo‘lmadi. Backend ishlayotganini tekshiring.'); }
    if (results[1].status === 'fulfilled') setHealth(results[1].value);
    else setHealth(null);
    setContextLoading(false);
    setUnderstandingLoading(false);
  }, []);
  useEffect(() => { refreshContext(); return () => { contextRequest.current++; }; }, [refreshContext]);
  useEffect(() => {
    try {
      let saved = state.sessions.slice(0, 20).map(session => ({ ...session, messages: session.messages.slice(-30) }));
      let text = JSON.stringify(saved);
      while (text.length > 2000000 && saved.length > 1) {
        const index = saved.findLastIndex(session => session.id !== state.active);
        if (index < 0) break;
        saved.splice(index, 1);
        text = JSON.stringify(saved);
      }
      if (text.length > 2000000) saved = saved.map(session => ({ ...session, messages: session.messages.slice(-8) }));
      localStorage.setItem(HISTORY_KEY, JSON.stringify(saved));
      localStorage.setItem(ACTIVE_KEY, state.active);
      setStorageError(false);
    } catch { setStorageError(true); }
  }, [state]);
  useEffect(() => {
    if (!sidebarOpen) return;
    const close = event => { if (event.key === 'Escape') setSidebarOpen(false); };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [sidebarOpen]);

  const createChat = (source = active.source, connection = source === 'external' ? externalKey : '', databaseName = source === 'external' ? externalConnection?.database_name || '' : '') => {
    const session = newConversation(source, connection, true, databaseName);
    setState(previous => ({ sessions: [session, ...previous.sessions.filter(item => item.messages.length)].slice(0, 20), active: session.id }));
    setSidebarOpen(false);
    setSearch('');
  };
  const updateMessages = useCallback((id, updater) => {
    setState(previous => ({ ...previous, sessions: previous.sessions.map(session => {
      if (session.id !== id) return session;
      const messages = typeof updater === 'function' ? updater(session.messages) : updater;
      const first = messages.find(message => message.role === 'user');
      return { ...session, title: first?.content.slice(0, 65) || 'Yangi suhbat', messages, updatedAt: Date.now() };
    }) }));
  }, []);
  const changeSource = value => {
    if (value === 'connect') { setConnectOpen(true); return; }
    if (value !== active.source || (value === 'external' && !sourceReady)) createChat(value, value === 'external' ? externalKey : '');
  };
  const onConnected = connection => {
    contextRequest.current++;
    const current = { database_name: connection.database_name || connection.dbname || 'Tashqi baza', dialect: connection.dialect || connection.db_type || 'postgresql', connection_fingerprint: connection.connection_fingerprint || connection.understanding?.connection_fingerprint, is_demo: false };
    setContext(previous => ({ ...previous, active_connection: current }));
    setUnderstanding(connection.understanding || null);
    setUnderstandingError('');
    createChat('external', connectionKey(current), current.database_name);
    refreshContext();
  };
  const visibleSessions = [...state.sessions].sort((a, b) => b.updatedAt - a.updatedAt).filter(session => !search || session.title.toLocaleLowerCase().includes(search.toLocaleLowerCase()));

  return <div className="assistant-app">
    {sidebarOpen && <button className="aw-backdrop" aria-label="Menyuni yopish" onClick={() => setSidebarOpen(false)} />}
    <aside className={`aw-sidebar ${sidebarOpen ? 'is-open' : ''}`} aria-label="Suhbatlar menyusi">
      <div className="aw-brand"><span className="aw-brand-mark"><Sparkles size={22} strokeWidth={1.8} /></span><span>Tablo<span>AI</span></span><button className="aw-mobile-close" aria-label="Menyuni yopish" onClick={() => setSidebarOpen(false)}><X size={18} /></button></div>
      <button className="aw-new-chat" onClick={() => createChat()}><Plus size={18} /><span>Yangi suhbat</span><span className="aw-key-hint">+</span></button>
      <div className="aw-history-heading"><span>SUHBATLAR</span><button aria-label="Suhbatlarni qidirish" onClick={() => { setSearchOpen(!searchOpen); setSearch(''); }}><Search size={15} /></button></div>
      {searchOpen && <div className="aw-history-search"><Search size={14} /><input autoFocus placeholder="Suhbatni qidiring" aria-label="Suhbatni qidirish" value={search} onChange={event => setSearch(event.target.value)} /></div>}
      <nav className="aw-history" aria-label="Suhbat tarixi">
        {visibleSessions.map(session => <button key={session.id} className={session.id === active.id ? 'is-current' : ''} onClick={() => { setState(previous => ({ ...previous, active: session.id })); setSidebarOpen(false); }} title={session.title}><MessageSquare size={15} /><span>{session.title}</span>{session.id === active.id && <i />}</button>)}
        {!visibleSessions.length && <p className="aw-history-empty">Suhbat topilmadi.</p>}
      </nav>
      <div className="aw-sidebar-bottom">
        <button className="aw-source-card" onClick={() => setConnectOpen(true)}><span className="aw-source-icon"><Database size={19} /></span><span><b>Ma‘lumot manbalari</b><small>{demoSource ? `${externalConnection ? 2 : 1} ta baza ulangan` : 'Baza ulash va sozlash'}</small></span><Plus size={16} /></button>
      </div>
    </aside>
    <main className="aw-main">
      <header className="aw-topbar"><div className="aw-topbar-title"><button className="aw-mobile-menu" aria-label="Menyuni ochish" onClick={() => setSidebarOpen(true)}><Menu size={20} /></button><span className="aw-topbar-symbol"><Sparkles size={17} /></span><b>AI yordamchi</b></div>
        <div className="aw-topbar-actions"><span className={`aw-ai-status ${aiReady ? 'is-ready' : ''}`}><i />{contextLoading ? 'Ulanmoqda' : aiReady ? 'AI tayyor' : context?.ai?.configured ? 'AI sozlangan' : 'Namuna rejimi'}</span><ThemeToggle /></div>
      </header>
      <div className="aw-sourcebar"><div className="aw-source-select"><Database size={14} /><select aria-label="Suhbat uchun ma‘lumot manbasi" value={active.source} onChange={event => changeSource(event.target.value)}><option value="demo">Namuna bazasi · {demoSource?.name || 'Atlas Auto Group'}</option>{externalConnection && <option value="external">{shortSource(externalConnection.database_name)}</option>}{active.source === 'external' && !externalConnection && <option value="external">Tashqi baza - ulanish kerak</option>}<option value="connect">+ Yangi baza ulash</option></select><ChevronDown size={13} /></div><button className="aw-source-refresh" aria-label="Ulanishni tekshirish" title="Ulanishni tekshirish" onClick={refreshContext} disabled={contextLoading}><RefreshCw size={14} className={contextLoading ? 'spin' : ''} /></button><span className="aw-sourcebar-note">{active.source === 'demo' ? 'Sun‘iy namuna ma‘lumotlari' : sourceReady ? 'Ulangan baza bilan suhbat' : 'Suhbat manbasini qayta ulash kerak'}</span></div>
      {contextError && <div className="aw-system-alert" role="alert">{contextError}<button onClick={refreshContext}>Qayta urinish</button></div>}
      {storageError && <div className="aw-storage-note">Suhbat ushbu oynada ishlaydi. Brauzer xotirasiga saqlash imkoni bo‘lmadi.</div>}
      <AssistantWorkspace key={`${active.id}:${active.source}:${active.connection}`} session={active} onMessagesChange={updateMessages} filters={DEFAULT_FILTERS} sourceName={sourceName} connected={sourceReady} connectionLoading={contextLoading} understanding={sourceReady ? understanding : null} understandingLoading={understandingLoading} understandingError={understandingError} onRefresh={refreshContext} onConnect={() => setConnectOpen(true)} aiConfigured={!!context?.ai?.configured} />
    </main>
    <ConnectionModal isOpen={connectOpen} onClose={() => setConnectOpen(false)} onConnected={onConnected} />
  </div>;
}
