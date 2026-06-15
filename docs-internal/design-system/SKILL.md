---
name: machinaos-design
description: Use this skill to generate well-branded interfaces and assets for MachinaOS (zeenie.ai), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.
If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.
If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

Key facts:
- MachinaOS is zeenie.ai's local-first AI workflow OS — a visual node canvas where users wire AI agents to email, WhatsApp, Android phones, browsers, and 50+ services.
- Link `styles.css` for all tokens; add `class="dark"` on `<html>` for the signature Solarized-dark + Dracula-neon look (the default for product UI).
- Color is semantic: action intents (run=green, stop=pink, save=cyan, config=orange, secret=yellow, tools=purple) and node roles (agent=purple, model=cyan, tool=green, trigger=pink, workflow=orange) — always as soft tints (8–15% fill, 30–60% border, full-strength text), never solid neon fills.
- Type: Geist (Google Fonts), 14px base, sentence case, terse copy; JetBrains Mono for counts/state/timestamps.
- Icons: Lucide only (stroke 2, currentColor); product node icons are colorful emoji-style glyphs.
- Reusable React components live in `components/` (with `.prompt.md` usage notes per component); a full interactive recreation of the app is in `ui_kits/machinaos/`.
