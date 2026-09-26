# Browser Harness — historical integration

The separate `browserHarness` node and its external-Chrome integration have been retired. The supported node and agent tool are both named `browser`.

Current documentation:

- [Native browser architecture](browser.md): managed Chrome, persistent profiles, installation, CDP, policy and runtime lifecycle.
- [Browser node flow](node-logic-flows/web_automation/browser.md): operations, operator settings, dispatch, outputs and legacy migration.
- [Browser workspace](browser_workspace.md): live viewing, frame delivery, direct input and human control.

The current runtime manages its own Chrome rather than requiring an already running, logged-in personal Chrome. Agent/workflow operations use generated scripts through the isolated, pinned browser-use CLI and its persistent daemon; WebMCP and live viewer capture/control have native CDP paths. The presence of browser-harness machinery inside that dependency is separate from the retired OpenCompany node.

[Saved-workflow migration](../server/services/workflow_migrations.py) renames `browserHarness` to `browser` and maps `goto` to `navigate`, `js` to `evaluate`, `doctor` to `diagnose`, and `tabs` to tab listing. Existing Python code is retained with a helper-compatibility warning. `evaluate` and `run_python` are now workflow-only; duplicate Browser tool edges created by migration are removed with a warning. See the node flow for complete parameter compatibility behavior.

Do not use the old service paths, installation commands, remote-debugging setup or Python helper examples from this document's history as current setup instructions. Runtime dependency pins are maintained in [`server/config/browser_runtime.json`](../server/config/browser_runtime.json).
