# Noodle: domain model, app store and the runtime-to-UI contract

Noodle's Mac UI is driven by one `@MainActor @Observable` store (`NoodleStore`) that caches a file-backed repository (`WorkspaceRepository`) and reads per-bot runtime snapshots from an observable coordinator (`AgentRuntimeCoordinator`). Transcripts are append-only JSON arrays. Bots never stream text into the transcript: they post complete messages by running a `messenger` CLI that writes into the same files, and the app picks new messages up with a one-second polling loop. Runtime state is a per-bot snapshot (`offline | starting | ready | working | failed`, a free-text `detail` and an optional typed `failure`), which the UI folds into one coloured dot per conversation. Unread state is a per-conversation boolean. A group is a conversation with several bots, and every one of them is woken by every message; the code has no addressing, mentions routing or turn-taking. The Hub/mobile protocol (`LinkProtocol`) already defines a request/response plus event-stream wire contract with idempotent sends and positional paging, and a strict JSON "Scenario" format seeds complete UI state and plays scripted films. This document turns all of that into TypeScript types, a store shape and a WebSocket event contract.

Permalink base for every citation below: https://github.com/pdparchitect/noodle/blob/702339e3153025c9fe9582783caff7a19b48ff97/ (citations are repo-relative `path:line` or `path:start-end`).

## 1. Scope: files read

Read in full:

- `Sources/Noodle/NoodleStore.swift` (2,266 lines), `Sources/Noodle/Components.swift`, `Sources/Noodle/AgentActivityWindows+Store.swift`, `Sources/Noodle/AgentSettingsCheckpoint.swift`, `Sources/Noodle/Scenario.swift` (1,722 lines).
- `Sources/NoodleCore/Models.swift`, `Harnesses.swift`, `WorkspaceRepository.swift` (1,566 lines), `MessageDelivery.swift`, `MessengerDocumentation.swift`, `MessengerBridge.swift`, `ConversationDrafts.swift`, `ConversationName.swift`, `ConversationEffects.swift`, `ConversationBackground.swift`, `TranscriptPositions.swift`, `VoiceMessage.swift`, `VoiceCall.swift`, `AttachmentAnnotation.swift`, `CompanionLink.swift`, `AgentConfiguration.swift`, `AgentHeartbeat.swift`, `AgentTurnRecovery.swift`, `AgentNameCompletion.swift`, `AgentSessionRollover.swift`.
- `Sources/NoodleRuntime/AgentRuntimeCoordinator.swift` (1,100 lines), `AgentRuntimeProcess.swift`, `AgentActivityLog.swift`, `AgentActivityParser.swift`, `MessageDeliveryRouter.swift`, `MessageDeliveryClassifier.swift`, `UsageHistory.swift`; `ClaudeAgentProcess.swift` lines 180-417 (phase transitions).
- `Sources/Noodle/SidebarView.swift`, `ChatView.swift`, `TranscriptScrollView.swift`, `MessageMarkdownCache.swift`, `NoodleNotifications.swift`, `MessageReceivedSound.swift`.
- `Shared/HubLink/Sources/HubLink/MessageTable.swift`, `CustomSpace.swift`; `LinkProtocol.swift` lines 1-135, 236-325, 540-620, 1068-1600.
- `Scenarios/bot-needs-attention`, `launch-week`, `personal-assistant`, `code-review` (timeline), `schedule-post` (timeline).

Skimmed: `Sources/Noodle/ScenarioFilm.swift`, `ConversationWindow.swift` (title, error alert, window registry), `ConversationEffectsView.swift`, `AgentProfileSheet.swift`, `NoodleSettingsView.swift`, `ConversationWindowSession.swift`; `Sources/NoodleRuntimeSettings/AgentActivityWindow.swift`, `AgentKickConfirmation.swift`, `BotAvatar.swift`; `Sources/NoodleCore/MessengerCLI.swift`, `AgentAccess.swift`, `AgentFolder.swift`, `AgentStorageLayout.swift`, `UsageLedger.swift`; `Sources/NoodleHubClient/HubMirror.swift` (sync loop); `Sources/HubCore/HubBots.swift` (event emission); `Mobile/Sources/NoodleMobile/Chats.swift` (unread, ordering, optimistic send); `Shared/HubLink/Sources/HubLink/LinkCall.swift`; `Tests/NoodleAppTests/ConversationRuntimeStatusTests.swift`, `TranscriptLayoutTests.swift` (test names).

## 2. Findings

### 2.1 Architecture in one picture

```
SwiftUI views --read--> NoodleStore (@MainActor @Observable)            AgentRuntimeCoordinator (@MainActor @Observable)
     |                   agents, conversations, messagesByConversation      snapshots[agentID] (phase/detail/failure)
     | intents           attachmentsByConversation, unread, pins, drafts    activity logs, heartbeats, restarts
     v                        |  ^ 1 s poll (mtime+size revisions)               ^ onSnapshot / onActivity callbacks
store.sendDraft() ---> WorkspaceRepository (JSON files, flock, atomic)          |
                              ^                                        AgentRuntimeProcess (one per bot)
                              | messenger --send/--react/--set-status         ^ notify() = wake event
                              | (file mailbox, broker scans every 100 ms)     |
                        bot harness (Claude Code, Codex, ...) <---------------+
```

- The repository files are the source of truth; the store holds an in-memory copy and updates it after each successful write (`NoodleStore.swift:1452-1480`) or when the poll sees a file change (`NoodleStore.swift:2218-2233`, `1838-1971`).
- Bots talk only through Messenger: the CLI writes a request file into `.noodle/messenger-bridge` in the bot's workspace, an in-app broker scans every 100 ms, checks a per-bot session token and calls the repository (`Sources/NoodleCore/MessengerBridge.swift:15-44`, `49-120`; dispatch in `MessengerCLI.swift:67-167`).
- The store learns about bot replies only by polling; runtime phases arrive by callback (`AgentRuntimeCoordinator.swift:1065-1082`).
- Bots on a Noodle Hub are mirrored into the local repository by `HubMirror`, which follows the Hub's event stream (`Sources/NoodleHubClient/HubMirror.swift:493-556`). The Hub is, in effect, the server a web client would replace.

### 2.2 Entity model

| Entity | Defined at | Stored in | Relationships |
|---|---|---|---|
| `AgentRecord` (bot, public fields) | `Sources/NoodleCore/Models.swift:5-56` | `Agents/<id>/agent.json` (merged with private config) | has one direct `BotConversation`; member of 0..n groups |
| `AgentConfiguration` (private) | `AgentConfiguration.swift:17-60` | same `agent.json` | backstory, folders, groupFolders, harnessProfile, owner, voice; never sent to other bots |
| `BotConversation` | `Models.swift:58-101` | `Conversations/<id>/conversation.json` | `participantIDs` -> agents; direct has exactly one |
| `ConversationGuest` | `Models.swift:104-112` | inside conversation | Hub-shared conversation with someone other than the owner |
| `ChatMessage` | `Models.swift:114-158` | `Conversations/<id>/messages.json` (array) | `author` -> agent; `attachmentIDs` -> attachments of the same conversation |
| `MessageReaction`, `MessageReactionChange` | `Models.swift:160-182` | inside the message | change log carries a per-conversation `sequence` |
| `ConversationAttachment` | `Models.swift:197-240` | `Conversations/<id>/Attachments/<id>.json` + `<id>.<ext>` | referenced by messages and drafts |
| `LinkCard`, `CompanionLink` | `CompanionLink.swift:8-69` | inside attachment | link to a browser tab, computer or noodlet |
| `AttachmentAnnotation` | `AttachmentAnnotation.swift:5-71` | inside attachment | points at a source attachment and optionally a source message |
| `VoiceMessage` | `VoiceMessage.swift:4-25` | inside attachment | |
| `VoiceCallRecord`, `VoiceCallLine` | `VoiceCall.swift:3-33` | inside a message (`call`) | |
| `ConversationBackground` | `Shared/Wallpaper/Sources/NoodleWallpaperCore/ConversationBackground.swift:5-19` | `Conversations/<id>/background.json` + `Backgrounds/` | per conversation, local appearance only |
| `ConversationEffect` | `ConversationEffects.swift:5-21` | `Conversations/<id>/effects.json` | ephemeral queue, sent by bots |
| `AgentInbox` | `Models.swift:242-249` | `<workspace>/.noodle/inbox.json` | per-bot read cursors per conversation |
| `AgentRuntimeSnapshot` | `Harnesses.swift:275-319` | memory only | one per bot |
| `ConversationDrafts.Draft` | `ConversationDrafts.swift:3-33` | memory only | one per conversation |
| `TranscriptViewport` | `TranscriptPositions.swift:3-22` | `scroll-positions.json` | one per conversation |
| `CustomSpace` | `Shared/HubLink/Sources/HubLink/CustomSpace.swift:5-31` | `spaces.json` (iCloud-synced) | members and pins by (hub key, conversation) |
| `AgentActivityEntry` | `AgentActivityLog.swift:19-33` | memory only, bounded | one log per bot |
| `UsageSample` | `UsageLedger.swift:40-60` | `usage.sqlite` | per bot, model, session |

The same data translated to TypeScript (field names exactly as Noodle encodes them on disk; comments carry the constraints the code enforces):

