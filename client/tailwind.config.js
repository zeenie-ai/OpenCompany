/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      colors: {
        // Tokens are hex / color-mix (see client/src/index.css) — map the
        // CSS var directly, NO hsl() wrapper. Tailwind composes /opacity via
        // color-mix() regardless of the underlying color format.
        border: "var(--border)",
        input: "var(--input)",
        ring: "var(--ring)",
        background: "var(--background)",
        foreground: "var(--foreground)",
        primary: {
          DEFAULT: "var(--primary)",
          foreground: "var(--primary-foreground)",
        },
        secondary: {
          DEFAULT: "var(--secondary)",
          foreground: "var(--secondary-foreground)",
        },
        destructive: {
          DEFAULT: "var(--destructive)",
          foreground: "var(--destructive-foreground)",
        },
        muted: {
          DEFAULT: "var(--muted)",
          foreground: "var(--muted-foreground)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          foreground: "var(--accent-foreground)",
        },
        popover: {
          DEFAULT: "var(--popover)",
          foreground: "var(--popover-foreground)",
        },
        card: {
          DEFAULT: "var(--card)",
          foreground: "var(--card-foreground)",
        },
        success: "var(--success)",
        warning: "var(--warning)",
        info: "var(--info)",
        dracula: {
          green: "var(--dracula-green)",
          purple: "var(--dracula-purple)",
          pink: "var(--dracula-pink)",
          cyan: "var(--dracula-cyan)",
          red: "var(--dracula-red)",
          orange: "var(--dracula-orange)",
          yellow: "var(--dracula-yellow)",
          selection: "var(--dracula-selection)",
          "current-line": "var(--dracula-current-line)",
          comment: "var(--dracula-comment)",
        },
        // Role-based node tokens — base + soft (tinted bg) + border
        // (tinted outline). Themes redefine the underlying CSS vars.
        // Call sites use bg-node-X-soft / border-node-X-border directly,
        // never with /N opacity arithmetic.
        "node-agent":           "var(--node-agent)",
        "node-agent-soft":      "var(--node-agent-soft)",
        "node-agent-border":    "var(--node-agent-border)",
        "node-model":           "var(--node-model)",
        "node-model-soft":      "var(--node-model-soft)",
        "node-model-border":    "var(--node-model-border)",
        "node-skill":           "var(--node-skill)",
        "node-skill-soft":      "var(--node-skill-soft)",
        "node-skill-border":    "var(--node-skill-border)",
        "node-tool":            "var(--node-tool)",
        "node-tool-soft":       "var(--node-tool-soft)",
        "node-tool-border":     "var(--node-tool-border)",
        "node-trigger":         "var(--node-trigger)",
        "node-trigger-soft":    "var(--node-trigger-soft)",
        "node-trigger-border":  "var(--node-trigger-border)",
        "node-workflow":        "var(--node-workflow)",
        "node-workflow-soft":   "var(--node-workflow-soft)",
        "node-workflow-border": "var(--node-workflow-border)",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
