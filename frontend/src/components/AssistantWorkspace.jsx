import React, { useEffect, useRef, useState } from 'react';
import { Activity, ArrowDown, ArrowRight, ArrowUp, ArrowUpRight, BarChart3, Calculator, Check, CheckCircle2, ChevronDown, CircleAlert, Copy, Database, FileText, Loader2, Mic, MicOff, PackageSearch, RefreshCw, Sparkles, Square, Volume2, VolumeX, X } from 'lucide-react';
import { platformApi } from '../services/api';
import MarkdownRenderer from './MarkdownRenderer';
import CommandArtifacts from './CommandArtifacts';
import DatabaseUnderstanding from './DatabaseUnderstanding';
import { commandFilters } from '../services/chatContext';

const PROMPTS = [
  { icon: BarChart3, title: 'Dashboard yaratish', prompt: '2026-yil uchun sotuv dashboardini yarat.' },
  { icon: FileText, title: 'Hisobot tayyorlash', prompt: '2026-yil oktabr oyi sotuv hisobotini tayyorla.' },
  { icon: PackageSearch, title: 'Omborni tekshirish', prompt: 'Ombor agentini ishga tushir va muammolarni tahlil qil.' },
  { icon: Calculator, title: 'Hisob-kitob qilish', prompt: '125000000 * 0.12 ni hisobla.', requiresData: false },
];
const EXTERNAL_PROMPTS = [
  { icon: BarChart3, title: 'Dashboard yaratish', prompt: 'Bazadagi ma’lumotlardan dashboard yarat.' },
  { icon: FileText, title: 'Hisobot tayyorlash', prompt: 'Bazadagi asosiy ma’lumotlar bo‘yicha hisobot tayyorla.' },
  { icon: Activity, title: 'Data sifatini tekshirish', prompt: 'Bazadagi ma’lumotlar sifatini tekshir.' },
  { icon: Calculator, title: 'Hisob-kitob qilish', prompt: '125000000 * 0.12 ni hisobla.', requiresData: false },
];
const id = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
function errorText(error) {
  const detail = error.response?.data?.detail;
  return typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => item.msg).join('. ') : error.message || 'So‘rov bajarilmadi. Qayta urinib ko‘ring.';
}
function ExecutionTrace({ steps }) {
  if (!steps?.length) return null;
  const completed = steps.filter(step => step.status === 'completed').length;
  const statuses = { completed: 'Bajarildi', partial: 'Qisman bajarildi', failed: 'Bajarilmadi', error: 'Bajarilmadi', running: 'Bajarilmoqda', unavailable: 'Mavjud emas', clarification: 'Aniqlashtirish kerak' };
  return <details className="aw-trace"><summary>{completed === steps.length ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}<span>{completed === steps.length ? `${completed} ta amal bajarildi` : `${completed}/${steps.length} amal bajarildi`}</span><ChevronDown size={13} /></summary><div className="aw-trace-list">{steps.map((step, index) => <div key={step.id || index}><span className={`aw-step-icon ${step.status === 'completed' ? 'is-complete' : ''}`}>{step.status === 'completed' ? <Check size={12} /> : <CircleAlert size={12} />}</span><div><b>{step.label || step.agent || 'Topshiriq'}</b><small>{statuses[step.status] || 'Holat noma’lum'}</small></div></div>)}</div></details>;
}