```ts
type UUID = string;      // Foundation encodes UUIDs uppercase in JSON (inferred); folder names are lowercased
type ISODate = string;   // repository files: ISO 8601 without fractional seconds (inferred from .iso8601)
type Base64 = string;    // Swift Data encodes as base64

type HarnessProvider = 'codex' | 'claude-code' | 'muse' | 'grok-build' | 'fx' | 'opencode' | 'antigravity' | 'apple';

interface AgentRecord {                    // Models.swift:5-56
  id: UUID;
  displayName: string;                     // trimmed, 1..100 chars, single line (ConversationName.swift:18-26)
  createdAt: ISODate;
  updatedAt: ISODate;
  harnessIdentifier?: HarnessProvider;     // absent -> bot shows failed "Choose a harness in Edit Bot"
  modelIdentifier?: string;
  reasoningEffort?: string;                // e.g. low | medium | high | xhigh | max (Harnesses.swift:109-115)
  publicDescription?: string;              // empty -> absent; visible to other bots in a group
  accentSeed: number;                      // random 0...5 at creation; avatar colour fallback
  avatarSymbolName?: string;               // SF Symbol, default "sparkles"
  avatarColorIndex?: number;               // wraps onto 6 gradients
  avatarImageData?: Base64;                // JPEG, <= 512 px when set from an attachment
  status?: string;                         // set only by the bot itself; one line, <= 60 chars
  archivedAt?: ISODate;                    // archived bots keep everything but never run
}

interface AgentPrivateConfig {             // AgentConfiguration.swift:17-60, same file as AgentRecord
  backstory: string;                       // rendered into the bot's generated AGENTS.md
  folders?: AgentFolder[];                 // up to 16 (AgentFolder.swift:14)
  groupFolders?: AgentFolder[];            // copied from the bot's groups at every sync
  harnessProfile?: UUID;                   // which login of the harness; absent = system login
  owner?: { id: UUID; name: string };      // Hub only
  voice?: string;                          // call voice id; only Codex has voices (VoiceCall.swift:102-110)
}
interface AgentFolder { path: string; writable: boolean; description?: string }   // <= 500-char description

type ConversationKind = 'direct' | 'group';
interface BotConversation {                // Models.swift:63-101
  id: UUID;
  displayName: string;                     // direct: kept in sync with the bot's name
  publicDescription?: string;
  kind: ConversationKind;
  participantIDs: UUID[];                  // direct: [botId]; group: >= 1, unique, sorted by UUID string
  createdAt: ISODate;
  updatedAt: ISODate;                      // sidebar recency key
  archivedAt?: ISODate;                    // groups only; a direct conversation follows its bot
  folders?: AgentFolder[];                 // groups only
  guest?: { id: UUID; name: string };      // conversations with this present are hidden on the owner's Mac
}

// Swift synthesized Codable on disk: {"user":{}} | {"agent":{"_0":"<UUID>"}} | {"system":{}} (inferred;
// the same "_0" shape is asserted for other enums in Shared/HubLink/Tests/HubLinkTests/LinkCompatibilityTests.swift:18-72)
type MessageAuthor = { kind: 'user' } | { kind: 'agent'; agentId: UUID } | { kind: 'system' };
type MessageDelivery = 'saved' | 'queued' | 'delivered' | 'failed';

interface ChatMessage {                    // Models.swift:127-158
  id: UUID;
  conversationID: UUID;
  author: MessageAuthor;
  body: string;                            // trimmed, never empty (WorkspaceRepository.swift:1498-1502)
  createdAt: ISODate;
  delivery: MessageDelivery;
  attachmentIDs?: UUID[];                  // omitted when empty
  reactions?: MessageReaction[];           // current badges
  reactionChanges?: MessageReactionChange[];  // append-only event log, feeds bot inboxes
  call?: VoiceCallRecord;                  // the card of a voice call
}
interface MessageReaction { id: UUID; author: MessageAuthor; emoji: string; createdAt: ISODate }
interface MessageReactionChange {
  id: UUID; conversationID: UUID; messageID: UUID;
  sequence: number;                        // max over the conversation + 1 (WorkspaceRepository.swift:1335)
  author: MessageAuthor; emoji: string; removed: boolean; createdAt: ISODate;
}

interface ConversationAttachment {         // Models.swift:197-240
  id: UUID;
  conversationID: UUID;
  originalFilename: string;
  storedFilename: string;                  // "<id>.<ext>"
  mediaType: string;                       // images re-detected from content (WorkspaceRepository.swift:1045-1066)
  byteCount: number;
  createdAt: ISODate;
  url?: string;                            // link attachment: http(s) page, noodlebrowser://, noodlecomputer://, noodlet://
  voice?: VoiceMessage;
  card?: LinkCard;                         // companion links only
  annotation?: AttachmentAnnotation;
}
interface LinkCard {                       // CompanionLink.swift:43-69
  title: string;                           // <= 300 chars
  detail?: string;                         // page address or terminal tail, last 2,000 chars
  image?: Base64;                          // JPEG snapshot <= 550,000 bytes
  symbol?: string; colour?: number; icon?: Base64; capturedAt?: ISODate;
}
interface VoiceMessage {                   // VoiceMessage.swift:4-25
  transcript?: string; duration: number;   // 0 < duration <= 660 s
  waveform: number[];                      // <= 120 samples in 0..1
  localeIdentifier?: string;
}
interface AttachmentAnnotation {           // AttachmentAnnotation.swift:5-71
  version: 1 | 2;                          // 1 = legacy PDF; 2 = text/plain or image/png
  sourceAttachmentID: UUID; sourceFilename: string; sourceMessageID?: UUID;
  quote?: string; comment: string;         // quote and region are mutually exclusive
  region?: { x: number; y: number; width: number; height: number };  // fractions, bottom-left origin
}
interface VoiceCallRecord { agentID: UUID; endedAt?: ISODate; lines: VoiceCallLine[] }
interface VoiceCallLine { speaker: 'person' | 'bot'; text: string; at?: ISODate }

interface ConversationBackground {         // NoodleWallpaperCore/ConversationBackground.swift:5-19
  preset?: 'sunset' | 'ocean' | 'forest' | 'dusk';
  imageFilename?: string;                  // "<uuid>.jpg|heic|heif|mov|mp4|m4v" in Backgrounds/
  mediaKind?: 'image' | 'video' | 'dynamicImage';
}                                          // isDefault = !preset && !imageFilename

interface ConversationEffect {             // ConversationEffects.swift:10-21
  id: UUID; conversationID: UUID; agentID: UUID;
  kind: 'confetti' | 'fireworks' | string; // unknown kinds are skipped by older clients
  createdAt: ISODate; expiresAt: ISODate;  // expiresAt = createdAt + 30 s
  consumedAt?: ISODate;
}

interface AgentInbox {                     // Models.swift:242-249, per bot
  conversationOffsets: Record<string /* lowercased conversation id */, number>;   // messages already read
  reactionOffsets?: Record<string, number>;                                        // last reaction sequence read
}

interface Draft { text: string; attachments: ConversationAttachment[] }   // memory only on the Mac
interface TranscriptViewport { offset: number; isAtBottom: boolean; messageID?: UUID }

interface CustomSpace {                    // CustomSpace.swift:5-31
  id: UUID; name: string;
  members: { hub?: string; conversation: UUID }[];   // hub = Hub public key (base64), absent = this Mac
  pins: { hub?: string; conversation: UUID }[];      // in pin order
}
```

### 2.3 Message lifecycle

**Creation paths.** Every message is appended to `messages.json` under an exclusive `flock` on `.messages.lock` (`WorkspaceRepository.swift:1090-1097`, `1542-1550`) and the conversation's `updatedAt` is bumped to the message time.

| Path | Code | Author | Initial delivery | Side effects in the store |
|---|---|---|---|---|
| Composer send | `NoodleStore.swift:1452-1480` -> `WorkspaceRepository.swift:1406-1432` | user | `queued` | mark read; append to cache; bump `updatedAt` and re-sort; clear draft; `runtime.notify(all participants)`; relay to an open voice call; push to the Hub |
| Voice message | `NoodleStore.swift:1423-1445` | user, body `"Voice message"` | `queued` | staged files travel with the recording; text draft untouched |
| Slash-style command | `NoodleStore.swift:1482-1500` | user | `queued` | as composer send |
| Share extension | `WorkspaceRepository.swift:1463-1490` | user; `id` = request id (idempotent retry) | `queued` | |
| Bot reply (`messenger --send`) | `MessengerCLI.swift:121-155` -> `WorkspaceRepository.swift:1348-1385` | agent | `delivered` | seen by the next poll |
| Group change notice | `WorkspaceRepository.swift:723-744` | system | `delivered` | written in the same transaction as the group change |
| Voice call card | `WorkspaceRepository.swift:1434-1461` | user, body `"Voice call"`, `call` set | `delivered` | lines and `endedAt` filled when the call ends |

A message with files but no text gets the body `"Sent N attachment(s)"` (`NoodleStore.swift:1457`; same rule on mobile, `Mobile/Sources/NoodleMobile/Chats.swift:698-701`). Messages cannot reach an archived group, an archived bot or a group whose bots are all archived (`WorkspaceRepository.swift:522-529`); the composer shows the reason in place of its placeholder: `"This group is archived"`, `"Every bot in this group is archived"`, `"<Name> is archived"` (`NoodleStore.swift:651-661`, `ChatView.swift:370-372`).

