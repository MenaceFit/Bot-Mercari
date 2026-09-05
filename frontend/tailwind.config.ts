import type { Config } from 'tailwindcss';

/**
 * Palette « terminal japonais premium » : fond presque noir, encre
 * blanc/gris, un seul accent bleu électrique. Vert réservé au LIVE,
 * orange/rouge aux alertes — jamais décoratifs.
 *
 * Les couleurs de statut ne portent JAMAIS le sens seules : chaque état
 * est accompagné d'un libellé, d'une icône, ou des deux.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        void: '#050506',
        surface: '#0a0a0c',
        raised: '#101013',
        line: '#1c1c21',
        edge: '#26262d',
        ink: '#f4f4f5',
        muted: '#8b8b96',
        faint: '#5a5a64',
        accent: '#3b82f6',
        'accent-dim': '#1e3a8a',
        live: '#22c55e',
        warn: '#f59e0b',
        danger: '#ef4444',
        rare: '#a855f7',
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['10px', '14px'],
        xs: ['11px', '16px'],
      },
      animation: {
        'pulse-live': 'pulse-live 2s ease-in-out infinite',
        'scan-sweep': 'scan-sweep 3s linear infinite',
      },
      keyframes: {
        'pulse-live': {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '.45', transform: 'scale(.85)' },
        },
        'scan-sweep': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
      },
    },
  },
  plugins: [],
};
export default config;
