// One source of truth for the icon and the current page's appearance.
// A full page load always starts in light mode; switching lasts until refresh.
// Apply the document change during the click, before React paints subscribers.
const listeners = new Set();
const browser = typeof window !== 'undefined' && typeof document !== 'undefined';
let currentTheme = 'light';

function apply(theme) {
  currentTheme = theme;
  if (browser) {
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.classList.toggle('dark', theme === 'dark');
    root.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#080f20' : '#f4f7fd');
  }
  listeners.forEach(listener => listener());
}

export function getAppearance() { return currentTheme; }
export function getServerAppearance() { return 'light'; }
export function subscribeAppearance(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function toggleAppearance() {
  apply(currentTheme === 'dark' ? 'light' : 'dark');
}

if (browser) {
  apply(currentTheme);
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    listeners.clear();
  });
}