**Delivery state machine.** `queued -> delivered` happens when any recipient bot consumes its inbox (`messenger --get-latest` without `--peek`): `latestMessages(consuming: true)` advances the bot's offsets and marks every queued user message up to that count delivered (`WorkspaceRepository.swift:1213-1231`, `1249-1260`). "Delivered" therefore means "a bot has read it". For Hub bots the flag follows the Hub's `delivered` field (`HubMirror.swift:694`, `717`). `saved` and `failed` exist in the enum and have labels, but no current code path assigns them: the only assignments are `queued` and `delivered` (`WorkspaceRepository.swift:742`, `1244`, `1257`, `1377`, `1425`, `1442`, `1479`); scenarios may seed them (`Scenario.swift:529`; `Scenarios/bot-needs-attention/scenario.json:31`). Labels appear only under the user's own bubbles (`Components.swift:305-310`, `522-529`):

| `delivery` | Label |
|---|---|
| `saved` | Saved |
| `queued` | Sent |
| `delivered` | Delivered |
| `failed` | Not delivered |

Mobile adds two client-only states for its optimistic send: `"Sending…"` while a send is in flight and `"Not delivered"` once its retries fail (`Chats.swift:672-678`).

**The bot side of delivery.** Each bot keeps `AgentInbox` offsets per conversation. `latestMessages` returns every message after the offset except the bot's own and call cards, plus reaction changes by others whose `sequence` is above the reaction offset, sorted by time (`WorkspaceRepository.swift:1119-1234`). Each delivery is a `MessengerDelivery` envelope: `me`, `conversation`, named `participants`, `sender` (handle `user | guest | me | bot | system`), `message`, `attachments` (with an absolute path copy inside the bot's workspace), current `reactions` and an optional `reactionChange` (`Models.swift:251-328`). A bot is never sent the message itself in its wake event: it gets a bodyless `<noodle-event type="inbox-changed" />` and must fetch (`MessengerDocumentation.swift:48-67`, `AgentHeartbeat.swift:88-98`).

**Streaming and partial content.** There is none in the transcript. A bot's reply appears whole, the moment the poll sees it (`MessageBubble.insertion`: slide up from the bottom plus fade; system rows fade only, `Components.swift:230-233`). Harness output (text deltas, reasoning summaries, tool calls, command output, plans) streams into a separate per-bot Activity log, shown in its own window (section 2.4). The scenario `stream` step writes into that log, not the chat (`Scenario.swift:1103-1111`), and the `code-review` scenario opens the activity window to show it (`Scenarios/code-review/scenario.json:71`, `79`). There is no typing indicator; "working" is only the blue dot.

**Edits and deletions.** Sent messages are never edited or deleted. What can change after a message exists: its reactions, its delivery flag, a call card's lines and end time (`WorkspaceRepository.swift:1450-1461`), and a repaired image media type. An annotation's comment can be revised only while it sits in a draft, before any message references it (`WorkspaceRepository.swift:973-1009`, `AttachmentAnnotation.swift:73-88`). Deleting a bot deletes its workspace and direct conversation and silently removes it from groups (no system notice; `WorkspaceRepository.swift:416-460`, `MessengerDocumentation.swift:28`); its old group messages keep its id and render without an avatar (`Components.swift:255-256`). Deleting a group deletes its messages and attachments (`NoodleStore.swift:1313-1319`). Only mobile can delete or "take back" its own unsent, failed message (`Chats.swift:759-782`).

**Reactions.** One reaction per (author, emoji); the emoji must be a single grapheme with an emoji scalar (`Models.swift:166-170`). `setReaction` is idempotent and writes the badge and its change-log entry in one atomic write (`WorkspaceRepository.swift:1302-1346`). The user's "change reaction" adds the new emoji before removing the old so a failure cannot lose both (`NoodleStore.swift:2064-2075`). A user reaction wakes every bot in the conversation; a bot reaction wakes the other participants (`NoodleStore.swift:1959-1968`). In the bubble, badges sit on the first text segment (or on the files when there is no text), deduplicated in first-seen order, with a count above one, tinted when the user is among the reactors; a popover lists names and offers a 12-emoji change palette (red heart, thumbs up, thumbs down, tears of joy, party popper, question mark, eyes, hourglass, check mark button, folded hands, fire, light bulb) (`Components.swift:381-520`). Every row reserves the badge overhang (12 pt) so reacting never moves a message (`Components.swift:209-228`). Reactions made on a Hub are not shown on the Mac yet (`HubMirror.swift:532`).

**Attachments.** Files are copied into the conversation the moment they are staged in the composer, before sending (`NoodleStore.swift:1710-1737`); the message only lists their ids; removing a staged chip deletes the files (`NoodleStore.swift:1779-1789`). Kinds: plain files; images (media type detected from content); links (a small `.webloc` plus `url`, with a `LinkCard` for companion links; `WorkspaceRepository.swift:950-964`); voice (`audio/x-caf` plus `VoiceMessage`; a message holding only recordings shows no text bubble, `Components.swift:376-379`); annotations (text or PNG, with metadata). Saved annotations are durable drafts: after a restart they are put back into the draft until a message references them (`NoodleStore.swift:588-591`). The bot path validates that attachment ids exist in the conversation (`WorkspaceRepository.swift:1366-1370`); the user path does not. A link preview is fetched for the first public web URL in the body unless the same URL is already an attachment, and only while the row is visible (`Components.swift:286-290`, `315-317`).

**Ordering.** Canonical order is append order in `messages.json`. Because the log is append-only and nothing is deleted, the Hub protocol pages by position (`after`, `before`, `limit`) rather than by time (`Shared/HubLink/Sources/HubLink/LinkProtocol.swift:1551-1579`). Bot deliveries are sorted by time (`WorkspaceRepository.swift:1223-1225`); mobile merges pages by `(createdAt, original index)` so equal timestamps stay stable (`Chats.swift:878-886`). Scenario messages must be seeded in time order (`Scenario.swift:531-533`).

**Rendering-relevant parsing.** Bodies are Markdown, rendered inline-only with whitespace preserved; only `http`, `https` and `mailto` links survive (`MessageMarkdownCache.swift:58-73`). Markdown tables split a body into separate bubbles; a table with more than 8 rows folds to 6 and is sortable (`Shared/HubLink/Sources/HubLink/MessageTable.swift:4-79`). Previews and notifications use the text around tables, or "Sent a table" (`MessageTable.swift:52-60`).

### 2.4 Runtime status model

**Per-bot snapshot** (`Harnesses.swift:275-319`):

```ts
type AgentRuntimePhase = 'offline' | 'starting' | 'ready' | 'working' | 'failed';
type AgentRuntimeFailure =                 // "supplied by the adapter, never inferred from display text"
  | { kind: 'missingSession'; sessionId: string }
  | { kind: 'usageLimit' } | { kind: 'authenticationRequired' }
  | { kind: 'recoveryFailed' } | { kind: 'safetyStop' };
interface AgentRuntimeSnapshot {
  agentID: UUID;
  phase: AgentRuntimePhase;
  detail: string;                          // human sentence, shown as tooltip/accessibility value
  processIdentifier?: number;
  failure?: AgentRuntimeFailure;           // present only with phase 'failed'
  reconnectingSince?: ISODate;             // first retry of the current connection episode
}
// canKick = phase === 'failed' || reconnectingSince != null      (Harnesses.swift:302)
```

A bot with no snapshot is `offline`, `"Not started"` (`AgentRuntimeCoordinator.swift:499-505`). Where snapshots come from:

| Trigger | Phase | Example `detail` | Code |
|---|---|---|---|
| No harness chosen | failed | Choose a harness in Edit Bot | `AgentRuntimeCoordinator.swift:539-544` |
| Harness installed, process not running | offline | Claude Code is configured | `AgentRuntimeCoordinator.swift:545-551` |
| Harness missing | failed | The configured harness is not installed | `AgentRuntimeCoordinator.swift:552-557` |
| Harness needs unrestricted access | failed | This harness requires unrestricted access. Allow it in Settings → Bots… | `AgentRuntimeCoordinator.swift:664-668` |
| Process launching | starting | Starting Claude Code | `ClaudeAgentProcess.swift:86` |
| Session open | ready | Claude Code ready | `ClaudeAgentProcess.swift:139` |
| Wake begins a turn | working | Checking for new messages / Heartbeat: checking for follow-up work / Recovering interrupted work | `ClaudeAgentProcess.swift:229-259` |
| Background tasks after a turn | working | 1 background task running | `ClaudeAgentProcess.swift:353-359` |
| Turn ends (even with an error) | ready | Claude Code ready, or Claude Code ready — last task failed | `ClaudeAgentProcess.swift:316-343` |
| Sign-in expired | failed + `authenticationRequired` | Claude Code needs you to sign in… | `ClaudeAgentProcess.swift:367-373` |
| Model refusal | failed + `safetyStop` | Claude's safeguards stopped a response… | `ClaudeAgentProcess.swift:375-382` |
| Process exits, or found dead by the 1 s reconcile | failed, then starting | Claude Code exited with status 1. Restarting in 1 seconds… / Runtime connection was lost. Restarting in 2 seconds… | `ClaudeAgentProcess.swift:361-392`, `AgentRuntimeCoordinator.swift:986-999`, `1004-1043` |
| Access mode change | starting | Changing agent access… | `AgentRuntimeCoordinator.swift:323` |
| Codex reconnecting for 10 min | restart; after two automatic restarts, failed | Codex could not reconnect after two automatic restarts… | `AgentRuntimeCoordinator.swift:973-983`, `844-847` |
| App quits | offline | Stopped | `AgentRuntimeCoordinator.swift:960-962` |

