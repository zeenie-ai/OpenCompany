> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Dialog for settings, credentials, confirmations. Scrim is flat rgba — never blurred.

```jsx
<Modal title="API Credentials" onClose={close} footer={<>
  <Button variant="outline" onClick={close}>Cancel</Button>
  <Button onClick={save}>Save</Button>
</>}>
  <Input type="password" placeholder="sk-ant-api03-..." />
</Modal>
```

- Titles are short noun phrases: "API Credentials", "Workflow Settings".
- Footer order: secondary left, primary right.
