# Credentials Test Suite

Covers local encryption/OAuth behavior and approved 1Password sources.
The current contract is [1Password credentials](../../../docs-internal/onepassword_credentials.md);
the [archived credentials panel](../../../docs-internal/ARCHIVE/credentials_panel.md)
records the original local-store invariants.

## Run

```bash
cd server
uv sync   # the dev dependency group (pytest, respx, ...) installs by default
uv run pytest tests/credentials/ -v

# Coverage report on the three critical modules
uv run pytest tests/credentials/ --cov=core.encryption --cov=core.credentials_database --cov=services.auth --cov-report=term-missing
```

## Layout

| File | Locks in |
|---|---|
| `test_encryption.py` | Invariant 13 — round-trip correctness, error paths, salt randomness |
| `test_credentials_database.py` | Invariants 3, 8 — two-table separation, encryption-at-rest, session/customer isolation |
| `test_auth_service.py` | Invariants 7, 8 — single point of access, memory cache behaviour, `clear_cache()` |
| `test_oauth_utils.py` | Invariant 12 — runtime redirect URI derivation (ws→http, wss→https) |
| `test_twitter_oauth.py` | Invariants 9, 10 — PKCE state single-use, code_challenge = base64url(sha256(verifier)) |
| `test_google_oauth.py` | Invariant 11 — `access_type=offline`, `prompt=consent` |
| `test_websocket_handlers.py` | Invariants 1, 5, 6 — WS message types, snake_case `has_key`, distinct provider-defaults handler |
| `test_credential_broadcasts.py` | Credential-change broadcast contract — every credential-mutation handler emits `update_api_key_status` and/or `broadcast_credential_event` |
| `test_credential_catalogue_shape.py` | `credential_providers.json` top-level shape and provider placement — every provider lives under `providers`, cross-checked against the registered credential classes |
| `test_openai_compatible_credential.py` | RFC-0003 AG15 — named endpoints against the real encrypted store: add (slug from the label, else the host and port, never userinfo; a duplicate label refused), refresh with the stored URL and key, the catalogue's `endpoints` / `stored` extras, delete of both rows, and `is_configured` for the workflow validator |
| `test_onepassword.py` | Metadata-only forms/catalogues, private CLI argument/output bounds, exact whitespace/Unicode, cancellation, authorization errors, per-call rotation, source ownership/revocation, supported static adapters and cluster fallback refusal |
| `test_onepassword_process_boundaries.py` | Bootstrap token exclusion after subprocess environment merges across CLI, PTY, code, services, events and installers |
| `test_onepassword_runtime_adapters.py` | Activity-local provider resolution and trusted principal propagation through model, employee, speech and other supported static callers |

## Fixtures

`conftest.py` provides:

- `encryption` / `uninitialized_encryption` — `EncryptionService` instances
- `credentials_db` — fresh on-disk SQLite per test in `tmp_path`, real Fernet
- `auth_service` — `AuthService` backed by the test DB

## What this suite does NOT test

- Real OAuth provider responses (`respx` mocks all upstream calls)
- The full FastAPI app boot (handlers are invoked directly)
- Native LLM SDK behaviour (covered by `tests/llm/`)
- Frontend `CredentialsModal` rendering (covered by `client/src/test/`)
- WhatsApp Go RPC binary
- Real 1Password desktop grants, vault reads, service-account permissions or platform installation; subprocess and provider fixtures use nonsecret test values

## Browser and deployment acceptance

Run `tests/nodes/browser/test_browser_credentials.py` for protected-login
targets, capture gates, private success checks and secret canaries. The opt-in
`test_browser_login_acceptance.py` uses installed Chrome and provisioned
browser-use against an isolated local fixture with fake credentials. It never
reads a vault or an external account. Set `BROWSER_ACCEPTANCE_LIVE=1` only
after provisioning the pinned CLI; supply `BROWSER_ACCEPTANCE_CLI` when it is
outside the fixture's default path.

Real PostgreSQL tests live in `integration_tests/`, separately from the
legacy database stubs under `tests/`. Native Temporal owner-routing/replay
fixtures are under `tests/temporal/test_browser_workspace_replay.py` and need
`TEMPORAL_TEST_CLI`. Commands, recorded results and remaining deployment
acceptance are in the [Browser deployment guide](../../../docs-internal/browser_agent_deployment.md#acceptance-and-release-gate).
