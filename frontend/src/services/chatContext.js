export const connectionKey = connection => connection?.connection_fingerprint || (connection ? `${connection.dialect}:${connection.database_name}` : '');

export function activateConnectedSource(state, connection) {
  const current = state.sessions.find(session => session.id === state.active);
  if (!connection || connection.is_demo || !current || current.source !== 'demo' || current.sourceExplicit || current.messages.length) return state;
  const updated = { ...current, source: 'external', connection: connectionKey(connection), databaseName: connection.database_name };
  return { ...state, sessions: state.sessions.map(session => session.id === current.id ? updated : session) };
}

export function commandFilters(messages, defaults, source) {
  const previous = [...messages].reverse().find(message => message.role === 'assistant' && !message.error && message.filters)?.filters;
  if (source === 'external') return Object.fromEntries(['start_date', 'end_date'].filter(key => previous?.[key]).map(key => [key, previous[key]]));
  return Object.fromEntries(['start_date', 'end_date', 'branch_id', 'category'].map(key => [key, previous && Object.hasOwn(previous, key) ? previous[key] : defaults[key]]));
}
