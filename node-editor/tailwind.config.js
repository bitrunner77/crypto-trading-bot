/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0b0e13",
        panel: "#161b22",
        panel2: "#1c2230",
        edge: "#2a313d",
        accent: "#ff7a1a",
        accent2: "#ff9a3d",
        ink: "#e6e8eb",
        muted: "#8b94a3",
        header: {
          prompt: "#1f2530",
          image: "#1f2530",
          video: "#2563eb",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