Supervision: unexpected exits restart with delay `min(2^(attempt-1), 30)` seconds, attempts capped at 7, and the counter resets after 60 seconds of a stable `ready` (`AgentRuntimeCoordinator.swift:1021-1056`). `reconcile` runs every second; a bot failed with a typed `failure` is left alone until the person kicks it, since "restarting cannot sign in, restore usage or answer a safety stop" (`AgentRuntimeCoordinator.swift:965-1002`). Snapshots from a superseded process are ignored by `runtimeID` (`AgentRuntimeCoordinator.swift:1065-1068`). A `working -> ready` transition counts as activity for heartbeats and clears the sign-in announcement (`AgentRuntimeCoordinator.swift:1070-1075`).

**Conversation status and colours.** One dot per conversation, aggregated over `shownParticipants` (archived bots leave a group's set unless all are archived; `NoodleStore.swift:642-649`):

```swift
// Sources/Noodle/Components.swift:64-82
enum ConversationRuntimeStatus: Equatable {
    case working, failed, ready, idle
    init(phases: [AgentRuntimePhase]) {
        if phases.contains(.working) { self = .working }
        else if phases.contains(.failed) { self = .failed }
        else if !phases.isEmpty, phases.allSatisfy({ $0 == .ready }) { self = .ready }
        else { self = .idle }
    }
    var color: Color { switch self { case .working: .blue  case .failed: .red  case .ready: .green  case .idle: .gray } }
}
```

Tests pin the precedence: working beats failed, failed beats ready, and anything short of all-ready (including an empty set, `starting` or `offline`) is idle (`Tests/NoodleAppTests/ConversationRuntimeStatusTests.swift:8-18`). A Hub bot shows the Hub's last phase while connected (unknown counts as ready) and `offline` when not (`Components.swift:85-91`); mobile uses the same colours with `offline` and `starting` grey and forces `offline` while the Hub is unreachable (`Chats.swift:2611-2618`; `Mobile/Sources/NoodleMobile/HubScreens.swift:935`). The tooltip and accessibility value list `"<Bot>: <detail>"` per member, one line each (`Components.swift:93-102`).

Where the dot appears: sidebar rows (10 pt with a 2 pt ring in the window colour; `SidebarView.swift:147-158`) and the title of separate conversation windows, where the ring is cut out of the picture with `destinationOut` so it reads over any wallpaper (`Components.swift:105-130`, `ConversationWindow.swift:128-143`).

**Needs attention.** "Attention" is not a separate state: it is `phase == failed`, usually with a typed failure. The person sees a red dot; the direct conversation's context menu shows Kick only while failed (`SidebarView.swift:118-123`); Settings shows a badge on the Harness tab counting harnesses with a failed, kickable bot (`NoodleSettingsView.swift:20-27`). Kick restarts at once unless the failure needs consent, in which case it returns an `AgentKickRequest` and an alert asks first (`AgentRuntimeCoordinator.swift:52-95`, `732-761`; `Sources/NoodleRuntimeSettings/AgentKickConfirmation.swift:6-40`):

| Failure | Alert title | Confirm button | Extra |
|---|---|---|---|
| `missingSession` | Recover <Bot>? | Recover Bot | |
| `usageLimit` | Usage limit reached | Retry Now | |
| `authenticationRequired` | Sign in to reconnect <Bot> | Retry Now | "Open Harness Settings" button |
| `recoveryFailed` | Retry recovery? | (restarts without asking) | |
| `safetyStop` | Safeguards stopped <Bot> | Resume | "New Session" button |

The confirmation is valid only for the same lifecycle, runtime id, failure and access mode (`AgentRuntimeCoordinator.swift:745-751`); the Hub mirrors this with a one-time `confirmationID` (`LinkProtocol.swift:1310-1330`). A sign-in failure also posts a system notification once per episode, even while Noodle is in front, replacing the previous one by identifier `sign-in-<agentID>` (`AgentRuntimeCoordinator.swift:1076-1078`, `Sources/Noodle/NoodleNotifications.swift:80-99`).

There are no in-chat approval prompts. Tool, command and file-change approvals requested by a harness are answered automatically by policy: accepted only when the bot has extended (unrestricted) access, declined otherwise; "request user input" is answered with no answers (`Sources/NoodleCore/AgentAccess.swift:111-133`, `Sources/NoodleRuntime/ACPAgentProcess.swift:245-250`). A harness that insists on interactive approval is stopped with a failure to kick (`MuseAgentProcess.swift:267-276`). A bot that needs a decision asks in the conversation like anyone else.

**Message delivery mode (interrupting a working bot).** Settings choose `automatic | immediate | queue` (`MessageDelivery.swift:3-18`). Wakes are coalesced into one pending notification per bot (`MessageDelivery.swift:22-45`). In automatic mode, while the bot is working, the router reads the last four unread messages without consuming them; an obvious stop request ("stop", "wait", "hold on", "pause", "cancel", "abort", "don't", or a punctuated "no") interrupts at once; otherwise an on-device model classifies each message and any of stop, correction, challenge, answer or emergency interrupts the current turn, within a 60 s deadline (`MessageDelivery.swift:49-114`, `MessageDeliveryRouter.swift:41-94`, `MessageDeliveryClassifier.swift:35-56`).

**Heartbeats and session rollover.** An idle, ready bot gets a `heartbeat` wake after 30 minutes without activity (1-1,440 minutes, per-bot opt-out; one overdue heartbeat, never a burst) (`AgentHeartbeat.swift:3-86`, `AgentRuntimeCoordinator.swift:431-439`). Sessions older than a day that have been idle an hour are replaced with a fresh one (`AgentSessionRollover.swift:5-31`, `AgentRuntimeCoordinator.swift:772-792`). Optionally, the Mac is kept from idle sleep while any bot is working (`Harnesses.swift:321-328`).

**Activity log (the only streaming surface).** Each bot has a bounded log: at most 500 entries and 256 KiB, details capped at 16 KiB, control characters stripped (`AgentActivityLog.swift:35-101`). An event is `{ title, detail, streamID?, appending }`; an event with a `streamID` updates the last entry with that id, appending or replacing its detail, which is how token deltas coalesce into one entry; every phase/detail change is itself recorded as an entry and ends all stream coalescing (`AgentActivityLog.swift:50-82`). Per-harness parsers map protocol messages to titles such as "Output", "Reasoning summary", "Running command" / "Command completed (exit 0)", "<tool>: started|completed", "Files changed", "Searching", "Plan" (`AgentActivityParser.swift:7-179`). The window polls the log's `revision` every 0.2 s and patches only the changed tail of its text (`Sources/NoodleRuntimeSettings/AgentActivityWindow.swift:93-114`, `254-276`).

### 2.5 Unread, notifications, badges and sidebar ordering

**Unread** is a `Set<UUID>` of conversations, persisted to `conversation-state.json` (`NoodleStore.swift:76-78`, `WorkspaceRepository.swift:84-98`). It is set when the poll finds a new message authored by a bot, unless that conversation is being viewed; system messages, user messages and reactions never mark a conversation unread (`NoodleStore.swift:1934-1941`, `2129-2148`). "Viewing" means the app is active and some conversation window showing it is visible, not minimised and not occluded (`ConversationWindow.swift:226-232`); a message that arrives while viewed is shared as read to other devices. It is cleared by: selecting the conversation (`SidebarView.swift:49-51`), the window becoming key (`ChatView.swift:44-46`, `140-141`), a tap in the chat (`ChatView.swift:43`), composer focus (`ChatView.swift:50-52`), scrolling the transcript (`ChatView.swift:523`), any draft change (`NoodleStore.swift:94-97`), sending, and archiving (`NoodleStore.swift:695-699`). Another device's read clears it only if no bot wrote after that read (`NoodleStore.swift:1567-1574`). The Hub keeps a monotonic per-conversation read mark set by message id, "since a date that crossed the link may no longer match", and computes unread counts from it (`LinkProtocol.swift:1298-1308`, `Sources/HubCore/HubBots.swift:991-1010`); mobile keeps a `seen[conversationID]` date and calls a conversation unread when its newest bot message is later (`Chats.swift:228-246`).

**Badges and notifications.** The Dock badge is the number of unread conversations, not messages (`NoodleStore.swift:1521-1525`). For each new bot message a notification is posted only when Noodle is not active or has no visible window: title the bot name, subtitle the group name in groups, body the preview text, identifier the message id, the bot's avatar attached (`NoodleNotifications.swift:21-78`, `NoodleStore.swift:2158-2179`). When notifications are not presented, the app plays the chosen receive sound itself (default "Blow"; `MessageReceivedSound.swift:5-28`). Opening a notification selects the conversation and scrolls to the message, or to the bottom when it is the newest (`NoodleNotifications.swift:153-170`). Errors are a single `errorMessage` string on the store, shown as an alert in the key window (`NoodleStore.swift:115`, `ConversationWindow.swift:146-160`).

**Sidebar.** Three sections, each hidden when empty: Pinned (in pin order), Bots (direct conversations) and Groups (`SidebarView.swift:20-24`, `NoodleStore.swift:495-509`). Within a section the order is the store's `conversations` order, which is `updatedAt` descending (`WorkspaceRepository.swift:809-813`; re-sorted after each send, `NoodleStore.swift:1468-1471`). The list excludes archived conversations and filters by the selected space (All, This Mac, a Hub, or a custom space with its own pins) and by search over title, group description, participant names and every message body (`NoodleStore.swift:463-493`). A row shows the avatar with its runtime dot, the title, a time (today) or abbreviated date, a two-line preview of the last message (or "No messages yet"), and a blue 8 pt unread dot in the leading inset (`SidebarView.swift:141-217`, `NoodleStore.swift:2181-2192`). Mobile uses one list: pinned first, then by the newest message time, falling back to creation time; Hub pins order by `pinnedAt` (`Chats.swift:304-317`, `348-351`). On mobile a pinned bot's bubble shows the start of an unread reply, else its status line (`Chats.swift:507-514`).

### 2.6 How the UI observes state, and performance tactics

- `NoodleStore` and `AgentRuntimeCoordinator` are `@MainActor @Observable`; everything SwiftUI should not track (tasks, caches, controllers, transcript revisions) is `@ObservationIgnored` (`NoodleStore.swift:30-32`, `127-181`; `AgentRuntimeCoordinator.swift:97-196`). Views read the store from the environment and call it directly; there is no reducer layer.
- The poll copies the current state, loads a snapshot on a detached utility task, and compares per-conversation revisions (`messages.json` modification time and size, the Attachments folder's modification time), reloading only changed transcripts (`NoodleStore.swift:1838-1922`).
- A `transcriptGeneration` counter, bumped by any local write to messages or attachments, makes a background snapshot that finished after a local change discard itself (`NoodleStore.swift:49-59`, `1855`).
- New bot messages are computed by diffing message ids against the known set, then drive four effects in order: heartbeat activity, waking other group members, unread, notifications (`NoodleStore.swift:1928-1970`).
- An id-keyed attachment lookup is rebuilt whenever the attachments dictionary changes (`NoodleStore.swift:52-58`, `1581-1584`).
- Caches: rendered Markdown and table segments keyed by message id (with a body check), and link detection keyed by body text, both `NSCache` with 1,000 entries (`MessageMarkdownCache.swift:6-56`, `Components.swift:157-181`).
- The draft is read only inside the composer's own views, so typing re-evaluates the composer and not the transcript (`ChatView.swift:381-382`). `TranscriptRenderProbe` counts bubble bodies and link scans so tests can assert this (`Components.swift:132-155`); tests cover "typing in the composer does not reevaluate transcript rows", "adding a reaction does not resize its transcript row", "a long conversation holds only its newest messages as it grows" and "reevaluated transcript rows do not detect their links again" (`Tests/NoodleAppTests/TranscriptLayoutTests.swift:288`, `346`, `360`, `383`).
- The transcript shows the newest 100 messages and adds 100 more each time the top is reached; rows are keyed by message id so a page load keeps the reading position, and later replies add rows instead of pushing old ones out (`ChatView.swift:486-562`). Rows sit in a `LazyVStack`; attachment thumbnails and link previews load only when a row is visible (`Components.swift:315-317`).
- Scroll position is saved at the end of a user scroll gesture, not per pixel, as `{ offset, isAtBottom, messageID }`; the view follows new messages only while at the bottom, and always after the user sends (`TranscriptScrollView.swift:21-26`, `138-167`).
- The whole transcript is rebuilt with `.id(conversation.id)` and no animation on a conversation switch (`ChatView.swift:196-211`).
- Editing a bot's settings is two-phase: a checkpoint copies every app-owned settings file, all writes happen, and on any error the checkpoint is restored; only after every write succeeds is the in-memory state published and the bot restarted (`AgentSettingsCheckpoint.swift:4-50`, `NoodleStore.swift:906-962`).

### 2.7 Groups: several agents in one conversation

- **Membership.** A group lists `participantIDs` (at least one bot, unique, all from the same place: this Mac or one Hub; sorted by UUID string, so its avatar shows effectively arbitrary members first) (`WorkspaceRepository.swift:611-639`, `NoodleStore.swift:1145-1149`; avatar of the first three, `Components.swift:42-60`).
- **Who is woken.** Every participant is woken for every user message (`NoodleStore.swift:1474`) and every other participant for every bot message in a group (`WorkspaceRepository.swift:1387-1404`, `NoodleStore.swift:2115-2127`); reactions wake as described in section 2.3. Direct conversations never wake anyone on a bot message.
- **Addressing.** Nothing routes by name. `@` in the composer opens a completion of all active bots, the group's members first, plus people the bots are shared with; choosing one inserts the plain name, removing the `@` (`AgentNameCompletion.swift:3-49`, "A plain-text editing operation, not a message mention or delivery primitive"; `ChatView.swift:434-437`). A bot's profile sheet "Reply" prefixes the draft with `"Name, "` (`ChatView.swift:118-120`, `349-351`).
- **Turn-taking.** None in code. Each woken bot reads the delivery, the named `participants` roster (with public descriptions and `lastActiveAt`, the time of its latest message in that conversation, `WorkspaceRepository.swift:1262-1300`) and decides itself whether to answer. Loops are discouraged only by the Messenger skill: system notices and reactions are "not a new request", "do not send routine acknowledgements or create reply loops", and reactions carry work status (eyes = received, hourglass = working, check mark = done) (`MessengerDocumentation.swift:16-35`).
- **Changes.** Adding or removing members or changing the description appends one system message combining the changes ("Ada and Kai were added to the group.", "The group description was updated: …") in the same transaction; creating, renaming or deleting a member bot produces none (`WorkspaceRepository.swift:663-778`, `MessengerDocumentation.swift:69-115`). New members' inbox offsets start at the current end of history, so they see only the notice and later messages unless they list history explicitly (`WorkspaceRepository.swift:703-716`).
- **Shared folders.** A group can share folders with its bots; changes restart the affected bots (`NoodleStore.swift:1151-1154`, `1233`).
- **Presentation.** On the Mac every bot message shows that bot's 27 pt avatar (`Components.swift:255-274`). Mobile shows the bot's name heading each run of its messages and its avatar beside the last of each run (`Chats.swift:516-534`).

### 2.8 Persistence

Root: `~/Library/Application Support/Noodle` in the sandbox container (`Sources/NoodleCore/HarnessSetup.swift:67-73`).

| Path (under root) | Contents |
|---|---|
| `Agents/<id>/agent.json` | `AgentRecord` plus private configuration (`AgentConfiguration.swift:36-60`) |
| `Agents/<id>/workspace/` | the bot's working directory: generated `AGENTS.md` (`CLAUDE.md` links to it), `preferences.md`, `memory.md`, `.agents/skills/messenger/`, `.noodle/inbox.json`, `.noodle/messenger-bridge/` (`WorkspaceRepository.swift:278-314`, `1099-1117`) |
| `Agents/<id>/runtime/<harness>-runtime[-extended].json` | harness session state; `.unfinished` marks an interrupted turn (`AgentStorageLayout.swift:19-32`, `AgentTurnRecovery.swift:3-37`) |
| `Conversations/<id>/conversation.json`, `messages.json`, `.messages.lock` | conversation and transcript |
| `Conversations/<id>/Attachments/<id>.<ext>`, `<id>.json` | attachment files and metadata |
| `Conversations/<id>/background.json`, `Backgrounds/` | wallpaper choice and media |
| `Conversations/<id>/effects.json`, `.effects.lock` | effect queue, at most 32 events, 64 KB |
| `conversation-state.json` | `{ version: 1, unreadConversationIDs }` |
| `pinned-conversations.json` | `{ version: 1, conversationIDs }` in pin order |
| `scroll-positions.json`, `conversation-windows.json` | per-conversation viewport and window frames |
| `spaces.json`, `spaces-sync.json` | custom spaces and their iCloud sync state |
| `usage.sqlite` | usage ledger |
| `Hubs/` | joined Hubs and their mirrors |

Writes are JSON with sorted keys and ISO 8601 dates, replaced atomically (`WorkspaceRepository.swift:1529-1540`). Messages, reactions and group changes take an exclusive `flock` per conversation (`WorkspaceRepository.swift:1542-1550`). Preferences and runtime bookkeeping live in `UserDefaults`: heartbeat settings and last-activity dates, session start dates, delivery mode, selected space (`Noodle.space`), floating windows, keyboard shortcuts (`AgentHeartbeat.swift:20-33`, `AgentRuntimeCoordinator.swift:414-417`). Drafts, runtime snapshots and activity logs are memory only on the Mac (`ConversationDrafts.swift:3-4`); mobile persists drafts, unsent messages and a cache of everything it shows (`Chats.swift:212-223`, `656-670`).

### 2.9 The Hub link protocol shared with mobile

`LinkProtocol` is the closest thing to a ready-made wire contract. Requests travel in an envelope `{ version, fetchesPictures, request }`; adding requests, events or fields keeps the version, every later field decodes with a default, and unknown events are skipped (`LinkProtocol.swift:6-87`). Dates are seconds since 1970 on this wire (`LinkProtocol.swift:76-86`), unlike the ISO 8601 files. Files travel as 512 KiB chunks, uploaded before the message that names them (`LinkProtocol.swift:13-14`, `HubMirror.swift:743-765`).

- Requests include `bots`, `createBot/updateBot/deleteBot`, `groups`, `createGroup/updateGroup/deleteGroup`, `archive`, `kick`, `confirmKick`, `newSession`, `messagePage {conversationID, before?, after?, limit=50}`, `send {conversationID, id, body, attachmentIDs}` with a device-chosen `id` so a retried send is never doubled, `upload`, `download`, `react {conversationID, messageID, emoji, present}`, `markRead {conversationID, messageID}`, `pin`, `setBackground`, `startCall`, and a `subscribe` that opens the event stream (`LinkProtocol.swift:89-231`, `1526-1549`).
- Events are mostly invalidations plus a few values: `conversationChanged {conversationID, count}`, `botsChanged`, `groupsChanged`, `messageChanged {message}` (reactions, a finished call card), `botPhase {botID, phase}`, `readChanged {conversationID, upTo}`, `pinChanged {conversationID, pinnedAt?}`, `backgroundChanged`, and resource-list changes (`LinkProtocol.swift:578-613`). The Hub emits them by diffing file sizes, counts and reaction sequences on each check, routed per person (`Sources/HubCore/HubBots.swift:927-975`).
- `LinkMessage` flattens the message: author `you | bot(id) | system`, `delivered: Bool`, attachments inline, reactions without ids (`LinkProtocol.swift:1336-1406`). `LinkBot` carries `phase`, `readUpTo`, `status`, `archivedAt`, `background`, `sharedWith`, `owner`, `canCall`, `pinnedAt` beside the editable draft (`LinkProtocol.swift:1149-1214`).
- Client sync: follow the stream with exponential reconnect (1 s doubling to 60 s), on connect re-sync everything, page messages after the last synced position, and re-read from the first user message not yet delivered (or call still running) so later delivery flags are picked up (`HubMirror.swift:493-556`, `684-727`).
- Mobile's optimistic send is the pattern a web client should copy: insert the message at once with its client id, keep it and its files locally, retry after pauses of 0, 2, 5 and 15 s only while it is still the newest message, settle when the server's copy arrives, and offer try again, take back (into the composer) or delete for a failed one (`Chats.swift:656-783`).

### 2.10 The Scenario format

A scenario is `Scenarios/<name>/scenario.json`, replayed through the real repository with stub harness processes, in development builds only, inside an isolated bundle (`com.pdparchitect.noodle.scenarios`) whose preferences and data folder are wiped on each launch (`Scenario.swift:11-13`, `913-915`, `1421-1428`). Loading is strict: any key that does not survive decode and re-encode is an error, "so a typo cannot silently change a screenshot" (`Scenario.swift:351-364`, `1621-1637`).

```ts
interface ScenarioFile {                     // Scenario.swift:13-304
  version: 1;
  title: string;
  clock?: string;                            // "HH:mm" today; default = wall clock
  appearance?: 'dark';                       // "Noodle is dark only" (Scenario.swift:445)
  settings?: Partial<Record<'chatAttachmentLayout' | 'BotNameStyle' | 'Noodle.firstBotSetup.dismissed'
    | 'Noodle.composer.showBotDescriptions' | 'Noodle.floatingConversations.keepsOne', boolean | number | string>>;
  harnesses: Record<HarnessProvider /* not 'apple' */, { models: 'builtin' /* claude-code only */ | ScenarioModel[] }>;
  agents: ScenarioAgent[];
  conversations?: ScenarioConversation[];
  present?: Presentation;
  timeline?: Step[];
  film?: Film;
}
interface ScenarioModel { id: string; name: string; description?: string; efforts?: string[]; defaultEffort?: string; default?: boolean }
interface ScenarioAgent {
  key: string;                               // unique, not "user"
  name: string; harness: HarnessProvider; model?: string; effort?: string;
  description?: string; backstory?: string;
  avatar?: { image?: string; tint?: 'white' | 'black'; symbol?: string; colour?: number };
  status?: ScenarioStatus;                   // its status the first time it starts
  unrestricted?: boolean;                    // extended access
  autoReplies?: string[];                    // answers cycled after the timeline ends
}
interface ScenarioStatus {
  phase: AgentRuntimePhase;
  detail?: string;                           // required when failed
  failure?: 'missingSession' | 'usageLimit' | 'authenticationRequired' | 'recoveryFailed';  // failed only
}
interface ScenarioConversation {
  key: string;
  direct?: string;                           // agent key; exactly one of direct | group
  group?: { name: string; description?: string; members: string[] };
  background?: { preset?: 'sunset' | 'ocean' | 'forest' | 'dusk'; image?: string; video?: string };  // exactly one
  unread?: boolean;
  pinned?: boolean;                          // pinned in listing order
  messages?: { key?: string; at: string; from: 'user' | string; text: string;
               delivery?: MessageDelivery;   // user: saved | delivered | failed; bots: delivered only
               attachments?: { file?: string; link?: string }[];   // exactly one of file | link
               reactions?: { from: 'user' | string; emoji: string }[] }[];
}
interface Presentation {
  window?: { size: [number, number]; origin?: [number, number] };
  sidebar?: 'visible' | 'hidden';
  select?: string; search?: string; draft?: string; draftAttachments?: string[];   // a draft needs select
  scroll?: Record<string /* conversation key */, 'bottom' | string /* message key */>;
  windows?: { conversation?: string; frame?: [number, number, number, number]; floating?: boolean;
              activity?: string /* agent key */; settings?: string /* tab */ }[];   // exactly one target each
  sheet?: { newBot?: boolean; newGroup?: boolean; editBot?: string; groupInfo?: string; background?: string };  // at most one
}
interface Step {                             // exactly one action, or only a wait
  wait?: number; agent?: string; in?: string;
  status?: ScenarioStatus;                   // stub process reports this snapshot
  reply?: Content;                           // bot message via sendAgentMessage
  say?: Content;                             // user message, wakes participants
  type?: Content & { interval?: number };    // typed into the composer then sent
  react?: { message: string /* message key */; emoji: string; remove?: boolean };  // by agent, else by user
  stream?: { text: string; chunk?: number; interval?: number };   // into the bot's activity log
  toolCall?: { title?: string; input: string; output?: string; exit?: number; duration?: number };  // activity log
  error?: string;                            // sets store.errorMessage
  focus?: 'composer' | 'transcript' | 'none';    // camera cue for recordings
  waitFor?: 'userMessage' | 'key';
  present?: Presentation;
  capture?: string;                          // screenshot name, no spaces or slashes
}
interface Content { key?: string; text: string; attachments?: { file?: string; link?: string }[] }
interface Film {
  background?: 'black' | 'white' | string;   // or an asset / URL behind the titles
  intro?: { icon?: string; iconTint?: 'white' | 'black'; kicker?: string; title?: string; subtitle?: string; hold?: number };
  outro?: { tagline?: string; hold?: number };
}
```

Times use a small grammar: `"HH:mm"` on the scenario day, `"-2d HH:mm"`, `"-15m"`, `"-3h"` relative to the clock (`Scenario.swift:1592-1618`). Media paths are assets in the scenario folder, shared assets under `Scenarios/cast/`, or web addresses that a script pre-fetches into `.cache/` named by the first 16 hex characters of their SHA-256 (`Scenario.swift:378-411`).

**Seeding** (`Scenario.swift:684-790`): preferences are applied and the delivery mode forced to `queue`; each harness gets a stub executable laid out as a Noodle-installed harness; bots are created through the real repository seven days before the clock, one minute apart, with avatar colour defaulting to their index; groups are created, messages appended with their reactions, backgrounds set; unread and pinned files written; every bot's inbox is consumed so seeded history does not wake anyone (`Scenario.swift:761`); scroll positions and window frames are written as the app would restore them; and anything the format does not model is copied over the repository from the scenario's `root/` folder.

**Playing**: a `ScenarioAgentProcess` stands in for every harness and only reports the scripted snapshot (default details per phase: "<Harness> ready", "Checking for new messages", "Starting <Harness>", "Stopped") (`Scenario.swift:649-668`, `796-842`). When it is notified and a user message is waiting, it records an arrival, which releases `waitFor: userMessage`; after the timeline ends, `autoReplies` answer with 1.5 s of `working` first (`Scenario.swift:951-969`). Steps run in order after their `wait` (`Scenario.swift:973-1145`); `type` sets the draft one character at a time with jittered intervals (default 0.075 s times 0.55-1.65) and sends what is staged in the composer too; `stream` appends chunks of 12 characters every 0.05 s by default; `toolCall` writes "Running command" then "Command completed (exit N)" after 0.8 s by default, plus "Tool output". A capture run talks to the driving script over stdout: `SCENARIO READY rect=…`, `SCENARIO SHOT <name> id=<window> rect=x,y,w,h`, `SCENARIO FOCUS x,y,w,h <seconds> steady|eased <epoch>`, `SCENARIO SOUND key|enter|reply <epoch>`, `SCENARIO DONE`, `SCENARIO FAILED <message>`; a newline on stdin answers a shot or advances a `waitFor: key` (`Scenario.swift:1155-1256`, `1327-1348`). The film wraps the timeline in fixed-timing title cards with an animated wordmark (`Scenario.swift:983-1039`, `ScenarioFilm.swift:14`, `151-190`, `330`).

## 3. Web translation

### 3.1 Wire types

Per the OpenCompany convention, payload keys on the wire are snake_case and the TypeScript side binds them to camelCase locals (`CLAUDE.md`, "Naming Conventions"). The wire shapes below are the section 2.2 entities with snake_case keys, plus these changes:

```ts
type AuthorWire = { kind: 'user' } | { kind: 'agent'; agent_id: string } | { kind: 'system' };   // flat, not "_0"
type DeliveryWire = 'sending' | 'queued' | 'delivered' | 'failed';   // 'sending' and 'failed' are client-side only

interface MessageWire {
  id: string;                       // client-chosen for user sends (idempotency key)
  conversation_id: string;
  position: number;                 // index in the append-only log; paging cursor
  author: AuthorWire;
  body: string;
  created_at: string;               // ISO 8601 with milliseconds
  delivery: 'queued' | 'delivered';
  attachments: AttachmentWire[];    // inline, as LinkMessage does
  reactions: { author: AuthorWire; emoji: string }[];
  call?: { agent_id: string; ended_at?: string; lines: { speaker: 'person' | 'bot'; text: string; at?: string }[] };
}

interface AgentRuntimeWire {
  agent_id: string;
  phase: 'offline' | 'starting' | 'ready' | 'working' | 'failed';
  detail: string;
  failure?: { kind: 'missing_session' | 'usage_limit' | 'authentication_required' | 'recovery_failed' | 'safety_stop';
              session_id?: string } | null;
  reconnecting_since?: string | null;
  can_kick: boolean;                // computed server-side, as Harnesses.swift:302
  conversation_id?: string | null;  // new: the conversation the current turn serves, when known
}

interface ActivityEntryWire {
  agent_id: string; entry_id: string; date: string;
  title: string; detail: string;
  stream_id?: string; appending: boolean;   // coalesce into the last entry with the same stream_id
}
```

### 3.2 Store shape

Server state, in TanStack Query:

| Query key | Holds | Kept fresh by |
|---|---|---|
| `['agents']` | `AgentRecord[]` (public fields, status line, archived) | `agent_changed`, `agent_removed` |
| `['conversations']` | `BotConversation[]` sorted by `updated_at` desc | `conversation_changed`, `message_appended` (bump and re-sort) |
| `['messages', conversationId]` | infinite query, pages of 100 by `position` (`before` cursor), newest page first | `message_appended` (append to the last page), `message_changed`, `messages_delivered` |
| `['unread']` | `Set<conversationId>` | `unread_changed` |
| `['pins', spaceId]` | ordered ids | `pin_changed` |
| `['spaces']` | `CustomSpace[]` | `spaces_changed` |
| `['backgrounds']` | per-conversation background | `background_changed` |
| `['harnesses']` | installations, models, capability errors | `harnesses_changed` |
| `['usage', range, agentId]` | usage days | on demand |

Push state, in Zustand with per-key selectors (the same slice-subscription idea as OpenCompany's `nodeStatusStore`): `runtime: Record<agentId, AgentRuntimeWire>` (high frequency, latest value wins, replaced wholesale on reconnect) and `activity: Record<agentId, { entries: ActivityEntryWire[]; revision: number }>` for subscribed bots only, bounded like Noodle's (500 entries, 256 KiB).

UI state, in Zustand (persist only what Noodle persists, plus drafts):

```ts
interface UiState {
  selectedConversationId: string | null;
  searchText: string;
  space: string | null;                                   // persisted, like UserDefaults "Noodle.space"
  drafts: Record<string, { text: string; attachmentIds: string[] }>;   // persist (Noodle loses them on quit)
  viewports: Record<string, { offset: number; isAtBottom: boolean; messageId?: string }>;   // persist
  pending: Record<string, { conversationId: string; reason?: string; attempts: number }>;   // unsent sends
  creationSheet: 'bot' | 'group' | null;
  editingAgentId: string | null; editingGroupId: string | null; editingBackgroundId: string | null;
  kickRequest: KickConfirmation | null;
  toasts: { id: string; message: string }[];              // instead of one errorMessage slot
}
```

Derived selectors (direct ports of the Swift):

```ts
export function conversationStatus(phases: AgentRuntimeWire['phase'][]): 'working' | 'failed' | 'ready' | 'idle' {
  if (phases.includes('working')) return 'working';
  if (phases.includes('failed')) return 'failed';
  if (phases.length > 0 && phases.every((p) => p === 'ready')) return 'ready';
  return 'idle';
}
export const STATUS_DOT = { working: 'bg-status-working', failed: 'bg-status-failed', ready: 'bg-status-ready', idle: 'bg-status-idle' };
// Noodle: blue / red / green / gray. Map through theme tokens, not palette names.

export function shownParticipants(c: BotConversation, agents: Map<string, AgentRecord>): AgentRecord[] {
  const all = c.participant_ids.map((id) => agents.get(id)).filter(Boolean) as AgentRecord[];
  if (c.kind !== 'group') return all;
  const active = all.filter((a) => !a.archived_at);
  return active.length ? active : all;
}

export function sidebarSections(convs: BotConversation[], pins: string[], visible: (c: BotConversation) => boolean) {
  const shown = convs.filter(visible);                    // archived, space and search filters
  const byId = new Map(shown.map((c) => [c.id, c]));
  const pinned = pins.map((id) => byId.get(id)).filter(Boolean) as BotConversation[];
  const rest = shown.filter((c) => !pins.includes(c.id)); // already updated_at desc
  return { pinned, bots: rest.filter((c) => c.kind === 'direct'), groups: rest.filter((c) => c.kind === 'group') };
}

export function deliveryLabel(m: MessageWire, pending?: { attempts: number; reason?: string }, sending?: boolean) {
  if (sending) return 'Sending…';
  if (pending) return 'Not delivered';
  return m.delivery === 'delivered' ? 'Delivered' : 'Sent';
}
```

Read tracking in the browser: treat a conversation as "viewed" when it is selected, `document.visibilityState === 'visible'` and the window has focus (the analogue of `ConversationWindow.swift:226-232`), and send `mark_read` with the newest message id on selection, focus, composer focus, scroll and draft change, as Noodle does. Keep unread server-owned so other devices agree.

### 3.3 Event contract

Server to client (push frames `{ type, data }`):

| Type | Data | Client action | Noodle analogue |
|---|---|---|---|
| `runtime_snapshot` | `{ agents: AgentRuntimeWire[] }` on connect | replace runtime slice | `snapshots` dictionary |
| `agent_runtime` | `AgentRuntimeWire` | set one slice | `onSnapshot` / `botPhase` |
| `agent_changed` / `agent_removed` | `{ agent }` / `{ agent_id }` | patch `['agents']` | `botsChanged`, status via Messenger |
| `conversation_changed` / `conversation_removed` | `{ conversation }` / `{ conversation_id }` | patch and re-sort `['conversations']` | `groupsChanged`, archive |
| `message_appended` | `{ message: MessageWire, count }` | append; bump `updated_at`; sound or notification if a bot wrote and the chat is not viewed | `conversationChanged(count)` plus refetch |
| `message_changed` | `{ message }` | replace by id | `messageChanged` |
| `messages_delivered` | `{ conversation_id, message_ids }` | flip `delivery` | `markDelivered` |
| `unread_changed` | `{ conversation_id, unread }` | patch `['unread']`; update the title badge | `registerUnreadMessages` |
| `read_changed` | `{ conversation_id, up_to_message_id }` | mark read if nothing newer from a bot | `readChanged` |
| `pin_changed` | `{ space_id, conversation_id, pinned_at }` | patch pins | `pinChanged` |
| `background_changed` | `{ conversation_id, background }` | patch backgrounds | `backgroundChanged` |
| `effect_queued` | `{ conversation_id, effect_id, kind, expires_at }` | if that chat is in front, `claim_effect` | `effects.json` |
| `activity_entry` | `ActivityEntryWire` | coalesce by `stream_id` | `AgentActivityLog.record` |
| `sign_in_required` | `{ agent_id, harness }` | toast or system notification, once per episode | `onSignInRequired` |
| `harnesses_changed` | `{}` | invalidate `['harnesses']` | capability refresh |
| `error` | `{ message, request_id? }` | toast | `errorMessage` |

Client to server (request/response, each answered with the result or `{ error }`):

| Type | Payload | Answer |
|---|---|---|
| `get_messages` | `{ conversation_id, before?, after?, limit }` | `{ messages, count, start }` |
| `send_message` | `{ conversation_id, id, body, attachment_ids }` (idempotent by `id`) | `{ message }` |
| `upload_attachment` | chunked `{ conversation_id, attachment, offset, data }` or HTTP multipart; staged before send | `{ attachment }` |
| `discard_attachment` | `{ conversation_id, attachment_id }` | `{ done }` |
| `react` | `{ conversation_id, message_id, emoji, present }` | `{ message }` |
| `mark_read` | `{ conversation_id, message_id }` | `{ done }` |
| `set_pinned` | `{ space_id, conversation_id, pinned }` | `{ done }` |
| `create_bot` / `update_bot` / `delete_bot` | bot draft as `LinkBotDraft` (`LinkProtocol.swift:1068-1118`) | `{ agent }` |
| `create_group` / `update_group` / `delete_group` | `{ name, public_description, bot_ids, folders? }` | `{ conversation }` |
| `set_archived` | `{ id, archived }` | `{ done }` |
| `kick` | `{ agent_id }` | `{ done }` or `{ kick_confirmation: { id, title, message, confirm_title, offers_new_session } }` |
| `confirm_kick` | `{ agent_id, confirmation_id }` | `{ done }` |
| `new_session` | `{ agent_id }` | `{ done }` |
| `subscribe_activity` / `unsubscribe_activity` | `{ agent_id }` | `{ entries, revision }` |
| `claim_effect` | `{ conversation_id }` | `{ effect }` or `{ effect: null }` (atomic, never replays) |
| `set_background` | `{ conversation_id, preset? , media_id? }` | `{ background }` |
| `set_delivery_mode` | `{ mode: 'automatic' | 'immediate' | 'queue' }` | `{ done }` |

### 3.4 A JSON fixture format for demos, Storybook and visual regression

Reuse Noodle's schema almost verbatim, so its scenarios can be ported by hand, with three web adjustments: `present.window` becomes `present.viewport: [width, height]`; `present.windows` keeps only `activity` (open the activity drawer) and `settings` (open a settings tab); and `film` is optional chrome for marketing recordings. Validate with a `.strict()` zod schema so unknown keys fail exactly as `Scenario.load` does, and keep the time grammar.

Run a fixture through an in-memory fake of the WebSocket server (inject the transport into the WebSocket provider) rather than mocking hooks, so the real store, query cache and selectors are exercised:

| Fixture element | Fake server behaviour |
|---|---|
| `agents`, `conversations` | seed the fake database; consume every bot inbox so history wakes nobody |
| `clock` | freeze `Date` at the clock; `now = clock + elapsed`, as `ScenarioSession.now` |
| `agents[].status` | first `runtime_snapshot` |
| `step.status` | emit `agent_runtime` |
| `step.reply` | append a bot message, emit `message_appended` |
| `step.say` | append a user message, emit `message_appended`, "wake" its participants |
| `step.type` | drive the composer through the UI store, then the real send path |
| `step.react` | emit `message_changed` |
| `step.stream`, `step.toolCall` | emit `activity_entry` with `stream_id` and `appending` |
| `step.error` | emit `error` |
| `step.present` | set UI store fields (selection, search, draft, sheet, viewport) |
| `step.waitFor: userMessage` | resolve when the client sends `send_message` |
| `step.waitFor: key` | pause until the Storybook "next step" control or a Playwright call |
| `step.capture` | Playwright `toHaveScreenshot(name)` at that point |
| `autoReplies` | after the timeline, answer each user message after 1.5 s of `working` |

A minimal example:

```json
{
  "version": 1,
  "title": "A bot that needs attention",
  "clock": "16:05",
  "harnesses": { "claude-code": { "models": "builtin" } },
  "agents": [
    { "key": "rex", "name": "Rex", "harness": "claude-code", "model": "sonnet",
      "status": { "phase": "failed", "failure": "usageLimit", "detail": "Usage limit reached. Kick to resume." } }
  ],
  "conversations": [
    { "key": "rex", "direct": "rex", "background": { "preset": "dusk" },
      "messages": [ { "at": "15:20", "from": "user", "text": "Sort the new reports." },
                    { "at": "15:26", "from": "rex", "text": "Sorted. Starting on the first one." } ] }
  ],
  "present": { "viewport": [1160, 810], "select": "rex" },
  "timeline": [ { "capture": "01-usage-limit" } ]
}
```

## 4. Patterns worth copying, and pitfalls to avoid

Patterns worth copying:

1. **Append-only transcript with positional cursors and client-chosen message ids.** Paging, resync and idempotent retries all fall out of it (`LinkProtocol.swift:1526-1579`, `Chats.swift:692-750`).
2. **Delivery as "a bot has read it".** Per-bot inbox cursors make "Sent" versus "Delivered" honest and also give bots exactly-once reading (`WorkspaceRepository.swift:1119-1260`).
3. **One snapshot per bot, typed failures, human detail.** UI decisions use `phase` and `failure`, never text; the `detail` sentence goes straight into tooltips (`Harnesses.swift:283-319`).
4. **A tested precedence for folding many bots into one dot** (working, then failed, then all-ready, else idle).
5. **Kick confirmations bound to the exact failure episode**, so a stale dialog cannot restart a bot that has since changed (`AgentRuntimeCoordinator.swift:745-751`).
6. **Unread as a boolean per conversation, cleared by any interaction**, with cross-device reads keyed by message id and never moving backwards (`HubBots.swift:991-1003`).
7. **Effects as an expiring queue claimed by the visible chat**, so reopening a chat never replays confetti and a background tab never consumes it (`ConversationEffects.swift:37-92`, `ConversationEffectsView.swift:28-80`).
8. **Commit, then publish**: multi-resource bot edits are saved transactionally with rollback before the in-memory state changes (`AgentSettingsCheckpoint.swift`, `NoodleStore.swift:949`).
9. **Generation counters for background loads** and **render probes in tests** to keep typing and reactions from re-rendering the transcript.
10. **Activity kept apart from the conversation**: bounded, coalesced by stream id, never acknowledging messages, and opened on demand.
11. **Strict fixture files with a relative time grammar**, seeded through the real write path, doubling as demo data, screenshot tests and film scripts.
12. **Approvals by policy**, not by prompts in the chat: access is a per-bot setting and a bot that needs a decision asks in words.

Pitfalls to avoid:

1. **Polling files every second and rewriting the whole `messages.json` on every append and reaction** is O(n) per message. Use a database and push events (`NoodleStore.swift:2218-2233`, `WorkspaceRepository.swift:1090-1097`).
2. **Count-only change events force a refetch**; send the appended message itself, as proposed above.
3. **States that are never emitted**: `saved` and `failed` exist and are labelled but nothing sets them on the Mac. Define only states the server produces; keep "sending" and "failed" client-side.
4. **Phase is per bot, not per conversation.** A bot working in one chat turns every chat it belongs to blue. Do not render a typing indicator from it unless the server says which conversation the turn serves (the proposed `conversation_id`).
5. **Unbounded group fan-out.** Every bot wakes for every message, including each other's; only prompt text prevents loops. Add server-side guards (turn budgets, rate limits, quiet periods) before shipping groups.
6. **Arbitrary member order.** Group participants are stored sorted by UUID; keep the user's order for avatars and lists.
7. **Unexpected reordering.** Renaming a bot bumps its direct conversation's `updatedAt` (`NoodleStore.swift:1037-1042`), which moves it to the top of the sidebar.
8. **One error slot.** `errorMessage` is overwritten by the next failure; use a queue of toasts.
9. **Second-precision timestamps on disk** (ISO 8601 without fractions, inferred) make `createdAt` ties likely; order by position and use milliseconds on the wire. Pick one date encoding: the files and the Hub wire currently differ.
10. **Swift enum encodings on the wire** (`{"agent":{"_0":…}}`) are awkward outside Swift; use flat discriminated unions.
11. **Client-side search over every message body** does not scale past a few conversations; search on the server.
12. **Memory-only drafts** are lost on quit; persist them, as mobile does.
13. **A failed turn shows as `ready`** with a detail suffix ("ready — last task failed"), so the dot stays green after an error; consider a separate "last turn failed" flag.

## 5. Open questions and inferences

- Exact JSON for `MessageAuthor` and `AgentRuntimeFailure` on disk is inferred from Swift's synthesized `Codable` (no custom coding exists; the `_0` shape is asserted for other enums in `LinkCompatibilityTests.swift`). Uppercase UUIDs and second-precision ISO dates in the files are inferred from Foundation defaults.
- Whether the Activity window is meant to stay a separate window or become part of the conversation view is a product question; the code keeps it separate and the films open it beside the chat (inferred from `Scenarios/code-review/scenario.json:71`).
- How a web version should present "working": Noodle has no per-conversation signal. A turn-to-conversation mapping would need the runtime to report which inbox items a turn consumed (inferred to be feasible from `latestMessages`, which knows the conversations it returned).
- The Hub's per-person routing of events (owner versus guests; guests receive phase but not the status line, `HubBots.swift:964-967`) matters if the web product becomes multi-user; this document did not trace Hub authorization.
- Delivery-mode classification relies on Apple's on-device model; a web backend would need its own classifier or a simpler rule (the stop-phrase heuristic alone is portable).
- Whether bots should be allowed to send effects in groups at the same rate as in direct chats (2 s per conversation, 30 s expiry) was not examined beyond the queue rules.
- Sidebar recency on the Mac uses `updatedAt` while mobile uses the newest message time; a web version should pick one (the newest message time is less surprising).
