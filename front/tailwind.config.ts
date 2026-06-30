import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  "#fff1f1",
          100: "#ffe0e0",
          200: "#ffc5c5",
          300: "#ff9a9a",
          400: "#ff5f5f",
          500: "#ff2c2c",
          600: "#ed1313",
          700: "#c80a0a",
          800: "#a50c0c",
          900: "#881111",
          950: "#4a0404",
        },
        surface: {
          DEFAULT: "#080808",
          raised:    "#111111",
          elevated:  "#191919",
          border:    "#242424",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
      backdropBlur: {
        xs: "2px",
      },
    },
  },
  plugins: [],
};

export default config;
