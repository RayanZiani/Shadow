/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: { montserrat: ["Montserrat", "sans-serif"] },
      colors: {
        ink: "#261E48",
        page: "#E4E6F3",
        card: "#A1A6D4",
        emerald: "#10B981",
        crimson: "#DC143C",
      },
    },
  },
  plugins: [],
};
