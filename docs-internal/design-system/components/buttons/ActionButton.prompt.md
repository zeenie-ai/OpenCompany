Soft-tinted toolbar button — the most recognizable MachinaOS pattern. Pick intent by meaning, never by color.

```jsx
<ActionButton intent="run"><Icon name="Play" size={12} /> Start</ActionButton>
<ActionButton intent="stop"><Icon name="Square" size={12} /> Stop</ActionButton>
<ActionButton intent="save" disabled>Save</ActionButton>
<ActionButton intent="config" iconOnly title="Settings"><Icon name="Settings" size={14} /></ActionButton>
```

- Intents: run (green), stop (pink), save (cyan), config (orange), secret (yellow / credentials), tools (purple / component palette).
- Labels are one word or a short verb phrase: Start, Stop, Save, Apply All.
- 32px tall; `iconOnly` makes it a 32px square for toolbar icon buttons.
