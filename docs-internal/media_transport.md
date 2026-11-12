# Media Transport — how files move through the engine

**Status:** shipped.
**Scope:** `server/services/media/`, `server/services/workspace_locator.py`,
`server/routers/workspace.py`, `nodes/filesystem/_backend.py`,
`nodes/filesystem/gallery/` (the second consumer of `preview.py`, and the
producer of `FileRef` listing rows).
**Companion:** [Speech Provider RFC](./speech_provider_rfc.md) — the first consumer.

One rule underneath everything here:

> **Media bytes do not travel through the workflow engine.** Nodes return a
> reference; the bytes stay on disk in the workflow's workspace.

This is not a style preference. It is forced by measured limits, and every API
in this module exists to make the correct thing the easy thing.

---

## 1. Why — the measured constraint

| Limit | Value | Where |
|---|---|---|
| Temporal blob **error** (activity result *and input*, workflow result) | **2,097,152 B** | server default; no custom DataConverter anywhere |
| Temporal blob **warning** | 524,288 B → `PayloadSizeWarning`, unfiltered | |
| Retries burned before failing | **none** since 1b.26: the SDK core fails an oversized result as `PayloadsTooLarge`, which is in `NON_RETRYABLE_ERROR_TYPES` (before, **3** attempts, each re-billing the producer) | [`_retry_policies.py`](../server/services/temporal/_retry_policies.py) |
| `node_outputs.data` | no cap, written **3×** per store | [`activities.py`](../server/services/temporal/activities.py) |
| WS broadcast | no size guard; payload retained in `_status` **forever** and replayed to every new client | [`status_broadcaster.py`](../server/services/status_broadcaster.py) |

A node result is not stored once. It is persisted three times, broadcast twice,
retained in the status cache, aggregated into the workflow result, copied into
every downstream activity's *input*, and — if the node is `usable_as_tool` —
serialized into an LLM message.

Historically, a 12 MB base64 TTS result could fail the Activity and burn
three attempts, re-billing its producer. Since 1b.26, `PayloadsTooLarge` is
non-retryable, so that oversized result fails without those additional
attempts. Returning contained file references remains required: disabling
retries does not make a large payload fit the engine.

A capped opt-in ("inline base64 up to N bytes") is worse than no opt-in,
because it makes the tool path *sometimes* catastrophic — which passes review.

---

## 2. `FileRef` / `AudioRef` — a reference, structurally

[`services/media/refs.py`](../server/services/media/refs.py). ~400 bytes
serialized, so thousands fit inside Temporal's *warning* threshold.

Two classes, not one. `FileRef` is the base and the honest default —
`kind="file"` asserts only that the thing exists in the workspace. A
kind-specific subclass is added only when a probe actually produces extra
metadata, which so far means audio alone.

```python
FileKind = Literal["file", "audio", "image", "video", "document"]

class FileRef(BaseModel):
    kind: FileKind = "file"
    path: str                      # workspace-RELATIVE POSIX, no leading slash
    workflow_id: Optional[str]     # immutable id — survives rename
    filename: str                  # display only, never used for resolution
    mime_type: str
    size_bytes: int
    modified_at: Optional[str]     # ISO 8601, advisory — not a cache key
    sha256: Optional[str]
    url: Optional[str]             # path-only, no scheme or host

    model_config = ConfigDict(extra="forbid")


class AudioRef(FileRef):
    """A FileRef whose container has actually been probed."""
    kind: Literal["audio"] = "audio"
    format: str                         # wav, mp3, opus, ...
    duration_seconds: Optional[float]   # None when unmeasurable; never guessed
    sample_rate: Optional[int]
    channels: Optional[int]

    model_config = ConfigDict(extra="forbid")
```

Three deliberate properties:

- **No bytes field, and `extra="forbid"`.** Adding one is a `ValidationError`,
  not a silent regression. A test asserts no `data` / `bytes` / `base64` /
  `content` field exists — the invariant is structural, not a convention
  someone has to remember.
- **`path` is workspace-relative.** An absolute path embeds the mutable
  workflow slug, leaks the operator's home directory into the database, the
  WebSocket broadcast and the LLM context, and cannot be safely turned into an
  HTTP URL.
