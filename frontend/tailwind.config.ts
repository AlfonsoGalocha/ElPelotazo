import type { Config } from "tailwindcss";

// Sistema de diseño "Bloomberg/trading terminal" (rediseño completo
// pedido por el usuario, nov-2026): paleta exacta que dio, centralizada
// aqui para que ningun componente reinvente un azul/morado parecido a
// ojo. `surface`/`edge`/`opportunity` se mantienen (muchos componentes ya
// los usan) pero remapeados a los valores nuevos; `accent` y `card` son
// los tokens nuevos del rediseño.
const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Fondo principal de la app (#080D19) y, dentro de el, dos
        // niveles de "superficie elevada": tarjeta (#101729) y elemento
        // secundario dentro de una tarjeta (#182238).
        surface: {
          DEFAULT: "#080D19",
          raised: "#101729",
          alt: "#182238",
          border: "#1f2a44",
        },
        card: {
          DEFAULT: "#101729",
          alt: "#182238",
        },
        // Morado electrico: acento de marca (CTAs, graficos, focos),
        // nunca para texto de cuerpo normal.
        accent: {
          DEFAULT: "#713CFF",
          muted: "#2a1f6b",
        },
        edge: {
          positive: "#20D995",
          negative: "#ef4444",
          neutral: "#64748b",
        },
        // Amarillo para oportunidades destacadas: reservado para la
        // "mejor senhal del dia" y badges de alta prioridad, nunca para
        // texto normal.
        opportunity: {
          DEFAULT: "#f59e0b",
          muted: "#78350f",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        ambient: "0 8px 30px -8px rgba(113, 60, 255, 0.12), 0 2px 8px -2px rgba(0, 0, 0, 0.4)",
        card: "0 1px 2px 0 rgba(0, 0, 0, 0.3)",
      },
    },
  },
  plugins: [],
};

export default config;
