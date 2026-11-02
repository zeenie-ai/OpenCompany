> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Radio list for exclusive choices (search provider, deploy mode).

```jsx
<RadioGroup defaultValue="duckduckgo" options={[
  { value: 'duckduckgo', label: 'DuckDuckGo (no key required)' },
  { value: 'brave', label: 'Brave Search' },
  { value: 'serper', label: 'Serper' },
]} onChange={setProvider} />
```
