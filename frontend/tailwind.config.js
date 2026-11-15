/** Tailwind CSS v3 — rutas relativas a la raíz del proyecto (donde se ejecuta el CLI). */
module.exports = {
  darkMode: 'class',
  content: [
    './app/templates/**/*.html',
    './app/static/js/**/*.js',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      keyframes: {
        pop: {
          '0%':   { transform: 'scale(.92)', opacity: '0' },
          '60%':  { transform: 'scale(1.03)', opacity: '1' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
        slideUp: {
          '0%':   { transform: 'translateY(14px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        pulseRing: {
          '0%':   { boxShadow: '0 0 0 0 rgba(16, 185, 129, .45)' },
          '70%':  { boxShadow: '0 0 0 16px rgba(16, 185, 129, 0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(16, 185, 129, 0)' },
        },
      },
      animation: {
        pop: 'pop .28s cubic-bezier(.2,.9,.3,1.4) both',
        slideUp: 'slideUp .3s ease-out both',
        pulseRing: 'pulseRing 1.4s ease-out infinite',
      },
    },
  },
  plugins: [],
};
