import type { Config } from 'tailwindcss';

/**
 * Palette sobre : un fond profond, deux niveaux de surface, une encre
 * lisible, un seul accent. Les couleurs de statut ne portent jamais le
 * sens seules — un libellé les accompagne toujours.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0b0c0e',
        card: '#141619',
        raised: '#1b1e22',
        line: '#24282e',
        ink: '#f2f4f7',
        muted: '#9aa2ad',
        faint: '#6b7280',
        accent: '#4f8cff',
        live: '#34d399',
        warn: '#fbbf24',
        danger: '#f87171',
        rare: '#c084fc',
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: { xl: '14px', '2xl': '18px' },
      keyframes: {
        in: { from: { opacity: '0', transform: 'translateY(-6px)' },
              to: { opacity: '1', transform: 'none' } },
        ping: { '0%': { opacity: '1', transform: 'scale(1)' },
                '75%,100%': { opacity: '0', transform: 'scale(2.4)' } },
      },
      animation: { in: 'in .25s ease-out', ping: 'ping 1.8s ease-out infinite' },
    },
  },
  plugins: [],
};
export default config;
