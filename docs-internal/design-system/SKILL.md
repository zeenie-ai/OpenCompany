---
name: opencompany-design
description: Use this skill to generate well-branded interfaces and assets for OpenCompany (opencompany.sh), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read `readme.md` within this bundle. For production, follow the live
`client/src/themes/`, existing React primitives and current frontend/theme
docs. Bundled CSS, component prompts and UI kits are prototype references;
they must not override shipped theme roles or application behavior.
If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.
If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

Key facts:
- OpenCompany is opencompany.sh's local-first AI workflow OS — a visual node canvas where users wire AI agents to email, WhatsApp, Android phones, browsers, and 50+ services.
- Link `styles.css` for all tokens; add `class="dark"` on `<html>` for the signature neutral-slate-dark + Dracula-neon look (the default for product UI).
- Color is semantic: action intents (run=green, stop=pink, save=cyan, config=orange, secret=yellow, tools=purple) and node roles (agent=purple, model=cyan, tool=green, trigger=pink, workflow=orange) — always as soft tints (8–15% fill, 30–60% border, full-strength text), never solid neon fills.
- Type: Geist (Google Fonts), 14px base, sentence case, terse copy; JetBrains Mono for counts/state/timestamps.
- Icons: Lucide only (stroke 2, currentColor); product node icons are colorful emoji-style glyphs.
- Reusable React components live in `components/` (with `.prompt.md` usage notes per component); a full interactive recreation of the app is in `ui_kits/opencompany/`.