- **`duration_seconds` is `None` rather than approximate** when it could not be
  measured. A fabricated duration silently mis-bills per-second providers.

### The id/slug asymmetry (read this before touching resolution)

Workspace directories are named by **`Workflow.slug`**, which changes on
rename — `WorkflowService._get_workspace_dir` creates them and
`workflow_storage/handlers.py` moves them. An `AudioRef` stores the
**immutable `workflow_id`** precisely so a reference keeps working across a
rename.

Converting one to the other needs a database read, so:

- `services.media` is **synchronous by contract** and never does the lookup.
- `workspace_root()` accepts an explicit `workspace_dir=` or a `ctx`, and
  **raises** when given only a `workflow_id` rather than guessing.
- The caller that *has* a database — the workspace HTTP route — does the
  lookup and passes the resolved directory in.

An earlier version composed `workspaces/<workflow_id>/` here. That path never
exists. It stayed invisible for a full wave because every caller happened to
supply a `ctx`; the HTTP route was the first that could not.

`core.paths.workspace_dir`'s parameter is named `workflow_slug` for the same
reason — it used to say `workflow_id` and misled exactly this way.

---

## 3. Containment

Everything funnels through `resolve_within(root, key)` in
[`nodes/filesystem/_backend.py`](../server/nodes/filesystem/_backend.py), which
applies two layers:

1. Reject `..`, leading `~`, and drive-prefixed input **before** touching the
   filesystem.
2. Resolve, then re-check containment with `relative_to(root)` — so a symlink
   or Windows junction cannot redirect the result outside the root.

It is module-level rather than a `WorkspaceBackend` method precisely because
`services.media` and the HTTP route both need it.

**This closed a live vulnerability.** The Sarvam speech-to-text node joined a
user-supplied path onto the workspace root with no check at all, so
`audio_file="../../credentials.db"` read the encrypted credential store and
uploaded it to a third-party provider. `coerce_file_param` closes that by
construction for every node that adopts it.

`whatsappSend` (and `socialSend` to WhatsApp) had the same hole until
2026-10-25: a `file_path` went to plain `open()`, so an agent could send any
readable host file to any number. It now reads through `coerce_file_param`
with the sending node's context.

---

## 4. The API

[`services/media/workspace.py`](../server/services/media/workspace.py)

| Function | Use |
|---|---|
| `write_audio(payload, *, ctx, stem, ext, …) -> AudioRef` | Atomic write into `<workspace>/audio/`. Filename is `<slug>-<node8>-<rand6>.<ext>`, so retries and repeated runs never collide or overwrite. `<node8>` is the node id's first eight characters through `core.paths.safe_path_component`, because canvas ids carry colons (`7:mobile:1`) and Windows refuses a colon in a file name. Probes the file and populates the metadata. |
| `write_media(payload, *, ctx, stem, ext, kind="file", mime_type=None, subdir="media") -> FileRef` | The kind-agnostic sibling of `write_audio`: same naming, containment and atomic write, into `<workspace>/<subdir>/`. Refuses `kind="audio"`, which would claim a probe that did not run. |
| `resolve_media(ref_or_path, *, ctx \| workspace_dir) -> Path` | Contained absolute path. Absolute inputs are tolerated for back-compat but still contained. |
| `read_media_bytes(...) -> (filename, bytes)` | `resolve_media` + non-empty and size checks. |
| `coerce_file_param(value, *, ctx) -> (filename, bytes)` | **The one nodes should call.** Accepts all three shapes the UI can produce. |
| `workspace_file_url(workflow_id, rel_path)` | Path-only URL for the HTTP route. No scheme, no host. |

`coerce_file_param` accepts:

1. A serialized `AudioRef` — what the upload route returns.
2. The legacy `{"type": "upload", "data": "<base64>"}` envelope the file widget
   used to emit. **Accepted indefinitely**: saved workflow rows carry it and
   are not migrated. Logs one warning naming the node so operators can find and
   re-save them.
3. A bare path string — typed, or dragged from an upstream node.

### Inspection never fails a workflow

[`services/media/inspect.py`](../server/services/media/inspect.py) falls back
`tinytag` → stdlib `wave` → raw-PCM arithmetic → empty probe, and **never
raises**. An unknown container yields an all-`None` probe and a DEBUG line.

