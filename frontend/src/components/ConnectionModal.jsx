import React, { useState, useEffect, useRef } from 'react';
import { X, Database, Check, AlertCircle, FileCode, Sliders, Link2, Key, Loader2 } from 'lucide-react';
import { api } from '../services/api';

export default function ConnectionModal({ isOpen, onClose, onConnected }) {
  const [activeTab, setActiveTab] = useState('params'); // 'params', 'env', 'url'
  const [dbType, setDbType] = useState('postgresql');
  const [host, setHost] = useState('');
  const [port, setPort] = useState(5432);
  const [user, setUser] = useState('postgres');
  const [password, setPassword] = useState('');
  const [dbname, setDbname] = useState('');
  const [includeSchemas, setIncludeSchemas] = useState('public');
  const [connectionUrl, setConnectionUrl] = useState('');
  const [envText, setEnvText] = useState('');

  // SSH Tunnel states
  const [useSsh, setUseSsh] = useState(false);
  const [sshHost, setSshHost] = useState('');
  const [sshPort, setSshPort] = useState(22);
  const [sshUser, setSshUser] = useState('');
  const [sshPkeyPath, setSshPkeyPath] = useState('');
  const [sshPkeyPassphrase, setSshPkeyPassphrase] = useState('');
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);
  const inFlight = useRef(false);
  const dialog = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setSuccess(false);
    const previous = document.activeElement;
    const initial = dialog.current?.querySelector('button, input, textarea');
    initial?.focus();
    const handleKey = event => {
      if (event.key === 'Escape' && !inFlight.current) onCloseRef.current();
      if (event.key !== 'Tab') return;
      const controls = [...(dialog.current?.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]') || [])].filter(node => node.offsetParent !== null);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.removeEventListener('keydown', handleKey); previous?.focus?.(); };
  }, [isOpen]);

  if (!isOpen) return null;

  const handleEnvPaste = (text) => {
    setEnvText(text);
    // The pasted configuration is a complete input, not a patch to another source.
    setConnectionUrl('');
    setHost('');
    setPort(5432);
    setUser('postgres');
    setPassword('');
    setDbname('');
    setIncludeSchemas('public');
    setUseSsh(false);
    setSshHost('');
    setSshPort(22);
    setSshUser('');
    setSshPkeyPath('');
    setSshPkeyPassphrase('');
    const lines = text.split('\n');
    lines.forEach(line => {
      const match = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*)?\s*$/);
      if (match) {
        const key = match[1].trim().toUpperCase();
        let val = match[2] ? match[2].trim() : '';
        if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
          val = val.slice(1, -1);
        }

        // SSH configurations (KIMYO_DB_SSH_* and standard SSH_*)
        if (key === 'KIMYO_DB_SSH_HOST' || key === 'SSH_HOST') {
          setSshHost(val);
          if (val) setUseSsh(true);
        }
        if (key === 'KIMYO_DB_SSH_PORT' || key === 'SSH_PORT') {
          setSshPort(Number(val) || 22);
        }
        if (key === 'KIMYO_DB_SSH_USERNAME' || key === 'KIMYO_DB_SSH_USER' || key === 'SSH_USER' || key === 'SSH_USERNAME') {
          setSshUser(val);
          if (val) setUseSsh(true);
        }
        if (key === 'KIMYO_DB_SSH_PKEY_PATH' || key === 'SSH_PKEY_PATH') {
          setSshPkeyPath(val || '');
        }
        if (key === 'KIMYO_DB_SSH_PKEY_PASSPHRASE' || key === 'SSH_PKEY_PASSPHRASE') {
          setSshPkeyPassphrase(val);
        }

        // Remote database configurations (KIMYO_DB_* and standard DB_*)
        if (key === 'KIMYO_DB_REMOTE_HOST' || key === 'DB_HOST' || key === 'DB_REMOTE_HOST') {
          setHost(val);
        }
        if (key === 'KIMYO_DB_REMOTE_PORT' || key === 'DB_PORT' || key === 'DB_REMOTE_PORT') {
          setPort(Number(val) || 5432);
        }
        if (key === 'KIMYO_DB_NAME' || key === 'DB_NAME') {
          setDbname(val);
          setDbType('postgresql');
        }
        if (key === 'KIMYO_DB_USER' || key === 'DB_USER') {
          setUser(val);
        }
        if (key === 'KIMYO_DB_PASSWORD' || key === 'DB_PASSWORD') {
          setPassword(val);
        }
        if (key === 'DB_INCLUDE_SCHEMAS') {
          setIncludeSchemas(val);
        }
        if (key === 'DATABASE_URL') {
          setConnectionUrl(val);
        }
      }
    });
  };

  const handleConnect = async (e) => {
    e.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    setError(null);
    setSuccess(false);

    try {
      let payload;
      if (activeTab === 'url' || (activeTab === 'env' && connectionUrl.trim())) {
        payload = { connection_url: connectionUrl, include_schemas: includeSchemas };
      } else {
        payload = {
          db_type: dbType,
          host: host || '127.0.0.1',
          port: Number(port) || 5432,
          user: user || 'postgres',
          password,
          dbname,
          include_schemas: includeSchemas,
          use_ssh: useSsh,
          ssh_host: sshHost,
          ssh_port: Number(sshPort) || 22,
          ssh_user: sshUser,
          ssh_pkey_path: sshPkeyPath,
          ssh_pkey_passphrase: sshPkeyPassphrase,
        };
      }

      const result = await api.connectDatabase(payload);
      setSuccess(true);
      setPassword('');
      setSshPkeyPassphrase('');
      setConnectionUrl('');
      setEnvText('');
      onConnected(result);
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Ulanishda xatolik yuz berdi.');
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fadeIn">
      <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="connection-dialog-title" className="w-full max-w-lg connection-dialog bg-white dark:bg-[#101d34] text-slate-800 dark:text-slate-100 rounded-2xl border border-slate-300 dark:border-slate-700 p-6 shadow-2xl relative max-h-[90vh] overflow-y-auto">
        
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-slate-200 dark:border-slate-800">
          <div className="flex items-center space-x-2">
            <Database className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            <h3 id="connection-dialog-title" className="font-bold text-slate-900 dark:text-white text-base">Baza ulash</h3>
          </div>
          <button
            onClick={onClose}
            disabled={loading}
            aria-label="Ulanish oynasini yopish"
            className="text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition p-1 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Mode Selector Tabs */}
        <div className="mt-4 flex rounded-lg bg-slate-100 dark:bg-slate-900/80 p-1 border border-slate-200 dark:border-slate-800 text-xs">
          <button
            type="button"
            onClick={() => setActiveTab('params')}
            className={`flex-1 flex items-center justify-center space-x-1.5 py-1.5 rounded-md font-medium transition ${
              activeTab === 'params' ? 'bg-blue-600 text-white shadow' : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
            }`}
          >
            <Sliders className="w-3.5 h-3.5" />
            <span>Parametrlar</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('env')}
            className={`flex-1 flex items-center justify-center space-x-1.5 py-1.5 rounded-md font-medium transition ${
              activeTab === 'env' ? 'bg-blue-600 text-white shadow' : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
            }`}
          >
            <FileCode className="w-3.5 h-3.5" />
            <span>.env</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('url')}
            className={`flex-1 flex items-center justify-center space-x-1.5 py-1.5 rounded-md font-medium transition ${
              activeTab === 'url' ? 'bg-blue-600 text-white shadow' : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
            }`}
          >
            <Link2 className="w-3.5 h-3.5" />
            <span>URL</span>
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleConnect} className="mt-4 space-y-3.5">
          
          {activeTab === 'env' && (
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                .env konfiguratsiyasi
              </label>
              <textarea
                value={envText}
                onChange={(e) => handleEnvPaste(e.target.value)}
                placeholder={`SSH_HOST=192.168.1.100\nSSH_PORT=22\nSSH_USERNAME=ubuntu\nDB_REMOTE_HOST=127.0.0.1\nDB_REMOTE_PORT=5432\nDB_NAME=company_db\nDB_USER=postgres\nDB_PASSWORD=secret`}
                rows={6}
                className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg p-2.5 text-xs text-emerald-700 dark:text-emerald-400 font-mono focus:outline-none focus:border-blue-500 placeholder-slate-500 dark:placeholder-slate-600"
              />
            </div>
          )}

          {activeTab === 'url' && (
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                Ulanish URL manzili
              </label>
              <input
                type="text"
                value={connectionUrl}
                onChange={(e) => setConnectionUrl(e.target.value)}
                placeholder="postgresql://user:password@localhost:5432/dbname"
                className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 font-mono"
                required
              />
              <div className="mt-3">
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                  Sxemalar
                </label>
                <input
                  type="text"
                  value={includeSchemas}
                  onChange={(e) => setIncludeSchemas(e.target.value)}
                  placeholder="public"
                  className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 font-mono"
                />
              </div>
            </div>
          )}

          {activeTab !== 'url' && (
            <>
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                  Baza turi
                </label>
                <select
                  value={dbType}
                  onChange={(e) => setDbType(e.target.value)}
                  className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500"
                >
                  <option value="postgresql">PostgreSQL</option>
                  <option value="sqlite">SQLite</option>
                </select>
              </div>

              {dbType === 'postgresql' && (
                <div className="grid grid-cols-3 gap-3">
                  <div className="col-span-2">
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                      Server manzili
                    </label>
                    <input
                      type="text"
                      value={host}
                      onChange={(e) => setHost(e.target.value)}
                      placeholder="192.168.1.7 yoki localhost"
                      className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 font-mono"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                      Port
                    </label>
                    <input
                      type="number"
                      value={port}
                      onChange={(e) => setPort(e.target.value)}
                      placeholder="5432"
                      className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 font-mono"
                      required
                    />
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    {dbType === 'sqlite' ? 'Fayl nomi' : 'Baza nomi'}
                  </label>
                  <input
                    type="text"
                    value={dbname}
                    onChange={(e) => setDbname(e.target.value)}
                    placeholder="my_database"
                    className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 font-mono"
                    required
                  />
                </div>
                {dbType === 'postgresql' && (
                  <div>
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                      Foydalanuvchi
                    </label>
                    <input
                      type="text"
                      value={user}
                      onChange={(e) => setUser(e.target.value)}
                      placeholder="postgres"
                      className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 font-mono"
                      required
                    />
                  </div>
                )}
              </div>

              {dbType === 'postgresql' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                      Parol
                    </label>
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                      Sxemalar
                    </label>
                    <input
                      type="text"
                      value={includeSchemas}
                      onChange={(e) => setIncludeSchemas(e.target.value)}
                      placeholder="public yoki public,sales"
                      className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 font-mono"
                    />
                  </div>
                </div>
              )}

              {/* SSH Tunnel Options Toggle */}
              {dbType === 'postgresql' && (
                <div className="pt-3 border-t border-slate-200 dark:border-slate-800">
                  <div className="flex items-center justify-between mb-2">
                    <label className="flex items-center space-x-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={useSsh}
                        onChange={(e) => setUseSsh(e.target.checked)}
                        className="w-4 h-4 rounded text-blue-600 bg-slate-50 dark:bg-slate-900 border-slate-300 dark:border-slate-700 focus:ring-0 focus:ring-offset-0 cursor-pointer"
                      />
                      <span className="text-xs font-semibold text-blue-700 dark:text-blue-300 flex items-center space-x-1.5">
                        <Key className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
                        <span>SSH orqali ulanish</span>
                      </span>
                    </label>
                  </div>

                  {useSsh && (
                    <div className="mt-2.5 p-3 rounded-xl bg-blue-50 dark:bg-blue-950/20 border border-blue-500/20 space-y-2.5 animate-fadeIn">
                      <div className="grid grid-cols-3 gap-2">
                        <div className="col-span-2">
                          <label className="block text-[11px] font-medium text-slate-700 dark:text-slate-300 mb-0.5">
                            SSH serveri
                          </label>
                          <input
                            type="text"
                            value={sshHost}
                            onChange={(e) => setSshHost(e.target.value)}
                            placeholder="192.168.1.100 yoki vpn.server.uz"
                            className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-900 dark:text-white font-mono focus:outline-none focus:border-blue-500"
                            required={useSsh}
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-medium text-slate-700 dark:text-slate-300 mb-0.5">
                            SSH porti
                          </label>
                          <input
                            type="number"
                            value={sshPort}
                            onChange={(e) => setSshPort(e.target.value)}
                            placeholder="22"
                            className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-900 dark:text-white font-mono focus:outline-none focus:border-blue-500"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-[11px] font-medium text-slate-700 dark:text-slate-300 mb-0.5">
                            SSH foydalanuvchisi
                          </label>
                          <input
                            type="text"
                            value={sshUser}
                            onChange={(e) => setSshUser(e.target.value)}
                            placeholder="ubuntu / root / admin"
                            className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-900 dark:text-white font-mono focus:outline-none focus:border-blue-500"
                            required={useSsh}
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-medium text-slate-700 dark:text-slate-300 mb-0.5">
                            SSH kaliti yo‘li
                          </label>
                          <input
                            type="text"
                            value={sshPkeyPath}
                            onChange={(e) => setSshPkeyPath(e.target.value)}
                            placeholder="C:\Users\user\.ssh\tabloai_key"
                            className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-900 dark:text-white font-mono focus:outline-none focus:border-blue-500"
                            required={useSsh}
                          />
                        </div>
                      </div>

                      <div>
                        <label className="block text-[11px] font-medium text-slate-700 dark:text-slate-300 mb-0.5">
                          Kalit paroli (ixtiyoriy)
                        </label>
                        <input
                          type="password"
                          value={sshPkeyPassphrase}
                          onChange={(e) => setSshPkeyPassphrase(e.target.value)}
                          placeholder="••••••••"
                          className="w-full bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-blue-500"
                        />
                      </div>
                      <p className="text-[10px] text-slate-600 dark:text-slate-400">
                        Loyiha tashqarisidagi kalitning to‘liq yo‘lini kiriting.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {loading && <div className="aw-connection-learning" role="status"><Loader2 size={19} className="spin" /><div><b>Baza o‘rganilmoqda</b></div></div>}

          {error && (
            <div className="flex items-center space-x-2 text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 p-2.5 rounded-lg border border-rose-200 dark:border-rose-500/20 text-xs">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <p className="break-all">{error}</p>
            </div>
          )}

          {success && (
            <div className="flex items-center space-x-2 text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40 p-2.5 rounded-lg border border-emerald-200 dark:border-emerald-500/20 text-xs">
              <Check className="w-4 h-4 shrink-0" />
              <p>Baza ulandi.</p>
            </div>
          )}

          <div className="pt-2 flex justify-end space-x-2">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs font-medium text-slate-700 dark:text-slate-300 transition"
            >
              Bekor qilish
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold disabled:opacity-50 transition"
            >
              {loading ? 'Kuting...' : 'Ulash'}
            </button>
          </div>

        </form>

      </div>
    </div>
  );
}
