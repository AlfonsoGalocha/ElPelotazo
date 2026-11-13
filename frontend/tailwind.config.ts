import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0b0e14",
          raised: "#11151d",
          border: "#1f2530",
        },
        edge: {
          positive: "#22c55e",
          negative: "#ef4444",
          neutral: "#64748b",
        },
        // Amarillo para oportunidades destacadas (pedido de rediseño):
        // reservado para la "mejor senhal del dia" y badges de alta
        // prioridad, nunca para texto normal.
        opportunity: {
          DEFAULT: "#f59e0b",
          muted: "#78350f",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
