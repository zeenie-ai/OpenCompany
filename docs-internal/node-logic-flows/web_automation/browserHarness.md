# Browser Harness (`browserHarness`) — retired

`browserHarness` is no longer a registered node or agent tool. Use the current [Browser node flow](browser.md), [native browser architecture](../../browser.md), and [browser workspace](../../browser_workspace.md).

The replacement `browser` node launches OpenCompany-managed Chrome with persistent owned profiles. The live viewer and human takeover use the native CDP path. Most agent/workflow operations still use the pinned browser-use CLI and its daemon internally; this dependency does not restore the old node or its real-Chrome attachment model.

[Workflow migration](../../../server/services/workflow_migrations.py) rewrites saved `browserHarness` nodes to `browser`:

- `goto` becomes `navigate`; `js` becomes `evaluate`; `doctor` becomes `diagnose`.
- `tabs` becomes `tabs` with `tab_action=list`.
- Numeric `timeout` becomes `op_timeout_s`, clamped to 5–300 seconds when no new value is present.
- `run_python` code remains, with a warning to check changed helper names. Both `run_python` and `evaluate` are now workflow-only, not agent tool operations.
- If the same agent already has a Browser tool, migration removes the duplicate migrated tool edge with a warning.

The old browser-harness service, helper examples, local-Chrome configuration and installation instructions in earlier versions of this document are historical. Follow the current node schema and runtime documentation for new workflows.
