Radio list for exclusive choices (search provider, deploy mode).

```jsx
<RadioGroup defaultValue="duckduckgo" options={[
  { value: 'duckduckgo', label: 'DuckDuckGo (no key required)' },
  { value: 'brave', label: 'Brave Search' },
  { value: 'serper', label: 'Serper' },
]} onChange={setProvider} />
```