Degrading a billing estimate is acceptable; hard-failing a valid file because a
metadata parser did not recognise a codec variant is not.

`tinytag` was chosen over mutagen (GPL-2.0 against this project's MIT — a
licensing decision, not a technical one) and over pydub / ffprobe, which both
need an ffmpeg binary on PATH that nothing installs on Windows.

### Limits

[`services/media/limits.py`](../server/services/media/limits.py) is the single
home for every size constant, each annotated with what it is defending against.
`MEDIA_MAX_UPLOAD_BYTES` is 25 MiB — deliberately far above the Temporal limit,
because uploaded bytes land on disk and only a reference enters a payload.

---

## 5. HTTP surface

[`routers/workspace.py`](../server/routers/workspace.py). A thin shell — it owns
no containment logic, no id → slug resolution
([`services/workspace_locator.py`](../server/services/workspace_locator.py)) and
no inline/attachment rule ([`services/media/preview.py`](../server/services/media/preview.py))
of its own.

**Listing is not here.** `list_workspace_files` (a WebSocket command owned by the
`gallery` plugin) is the listing channel; these HTTP routes are the *content*
channel. The consumer is the parameter panel, which already holds an
authenticated socket with request correlation — a second HTTP listing surface
would mean a second auth path and a second error envelope for no gain.

### `GET /api/workspace/{workflow_id}/files/{file_path:path}`

- id → slug via `workspace_locator.resolve_workspace_root` (reads keep the
  `"default"` fallback), then `resolve_within`.
- **404, never 403** — a distinct status would confirm the existence of files
  outside the workspace.
- **Range/seeking is free.** Starlette's `FileResponse` already implements
  `Accept-Ranges`, `206`, `Content-Range`, `If-Range` and `416`. Do not wrap it
  in a `StreamingResponse`; that loses all of it and breaks `<audio>` seeking.
- **Inline allowlist — owned by [`services/media/preview.py`](../server/services/media/preview.py),
  not by this route.** `serves_inline(mime)` decides the `Content-Disposition`;
  `preview_kind(mime)` (gated on `serves_inline` first) gives the gallery
  listing each row's `preview` verdict. **Two consumers, one function** — if
  they ever disagreed, the panel would open a player for a file the route forces
  to download, and the user would see a dead frame with no explanation. The
  `audio/ image/ video/` prefixes render inline, plus the exact-match set
  `INLINE_EXACT = {application/pdf}` (added for the Canvas node's PDF surface —
  the browser's built-in viewer renders isolated from the page, so the exposure
  class is images, not markup; exact-match so no other `application/*` type
  gains inline serving as a side effect). `NEVER_INLINE` is the full set
  `{image/svg+xml, text/html, text/xml, application/xhtml+xml}`. Everything else
  gets `Content-Disposition: attachment`, plus `X-Content-Type-Options: nosniff`.

  This is load-bearing. `shell`, `fileDownloader` and `fileModify` can all
  write arbitrary files into a workspace, so serving attacker-authored markup
  inline **from the app origin** would be stored XSS with session-cookie
  access. Both excluded types are script-bearing. The Canvas node displays
  workspace HTML anyway — but through a `srcDoc` iframe with
  `sandbox="allow-scripts"` and no `allow-same-origin` (opaque origin, no
  cookies), i.e. by respecting this rule, not weakening it
  (see [canvas_node.md](./canvas_node.md)). Two adjacent notes: attachment
  disposition does NOT block `fetch()` reading a body — the Canvas text
  renderers fetch markdown/code over this same route with a client-side
  512 KB cap (`client/src/hooks/useWorkspaceText.ts`) — and `PreviewKind`
  now includes `"pdf"` on both sides of the wire.
- No immutable caching: workspace files are mutable. `AudioRef.sha256` is a
  natural `ETag`.

### `POST /api/workspace/{workflow_id}/uploads`

The first multipart endpoint in the repo, on either side of the wire.

- Read in bounded chunks with a running total — never `await file.read()`, and
  never trust `Content-Length`, which is attacker-controlled. The cap is
  enforced on bytes **actually read**, so an oversize body aborts before it has
  all arrived.
