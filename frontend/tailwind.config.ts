import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        hazard: {
          'wildfire-very-high': '#dc2626',
          'wildfire-high': '#f97316',
          'wildfire-moderate': '#facc15',
          'flood-sfha': '#3b82f6',
          'flood-x-shaded': '#93c5fd',
          'flood-x': '#475569',
          tsunami: '#a855f7',
          none: '#64748b',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};

export default config;
