# Persistence and secrets

Status: current implementation. Source baseline: `56f0b397d0b313ec550791c1e2772525a9f7cf12`.

## Components and boundaries

Use three distinct regions: application persistence left, credential resolution center, runtime files right. Keep Application DB and Temporal history DB as separate cylinders with different owners. The separate credentials.db cylinder is encrypted; 1Password reference metadata lives in Application DB while secret values are fetched from external 1Password. Place workspaces, owner-local profiles and packages as distinct filesystem boxes. No arrows imply the model receives secrets or that every data store is encrypted.

- The resolved data root contains application SQLite, separate credentials.db, separate Temporal SQLite history, workflow workspaces, packages, daemon state and browser profiles. App PostgreSQL is supported when configured and required for distributed mode.
- Fernet field encryption protects local credential fields with a configured key. This does not establish encryption of application rows, filesystem contents or Temporal payload history.
- Saved 1Password enrollment/reference metadata is stored in application rows without secret values. Resolution reads the secret from the external source when needed.
- Distributed mode requires approved 1Password static-secret adapters and rejects unsupported OAuth/CLI-machine-local/inline credentials. Local credential behavior is not universally portable across replicas.
- Workspace identifiers/slugs and containment checks vary by handler; the diagram describes storage organization, not a universal sandbox guarantee. Known downloader/propagation limitations remain documented separately.
- Live browser profile/process state stays with its owner. Durable ownership metadata belongs to the shared application database, not the profile directory or credentials store.
- The optional external Gateway has its own state/credential plane. It does not replace the application database or credentials store.

## Exact labels and relationships

- `runtime`: **Application services / plugins** (service).
- `db`: **Application DB** (state).
- `history`: **Temporal history DB** (state).
- `temporal`: **Temporal server** (temporal).
- `auth`: **Credential resolution** (service).
- `credentials`: **Encrypted credentials.db** (state).
- `key`: **Configured encryption key** (state).
- `refs`: **Credential reference metadata** (state).
- `onepassword`: **1Password** (external).
- `workspace`: **Workflow workspaces** (state).
- `profiles`: **Owner-local browser profiles** (state).
- `packages`: **Managed packages / daemon state** (state).
- `external`: **Provider / integration APIs** (external).

- Application services / plugins → Application DB: application records.
- Application services / plugins → Temporal server: durable execution.
- Temporal server → Temporal history DB: workflow history.
- Application services / plugins → Credential resolution: resolve configured connection.
- Credential resolution → Encrypted credentials.db: read / write encrypted fields.
- Configured encryption key → Credential resolution: key derivation / decryption.
- Credential resolution → Credential reference metadata: binding lookup.
- Credential reference metadata → Application DB: metadata rows.
- Credential resolution → 1Password: resolve approved reference.
- Credential resolution → Application services / plugins: resolved credential.
- Application services / plugins → Provider / integration APIs: authenticated operation.
- Application services / plugins → Workflow workspaces: files / outputs / skills.
- Application services / plugins → Owner-local browser profiles: owned browser state.
- Application services / plugins → Managed packages / daemon state: managed runtime assets.

## Evidence

| Source | Implementation boundary |
|---|---|
| [server/core/paths.py](../../../server/core/paths.py) | data_path, workspaces_dir, workspace_dir, packages_dir and documented distinct state files. |
| [server/core/config.py](../../../server/core/config.py) | database_url local SQLite/explicit PostgreSQL and distributed prerequisites. |
| [server/core/database.py](../../../server/core/database.py) | Application SQLModel/SQLAlchemy async engine. |
| [server/services/temporal/_runtime.py](../../../server/services/temporal/_runtime.py) | Separate Temporal CLI history database. |
| [server/core/encryption.py](../../../server/core/encryption.py) | EncryptionService field-level Fernet/PBKDF2 behavior. |
| [server/services/auth.py](../../../server/services/auth.py) | AuthService using separate CredentialsDatabase and credential-source resolution. |
| [server/services/credentials/sources.py](../../../server/services/credentials/sources.py) | CredentialSources metadata-only transactions. |
| [server/services/credentials/onepassword.py](../../../server/services/credentials/onepassword.py) | External secret reference validation and resolution. |
| [server/nodes/browser/_profiles.py](../../../server/nodes/browser/_profiles.py) | Per-profile owner-local user-data directories. |