export default function AssistantWorkspace({ session, onMessagesChange, filters, sourceName, connected, connectionLoading, understanding, understandingLoading, understandingError, onRefresh, onConnect, aiConfigured }) {
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [autoSpeak, setAutoSpeak] = useState(false);
  const [voiceEngine, setVoiceEngine] = useState('openai');
  const [voiceError, setVoiceError] = useState('');
  const [audioUrl, setAudioUrl] = useState('');
  const [audioBusy, setAudioBusy] = useState(false);
  const [copied, setCopied] = useState(null);
  const [mobileView, setMobileView] = useState('chat');
  const [selectedResultKey, setSelectedResultKey] = useState(null);
  const resultScroll = useRef(null);
  const alive = useRef(true), busyRef = useRef(false), request = useRef(null), lastPrompt = useRef('');
  const textarea = useRef(null), scrollArea = useRef(null), bottom = useRef(null), audioRef = useRef(null), urlRef = useRef('');
  const recognizer = useRef(null), recorder = useRef(null), mediaStream = useRef(null), recordingTimer = useRef(null);
  const recordingRef = useRef(false), uploadRecording = useRef(false), audioRequest = useRef(null), copyTimer = useRef(null);
  const messages = session.messages;
  const resultGroups = messages.map((message, index) => ({ message, key: message.id || `message-${index}`, index }))
    .filter(({ message }) => message.role === 'assistant' && message.artifacts?.length);
  const latestResultKey = resultGroups.at(-1)?.key;
  const selectedResult = resultGroups.find(result => result.key === selectedResultKey) || resultGroups.at(-1);
  const external = session.source === 'external';
  const prompts = external ? EXTERNAL_PROMPTS : PROMPTS;
  const unavailable = external && (!connected || connectionLoading || understandingLoading || !understanding || understanding.status === 'empty');
  const dataUnavailable = unavailable || (external && (understanding?.status !== 'ready' || understanding?.profile_complete === false));
  const append = message => onMessagesChange(session.id, previous => [...previous, { id: id(), createdAt: Date.now(), ...message }]);

  const stopPlayback = () => {
    audioRequest.current?.abort();
    audioRef.current?.pause();
    if (typeof window !== 'undefined') window.speechSynthesis?.cancel();
    setAudioBusy(false);
  };
  const stopRecording = (upload = false) => {
    uploadRecording.current = upload;
    clearTimeout(recordingTimer.current);
    if (upload) recognizer.current?.stop(); else recognizer.current?.abort();
    if (recorder.current?.state === 'recording') recorder.current.stop();
    mediaStream.current?.getTracks().forEach(track => track.stop());
    recordingRef.current = false;
    setListening(false);
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      request.current?.abort();
      audioRequest.current?.abort();
      uploadRecording.current = false;
      clearTimeout(recordingTimer.current);
      clearTimeout(copyTimer.current);
      recognizer.current?.abort();
      if (recorder.current?.state === 'recording') recorder.current.stop();
      mediaStream.current?.getTracks().forEach(track => track.stop());
      audioRef.current?.pause();
      if (typeof window !== 'undefined') window.speechSynthesis?.cancel();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);
  useEffect(() => {
    const target = textarea.current;
    if (target) { target.style.height = 'auto'; target.style.height = Math.min(target.scrollHeight, 160) + 'px'; }
  }, [input]);
  useEffect(() => { scrollArea.current?.scrollTo({ top: scrollArea.current.scrollHeight, behavior: 'smooth' }); }, [messages.length, busy]);
  useEffect(() => { setSelectedResultKey(null); }, [latestResultKey]);
  useEffect(() => { resultScroll.current?.scrollTo({ top: 0 }); }, [selectedResult?.key]);

  function openResult(key) {
    setSelectedResultKey(key);
    setMobileView('results');
  }

  async function speakAnswer(content) {
    stopPlayback();
    setVoiceError('');
    const text = String(content).replace(/[*#`]/g, '').slice(0, 2000);
    if (voiceEngine === 'browser') {
      if (!window.speechSynthesis) { setVoiceError('Brauzer ovozli javobni qo‘llamaydi. OpenAI ovozini tanlang.'); return; }
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'uz-UZ';
      window.speechSynthesis.speak(utterance);
      return;
    }
    const controller = new AbortController();
    audioRequest.current = controller;
    setAudioBusy(true);
    try {
      const blob = await platformApi.speech(text, { signal: controller.signal });
      if (!alive.current || controller.signal.aborted) return;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = URL.createObjectURL(blob);
      setAudioUrl(urlRef.current);
    } catch (error) {
      if (!controller.signal.aborted && alive.current) setVoiceError(errorText(error));
    } finally { if (alive.current && audioRequest.current === controller) setAudioBusy(false); }
  }
  useEffect(() => {
    if (audioUrl && audioRef.current) audioRef.current.play().catch(() => setVoiceError('Ovozni eshitish uchun audio pleerdagi ijro tugmasini bosing.'));
  }, [audioUrl]);

  async function send(text = input) {
    const content = String(text).trim();
    if (!content || busyRef.current || recordingRef.current || transcribing || unavailable) return;
    busyRef.current = true;
    setBusy(true);
    setInput('');
    lastPrompt.current = content;
    const controller = new AbortController();
    request.current = controller;
    const history = messages.filter(message => !message.error).slice(-8).map(message => ({ role: message.role, content: message.content.slice(0, 8000) }));
    append({ role: 'user', content });
    try {
      const conversationFilters = commandFilters(messages, filters, session.source);
      const response = await platformApi.command(content, history, conversationFilters, session.source, { signal: controller.signal }, external ? session.connection : undefined);
      if (!alive.current || controller.signal.aborted || request.current !== controller) return;
      const answer = typeof response.answer === 'string' && response.answer.trim() ? response.answer : 'Topshiriq yakunlandi. Natijalar tayyor.';
      append({ role: 'assistant', content: answer, mode: response.mode, steps: response.steps || [], artifacts: response.artifacts || [], sources: response.sources || [], suggestions: response.suggestions || [], filters: response.filters });
      if (autoSpeak) speakAnswer(answer);
    } catch (error) {
      if (!controller.signal.aborted && alive.current && request.current === controller) append({ role: 'assistant', content: errorText(error), error: true, retryPrompt: content });
    } finally {
      if (request.current === controller) { busyRef.current = false; request.current = null; if (alive.current) setBusy(false); }
    }
  }
  function cancelWait() {
    const controller = request.current;
    request.current = null;
    controller?.abort();
    busyRef.current = false;
    setBusy(false);
    append({ role: 'assistant', content: 'Javobni kutish to‘xtatildi. Serverda boshlangan amal hali yakunlanishi mumkin.', error: true });
  }

  async function toggleMic() {
    if (recordingRef.current) { stopRecording(true); return; }
    setVoiceError('');
    stopPlayback();
    if (voiceEngine === 'browser') {
      const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!Speech) { setVoiceError('Brauzer nutqni tanishni qo‘llamaydi. OpenAI ovozini tanlang yoki matn kiriting.'); return; }
      const recognition = new Speech();
      recognition.lang = 'uz-UZ';
      recognition.interimResults = false;
      recognition.onresult = event => { if (alive.current) { setInput(event.results[0][0].transcript.slice(0, 4000)); textarea.current?.focus(); } };
      recognition.onerror = event => { if (alive.current) { setVoiceError(event.error === 'not-allowed' ? 'Mikrofon ruxsati berilmadi. Brauzer sozlamalarida ruxsat bering yoki yozib yuboring.' : 'Ovoz tanilmadi. Qayta urinib ko‘ring.'); setListening(false); recordingRef.current = false; } };
      recognition.onend = () => { recordingRef.current = false; if (alive.current) setListening(false); };
      recognizer.current = recognition;
      try { recognition.start(); recordingRef.current = true; setListening(true); recordingTimer.current = setTimeout(() => stopRecording(true), 30000); } catch { setVoiceError('Mikrofonni ishga tushirib bo‘lmadi.'); }
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { setVoiceError('Bu brauzer audio yozishni qo‘llamaydi. Matn orqali davom eting yoki boshqa ovoz xizmatini tanlang.'); return; }
    recordingRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!alive.current || !recordingRef.current) { stream.getTracks().forEach(track => track.stop()); return; }
      mediaStream.current = stream;
      const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      const media = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorder.current = media;
      const chunks = [];
      media.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      media.onstop = async () => {
        clearTimeout(recordingTimer.current);
        stream.getTracks().forEach(track => track.stop());
        recordingRef.current = false;
        if (!alive.current) return;
        setListening(false);
        if (!uploadRecording.current || !chunks.length) return;
        setTranscribing(true);
        try {
          const type = media.mimeType || 'audio/webm';
          const response = await platformApi.transcribe(new Blob(chunks, { type }), type.includes('mp4') ? 'recording.m4a' : type.includes('ogg') ? 'recording.ogg' : 'recording.webm');
          if (alive.current) { setInput((response.text || response.transcript || '').slice(0, 4000)); textarea.current?.focus(); }
        } catch (error) { if (alive.current) setVoiceError(errorText(error)); }
        finally { if (alive.current) setTranscribing(false); }
      };
      media.start();
      uploadRecording.current = true;
      setListening(true);
      recordingTimer.current = setTimeout(() => stopRecording(true), 30000);
    } catch (error) {
      recordingRef.current = false;
      mediaStream.current?.getTracks().forEach(track => track.stop());
      if (alive.current) { setListening(false); setVoiceError(error.name === 'NotAllowedError' ? 'Mikrofon ruxsati berilmadi. Brauzer sozlamalarida ruxsat bering yoki yozib yuboring.' : 'Mikrofon topilmadi yoki band. Boshqa qurilma yoki matndan foydalaning.'); }
    }
  }
  async function copyAnswer(message) {
    try { await navigator.clipboard.writeText(message.content); setCopied(message.id); clearTimeout(copyTimer.current); copyTimer.current = setTimeout(() => setCopied(null), 2000); }
    catch { setVoiceError('Matnni nusxalab bo‘lmadi. Javob matnini belgilang va nusxalang.'); }
  }

  const isWelcome = messages.length === 0;
  const composer = <div className="aw-composer-area">
    {external && !connected && !connectionLoading && <div className="aw-connect-notice"><Database size={17} /><span>Suhbat bazasini qayta ulang yoki yangi suhbat oching.</span><button onClick={onConnect}>Baza ulash <ArrowUpRight size={13} /></button></div>}
    {voiceError && <div className="aw-voice-error" role="alert"><CircleAlert size={16} /><span>{voiceError}</span><button aria-label="Xabarni yopish" onClick={() => setVoiceError('')}><X size={15} /></button></div>}
    {audioBusy && <div className="aw-audio-status"><Loader2 size={14} className="spin" />Ovozli javob tayyorlanmoqda<button onClick={stopPlayback}>To‘xtatish</button></div>}
    {audioUrl && <div className="aw-audio-player"><Volume2 size={16} /><audio ref={audioRef} controls src={audioUrl} aria-label="Jarvis ovozli javobi" /><button aria-label="Ovozni yopish" onClick={() => { stopPlayback(); setAudioUrl(''); if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = ''; } }}><X size={16} /></button></div>}
    <form className={`aw-composer ${listening ? 'is-listening' : ''}`} onSubmit={event => { event.preventDefault(); send(); }}>
      {listening ? <div className="aw-recording"><span className="aw-recording-dot" /><span>Sizni tinglayapman...</span><span className="aw-wave"><i /><i /><i /><i /><i /><i /><i /></span><small>30 soniyagacha</small><button type="button" onClick={() => stopRecording(true)}>Tugatish</button></div> : <textarea ref={textarea} aria-label="Topshiriq yoki savolingiz" value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} placeholder={transcribing ? 'Ovozingiz matnga aylantirilmoqda...' : 'Savol yoki topshiriq yozing...'} rows={2} maxLength={4000} disabled={unavailable || transcribing} />}
      <div className="aw-composer-tools"><div className="aw-composer-left"><button type="button" className="aw-tool-button" onClick={onConnect} aria-label="Ma‘lumot bazasini ulash" title="Baza ulash"><Database size={17} /></button><span className="aw-tool-divider" /><details className="aw-voice-settings"><summary><Activity size={15} /><span>Jarvis</span><ChevronDown size={12} /></summary><div className="aw-voice-popover"><b>Ovozli yordamchi</b><label>Ovoz xizmati<select aria-label="Ovoz xizmati" disabled={listening || transcribing || audioBusy} value={voiceEngine} onChange={event => { stopPlayback(); setVoiceEngine(event.target.value); setVoiceError(''); }}><option value="openai">OpenAI</option><option value="browser">Brauzer</option></select></label><label className="aw-checkbox"><input type="checkbox" checked={autoSpeak} onChange={event => { setAutoSpeak(event.target.checked); stopPlayback(); }} /><span>Javobni ovozda o‘qish</span></label><p>Ovozli javob AI tomonidan yaratiladi. Yozuvdan keyin matnni tekshirib yuboring.</p></div></details></div><div className="aw-composer-right"><span className="aw-enter-hint">Enter <span>↵</span></span><button type="button" className={`aw-mic-button ${listening ? 'is-recording' : ''}`} aria-label={listening ? 'Ovoz yozuvini tugatish' : 'Ovozli buyruq berish'} title={listening ? 'Yozuvni tugatish' : 'Ovozli buyruq'} onClick={toggleMic} disabled={busy || transcribing || unavailable}>{transcribing ? <Loader2 size={19} className="spin" /> : listening ? <MicOff size={19} /> : <Mic size={19} />}</button>{busy ? <button type="button" className="aw-send-button" onClick={cancelWait} aria-label="Javobni kutishni to‘xtatish"><Square size={15} fill="currentColor" /></button> : <button type="submit" className="aw-send-button" aria-label="Topshiriqni yuborish" disabled={!input.trim() || listening || transcribing || unavailable}><ArrowUp size={21} /></button>}</div></div>
    </form>
  </div>;

  return <section className={`aw-workspace aw-split-workspace ${isWelcome ? 'is-welcome' : 'has-messages'}`} data-mobile-view={mobileView} aria-label="AI ish maydoni">
    <nav className="aw-view-switch" aria-label="Ish maydoni ko‘rinishi">
      <button type="button" aria-pressed={mobileView === 'chat'} aria-controls="aw-chat-panel" onClick={() => setMobileView('chat')}><Sparkles size={15} />Suhbat</button>
      <button type="button" aria-pressed={mobileView === 'results'} aria-controls="aw-results-panel" onClick={() => setMobileView('results')}><BarChart3 size={15} />Natijalar{resultGroups.length > 0 && <span>{resultGroups.length}</span>}</button>
    </nav>
    <section className="aw-chat-pane" id="aw-chat-panel" aria-label="AI bilan suhbat">
      <header className="aw-pane-header"><span className="aw-pane-title"><Sparkles size={16} /><h2>Suhbat</h2></span><span className="aw-chat-source" title={sourceName}>{sourceName}</span></header>
      <div className="aw-scroll" ref={scrollArea}>
      {isWelcome ? <div className="aw-welcome"><div className="aw-hero-emblem"><Sparkles size={28} strokeWidth={1.5} /></div><h1>Nimani <span>tahlil qilamiz?</span></h1><div className="aw-prompt-grid">{prompts.map(prompt => <button key={prompt.title} onClick={() => send(prompt.prompt)} disabled={busy || unavailable || (dataUnavailable && prompt.requiresData !== false)}><span className="aw-prompt-icon"><prompt.icon size={19} strokeWidth={1.7} /></span><b>{prompt.title}</b><ArrowUpRight className="aw-prompt-arrow" size={13} /></button>)}</div></div> : <div className="aw-thread">
        {messages.map((message, index) => <article key={message.id || index} className={`aw-message aw-message-${message.role} ${message.error ? 'is-error' : ''}`}>
          <div className="aw-message-avatar">{message.role === 'user' ? 'S' : <Sparkles size={18} />}</div>
          <div className="aw-message-body"><div className="aw-message-heading"><b>{message.role === 'user' ? 'Siz' : 'TabloAI'}</b>{message.mode === 'demo' && <small>Namuna rejimi</small>}</div>
            {message.role === 'user' ? <div className="aw-user-text">{message.content}</div> : <><ExecutionTrace steps={message.steps} /><div className="aw-answer"><MarkdownRenderer content={message.content} /></div>{message.artifacts?.length > 0 && <button type="button" className={`aw-open-result ${selectedResult?.key === (message.id || `message-${index}`) ? 'is-selected' : ''}`} aria-controls="aw-results-panel" onClick={() => openResult(message.id || `message-${index}`)}><BarChart3 size={16} /><span>Natijani ochish</span><small>{message.artifacts.length}</small><ArrowUpRight size={15} /></button>}{message.sources?.length > 0 && <div className="aw-message-sources"><Database size={12} /><span>{message.sources.map(source => typeof source === 'string' ? source : source.name || sourceName).filter(Boolean).join(' / ')}</span></div>}
            <div className="aw-message-actions">{message.retryPrompt ? <button disabled={busy} onClick={() => send(message.retryPrompt)}><RefreshCw size={14} />Qayta urinish</button> : !message.error && <><button aria-label="Javobni nusxalash" title="Nusxalash" onClick={() => copyAnswer(message)}>{copied === message.id ? <Check size={15} /> : <Copy size={15} />}</button><button aria-label="Javobni ovozda o‘qish" title="Ovozda o‘qish" disabled={audioBusy} onClick={() => speakAnswer(message.content)}><Volume2 size={16} /></button></> }</div>
            {index === messages.length - 1 && !busy && message.suggestions?.length > 0 && <div className="aw-followups">{message.suggestions.slice(0, 3).map((suggestion, item) => typeof suggestion === 'string' && <button key={item} onClick={() => send(suggestion)}>{suggestion}<ArrowRight size={13} /></button>)}</div>}</>}
          </div>
        </article>)}
        {busy && <div className="aw-working" role="status"><span className="aw-working-mark"><Sparkles size={18} /></span><div><b>Bajarilmoqda<span className="aw-working-dots"><i /><i /><i /></span></b><div className="aw-working-line" /></div></div>}
        <div ref={bottom} />
      </div>}
      </div>
      {composer}
    </section>
    <section className="aw-results-pane" id="aw-results-panel" aria-label="Tahlil natijalari">
      <header className="aw-pane-header"><span className="aw-pane-title"><BarChart3 size={16} /><h2>Natijalar</h2></span>{resultGroups.length > 0 && <div className="aw-result-select"><select aria-label="Ko‘rsatiladigan natija" value={selectedResult?.key || ''} onChange={event => setSelectedResultKey(event.target.value)}>{[...resultGroups].reverse().map(({ message, key, index }) => <option key={key} value={key}>{(messages.slice(0, index).findLast(item => item.role === 'user')?.content || message.artifacts[0]?.title || 'Natija').slice(0, 90)}</option>)}</select><ChevronDown size={13} /></div>}</header>
      <div className="aw-results-scroll" ref={resultScroll}>
        {external && (connected || connectionLoading) && <DatabaseUnderstanding profile={understanding} loading={connectionLoading || understandingLoading} error={understandingError} sourceName={sourceName} onRefresh={onRefresh} onReconnect={onConnect} onQuestion={question => { setMobileView('chat'); send(question); }} disabled={busy || dataUnavailable} compact={!!selectedResult} />}
        {selectedResult ? <div className="aw-result-content" key={selectedResult.key}><CommandArtifacts artifacts={selectedResult.message.artifacts} /></div> : <div className="aw-results-empty"><div className="aw-results-emblem"><BarChart3 size={32} strokeWidth={1.4} /><span><FileText size={16} /></span></div><h3>{busy ? 'Natija tayyorlanmoqda' : 'Natijalar shu yerda'}</h3>{busy ? <Loader2 size={18} className="spin" aria-label="Bajarilmoqda" /> : <p>Dashboard, hisobot va fayllar</p>}</div>}
      </div>
    </section>
  </section>;
}