- A write, so stricter than the file route: only the workflow's owner may
  upload (the saved graph's `owner_id` against the caller the auth middleware
  resolved), and an id that does not resolve is a 404 rather than a write into
  the shared anonymous workspace (`resolve_workspace_root(...,
  allow_default=False)`). Both refusals are 404, like the file route's.
- Audio comes back as a serialized `AudioRef` (`write_audio` probes the
  container). Anything else comes back as a `FileRef` from `write_media`,
  whose kind follows `preview_kind`: `image`, `video`, `document` for a PDF,
  otherwise `file`, so script-bearing types such as SVG and HTML are plain
  files. The media type comes from the stored filename, the same signal the
  file route serves by. `coerce_file_param` accepts both.
- The chat uses it for attachments and dictation. It sends only the stored
  path back; `services/chat/attachments.py` rebuilds the reference from the
  file under `uploads/` (refusing anything else), and dictation
  (`nodes/speech/_handlers.py`) transcribes a recording there and deletes it.
  Neither trusts a reference the client sends.

### Auth

No work needed, and **no signed-URL scheme** — tokens in URLs leak into logs,
`Referer` headers and browser history.

`AuthMiddleware`'s SPA bypass explicitly excludes `/api/` and `/ws/`, so these
routes are gated. A same-origin `<audio src="/api/…">` sends the session cookie
under `SameSite=Lax`.

One caveat the frontend must handle: **a 401 surfaces to `<audio>` as a silent
`error` event.** Without an explicit handler the user sees a dead player and no
explanation — `AudioPreview.tsx` catches it and says so.

---

## 6. Frontend

| Piece | File |
|---|---|
| `AudioRef` type + `isAudioRef` + player | [`components/output/AudioPreview.tsx`](../client/src/components/output/AudioPreview.tsx) |
| Upload helper (`FormData`, cap pre-check) | [`lib/workspaceUpload.ts`](../client/src/lib/workspaceUpload.ts) |
| Output routing | [`components/output/OutputPanel.tsx`](../client/src/components/output/OutputPanel.tsx) |
| File parameter widget | [`components/ParameterRenderer.tsx`](../client/src/components/ParameterRenderer.tsx) `case 'file'` |

Detection in the output panel is **structural** (`kind === 'audio'`) rather than
purely hint-driven, so a ref arriving from anywhere still renders as a player
instead of a wall of JSON metadata. `uiHints.outputMode: "audio"` exists as the
declarative signal but is not required.

Do not set `Content-Type` on the upload fetch — the browser must set it so the
multipart boundary is generated.

**Pre-existing hazard this replaces.** The file widget base64s into the
parameters dict with *no size check anywhere*: not in the handler, not in
`sendRequest`, not in the WS handler. `hasUnsavedChanges` also re-`JSON.stringify`s
the whole buffer on every render. A ~1.5 MB clip becomes ~2 MB of base64 and
dies at the Temporal threshold. The base64 path survives only as a fallback for
a workflow that has never been saved and therefore has no workspace yet.

---

## 7. Extending to other media

This already resolved, and not the way the original sketch guessed. What was
rejected is a single fat `MediaRef` carrying every kind's optional metadata,
because the metadata that matters differs per kind (`duration` and `sample_rate`
for audio; `width` / `height` for images). What was *adopted* is a shared base
carrying only kind-agnostic fields — `FileRef` — with `FileKind` already
enumerating `image`, `video` and `document`, and `AudioRef(FileRef)` as the one
narrowing subclass so far.

So the rule for the next kind:

- **`FileRef` is the honest default.** `kind="file"` asserts only that the file
  exists in the workspace. The `gallery` node emits this for everything,
  including a `.wav`.
- **Subclass only when a probe produces real extra metadata.** `AudioRef` earns
  its subclass because `inspect_audio` measures duration / sample rate /
  channels. An `ImageRef` earns one the moment something actually reads
  width / height — and not before, because `kind` is an assertion about what was
  measured. A fabricated `duration_seconds` mis-bills a per-second provider
  downstream; the same trap exists for any guessed field.
- The transport layer underneath — containment, atomic write, the workspace
  routes, the limits, `preview.py` — is already kind-agnostic, so `write_image`
  would differ from `write_audio` only in subdirectory and probe.

