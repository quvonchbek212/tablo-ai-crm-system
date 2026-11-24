import React, { useSyncExternalStore } from 'react';
import { Moon, Sun } from 'lucide-react';
import { getAppearance, getServerAppearance, subscribeAppearance, toggleAppearance } from '../appearance';

export default function ThemeToggle() {
  const theme = useSyncExternalStore(subscribeAppearance, getAppearance, getServerAppearance);
  const dark = theme === 'dark';
  const label = dark ? 'Kunduzgi rejimni yoqish' : 'Tungi rejimni yoqish';

  return (
    <button type="button" className="theme-toggle" onClick={toggleAppearance} aria-label={label} aria-pressed={dark}>
      {dark ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}
