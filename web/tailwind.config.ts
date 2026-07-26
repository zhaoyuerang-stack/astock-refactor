import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Apple Premium Dark style colors
        bg: "#000000",          // Apple pure black page background
        navy: "#1C1C1E",        // Apple dark secondary card background
        line: "#2C2C2E",        // Apple border color
        ink: "#F5F5F7",         // Apple primary active text
        subink: "#8E8E93",      // Apple secondary gray text
        weak: "#6E6E73",        // Apple muted gray text
        
        // State colors (SF Symbols colors)
        ok: "#30D158",          // Apple green
        warn: "#FF9F0A",        // Apple orange
        danger: "#FF453A",      // Apple red
        brand: "#0A84FF",       // Apple SF blue
        neutral: "#8E8E93",     // Apple gray
        
        // Keep compatibility aliases
        cardline: "#2C2C2E",    // Border color
        songshi: "#30D158",     // Green
        qielan: "#0A84FF",      // Blue
        taishi: "#FF9F0A",      // Yellow
      },
      borderRadius: { card: "12px" },
    },
  },
  plugins: [],
};

export default config;
