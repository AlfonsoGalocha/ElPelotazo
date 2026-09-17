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
      },
    },
  },
  plugins: [],
};

export default config;
