import axios from 'axios';

const API_BASE = '/api';

const client = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 300000, // 5 minutes for comprehensive LLM processing and execution
});

export const api = {
  getHealth: async () => {
    const res = await client.get('/health');
    return res.data;
  },

  connectDatabase: async (connData) => {
    const res = await client.post('/database/connect', connData);
    return res.data;
  },

  getSchema: async (forceRefresh = false) => {
    const res = await client.get(`/database/schema${forceRefresh ? '?force_refresh=true' : ''}`);
    return res.data;
  },

  getUnderstanding: async () => (await client.get('/database/understanding')).data,

  getSemanticCatalog: async () => {
    const res = await client.get('/semantic-catalog');
    return res.data;
  },

  updateSemanticCatalog: async (catalog) => {
    const res = await client.put('/semantic-catalog', catalog);
    return res.data;
  },

  generateDashboard: async (forceRefresh = false) => {
    const res = await client.post(`/dashboard/generate${forceRefresh ? '?force_refresh=true' : ''}`);
    return res.data;
  },

  queryChat: async (prompt, history = [], options = {}) => {
    const res = await client.post('/query/chat', { prompt, history }, options);
    return res.data;
  },
};

const cleanParams = (params = {}) => Object.fromEntries(Object.entries(params).filter(([, value]) => value !== '' && value !== null && value !== undefined));
export const platformApi = {
  overview: async (filters) => (await client.get('/platform/overview', { params: cleanParams(filters) })).data,
  inventory: async (filters) => (await client.get('/platform/inventory', { params: cleanParams(filters) })).data,
  sources: async () => (await client.get('/platform/sources')).data,
  agents: async () => (await client.get('/platform/agents')).data,
  runAgent: async (id, filters) => (await client.post(`/platform/agents/${id}/run`, cleanParams(filters))).data,
  reports: async () => (await client.get('/platform/reports')).data,
  createReport: async (filters) => (await client.post('/platform/reports', cleanParams(filters))).data,
  report: async (id) => (await client.get(`/platform/reports/${id}`)).data,
  chat: async (prompt, history, filters, options = {}) => (await client.post('/platform/chat', { prompt, history, ...cleanParams(filters) }, options)).data,
  command: async (prompt, history = [], filters = {}, source = 'demo', options = {}, expectedConnectionFingerprint = null) => (await client.post('/platform/command', { prompt, history, source, ...cleanParams(filters), ...(expectedConnectionFingerprint ? { expected_connection_fingerprint: expectedConnectionFingerprint } : {}) }, options)).data,
  transcribe: async (blob, filename) => { const form = new FormData(); form.append('file', blob, filename); return (await client.post('/platform/voice/transcribe', form, { headers: { 'Content-Type': undefined }, timeout: 120000 })).data; },
  speech: async (text, options = {}) => { try { return (await client.post('/platform/voice/speech', { text }, { ...options, responseType: 'blob', timeout: 120000 })).data; } catch (error) { if (error.response?.data instanceof Blob) { try { error.response.data = JSON.parse(await error.response.data.text()); } catch {} } throw error; } },
};
