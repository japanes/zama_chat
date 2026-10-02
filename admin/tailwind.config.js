import colors from 'tailwindcss/colors'

// The factory dashboard palette: slate re-cut in the brand navy at the dark end,
// primary / secondary from the brand gradient (#EC6EAD → #3494E6).
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './components/**/*.{js,vue,ts}',
    './layouts/**/*.vue',
    './pages/**/*.vue',
    './plugins/**/*.{js,ts}',
    './composables/**/*.{js,ts}',
    './utils/**/*.{js,ts}',
    './app.vue',
    './error.vue',
  ],
  theme: {
    extend: {
      colors: {
        slate: {
          ...colors.slate,
          700: '#30385a',
          800: '#222a4f',
          900: '#141b3b',
          950: '#0c1022',
        },
        primary: { DEFAULT: '#3494E6', light: '#5DA9EC', dark: '#1C68AB' },
        secondary: { DEFAULT: '#EC6EAD', light: '#F3A5CC', dark: '#C94B8C' },
        accent: { DEFAULT: '#9081CA', light: '#B3A8DC', dark: '#6E5FA8' },
      },
      fontFamily: {
        heading: ['Plus Jakarta Sans', 'system-ui', 'sans-serif'],
        body: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      borderRadius: {
        card: '12px',
        button: '8px',
        input: '8px',
        modal: '16px',
        badge: '9999px',
      },
      boxShadow: {
        card: '0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px -1px rgba(0, 0, 0, 0.1)',
        'card-hover': '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1)',
        modal: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1)',
      },
    },
  },
  plugins: [],
}
