Credentials row — one per provider in the API Credentials panel.

```jsx
<ApiKeyInput placeholder="sk-ant-api03-..." onSave={validate} />
<ApiKeyInput defaultValue="sk-ant-xxxx" isStored onSave={()=>{}} onDelete={remove} />
<ApiKeyInput loading defaultValue="gsk_xxxx" onSave={()=>{}} />
```

- Key text is always mono and masked by default (eye toggles).
- Button flow: Validate (primary) → spinner → "Valid" (green soft tint + check). Delete appears only once stored.
