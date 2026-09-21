/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 暗色金融风底色
        ink: {
          900: '#070a10',
          800: '#0b0e14',
          700: '#11151f',
          600: '#161b27',
          500: '#1d2431',
          400: '#2a3342',
        },
        muted: '#8b95a7',
        // 中国习惯：涨红跌绿
        up: '#f2385a',
        upSoft: 'rgba(242, 56, 90, 0.14)',
        down: '#12b981',
        downSoft: 'rgba(18, 185, 129, 0.14)',
        flat: '#94a3b8',
        accent: '#5b8cff',
        gold: '#f5c451',
      },
      fontFamily: {
        sans: [
          'Inter',
          'system-ui',
          '-apple-system',
          'PingFang SC',
          'Microsoft YaHei',
          'Noto Sans SC',
          'sans-serif',
        ],
        mono: ['JetBrains Mono', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        panel: '0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.6)',
      },
      borderRadius: {
        xl2: '14px',
      },
    },
  },
  plugins: [],
};
