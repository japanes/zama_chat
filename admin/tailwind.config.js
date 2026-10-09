import colors from 'tailwindcss/colors'

// The palette: slate re-cut in the brand navy (cool light surfaces, navy ink),
// primary / secondary from the brand gradient (#EC6EAD → #3494E6). Filled buttons and
// links use primary-dark: white text on #3494E6 is too pale to read.
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
          50: '#f4f5fa',
          100: '#e9ecf4',
          200: '#e2e5f0',
          300: '#c9cedf',
          400: '#9aa3c7',
          500: '#5c6485',
          600: '#454d70',
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
        heading: ['Onest', 'system-ui', 'sans-serif'],
        body: ['Onest', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      borderRadius: {
        card: '14px',
        button: '10px',
        input: '10px',
        modal: '16px',
        badge: '9999px',
      },
      boxShadow: {
        card: '0 1px 2px 0 rgba(20, 27, 59, 0.04)',
        'card-hover': '0 4px 10px -2px rgba(20, 27, 59, 0.1)',
        modal: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1)',
      },
    },
  },
  plugins: [],
}
