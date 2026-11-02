> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Range control — compaction ratio, thresholds. Pair with a bold accent % readout.

```jsx
<Slider min={5} max={95} step={5} defaultValue={80} color="var(--node-model)" onChange={setRatio} />
```

- Show min/center/max tick labels below in 10px muted text when range context matters.