### Large data results are files too

The arithmetic in section 1 does not care whether the bytes are media. A
node that returns a third-party API's raw answer can produce hundreds of
kilobytes of JSON, and that result takes every path a media payload does.
When the node is an agent's tool there is a second cost: the loop keeps
only the first `TOOL_RESULT_MAX_CHARS` characters, so the model sees a
fraction of what the call returned, and a few such results clear the
rest of the conversation
([agent_context_flow.md](./agent_context_flow.md#transcript-size-capped-results-cleared-old-results)).

`tikhubAction` is the reference. One page of 12 Instagram reels is about
310,000 characters, so a payload over 20,000 characters is written with
`write_media(..., ext="json", mime_type="application/json", subdir="tikhub")`
and the node returns the `FileRef` under `file`, with a `preview` of the
payload's shape (the first element of each list, long strings clipped)
in place of the payload
([tikhub_service.md](./tikhub_service.md#large-results---_resultpy)). The
file is indented so tools that work by line can search and page through
it. Use the same shape for any node whose answers are routinely larger
than a model can use.

---

## 8. Invariants (tests)

| Invariant | Test |
|---|---|
| `AudioRef` has no bytes/base64 field; injecting one raises | `tests/media/test_audio_ref.py` |
| Traversal, absolute-outside, and cross-workflow access all refused | `tests/media/test_audio_ref.py` |
| `workspace_root` raises on a bare `workflow_id` rather than guessing | `tests/media/test_audio_ref.py` |
| `inspect_audio` never raises, on any input | `tests/media/test_audio_ref.py` |
| Route serves `text/html` and `image/svg+xml` as `attachment` | `tests/routers/test_workspace.py` |
| Range request returns `206` with correct `Content-Range` | `tests/routers/test_workspace.py` |
| Oversize upload is 413; traversal filename cannot escape `uploads/` | `tests/routers/test_workspace.py` |
| The route delegates its disposition to `preview.serves_inline` rather than re-deriving it | `tests/routers/test_workspace.py::TestInlineDispositionHasOneDefinition` |
| Listing paths are relative, POSIX, and round-trip through `resolve_media` | `tests/nodes/test_gallery.py::TestPathShape` |
| Every listing row carries a finished `ref`, and `preview` matches `serves_inline` | `tests/nodes/test_gallery.py::TestRowsAreSelfSufficient` |
| Traversal (`..`, `~`) refused; `/etc` and `C:/Windows` resolve *inside* the workspace | `tests/nodes/test_gallery.py::TestTraversal` |
| An escaping symlink is not listed | `tests/nodes/test_gallery.py::TestWorkspaceContainmentOfSymlinks` |
| A mutating caller with an unresolvable workflow id is refused, not defaulted | `tests/services/test_workspace_locator.py` |
| A returned ref round-trips through the GET route | `tests/routers/test_workspace.py` |


## Images to models — the hydration boundary

Multimodal input extends the never-bytes rule to LLM requests. Durable state
(the `agent_conversations` conversation store, Temporal payloads, node results) carries image **FileRefs**
inside `ContentBlock.source` (`kind="file_ref"`, ~450 B); actual bytes exist
only between `services/llm/media.py::hydrate_image_blocks` and the provider
HTTP call, on throwaway message copies. The protocol codec raises if a
bytes-kind source ever reaches serialization — same structural-enforcement
philosophy as `FileRef.extra="forbid"`.

`services/media/image_fit.py` is the sizing authority: budgets are defined in
visual tokens (`small`/`normal`/`large` = 256/1024/2048), converted to a
pixel area (`tokens x patch^2`) and applied with the aspect-preserving,
patch-grid-snapping `smart_resize` (floors AND ceilings — thumbnails scale
up). JPEG q90 for opaque images, PNG when alpha must survive. Consumers: the
`visionAnalyze` delegate node and image-block hydration.

The `dataSource` node is the reference producer: its image read tier returns
Pillow header metadata plus the FileRef, and adds the `llm_media` opt-in for
workspace refs of allowlisted types. External-mount images have no FileRef by
design — `copy_to_workspace` imports them first. Full node reference:
[data_node.md](data_node.md).
