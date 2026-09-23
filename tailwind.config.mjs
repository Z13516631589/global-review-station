/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 暗色金融风底色（保留旧 ink 作为兜底，新增 surface 分层体系）
        ink: {
          900: '#070a10',
          800: '#0b0e14',
          700: '#11151f',
          600: '#161b27',
          500: '#1d2431',
          400: '#2a3342',
        },
        // 分层表面：页面 < 面板 < 卡片 < 凹陷
        surface: {
          0: '#121826', // 面板
          1: '#161d2e', // 抬升卡片
          2: '#0e1421', // 内嵌/凹陷
          3: '#0b101b', // 深底
        },
        line: '#243049', // 统一描边色（带蓝调，比中性灰更"科技"）
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
      fontSize: {
        eyebrow: ['11px', { lineHeight: '16px', letterSpacing: '0.12em' }],
        caption: ['12px', { lineHeight: '18px' }],
        body: ['14px', { lineHeight: '22px' }],
        section: ['15px', { lineHeight: '22px' }],
        h1: ['24px', { lineHeight: '32px' }],
        h2: ['28px', { lineHeight: '34px', letterSpacing: '-0.01em' }],
        display: ['32px', { lineHeight: '38px', letterSpacing: '-0.01em' }],
        'num-lg': ['26px', { lineHeight: '30px' }],
        'num-xl': ['32px', { lineHeight: '34px' }],
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
        panel: '0 1px 0 0 rgba(255,255,255,0.04) inset, 0 10px 30px -16px rgba(0,0,0,0.7)',
        soft: '0 8px 24px -16px rgba(0,0,0,0.6)',
        glow: '0 0 0 1px rgba(91,140,255,0.45), 0 10px 28px -10px rgba(91,140,255,0.45)',
        'up-glow': '0 0 18px -4px rgba(242,56,90,0.5)',
        'down-glow': '0 0 18px -4px rgba(18,185,129,0.45)',
      },
      backgroundImage: {
        grid:
          'linear-gradient(to right, rgba(255,255,255,0.022) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.022) 1px, transparent 1px)',
        'hero-glow':
          'radial-gradient(1100px 480px at 18% -30%, rgba(91,140,255,0.20), transparent 60%), radial-gradient(900px 380px at 100% 0%, rgba(242,56,90,0.10), transparent 55%)',
      },
      backgroundSize: {
        grid: '34px 34px',
      },
      borderRadius: {
        xl2: '14px',
        card: '16px',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-soft': {
          '0%,100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.5s ease both',
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
