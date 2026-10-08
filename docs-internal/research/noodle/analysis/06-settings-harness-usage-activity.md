# Noodle settings, harness setup, usage and activity

Noodle's operational UI is one macOS Settings window with eleven toolbar tabs, plus two free-standing windows (Usage, and one floating Activity log per bot) and a family of sheets opened from settings rows (harness profiles, local and remote models, tool catalog and editor, Hub join and invitation). Almost everything is a stock SwiftUI grouped `Form`; a small shared package (`Shared/SettingsUI`, module `NoodleSettingsUI`) patches three macOS gaps: tab content that fits its height but never exceeds the screen, scroll indicators that flash while the window resizes, and toolbar tab badges SwiftUI cannot draw. Every row family (harness, permission, companion, Hub, tool) shares one anatomy: a 32 pt secondary-tinted icon column, a semibold title with a right-aligned caption status label (SF Symbol plus text in green, orange or secondary), caption detail lines, then one line of link-style actions. Status is always derived from facts in a fixed priority order, never stored as display text, and the last confirmed harness presentation is cached so the Harness tab paints instantly and re-checks in the background. Destructive actions always confirm with a sentence describing consequences, and Cancel is the keyboard default.

Permalink base for every citation below: https://github.com/pdparchitect/noodle/blob/702339e3153025c9fe9582783caff7a19b48ff97/

## 1. Scope

Read in full:

- `Sources/Noodle/`: `NoodleSettingsView.swift`, `HubSettingsView.swift`, `PermissionsSettingsView.swift`, `KeybindingsSettingsView.swift`, `UsageWindow.swift`, `ToolExtensionDiscovery.swift`.
- `Sources/NoodleRuntimeSettings/`: `HarnessSettingsView.swift`, `HarnessProfilesView.swift`, `HarnessSignInChallengeView.swift`, `HarnessProviderIcon.swift`, `AppleRemoteModelsView.swift`, `AppleLocalModelsView.swift`, `UsageView.swift`, `AgentActivityWindow.swift`, `MCPSettingsView.swift`, `MCPController.swift`, `ToolCatalogView.swift`, `CompanionAppsSettingsView.swift`, `CompanionUpdateChecker.swift`, `CompanionApp.swift`, `HubLinkRows.swift`, `HubInvitationSheet.swift`, `SettingsRowList.swift`, `SettingsStatusLabel.swift`, `ConversationRuntimeSettings.swift`.
- `Shared/SettingsUI/Sources/NoodleSettingsUI/`: all seven files.
- Docs: `docs/harness-setup.md`, `docs/mcp-connections.md`, `docs/security.md`, `docs/privacy.md` (skimmed), plus `docs/usage.md` and `docs/keyboard-shortcuts.md` for context.

Read in part for exact strings and rules: `Sources/NoodleCore/{Harnesses,HarnessSetup,HarnessVersions,HarnessPresentationCache,KeyboardShortcuts,ToolCatalog,UsageLedger,AgentSessionRollover,MessageDelivery}.swift`, `Sources/NoodleRuntime/{HarnessSetupController,AgentActivityLog,AgentActivityParser,UsageHistory}.swift`, `Sources/Noodle/{AppUpdater,KeyboardBindings,MCPAssignmentPicker,ThisMacHub,GroupsSettingsView,NoodleApp,SidebarView}.swift`, `Sources/NoodleRuntimeSettings/{AgentKickConfirmation,BotsSettingsView}.swift` (access copy only; the Bots tab belongs to another analysis), `Sources/HubCore/HubReachability.swift`, `Shared/HubLink/.../{LinkProtocol,LinkIdentity,LinkInvitationImage,HubTroubleshooting}.swift`, `Hub/Sources/NoodleHub/{HubSettings,HubAccessSettings,HubLinkSettings}.swift`, `Mobile/Sources/NoodleMobile/HubScreens.swift` (troubleshooting sheet), `CHANGELOG.md`.

Screenshots viewed: `website/assets/screenshot-harness.png` (Noodle Settings, Harness tab, dark) and `website/assets/screenshot-hub.png` (Noodle Hub app Settings, Users and Plans tabs, dark).

Both screenshots predate this commit. The harness screenshot shows tabs General, Conversation, Harness, Heartbeat, Sandbox, Tools, Keybindings, Companions, Update; `CHANGELOG.md:138` (0.41.0, 2026-10-03) records that "Settings has one Bots tab in place of Heartbeat and Sandbox". It also shows bordered "Sign In…" and "Local Models…" buttons, a "Version 0.154.0" line under the path, and no Antigravity row, where the current code uses link buttons on one action line, shows the version after the path separated by "·", and lists Antigravity. The Hub screenshot shows bordered "Invite…" and "Remove…" buttons and an inline plan popup, where current code uses link buttons and puts the plan picker in the row's actions menu. Treat the screenshots as ground truth for the overall look (grouped dark container, row rhythm, status label style, footer bar) and the code as ground truth for current controls and copy.

## 2. Findings

### 2.1 Settings window shell

Tabs, in order (`Sources/Noodle/NoodleSettingsView.swift:40-91`). The enum is `NoodleSettingsTab` (`:8-10`); several cases display a different name.

| # | Label | SF Symbol | Enum case | Badge count (`:94-98`) |
|---|---|---|---|---|
| 1 | General | `gearshape` | `general` | none |
| 2 | Conversation | `bubble.left.and.bubble.right` | `chat` | none |
| 3 | Harness | `terminal` | `harnesses` | harnesses needing attention |
| 4 | Bots | `sparkles` | `bots` | none |
| 5 | Groups | `person.3` | `groups` | none |
| 6 | Tools | `puzzlepiece.extension` | `mcps` | connections whose row shows an error |
| 7 | Keybindings | `keyboard` | `keybindings` | none |
| 8 | Permissions | `hand.raised` | `permissions` | permissions the user refused |
| 9 | Companions | `square.stack.3d.up` | `companions` | companions behind their update feed |
| 10 | Hub | `server.rack` | `hub` | none |
| 11 | Update | `arrow.triangle.2.circlepath` | `updates` | 1 when a Noodle update is available |

The Harness count (`:20-27`) is the number of providers where `HarnessSetupController.needsAttention` is true (a setup error, a compatibility issue, or an available update; `Sources/NoodleRuntime/HarnessSetupController.swift:78-83`) or where any bot on that harness has `canKick && phase == .failed`. Reconnecting bots, uninstalled harnesses and signed-out harnesses are not counted. Permissions counts only `.denied`, never `.notRequested` (`PermissionsSettingsView.swift:93-95`), because an unrequested permission is asked for on first use.

Window rules:

- Every tab is wrapped in `.settingsContentSize(width: 740)` (`NoodleSettingsView.swift:34-35`): "Wide enough for every tab in the toolbar, none left in its overflow menu". The badge code depends on no overflow (see below).
- The scene uses `.windowResizability(.contentSize)` (`Sources/Noodle/NoodleApp.swift:189-193`) and the TabView has `.windowResizeAnchor(.top)` (`NoodleSettingsView.swift:214-219`): the window height follows the selected tab and its top edge stays put.
- Selection is bound to `store.selectedSettingsTab` with `.animation(.easeInOut(duration: 0.22))` (`:40`), so the height change animates. Other surfaces deep-link by setting the tab: `showHarnessSettings()` sets `.harnesses` (`:222`), used by the sign-in notification (`NoodleApp.swift:285`) and the Kick alert's "Open Harness Settings" button (`AgentKickConfirmation.swift:15-19`). An invitation link received from anywhere selects the Hub tab and opens Settings, which presents the join sheet pre-filled (`NoodleStore.swift:868-869`, `NoodleApp.swift:357-360`, `HubSettingsView.swift:89-91`).
- The window title is the selected tab's label (the screenshot reads "Harness").
- On open, `.onAppear` refreshes companion update feeds, asks Sparkle quietly whether an update exists, and re-reads permissions; `.task` runs the full harness refresh (`NoodleSettingsView.swift:99-105`). Badges are therefore correct before a tab is selected.

Content sizing (`Shared/SettingsUI/.../SettingsContentSize.swift`). A custom `Layout` reports the content's ideal height capped at `maxHeight` and then lays the content out in the bounds it is actually given, so a `Form` or `ScrollView` inside scrolls instead of running off screen (`:33-47`). `maxHeight` is the screen's `visibleFrame.height` (below the menu bar, above the Dock) minus the window's chrome (title bar plus toolbar), floored at 200 pt (`:52-57`). A zero-hit-test observer view re-reports on `NSWindow.didChangeScreenNotification` and `NSApplication.didChangeScreenParametersNotification`, deferring the state change out of the layout pass (`:75-93`).

Scroll indicators (`SettingsScrollIndicators.swift`, macOS 27 only). Indicators are forced hidden in the very update that changes the selected tab and on every window resize, and restored only after 350 ms with no further resize (`:51-58`); a live drag never restores them midway. It uses `.hidden`, which keeps the scrollbar gutter for users who always show scrollbars, and writes the environment values directly because switching the modifier back to automatic could stay hidden on macOS 27 (`:28-36`).

Tab badges (`SettingsTabBadge.swift`). SwiftUI's `badge` does not reach Settings tabs, and SwiftUI clears `NSToolbarItem.badge` on every window update, which makes AppKit rebuild and flicker its badge view (`:4-10`). Noodle instead adds its own `CountView` as a subview of each tab's toolbar button. Geometry copies the macOS 26 toolbar badge: height 12 pt, width `max(12, ceil(textWidth) + 4)`, corner radius 6 (full capsule), `systemRed` fill, white system font 8 pt centred, pinned to the button's top-trailing corner (top-leading in right-to-left) with an autoresizing mask so it stays pinned, `hitTest` returning nil so clicks reach the tab, and an accessibility static-text label of the count (`:92-141`). Buttons are found by walking the title bar's view tree (excluding the window's own traffic-light and toolbar buttons), sorting by x, and zipping them with `toolbar.visibleItems` by label; any count mismatch, such as an overflow chevron, yields no badges at all (`:72-89`). A `CFRunLoop` observer (`beforeWaiting`) re-applies after the toolbar's buttons exist (`:39-45`). Badges are keyed by tab label strings ("Harness", "Tools", "Permissions", "Companions", "Update"), so renaming a tab silently drops its badge. macOS 26 and later only (`:49`).

Noodle Hub's Settings window reuses the same toolkit (`Hub/Sources/NoodleHub/HubSettings.swift:95-160`): tabs Network (`network`, selected by default), Harness, Users (`person.2`), Plans (`rectangle.stack.badge.person.crop`), Bots, Groups, Conversation, a hidden Tools tab, Companions, Update; badges on Harness, Network (1 when the link failed), Companions and Update. Its pane width is 680 with the stale comment "The same pane width as Noodle's settings" (`:169-172`); Noodle now uses 740.

### 2.2 Recurring row, list, status and sheet patterns

Status label (`Sources/NoodleRuntimeSettings/SettingsStatusLabel.swift:8-14`): `Label(title, systemImage:)` with `.labelStyle(.titleAndIcon)`, `.font(.caption)`, one foreground colour for icon and text, `.fixedSize()` so it never wraps. It always sits at the trailing end of the title line. The full vocabulary across surfaces:

| Surface | Title | SF Symbol | Colour |
|---|---|---|---|
| Harness, profile | Signed in | `checkmark.circle.fill` | green |
| Harness, profile | Ready | `checkmark.circle.fill` | green |
| Harness, profile, tool | Sign-in required | `person.crop.circle.badge.questionmark` | secondary |
| Harness | Sign-in status unknown / Checking sign-in… / Installed | `person.crop.circle.badge.questionmark` | secondary |
| Harness | Checking… | `ellipsis.circle` | secondary |
| Harness, companion | Not installed | `arrow.down.circle` | secondary |
| Harness, tool | Needs attention | `exclamationmark.triangle` | orange |
| Harness | Reconnecting | `arrow.trianglehead.2.clockwise` | orange |
| Harness | Setting up | (falls through to the icon of the underlying state) | (underlying) |
| Companion | Installed | `checkmark.circle.fill` | green |
| Tool, joined Hub | Connected | `checkmark.circle.fill` | green |
| Joined Hub | Connecting… | `circle.dotted` | secondary |
| Joined Hub | Not connected | `exclamationmark.circle.fill` | orange |
| Hub link | (reachability summary) | `checkmark.circle.fill` or `exclamationmark.circle.fill` | green or orange |
| Hub link | Starting… | `circle.dotted` | secondary |
| Permission | Allowed | `checkmark.circle.fill` | green |
| Permission | Not requested | `questionmark.circle` | secondary |
| Permission | Not allowed | `exclamationmark.triangle.fill` | orange |
| Permission | Checking… | `circle.dotted` | secondary |

Colour semantics are consistent: green means usable, orange means the user must act or something failed but is recoverable, secondary means neutral or pending, and red is reserved for inline error text (caption, selectable, wrapping) and the badge fill.

Icon row anatomy (harness `HarnessSettingsView.swift:120-240`, permission `PermissionsSettingsView.swift:197-222`, companion `CompanionAppsSettingsView.swift:60-110`, Hub `HubSettingsView.swift:94-114,139-179`):

- `HStack(alignment: .top, spacing: 12)`.
- Icon column: SF Symbol at `.system(size: 24)`, `.secondary`, in a 32 x 32 frame, accessibility hidden. Harness marks are template images at 28 x 28 plus 2 pt padding inside the same 32 x 32 (`HarnessSettingsView.swift:122-127`).
- Text column: `VStack(alignment: .leading, spacing: 6)`; first line is the name `.fontWeight(.semibold)`, a `Spacer`, then the status label; following lines are `.font(.caption)` `.secondary`, `fixedSize(horizontal: false, vertical: true)` so they wrap.
- Actions: `.buttonStyle(.link)` text buttons in an `HStack(spacing: 12)`, either on their own line (harness) or trailing the summary line on its first text baseline (permission, companion, Hub).
- Row padding: `.padding(.vertical, 4)` (6 for harness rows).

Grouped lists inside a form section use `SettingsRowList` (`SettingsRowList.swift`): rows in a `VStack(spacing: 10)` separated by `Divider`, toggles forced to `.switch` style, and a custom `SettingsBotListLayout` that proposes a fixed 360 pt height during measurement so the list never resizes the Settings window after the first layout (`:47-58`). `ViewThatFits(in: .vertical)` shows the rows plainly when they fit and a `ScrollView` otherwise; the scroll view gets 20 pt of trailing padding inside and minus 20 pt outside, so its scrollbar sits in the form's trailing margin while rows stay aligned with other controls (`:10,13-24`). The Groups tab uses it with a 64 pt trailing "Archived" switch column and an "Archived" header button that opens an explanation popover (`GroupsSettingsView.swift:9,21-35,55-69`).

Footer action bar, used by Harness, Keybindings, Companions, Tools and the Hub app's Users/Plans tabs: `Divider()` then `HStack { Spacer(); [optional small ProgressView]; Button }` with `.padding(.horizontal, 20).padding(.vertical, 12)` (`HarnessSettingsView.swift:47-60`, `CompanionAppsSettingsView.swift:34-41`, `HubAccessSettings.swift:408-418`).

Sheet anatomy, used by Profiles, Local Models, Remote Models, Hub join and invitation: title `.font(.title2.bold())` at the top of a `VStack(alignment: .leading, spacing: 18)` padded 24, then `Divider`, then a footer `HStack` padded 24 horizontal and 16 vertical with secondary actions leading and "Done" (`.keyboardShortcut(.defaultAction)`) trailing (`HarnessProfilesView.swift:23-62`). Widths: 480 (profiles, local and remote models, MCP editor, tool catalog), 460 (Hub join, model editor, Hub users), 420 x 360 (Hub archived), 400 (invitation), 380 (account form). Sheets that change height use `noodleSheetSizing(animated:)` (`SheetSizing.swift`): `fixedSize(vertical)`, `.presentationSizing(.fitted)`, plus a bridge that resizes the real sheet window to the measured content outside the layout pass, coalescing measurements, animating with the top edge pinned unless Reduce Motion is on, so editors are never recreated and lose drafts (`:16-66`). Smaller form sheets (account form, model form) use a headline title and `padding(20)`.

Row actions menu: `Menu { … } label: { Image(systemName: "ellipsis.circle") }` with `.menuStyle(.borderlessButton)`, `.menuIndicator(.hidden)`, `.fixedSize()` and an accessibility label such as "Profile Actions", "User Actions" or "Actions for <name>" (`HarnessProfilesView.swift:100-113`, `HubSettingsView.swift:431-456`, `AppleRemoteModelsView.swift:158-186`). Order inside: non-destructive commands, `Divider`, destructive "Remove" or "Delete" last.

Confirmations: `.alert` or `.confirmationDialog` whose title names the object ("Remove <name>?", "Leave <hub>?", "Delete Profile?"), a destructive button with the verb, and `Button("Cancel", role: .cancel) {}.keyboardShortcut(.defaultAction)`, which makes Return cancel rather than destroy (`HubSettingsView.swift:45-51,196-201`, `HarnessProfilesView.swift:71-77`). The message always states the consequence and what is kept, for example "Bots that use <harness> stop working until it is installed again. Your sign-in is kept." (`HarnessSettingsView.swift:396-398`).

Explain-on-click: a caption-sized plain `Button` styled as text (a column header like "Archived", "Unrestricted", "Apps", a tag like "Experimental", or a status word like "restricted") opens a `.popover` with a headline and one to three paragraphs, `.padding(20)`, `.frame(width: 360, alignment: .leading)` (`HarnessSettingsView.swift:133-149`, `GroupsSettingsView.swift:75-86`, `BotsSettingsView.swift:309-334`).

Delayed progress: background checks never flash a spinner. The Harness footer spinner turns visible only after 300 ms of continuous refreshing and is kept in layout at opacity 0 otherwise (`HarnessSettingsView.swift:49-54,63-70`); while a version re-check runs, an old check error is replaced by "Checking for updates…" rather than removed (`:342-348`).

Relative times re-render on a timer: device "Last seen …"/"Joined …" via `TimelineView(.periodic(from: .now, by: 15))` (`HubSettingsView.swift:60-69`), the invitation countdown and the reconnecting timer every 1 s (`HubInvitationSheet.swift:99-108`, `HarnessSettingsView.swift:276-280`).

Refresh on return to the app: Harness, Companions, Permissions and the Hub app's Network tab all re-check on `NSApplication.didBecomeActiveNotification`, because the most likely moment for an external change is returning from Terminal, System Settings or a browser (`PermissionsSettingsView.swift:191-194`).

### 2.3 General, Conversation and Update tabs

| Tab | Control | Copy and options | Default |
|---|---|---|---|
| General | Segmented picker | "Generated bot names": Real, Playful | Real |
| General | Toggle + footer | "Keep Mac awake while agents work"; footer "Prevents automatic idle sleep only while an agent is working. Closing the lid or choosing Sleep still suspends the Mac." | runtime setting |
| Conversation | Segmented picker | "Attachment layout": Wrap, Vertical, Stack; help is the selected option's explanation ("Fit attachments side by side and wrap onto new rows as needed.", "Show attachments one below another.", "Overlap attachments while keeping part of each one visible.") | Wrap |
| Conversation | Picker + footer | "Message delivery": Automatic, Send immediately, Queue; footer "Automatic uses Apple Intelligence to decide whether new messages should reach a busy agent immediately or wait until its turn finishes. When Apple Intelligence is unavailable, messages wait." | Automatic |
| Conversation | Two pickers + footer | "New session": Never, Daily, Every 3 days, Weekly; "When idle for": 30 minutes, 1 hour, 2 hours, 4 hours (disabled when Never); footer "An idle bot starts a fresh session once its current one is this old. Its workspace, memory and messages are kept." | Daily, 1 hour |
| Conversation | Picker | "Message received sound": None, divider, Basso, Blow, Bottle, Frog, Funk, Glass, Hero, Morse, Ping, Pop, Purr, Sosumi, Submarine, Tink; plays on change | Blow |
| Conversation | Picker | "Microphone": "System Default — <device>", then devices, plus "Selected microphone unavailable" when the saved one is missing; device list polled every 2 s while visible | System Default |
| Conversation | Toggle + footer | "Show descriptions in the @ name menu"; footer "Show each bot's public description beside its name. Private backstories are never shown." | on |
| Conversation | Toggle | "Keep one floating conversation"; help "Floating another conversation replaces the open one, in the same place and size." | off |
| Conversation | Toggle | "Preview web links"; help "Open web links in Quick Look first, with a button to continue in your browser. When off, links open in your browser." | on |
| Update | LabeledContent | "Installed Version": short version or "Development"; then "Update available — <version>" in orange caption | |
| Update | Button | "Check for Updates", or "Install Update…" once a version is known; disabled when Sparkle cannot check | |
| Update | Toggles | "Automatically check for updates"; "Automatically download and install updates" (disabled unless Sparkle allows automatic updates) | Sparkle |

Sources: `NoodleSettingsView.swift:109-212`, `ConversationRuntimeSettings.swift:14-37`, `Sources/NoodleCore/AgentSessionRollover.swift:8-46`, `Sources/Noodle/AppUpdater.swift:76-103`, `Shared/SettingsUI/.../UpdateSettingsButton.swift:16-22`. Pickers that hold a value outside their option list add it (`ConversationRuntimeSettings.swift:40-42`), so a stored custom value never renders blank. `docs/usage.md:209-210` mentions a "link-preview timeout" in Conversation settings that does not exist in this code.

### 2.4 Harness tab

Layout (`HarnessSettingsView.swift:21-75`): one grouped section listing every provider in `HarnessProvider.allCases` order, which is Codex, Claude Code, Muse Code, Grok Build, FX, OpenCode, Antigravity, Apple Intelligence ("the four the welcome offers, then the rest, experimental last", `Sources/NoodleCore/Harnesses.swift:3-12`). A second section with the toggle "Update harnesses installed by Noodle automatically" appears only when at least one displayed harness was installed by Noodle (default on, key `Noodle.harness.automaticUpdates`). The footer has the delayed spinner (accessibility label "Checking for harnesses") and "Check Again", which forces a fresh latest-version lookup and is disabled while any refresh runs. The tab refreshes on appear and on app activation, and cancels every in-flight install or sign-in on disappear (`:62-74`).

Per-harness facts:

| Harness | Icon | Install command | Update command | Sign-in in Noodle |
|---|---|---|---|---|
| Codex | `CodexHarness` | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` | same as install | Sign In (device code challenge) |
| Claude Code | `ClaudeHarness` | `curl -fsSL https://claude.ai/install.sh \| bash` | `claude update` | Sign In (opens browser) |
| Muse Code | `MuseHarness` | `curl -fsSL https://dev.meta.ai/install.sh \| bash` | same as install | Sign In |
| Grok Build | `GrokHarness` | `curl -fsSL https://x.ai/cli/install.sh \| bash` | `grok update` | Sign In |
| FX | `FxHarness` | `curl -fsSL https://fx.sh/setup.sh \| bash` | `fx upgrade` | Sign In |
| OpenCode | `OpenCodeHarness` | `curl -fsSL https://opencode.ai/v2/install \| bash` | same as install | Terminal command `opencode auth login` |
| Antigravity | `AntigravityHarness` | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` | `agy update` | Terminal command `agy` |
| Apple Intelligence | `apple.logo` (SF Symbol) | bundled, no install | reinstall Noodle | not required |

Icons are monochrome template SVGs in `Support/Assets.xcassets` (`HarnessProviderIcon.swift:8-34`; the website ships the same marks as `website/assets/harness-*.svg`). Install commands and instructions come from each provider's `installationGuide` (for example `Sources/NoodleRuntime/ClaudeCodeSetupProvider.swift:10-13`: "Run Anthropic's official installer in Terminal, then return here and check the installation. Claude Code opens your browser when you sign in."); update commands from `HarnessVersionPolicy.updateGuide` (`Sources/NoodleCore/HarnessVersions.swift:168-202`). Terminal sign-in is used by providers that implement `terminalSignIn` (`OpenCodeSetupProvider.swift:20`, `AntigravitySetupProvider.swift:18`); for a copy Noodle installed, the command names the executable by its full quoted path because it is not on the shell's PATH.

Status derivation (`HarnessSettingsView.swift:441-469`). The first matching rule wins for the title; icon and colour use their own slightly different order.

| Priority | Condition | Title | Icon | Colour |
|---|---|---|---|---|
| 1 | an install or sign-in is in progress (`activity`) | Setting up | (rules 2-9) | (rules 2-9) |
| 2 | a setup error, or a bot on this harness failed | Needs attention | `exclamationmark.triangle` | orange |
| 3 | a bot on this harness is reconnecting | Reconnecting | `arrow.trianglehead.2.clockwise` | orange |
| 4 | never checked (no snapshot) | Checking… | `ellipsis.circle` | secondary |
| 5 | not installed | Not installed | `arrow.down.circle` | secondary |
| 6 | authenticated | Signed in | `checkmark.circle.fill` | green |
| 7 | unauthenticated | Sign-in required | `person.crop.circle.badge.questionmark` | secondary |
| 8 | sign-in not required | Ready | `checkmark.circle.fill` | green |
| 9 | managed externally (status unreadable) | Sign-in status unknown | `person.crop.circle.badge.questionmark` | secondary |
| 10 | no sign-in result yet | Checking sign-in… while checking, else Installed | `person.crop.circle.badge.questionmark` | secondary |

When any bot on the harness can be kicked, the status label becomes a plain button (help "Show affected bots") opening a 340 pt popover anchored at the bottom edge (`:245-305`). Each affected bot shows its name in semibold, then either an orange caption "Reconnecting… · 3m 12s" ticking every second, or the runtime's detail text (secondary, selectable), then small "Kick" and "New Session" buttons, rows separated by dividers, `padding(16)`, scrolling past the same 360 pt cap. "Kick" opens the recovery alert (titles such as "Recover <bot>?", "Usage limit reached", "Sign in to reconnect <bot>", "Retry recovery?", "Safeguards stopped <bot>"; confirm buttons "Recover Bot", "Resume" or "Retry Now"; an extra "Open Harness Settings" for authentication failures; `Sources/NoodleRuntime/AgentRuntimeCoordinator.swift:61-94`). "New Session" confirms with "Start a new session for <bot>?" and "<bot> will start with a fresh context. Its workspace, memory and messages are kept." (`ConversationRuntimeSettings.swift:55-66`).

Detail lines below the title, in order (`HarnessSettingsView.swift:154-237`):

1. Not installed: "Checking the installation…" (never checked), "Missing from this copy of Noodle. Reinstall Noodle." (Apple), otherwise "Install the native harness to use it with Noodle."
2. Setup error in red caption, selectable, wrapping.
3. Version line when installed: location ("Local" for Apple, "Installed by Noodle" for a managed copy, otherwise the executable path, one line, middle-truncated, selectable), "·", the installed version or "Version not checked yet", then either an orange `Label("Update required", systemImage: "exclamationmark.triangle.fill")` or orange "Update available — 2.1.280". A compatibility issue adds an orange paragraph ("This <harness> installation is missing required support for <options>. Update the harness, then choose Check Again.", `HarnessVersions.swift:101`); a failed version check shows "Could not inspect the harness version. Try Check Again." in orange (`HarnessSetupController.swift:111`).
4. "Could not determine the saved sign-in status." when sign-in is needed but the status is managed externally.
5. One action line (link buttons, spacing 12, all optional): "Profiles" (installed and the provider supports profiles), "Update" (managed copy with an update; disabled during activity) or "Update Instructions" (user's own copy), "Remove" (managed copy), "Local Models" and "Remote Models" (Apple), "Sign In" (signed out or unknown, nothing else running; disabled while this harness's own check runs or its live installation is missing, never by other harnesses' checks).
6. Terminal sign-in block when requested: caption "Run this command in Terminal and complete sign-in, then choose Check Again.", the command block, then links "Open Terminal" and "Check Again" (`:473-497`).
7. Update guide when "Update Instructions" was toggled: the provider's update instructions ("Run this command in Terminal, then choose Check Again. Updates follow the provider's configured release channel; managed or pinned installs may intentionally remain on an older version. Existing bot processes keep their running version until restarted."), the command block, "Open Terminal", "Official Update Guide" (`:421-439`).
8. Activity line: a determinate `ProgressView(value:)` 120 pt wide when a download fraction is known, else a small spinner, the activity caption, a spacer and a "Cancel" link (`:185-197`). Activity strings: "Downloading…", "Verifying…", "Unpacking…", "Installing…", "Starting sign-in…", "Waiting for sign-in…" (`HarnessSetupController.swift:184,191,250-262`).
9. Sign-in challenge (device code), or, when idle and not installed, the install section.

Install section states (`HarnessSettingsView.swift:200-237`): when Noodle can install the harness (every provider except Apple, `Sources/NoodleCore/ManagedHarnesses.swift:177-180,256-267`), links "Install" and "Install Manually…"; otherwise a single "Install…". "Install Manually…" or "Install…" expands the guide in place: instructions caption, the command block, then "Open Terminal", "Installation Guide" (a `Link` to the vendor docs) and "Check Installation". After checking, if still missing: "Not detected yet. Finish the installer in Terminal, then check again." If Terminal cannot be found: "Open your preferred terminal and paste the command."; if it fails to open: "Could not open Terminal. Open it manually and paste the command." (`:503-513`).

Command block (`:499-542`): `HStack(alignment: .firstTextBaseline, spacing: 10)` with a secondary `terminal` symbol, the command in `.callout` monospaced, selectable and wrapping, and an icon-only borderless "Copy Command" button (`doc.on.doc`, help "Copy command"); `padding(10)`, background `textBackgroundColor` at 50% opacity in a 6 pt rounded rectangle with a 1 pt border of primary at 10%.

Sign-in challenge (`HarnessSignInChallengeView.swift:10-21`): the device code in monospaced body text (selectable), a "Copy Code" button and an "Open Sign-In Page" button on one line, then the caption "Enter this code on the sign-in page." The challenge arrives asynchronously from the provider's sign-in call; the row then reads "Waiting for sign-in…" until the provider returns a status.

Install outcomes (`HarnessSetupController.swift:243-292`): the row stays busy until a refresh can show the result, so it never offers Install again prematurely. If the download succeeded but the harness is not found: "<harness> was downloaded, but Noodle cannot find the installed harness." If a new managed version fails the compatibility check while an older one exists, the new one is deleted, recorded as rejected and never fetched again: "<harness> <version> does not work with this version of Noodle. The previous version was kept." Managed copies are checked every six hours and auto-installed when the toggle is on (`docs/harness-setup.md:127-132`); the user's own installation always wins over Noodle's copy.

"Remove" (managed copies only) opens a confirmation dialog "Remove <harness>?" with a destructive "Remove" and the message "Bots that use <harness> stop working until it is installed again. Your sign-in is kept."; before removal every bot on that harness is stopped (without revoking access) so none loses its tools mid-turn (`HarnessSettingsView.swift:384-398`).

"Experimental" (Apple only) is an orange caption-sized plain button beside the name, help "Why is this harness experimental?", opening the 360 pt popover: headline "Experimental", text "Apple's on-device model can respond slowly, miss details from earlier messages, or fail to complete tool tasks. This harness is still being tested." (`:133-149`).

Instant paint: `HarnessPresentationCache` stores the last confirmed installation, sign-in and version report per harness in user defaults (key `Noodle.harnessPresentation.v1`) and seeds the controller at launch, so the tab opens with real statuses before any probe runs. It is explicitly display-only: "Never use this cache to launch a harness or authorize access. It contains no credentials, account details or sign-in codes." (`Sources/NoodleCore/HarnessPresentationCache.swift:3-34`). A failed discovery check sets an error but does not erase the installation or sign-in ("A failed check is not proof of uninstallation or sign-out", `HarnessSetupController.swift:133-137`).

### 2.5 Harness profiles sheet

Opened by "Profiles" (`HarnessProfilesView.swift`, width 480, `noodleSheetSizing(animated: true)`). Title "<Harness> Profiles". Empty state "No profiles" in secondary, at least 60 pt tall. Otherwise the rows sit in one container with dividers: background secondary at 7.5% opacity in a 12 pt continuous rounded rectangle with a secondary 11% border (`:32-43`). Row (`:85-126`), padded 12 horizontal and 10 vertical: `person.crop.circle` at `.title3` secondary; the name at `.fontWeight(.medium)` above its status; then "Cancel" while an operation runs, or "Sign In…" when not signed in; then the actions menu ("Sign In Again…" when signed in, because "A login can be revoked on the server while still reading as signed in here", "Rename…", destructive "Delete"). Under the row: the device-code challenge, a Terminal sign-in block (Antigravity and OpenCode profiles), or a red error. Status: a mini spinner plus "Starting sign-in…" or "Waiting for sign-in…" during activity; otherwise "Signed in" or "Ready" (green), "Sign-in required" (secondary), or plain caption "Checking sign-in…" / "Sign-in status unknown". Footer: "Add Profile…" leading, "Done" trailing. The name alert is "New Profile" or "Rename Profile" with a "Profile name" field and "Cancel"/"Save", Save disabled while the name is invalid ("Enter a name.", "Names must be a single line. Put longer text in Description or Backstory.", "Keep names to 100 characters or fewer."). Deleting confirms "Delete Profile?" with "“<name>” is signed out of Noodle. Bots using it return to the system profile and restart." In code every provider except Apple supports profiles (`Harnesses.swift:28`), while `docs/harness-setup.md:62,83` says FX and OpenCode use the System sign-in only.

### 2.6 Models: Apple local and remote

There is no global model list; models are chosen per bot in the bot editor. The Settings surfaces are the two Apple sheets on the Apple Intelligence row.

Local Models (`AppleLocalModelsView.swift`, width 480): title "Local Models"; a scroll area whose height tracks its content up to 520 pt (`:61-72`), then an optional red error label, then the footer "Import Model…" and "Done". While a download or import runs, "Done" is disabled and interactive dismissal is blocked (`:84-93`). States: a small spinner with "Checking local model support…" or "Importing model files…"; unsupported "Requires macOS 27 and a Noodle build with local model support."; otherwise an "Installed" headline section and an "Available" headline section, each model a card padded 12 on `quaternary` at 40% opacity in a 10 pt rounded rectangle (`:159-160`). A card shows the name (medium weight, two lines), a "Recommended" capsule for the best fit for the Mac's memory (caption2, tint-coloured text on tint at 12%, padding 6 x 2, help "The best fit for this Mac's memory."), the catalogue summary, and "4-bit · 4.5 GB" plus a "Details" link to Hugging Face (help "Model details and license on Hugging Face"); imported models show only name and size (`:202-232`). The trailing action is "Download", "Cancel" during a download, or a destructive "Remove" for installed models (help lists the bots using it). Download progress is a `ProgressView(value:)` with a monospaced-digit caption: "Preparing download…", "Downloading 1.20 GB of 4.50 GB…", "Verifying model files…", "Importing model files…", "Cancelling…" (`:234-245`). "Import Model…" opens a folder chooser titled "Import MLX Model" with prompt "Import". Removal confirms "Remove Model?" with "Noodle's copy of “<name>” will be deleted. You can download or import it again later."

Removal guarded by usage: if any bot uses the model (or a remote account), "Remove" opens `ModelUsagePopover` instead of deleting (`:310-318,338-384`). The popover (340 wide, padding 16) has the headline "Model in Use" or "Model Unassigned" with a close `xmark`, the model name, then either "No bots use this model." and a destructive "Remove Model", or "Choose another model for these bots before removing it." and a list of rows (28 pt avatar, name, "Edit…", each at least 44 pt tall; list height `min(240, count * 44)`). "Edit…" opens that bot's runtime editor as a sheet, and when the editor closes the popover reopens on the same model (`returnToModelID`, `:95-101`).

Remote Models (`AppleRemoteModelsView.swift`, width 480): title "Remote Models"; states "Checking model support…", "Requires macOS 27 and a Noodle build with custom model support.", "No accounts", or account cards (same card style). An account card shows the account name (medium weight) and its base URL or provider name (caption, middle-truncated); its actions menu offers "Add Model…" for custom servers that need models described, or a "Models" submenu of toggles (a model in use cannot be turned off), then "Refresh Models" for providers that list models (Ollama), "Rename…", "Change API Key…", a divider and destructive "Remove" (`:149-230`). Below a divider, enabled models list name and summary, with help "Used by A, B."; custom models get their own menu ("Edit…", "Remove", the latter disabled while in use). Footer: an "Add Account" menu with one item per provider ("OpenAI…", "OpenRouter…", "Vercel AI Gateway…", "Ollama…", "Custom…" per `docs/harness-setup.md:211-218`), disabled until support is confirmed, and "Done".

The account form (width 380, padding 20): title "Add <Provider> Account", "Rename Account" or "Change API Key"; columns-style form with "Name" (prompt "Personal"), "Address" (custom provider only, prompt the provider's default URL), and a secure "API Key" field (prompt "Optional" when the key is not required); a red error label; a footer spinner, "Cancel" and "Add"/"Save" (`:337-429`). Submit validates the key with the provider before saving, and for providers that list models fetches them, failing with "<Provider> has no models that can use tools. Download one, then try again." Account removal confirms "Remove Account?" with "“<name>” and its API key will be removed from Noodle."

The model form for custom servers (width 460): "Add Model" or "Edit Model"; "Model ID" (prompt "llama-3.3-70b", with a chevron menu "Models on the server" listing ids the server reports that are not yet added); "Name" (prompt the id or "Llama 3.3 70B"); "Context" and "Maximum Output" number fields 110 pt wide followed by "tokens"; "Input" with a switch "Images"; "Reasoning" as a two-row grid of switches None, Low, Medium / High, Extra High, Max; and "Default Reasoning" once any is chosen, which falls back to Medium when available, else the first chosen (`:456-577`).

### 2.7 Usage window

Opened from the app menu item "Usage" (`chart.bar`, ⇧⌘U) placed between Settings and Services, from "Show Usage" in a direct conversation's sidebar menu, and from "Usage" on a bot's profile; the latter two pre-filter to that bot (`UsageWindow.swift:5-19`, `SidebarView.swift:110-113`). The window is a single `Window("Usage")` with default size 860 x 680, content minimum 840 x 560 (`NoodleApp.swift:180-185`, `UsageView.swift:172`). The subtitle shows the period, for example day-month-year for 7 and 30 days, month-year for 12 months (`:218-225`).

Toolbar (`:177-216`): centred segmented "Period" with "7 Days", "30 Days" (default), "12 Months"; trailing a "Bot" menu ("All Bots", divider, every bot), a fixed spacer, a "Group By" menu ("By Bot" default, "By Harness", "By Model"), a fixed spacer, and a segmented "Measure" with "Tokens" (default) and "Cost".

What is measured (`Sources/NoodleCore/UsageLedger.swift`): an append-only SQLite ledger of samples per bot, harness, model and session with input, output, cache-read, cache-write and reasoning tokens and an optional USD cost. Reasoning is part of output and not added to the total; input excludes cached tokens (`:4-27`). Days are local calendar days, the newest name of a bot labels its whole history so renames do not split it, and history outlives deleted bots (`:133-145`). Per `docs/usage.md:97-99`, Claude Code, Codex, Grok Build, FX and OpenCode report tokens; only Claude Code reports cost; Muse, Antigravity and Apple Intelligence are not counted.

Layout, top to bottom (`UsageView.swift:149-171`):

1. Summary strip (`:227-256`): six stats separated by 36 pt tall dividers, `padding(.vertical, 12)`, background `quaternary` at 50% in a 12 pt rounded rectangle, outer padding 20 on the sides and top. Each stat: title `.subheadline` secondary, value `.title2.weight(.semibold)` with monospaced digits, one line, minimum scale 0.6, 16 pt horizontal padding. Stats: "Tokens"; "Cost" ("—" when no harness reported cost; help "Only harnesses that report cost are included."); "Cache Hits" (percent, no decimals; help "Share of input tokens read from the cache."); "Input" (input plus cache writes; help "Input tokens not read from the cache."); "Output"; "Daily Average" (the period's value divided by its day count, so per day even for 12 months).
2. Stacked bar chart (`:258-286`), minimum 200 pt, padded 20: one bar per day (month for 12 months), stacked by group, legend top-leading, y grid lines in `quaternary`, y labels in the measure's format. Hovering selects a bucket: other buckets dim to 40% opacity and a tooltip appears above the bar (fitted inside the chart horizontally), on `.regularMaterial` in an 8 pt rounded rectangle, padding 8, at least 160 wide: the date in semibold caption ("Tue 7 Oct" style, or "October 2026" for months), then rows sorted by value with an 8 pt colour dot, the group name and the value (`:288-305`).
3. Divider, then the breakdown table (`:307-339`), at least 140 tall, inset style with alternating row backgrounds and monospaced digits. Columns: the grouping name (Bot, Harness or Model; minimum 140, ideal 200, with the 8 pt colour dot), then right-aligned numeric columns "Tokens", "Input", "Output", "Cached", "Cost" and "Share" (minimum 64, ideal 88 each).

Empty period: `ContentUnavailableView("No Usage", systemImage: "chart.bar.xaxis")` with "No tokens were used in this period." (`:156-158`).

Report rules (`UsageReport`, `:7-120`): ranges are today minus 6 days to tomorrow, today minus 29 days to tomorrow, and the first of the month 11 months back to tomorrow. Repeated bot names are told apart by id as "Name (2)". An empty model name groups as "Default Model". Rows sort by value descending, then name. Only the seven largest groups get hues; the rest fold into "Other" in the chart (gray) but remain individual rows in the table. Hues are assigned to those seven in name order, not rank order, "so a new period or measure does not repaint a group just because its rank changed" (`:91-94`). The seven categorical colours, "stepped for the dark surface", are `#3987e5`, `#d95926`, `#199e70`, `#c98500`, `#d55181`, `#008300`, `#9085e9` (`:133-134`). Token counts use compact notation with one to three significant digits (for example "1.24M"); cost uses USD with three decimals when strictly between 0 and 1, otherwise two (`:350-356`). Cache hit rate is cache reads over input plus cache reads plus cache writes. The view re-reads on every ledger revision, so it updates live while bots run (`:150`).

### 2.8 Agent Activity window

One floating log window per bot, opened by "Show Activity" in the sidebar menu of a direct conversation and in the context menu of a bot's avatar, both beside its messages and in the conversation header, only for bots that run on this Mac (`SidebarView.swift:107-109`, `Components.swift:266,271`, `ChatView.swift:581`, `NoodleStore.swift:801`). Re-opening brings the existing window forward; its title follows renames and it closes when the bot is deleted (`AgentActivityWindow.swift:7-39`). Opening always scrolls to the latest output.

Window (`:50-100,155-168`): an `NSPanel` 760 x 500, minimum 440 x 260, floating, not hidden when the app deactivates, transparent and shadowed, title hidden, traffic-light buttons hidden (the frame draws its own close button), full-screen auxiliary. It cannot become main, ignores zoom, minimise and full screen, and closes on Escape or ⌘W.

Frame (`AnnotationPreviewFrame`, `:303-363`, shared with attachment previews): a dark HUD material (`.hudWindow`, behind-window blending, forced dark appearance), corner radius 18, 1 pt white border at 22% opacity. A 36 pt header holds an 18 x 18 `xmark.circle.fill` close button 10 pt from the leading edge (tooltip "Close Activity (Esc or ⌘W)"), the title "<Bot> - Activity" in 13 pt semibold, middle-truncated, 8 pt after the button, and an empty kind label 14 pt from the trailing edge. Dragging the header moves the window. The content sits in an inset with 5 pt margins (none at the top) and a 13 pt corner radius.

Log view (`:173-288`): a native read-only selectable `NSTextView` (Find bar enabled) in 12 pt monospaced regular, label colour on text-background colour, 12 pt insets, autohiding vertical scroller. Empty state "No activity yet" in 13 pt secondary, centred. Each entry renders as `[<time with seconds, in the user's locale format>] Title` on one line, then its detail lines (`Sources/NoodleRuntime/AgentActivityLog.swift:26-30`). Titles come from parsing harness events: "Output", "Reasoning summary", "Tool output", "Tool result", "Tool failed", "Plan", "Running command" / "Command completed", "Changing files" / "Files changed", "Searching" / "Search completed", "<tool>: started|completed|failed", "Working" (Apple), and runtime lifecycle details such as the bot's status text (`AgentActivityParser.swift:22-175`). Streamed events with the same stream id update one entry in place (appending deltas), and every lifecycle change ends coalescing so a new turn starts new entries (`AgentActivityLog.swift:50-82`). Limits: 500 entries and 256 KB per bot, titles 256 bytes, details 16 KB, older output trimmed with a leading "…" line and terminal control characters stripped (`:45-48,93-100`). Logs persist while Noodle runs, even with the window closed, and are cleared on quit.

Live updating: a 0.2 s timer redraws only when the log's revision changed and only while the window is visible, so rendering is batched independently of token rate (`AgentActivityWindow.swift:92-99,110-116`). Updates are incremental: trimmed entries are deleted from the top while the scroll offset is shifted up by their laid-out height, unchanged leading entries are kept, only the changed suffix is replaced, and the selection is preserved (`:254-287`). Auto-follow is on only when the view is within 4 pt of the bottom and nothing is selected; scrolling up or selecting text pauses it (`:246-247`). A 34 pt circular glass button with `arrow.down` (13 pt semibold, tooltip "Follow Latest") floats centred 16 pt above the bottom while the newest output is out of view, fading in over 0.15 s (`:199-242`). The context menu, available anywhere in the log area, has "Copy" (enabled with a selection), "Copy All", "Select All", a separator, "Follow Latest" (enabled when not following) and "Clear" (`:118-136`).

### 2.9 Tools tab, catalog, editor and assignment

Tools tab (`MCPSettingsView.swift:10-104`): a single rounded list (background `quaternary` at 25% in a 12 pt rounded rectangle, padding 20) whose height tracks its content up to 430 pt, then the footer button `Label("Add Tools…", systemImage: "plus")` (help "Add a service or another account"). Empty state: `Label("No connections", systemImage: "puzzlepiece.extension")` in secondary, at least 80 pt tall. A connection row (`padding(14)`, dividers inset 58 pt from the leading edge) shows a 32 pt icon (the service's own icon fetched at sign-in, else its catalogue icon, else `puzzlepiece.extension.fill`, clipped to a rounded rectangle with radius 22% of the size), the name in `.headline` with an orange "Experimental" capsule for experimental catalogue entries, the status, the endpoint URL (caption, one line), an optional description (caption, two lines), a red error, and links "Connect" or "Reconnect", "Edit…" and "Remove". While signing in, the status shows the current stage text and the row adds a mini spinner and a small "Cancel" button. Stages: "Signing in…" (initial), "Waiting for existing requests…", "Checking saved sign-in…", "Discovering authorization…", "Checking authorization server…", "Registering this connection…", "Complete sign-in in your browser…", "Checking available tools…" (`Sources/NoodleMCP/MCPService.swift:74-103`, `MCPOAuth.swift:90-141`, `MCPController.swift:163`). Otherwise the status is "Needs attention", "Connected" or "Sign-in required".

Removal confirms with a dialog titled "Remove Tool Connection?", button "Remove Connection" and the message "This removes this connection from all bots and deletes its saved sign-in from Noodle. Other connections to the same service are unchanged. To revoke the provider's grant too, use its account settings." (`:77-86`). Errors surface inline per row, and registry-level problems in an alert titled "Tools", for example "Could not read saved tool connections. They have not been replaced." (`MCPController.swift:42`). Sign-in errors are mapped to plain sentences, for example "Reconnect this connection in Settings → Tools.", "The service rejected this sign-in (HTTP 401). Connect again to renew access.", "The MCP request timed out. A remote action may have completed; verify before retrying.", "Sign-in cancelled." (`Sources/NoodleMCP/MCPCredentials.swift:73-86`, `MCPController.swift:172`).

Catalog sheet "New Tool" (`ToolCatalogView.swift:13-62`, width 480): a header with "Cancel", the centred headline "New Tool", and an invisible "Cancel" copy to balance it; a "Search tools" field matching every term against name, summary, kind and badge; the caption "Choose a service to add it and sign in. You can customize it afterward."; a 310 pt scroll list of 94 presets (90 stable, then 4 experimental Google Workspace services, each group alphabetical; `Sources/NoodleCore/ToolCatalog.swift:62-361`). A preset row (padding 9, whole row clickable, help "Add <name> and sign in") shows a 32 pt icon (bundled `.icon` file, else the first letter on `quaternary`), the name, a one-line summary, the maturity badge, the kind "MCP" in caption2, and a blue `plus.circle.fill`. No match: "No matching tools. You can add a custom MCP below." Footer: "Custom MCP…" and the caption "Connect your own MCP server". Choosing a preset saves a new, separately named account immediately ("Notion", then "Notion 2", …), closes the sheet and starts the browser sign-in 250 ms later so the authorization page is presented after the sheet is gone (`:129-155`). A retry of the same preset reuses the same connection id, so a partly failed add resumes rather than duplicating.

MCP editor (`MCPSettingsView.swift:111-246`, width 480): a header with "Cancel" (or "Back" from the catalog), the headline "Custom MCP" or "Edit MCP", and "Add & Connect" or "Save" (disabled until name and URL are filled). Fields with caption semibold labels: "Name" (placeholder "e.g. Notion — Work", help "Use a distinct name for each account"), "MCP Server URL" (placeholder "https://…", read-only when editing), "Description" (placeholder "Optional", two to three lines, truncated to 1,000 characters), "Instructions" (a 13 pt text editor 130 pt tall on secondary at 10% in an 8 pt rounded rectangle with an 18% border, help "Optional guidance for bots using this connection. Do not include passwords or tokens.", truncated to 20,000 characters). Captions: "MCP connection · Sign-in opens in your browser." and, when editing, "Changes apply to every bot using this connection." Invalid URL: "Enter a valid HTTPS server URL." Catalogue presets prefill instructions with a service-specific paragraph that always ends "Use only tools actually offered by this connection and only within the user's request and granted permissions."

Browser OAuth (`MCPController.swift:216-282`): an ordinary default-browser tab; only a callback matching the pending redirect's scheme, host, port and path and the single `state` value is accepted; a 180 s timeout; "Could not open your browser. Check your default browser and try again." on failure; on success the Settings window that started the sign-in is brought back to the front.

Assignment to bots happens in the bot editor's Tools tab (`Sources/Noodle/MCPAssignmentPicker.swift:37-169`): a headline "Tools" with an "Add Tools…" button that opens a 330 x 260 popover containing a "Search connections" field, built-in "Calendar" and "Reminders" entries ("Calendars on this Mac, chosen per bot"), saved connections not yet assigned (icon 28, name, badge, one-line description, blue plus), "No saved connections. Choose New Tool to add one." when there are none, and "New Tool…" (which waits for the popover to close and then opens the catalog sheet). With nothing assigned, a large button fills the list area: `puzzlepiece.extension` at `.largeTitle` over "Add tools to this bot", at least 220 pt tall (room for five 44 pt rows) on `quaternary` at 25% in a 10 pt rounded rectangle. Assigned rows (padding 8) show a 26 pt icon, the name, the badge, a `pencil` edit button and a `minus.circle.fill` remove button, which confirms "Remove “<name>”?" with "Remove Tool" and "This bot loses access to it when you save. The tool connection itself is not deleted." The list keeps a fixed 220 pt height so the sheet does not jump. Assignments apply on save.

Native tool extensions: tools can also come from installed app extensions at the extension point `<bundle id>.tool` (`ToolExtensionDiscovery.swift:6-71`); these register automatically and have no settings UI.

### 2.10 Keybindings editor

Three grouped sections (`KeybindingsSettingsView.swift:11-32`): "Conversations" (New Bot ⌘N, New Group ⇧⌘N, Search Conversations ⌘F, Capture ⇧⌘S, Record / Stop Voice Message ⇧⌘D, Call / End Call ⇧⌘C), "Any App" (Choose Conversation ⌃⌥Space), and "Annotations" (Add Annotation ⇧⌘A, Annotate Region ⇧⌘R, Save Annotation Comment ⌘↩) with the footer "Select conversation text to add an annotation, or annotate a region of the Noodle window. The same shortcuts work in attachment previews. Escape cancels an annotation." The Usage shortcut (⇧⌘U) is defined (`Sources/NoodleCore/KeyboardShortcuts.swift:75,105`) but has no row, so it cannot be changed here.

Row (`:55-83`): an `HStack(alignment: .center, spacing: 14)` with the command title, its one-line summary in caption secondary (for example "Create a bot.", "From any app, pick a bot or group to float over your work."), and any error in red caption; then a 20 pt slot holding a borderless `arrow.counterclockwise` reset button only when the binding differs from the default (help "Reset to ⌘N"), so recorders stay aligned; then the recorder, a rounded-bezel button 112 x 28 in 12 pt medium monospaced text showing the binding, "Not set" when cleared, or "Press keys…" while recording. Its tooltip is "Click to record a shortcut; right-click to reset or clear it."; its context menu has "Reset to <default>" and "Clear Shortcut".

Footer: a caption hint, "Use ⌘, ⌃ or ⌥ with a key." normally and "Press a shortcut. Escape cancels; Delete clears." while recording, then "Restore Defaults" (disabled when nothing is changed and nothing is recording).

Recording rules (`:130-190`): only one recorder at a time; a local key monitor consumes events before menus can fire; recording stops when the Settings window resigns key, on a click outside the button, or on right-click. Key-up and repeats are swallowed; Escape cancels; Tab without modifiers stops recording and passes through; Delete or Forward Delete without modifiers clears the binding; anything else is validated and saved. Validation (`KeyboardShortcuts.swift:33-56,133-157`): a single key with at least one of ⌘, ⌃ or ⌥ (Shift alone is not enough) or the error "Choose a key with Command (⌘), Control (⌃) or Option (⌥)."; reserved system shortcuts (⌘Q, ⌘W, ⌘H, ⌘M, ⌘,, ⌘C, ⌘X, ⌘V, ⌘Z, ⌘A, ⌘O, ⌘Tab, ⌘Space, ⌘1-9, ⇧⌘Z, ⇧⌘3-6, ⌥⌘H, ⌃⌘Q, ⌃Space) give "That shortcut is used by <action>."; a duplicate gives "Already assigned to <command>. Change or clear that shortcut first." Only overrides are stored (key `Noodle.keyboardShortcuts.v1`): a missing override means the default, an explicit null means disabled, and a stored set with any invalid or duplicate binding is ignored wholesale. Display order of modifiers is ⌃⌥⇧⌘, with names such as ↩, ⇥, Space, Esc, ⌫, arrows and F-keys (`:19-31`).

### 2.11 Permissions and access modes

The Permissions tab covers macOS privacy permissions, not bot access (`PermissionsSettingsView.swift`). Three rows in the icon-row style:

| Permission | Symbol | Summary | Request path |
|---|---|---|---|
| Microphone | `mic` | "Record voice messages." | system prompt |
| Screen Recording | `rectangle.dashed.badge.record` | "Preview and capture screens and windows for a message." | system prompt |
| Notifications | `bell.badge` | "Announce bot replies and show the unread count on the Dock icon." | system prompt |

The trailing action is "Request" (shown as "Requesting…" and disabled for all rows while any request runs; accessibility "Request <name> access") for not requested, or "Open System Settings" for refused (help "Turn on Noodle under <name>", or for Screen Recording "Turn on Noodle under Screen Recording. macOS may ask to reopen Noodle."), each deep-linking to its pane (`:237-260,40-51`). macOS reports Screen Recording only as allowed or not, so Noodle remembers that it asked to tell a refusal from a never-asked state (`:62-76`). Statuses are refreshed on appear and whenever Noodle becomes active. A "Hub Noodlets" section lists noodlets granted device permissions (title plus "Camera, Microphone, …" in caption) with a "Remove" link each (`:166-184`).

Bot access modes (restricted versus unrestricted) live in the Bots tab, analysed separately; the relevant UX for this document is: every bot starts restricted; turning on "Unrestricted" or "Apps" for a bot asks first ("Allow Unrestricted Access for <bot>?" with "<bot> will be able to read and change files and use services beyond its private workspace, with the access available to your Mac account. This restarts the bot." and the button "Allow Unrestricted Access"), while turning either off applies at once (`BotsSettingsView.swift:92-96,262-281`, `docs/security.md:8-12`). Each bot row's status word "restricted" (secondary) or "unrestricted" (orange) is an explain-on-click button; while access changes it reads "Restarting runtime…". The explanations: restricted, "This bot runs in a macOS filesystem sandbox. It can work in its private workspace, the folders added in Edit Bot, and allowed harness storage, while unrelated personal files are blocked. Assigned tools and computers use their own permissions."; unrestricted, "This bot can read and change files and use services beyond its private workspace, with the access available to your Mac account. macOS and tool permissions still apply. Noodle approves supported tool requests automatically." plus "Off by default. Changing this restarts the bot. Turning it off restores Restricted mode; it does not undo completed actions or revoke macOS permissions." (`BotsSettingsView.swift:284-334`). Noodle never shows approval forms in chat; it approves tool requests automatically within the bot's access (`docs/security.md:88-92`).

### 2.12 Companions tab

Rows for Noodle Computer, Noodle Applet and Noodle Browser (in that order), then Noodle Mobile, then the footer "Check Again" (`CompanionAppsSettingsView.swift:22-58`).

| App | Symbol | Summary | Requirements |
|---|---|---|---|
| Computer | `desktopcomputer` | "Give your bots Linux desktops and terminals to run tools and work on files." | "Requires Apple silicon and macOS 26 or later." |
| Applet | `square.grid.2x2` | "Create and enjoy little tools, websites, games, and native experiments." | "Requires macOS 15 or later." |
| Browser | `globe` | "Give your bots persistent browsers for websites, signed-in accounts, and file transfers." | "Requires macOS 26 or later." |
| Mobile | `iphone` | "Chat with the agents on your Noodle Hub from iPhone and iPad." | "Requires iOS 26 or later." |

Each desktop companion shows "Installed" (green) or "Not installed" (secondary); the detail line shows "Version 1.4" (or "Version unavailable") and, when behind, "Update available — 1.5" in orange, or the requirements when not installed; the trailing link reads "Install" (opens the latest release DMG, or the project page for development builds), "Update" (opens the companion with its update-check URL so the companion's own Sparkle installs it), "Open" (opens its library), or "Opening…" while busy (`CompanionApp.swift:22-44,62-89`, `CompanionAppsSettingsView.swift:60-110,147-169`). Mobile has a "Join Beta" link to TestFlight (`:112-138`). Failures show an alert titled with the app name, with "View Project" when not installed, and "OK". Update detection reads each installed companion's own appcast: HTTPS only (redirects to non-HTTPS refused), 8 s request and 10 s resource timeouts, 1 MiB cap, cached for six hours unless "Check Again" forces it, skipped for builds with updates off, and an unreachable feed keeps the last known release (`CompanionUpdateChecker.swift:49-89`). Companion apps themselves offer "Show in Dock" (help "Show the app in the Dock and app switcher.") and "Show in Menu Bar" in their own settings; if both are off a gear toolbar button "App Settings" keeps settings reachable (`Shared/SettingsUI/.../CompanionAppVisibility.swift:88-180`).

### 2.13 Hub tab, pairing, invitations and devices

The Hub tab has two sections, "This Mac" and "Noodle Hubs" (`HubSettingsView.swift:10-20`).

This Mac (`:23-75`): a switch whose label is "Let My Devices Reach This Mac" with the secondary line "Your phone and other Macs talk to the bots here as they would a Noodle Hub's. The Mac stays awake while this is on." Turning it on starts a personal Hub and an idle-sleep assertion with the reason "Your devices can reach this Mac" (`ThisMacHub.swift:53-80`). When on, the shared `HubLinkRows` follow (`HubLinkRows.swift:24-111`):

- The status label: "Reachable only on this network" or "Reachable from anywhere through Tailscale, the internet and your address" (whichever routes exist) in green, orange when the router mapping failed and nothing else reaches the Mac, the failure reason in orange, or "Starting…" (`Sources/HubCore/HubReachability.swift:24-35`).
- "Name": a trailing-aligned field whose placeholder is the Mac's name, a clear button (help "Use this Mac's name") when customised, the secondary label line "Press Return to save" while edited, saving on Return or focus loss; help "What paired devices call this Hub".
- "Open Port on Router" switch with a status line ("Asking the router…", "Open at <host>", or the failure in orange); help "Asks the router, through UPnP or NAT-PMP, to forward the port so devices reach it away from home".
- "Largest File" picker of byte sizes; help "The largest file a device can send to a conversation here".
- "Addresses": a grid of endpoints in monospaced caption (selectable) with their network in tertiary caption ("home", "tailscale", "internet"); the manual address has a remove button; "Add Remote Address…" (right-aligned) opens an alert "Remote Address" with a field prompt "mac.example.com", the message "A domain, public address or forwarded port that reaches this Mac from outside your network." and Save disabled until it parses.
- Device rows: `iphone` symbol (22 pt column), the device name, and "Connected", "Last seen 2 minutes ago" or "Joined yesterday" refreshed every 15 s, with a "Remove" button confirming "Remove <device>?" and "“<device>” can no longer reach this Mac until it joins again."
- "Add Device…" (right-aligned, disabled while the link is stopped or starting) opens the invitation sheet titled "Add a Device".

Noodle Hubs (`:78-225`): each joined Hub is an icon row with `server.rack` (24 pt in 32 x 32); the Hub's name in semibold and its status ("Connecting…", "Not connected" in orange, or "Connected" in green with help "Connected via <endpoint>"); "Noodle Hub · <user> · <plan> plan" in caption (selectable); "Hub key AB12 CD34 EF56 7890" in monospaced caption (the first 8 bytes of the key's SHA-256 as four groups of four hex characters, `Shared/HubLink/.../LinkIdentity.swift:97-101`); details, either the error in orange or "Your plan lends no harnesses" / "Lends Codex (Work), Claude Code"; and trailing links "Your Picture…", "Users" (admins only), "Archived (3)" (when any) and "Leave". Leave confirms "Leave <hub>?" with "Its bots stay on the Hub for your other devices. Joining again needs a new invitation." The last row invites joining: icon, "Noodle Hub" in semibold, "Use the harnesses a Noodle Hub lends you, such as a friend's or one of your own Macs." and a "Join" link (help "Join a Noodle Hub with an invitation").

Join sheet (`:488-619`, width 460): title "Join Noodle Hub"; an "Invitation" field with the prompt "noodle://join-hub?…" that submits on Return; once the text parses, "Hub key …" appears below it so the user can compare keys before joining; buttons "Paste" (a copied link joins at once, a copied QR picture is decoded), "Choose Image…", and "Scan with Camera"/"Stop Camera"; the camera preview is 240 pt tall with 10 pt corners and joins on the first QR code seen; errors show as a red `exclamationmark.triangle` label ("The clipboard holds no invitation.", "The picture could not be read.", "The camera cannot read QR codes.", "This invitation has expired. Ask for a new one."). Dragging an image or file onto the sheet highlights it with a 3 pt accent-colour border inset 4 pt in a 12 pt rounded rectangle. Footer: a small spinner while joining, "Cancel" and "Join" (default; disabled while joining or empty). An invitation link opened from anywhere lands here pre-filled rather than joining silently (`docs/security.md:221-224`).

Invitation sheet (`HubInvitationSheet.swift`, width 400): title "Invite <user>" (or "Add a Device"); a QR code of the invitation URL rendered at 200 x 200 with nearest-neighbour scaling on a white tile padded 10 with 10 pt corners (accessibility "Invitation QR Code"), generated with error-correction level M (`LinkInvitationImage.swift:30-36`); the URL in monospaced caption, one line, middle-truncated, selectable; "Hub key …"; "Copy Link" and a share button "Share…"; then a green "“<device>” joined" label once a device of that user pairs while the sheet is open, otherwise "Expires in 14 minutes" ticking every second, then "Expired" or a "New Invitation" button. Invitations last 15 minutes and use the `noodle://join-hub` URL (`LinkProtocol.swift:1583-1584`).

Users sheet for Hub admins (`HubSettingsView.swift:315-485`, 460 x 420): header "Users on <hub>" and "Done"; rows with a 24 pt person badge (picture, or initials on a colour), the name, and "<plan>" or "<plan> · Admin"; non-admins get an "Invite" link and an actions menu with a "Plan" picker, a "Can Pair Devices" toggle, "Rename…" and destructive "Remove"; device rows are indented 32 pt with `laptopcomputer`, "Connected"/"Last seen …"/"Paired …" and a "Remove" link. Footer "Add User…". Alerts: "New User"/"Rename User" with a "Name" field and "Add"/"Rename"; "Remove <user>?" with "“<user>”, their devices and their bots are removed from <hub>."; "Remove <device>?" with "“<device>” can no longer reach <hub> until it joins again." Admins appear without controls; a code comment says they are managed on the Hub itself, and their devices have no "Remove".

Archived sheet (420 x 360): "Archived on <hub>", "Nothing archived" when empty, rows with a 32 pt conversation avatar, title, "Group" or "Bot" and an "Unarchive" link. "Your Picture…" opens an icon editor titled "Your Picture" (symbols, colours or a photo, JPEG quality 0.86); a failed save shows "Your Picture Was Not Changed".

Troubleshooting UI exists on Noodle Mobile, not in Mac Settings: a "Help Me Connect" sheet lists the Hub's routes nearest first ("Home Wi-Fi", "Tailscale", "Internet") each with "Answers" (green check) or "No answer", then ordered advice: "You're offline. Connect to Wi-Fi or mobile data.", "Noodle isn't allowed to look for your Hub on this network. Turn on Local Network for Noodle in Settings." with "Open Settings", "Make sure you're on the same Wi-Fi as the Mac your Hub runs on." (or the mobile-data variant), "Away from that Wi-Fi? Open Tailscale and connect.", "Make sure the Mac your Hub runs on is awake and its Hub is open.", or, when an address answered, "Your Hub answers now. Tap Try Again." with a prominent full-width "Try Again" (`Mobile/Sources/NoodleMobile/HubScreens.swift:234-351`, rules in `Shared/HubLink/.../HubTroubleshooting.swift:26-38`). On the Mac, the troubleshooting surface is limited to row status, reachability summary and inline errors.

The Noodle Hub app (screenshot) has its own Users and Plans tabs: user rows with Invite and an actions menu (Plan, Can Pair Devices, Admin, Rename…, Remove), indented device rows with a green 6 pt dot "Connected" label style, plan rows with `rectangle.stack` and summaries such as "Lends Codex (pdp@chatbotkit.com) · 2 users" or "Lends nothing · 0 users" with "Edit…", and footers "Add User…" and "Add Plan…" (`Hub/Sources/NoodleHub/HubAccessSettings.swift:26-186,443-450`). Its Network tab adds "Connected" ("No one" or "2 people on 3 devices") and "Open at Login" (`HubLinkSettings.swift:13-35`).

### 2.14 Other shared behaviours

`WindowFocusGuard` (`Shared/SettingsUI/.../WindowFocusGuard.swift:53-92`) makes the first physical click on an inactive titled window or sheet only focus it, swallowing the click and its drag and mouse-up, so settings controls are never triggered by a click meant to activate the window. Agents driving the UI programmatically bypass it.

## 3. Web translation

These notes target React 19 with Tailwind v4, shadcn/ui and Radix, and follow OpenCompany's frontend rules (semantic tokens, lucide icons, primitives rather than hand-rolled controls).

Settings layout:

- Render Settings as a dedicated route or a large non-modal `Dialog` with a top tab strip. Use Radix `Tabs` with a custom `TabsList`: each trigger is an icon (about 20 px) stacked over an 11 px label in a roughly 56 x 44 px hit area with a rounded selected background (inferred from the screenshot; macOS draws these). Keep the strip on one row at 740 px content width; if a narrower viewport would overflow, switch to an icon-only strip or a select rather than a hidden overflow menu.
- Content column fixed at 740 px; height follows the tab's content up to `100dvh` minus the dialog chrome, then scrolls inside. Animate height changes between tabs over 220 ms ease-in-out by measuring the incoming panel with a `ResizeObserver` and transitioning `height`, pinned to the top edge (the equivalent of `windowResizeAnchor(.top)`). Use `scrollbar-gutter: stable` on the scroll container instead of Noodle's hide-then-restore indicator hack.
- Grouped sections: a `div` per section with `rounded-xl bg-card` and `divide-y divide-border` rows; section headers and footers outside the box in caption text. This is the macOS grouped `Form` look visible in the screenshot.
- Tab badge: an absolutely positioned pill at the trigger's top-right, `h-3 min-w-3 px-0.5 rounded-full bg-destructive text-[8px] leading-3 text-white tabular-nums`, `aria-label` with the count, `pointer-events-none`. Drive it from a map keyed by tab id, not by label.
- Footer bar: a sticky bottom row `border-t px-5 py-3 flex justify-end gap-2`, with a `useDelayedFlag(isRefreshing, 300)` spinner kept in layout via opacity.
- Deep links: keep the selected tab in the URL or a store so other surfaces (a notification, a Kick dialog) can open Settings on Harness.

Components to build once:

| Component | Spec |
|---|---|
| `StatusLabel` | lucide icon + caption text, one tone class (`text-success`, `text-warning`, `text-muted-foreground`), `whitespace-nowrap`; tones never red |
| `SettingsIconRow` | `grid grid-cols-[32px_1fr] gap-3 py-1`; icon 24 px muted; title row `font-semibold` + status at end; caption lines `text-xs text-muted-foreground`; action line of `Button variant="link" size="sm"` with `gap-3` |
| `RowList` | rows with dividers, gap 10 px, max height 360 px then scroll, scrollbar in the trailing margin |
| `CommandBlock` | monospace `text-sm`, terminal icon, icon-only copy button, `p-2.5 rounded-md border bg-muted/50` equivalent via a soft token |
| `DeviceCodeChallenge` | code in monospace, "Copy Code", "Open Sign-In Page", caption "Enter this code on the sign-in page." |
| `ExplainPopover` | Radix `Popover`, 360 px, `p-5`, headline + paragraphs; trigger styled as caption text |
| `RowActionsMenu` | `DropdownMenu` on a lucide `Ellipsis` in a circle, destructive item last after a separator |
| `ConfirmDestructive` | `AlertDialog`; initial focus on Cancel (Radix `onOpenAutoFocus` targeting Cancel) so Enter cancels, matching Noodle |
| `UsageGuardPopover` | lists bots blocking a removal, each with "Edit…", reopening after the editor closes |

SF Symbol to lucide suggestions (inferred equivalents): `gearshape` Settings, `bubble.left.and.bubble.right` MessagesSquare, `terminal` SquareTerminal, `sparkles` Sparkles, `person.3` Users, `puzzlepiece.extension` Puzzle, `keyboard` Keyboard, `hand.raised` Hand, `square.stack.3d.up` Layers, `server.rack` Server, `arrow.triangle.2.circlepath` RefreshCw, `checkmark.circle.fill` CircleCheck, `exclamationmark.triangle` TriangleAlert, `person.crop.circle.badge.questionmark` UserRoundX or CircleHelp, `arrow.down.circle` CircleArrowDown, `ellipsis.circle` CircleEllipsis, `circle.dotted` CircleDashed, `arrow.trianglehead.2.clockwise` RotateCw, `doc.on.doc` Copy, `chart.bar` ChartColumn, `arrow.down` ArrowDown, `xmark.circle.fill` CircleX, `plus.circle.fill` CirclePlus, `minus.circle.fill` CircleMinus, `iphone` Smartphone, `laptopcomputer` Laptop, `desktopcomputer` Monitor, `globe` Globe, `square.grid.2x2` LayoutGrid, `mic` Mic, `rectangle.dashed.badge.record` ScreenShare, `bell.badge` BellDot. Harness marks should ship as monochrome SVGs coloured by `currentColor`.

State shapes (TypeScript):

```ts
type AuthStatus = 'authenticated' | 'unauthenticated' | 'notRequired' | 'managedExternally';
interface VersionReport { installed?: string; latest?: string; compatibilityIssue?: string; checkError?: string; checkedAt?: string }
interface HarnessSnapshot { provider: HarnessId; executablePath: string | null; auth: AuthStatus | null; version: VersionReport | null }
interface HarnessRow {
  snapshot?: HarnessSnapshot;            // undefined = never checked; seeded from a display-only cache
  error?: string; activity?: string; progress?: number;   // progress absent = indeterminate
  challenge?: { url: string; code: string }; terminalCommand?: string;
  checking: boolean; managed: boolean;
  affectedBots: { id: string; name: string; failed: boolean; reconnectingSince?: string; detail: string }[];
}
type PermissionStatus = 'allowed' | 'notRequested' | 'denied' | undefined;  // undefined = Checking…
interface ToolConnection { id: string; name: string; endpoint: string; description: string; instructions: string; iconUrl?: string; connected: boolean; error?: string }
interface ToolsState { connections: ToolConnection[]; signingIn?: string; signInStage: string }
interface Invitation { url: string; hubKeyFingerprint: string; userName: string; expiresAt: string }
interface ActivityEntry { id: string; at: string; title: string; detail: string; streamId?: string }
```

Derive the harness status in one pure function that mirrors section 2.4's priority table, and compute badge counts from the same facts (errors, compatibility issues, available updates, failed bots), never from rendered text. Persist the last confirmed `HarnessSnapshot` per provider in local storage for instant paint, and never read it for authorization.

Live views:

- Activity log: keep a per-bot ring buffer (500 entries, 256 KB, 16 KB per detail, trim with a leading ellipsis line) outside React state; flush to the view at most every 200 ms and only when the panel is visible. Render the text in one `<pre>` (or a single read-only text node per entry) so native selection and browser find work; append only the changed suffix. Follow the bottom only when within 4 px of it and the selection is empty; otherwise show a 34 px round "Follow Latest" button 16 px above the bottom with a 150 ms fade. Offer a Radix `ContextMenu` with Copy, Copy All, Select All, Follow Latest, Clear. Make the panel a draggable, resizable floating surface (760 x 500 default, 440 x 260 minimum) with a 36 px drag header, always dark, 18 px outer radius, 1 px white border at 22%, 5 px inset to a 13 px-radius body; Escape closes.
- Usage: a page with a `ToggleGroup` for 7 Days / 30 Days / 12 Months and Tokens / Cost, `Select`s for bot and grouping, a six-stat KPI strip with vertical dividers, a stacked bar chart (Recharts `BarChart` with one `Bar` per group sharing a `stackId`, dimming non-hovered buckets to 0.4 opacity, a custom tooltip), and a shadcn `Table` with `tabular-nums` right-aligned numeric columns. Format with `Intl.NumberFormat(undefined, { notation: 'compact', maximumSignificantDigits: 3 })` and USD with three decimals strictly between 0 and 1. Define the seven categorical colours as theme tokens (`--chart-1` to `--chart-7`) rather than hex in components, assign them to the top seven groups in name order, and render "Other" in a neutral token. Show "—" for cost when no source reported cost.
- Relative times: re-render "Last seen" every 15 s and countdowns every 1 s with `Intl.RelativeTimeFormat`.
- Refresh on focus: re-check permissions, installs and companion versions on `visibilitychange` to visible and on window `focus`, the browser equivalent of `didBecomeActive`.

Permissions on the web: use `navigator.permissions.query` for microphone and notifications; screen capture has no query, so store a "requested" flag (as Noodle does for Screen Recording) to tell refused from never asked. A refused permission can only be fixed in the browser's site settings; replace "Open System Settings" with instructions.

Keybindings on the web: a recorder button listening on `keydown` at the window while recording, ignoring `repeat` and `keyup`, Escape cancels, unmodified Tab ends recording without capturing, Backspace or Delete clears, and at least Ctrl/Cmd or Alt is required. Store only overrides with explicit `null` for disabled. Extend the reserved list with browser shortcuts the page cannot intercept (Cmd/Ctrl+W, T, N, Q, L, R, Tab).

Hub invitation on the web: generate the QR with error level M at 200 px, `image-rendering: pixelated`, on a white tile with 10 px padding and 10 px radius in every theme; show the URL truncated in the middle, the key fingerprint, "Copy Link" and a Web Share button with a copy fallback, and the 15-minute countdown.

## 4. Patterns worth copying, and pitfalls to avoid

Worth copying:

- One status vocabulary and colour grammar for every operational row: green usable, orange act or recover, secondary pending or neutral, red only for inline error text and badges.
- Status as a pure function of facts in a fixed priority order, with the last confirmed snapshot cached for instant paint and explicitly barred from authorization; a failed check never erases what was known.
- Badges count only actionable items: refused permissions (not unrequested ones), failed bots (not reconnecting ones), harnesses with errors or updates (not uninstalled ones).
- Never flash progress: 300 ms delay for spinners, old error text replaced by "Checking for updates…" during a re-check, rows stay busy until the result can be shown.
- Three sign-in modes behind one row: device code (Copy Code, Open Sign-In Page), browser OAuth with stage text and Cancel, and a Terminal command block with Copy, Open Terminal and Check Again.
- Destructive confirmations that state consequences and what is kept, with Cancel as the default button.
- Removal blocked by dependants, with a popover that lists them, links straight to fixing each, and returns afterwards.
- Idempotent creation on retry (preset attempts and new connections keep their ids), so partial failures resume.
- Hues assigned by name order, not rank, so chart colours stay put across periods and measures.
- Activity log batching at 200 ms with incremental suffix updates that preserve selection and scroll.
- Explain-on-click headers and status words instead of permanent help text.
- Re-check on return to the app for anything changed outside it.

Pitfalls:

- Label-keyed badges silently disappear when a tab is renamed or the toolbar overflows; key by id.
- The macOS-specific techniques (walking the title bar view tree, run-loop observers, sheet window resizing) should not be ported; use measured CSS height transitions and `scrollbar-gutter`.
- Website screenshots and docs lag the code: the harness screenshot shows removed Heartbeat and Sandbox tabs; docs mention a link-preview timeout that does not exist and say FX and OpenCode lack profiles while the code supports them; the Hub app keeps a stale "same width as Noodle" comment (680 vs 740).
- Device icons are inconsistent: Noodle's This Mac rows use `iphone` for every device while Hub users' devices use `laptopcomputer` for every device, including iPhones.
- The Usage shortcut can be bound in preferences but has no editor row.
- "Sign-in status unknown" must never be treated as signed out, and cost totals must show "—", not $0, when nothing reported cost.
- Browsers cannot capture several system and browser shortcuts; validate against them before accepting a binding.

## 5. Open questions and inferences

- Toolbar tab icon and label sizes, selected-tab background and window corner radius are system-drawn and not in code; values in section 3 are inferred from the screenshot.
- The screenshot's two-line path and "Version …" layout, bordered "Sign In…" buttons and the Hub app's inline plan popup belong to older builds; the current visual of the one-line "path · version" row is inferred from code only.
- The exact provider list in the "Add Account" menu comes from `RemoteProviders.all`, which was not read; the list given follows `docs/harness-setup.md:211-218` (inferred).
- Which providers raise a device-code challenge versus opening the browser directly depends on provider files not read in full; the docs say Claude Code opens the browser and others show a code (inferred from `docs/harness-setup.md:64-67`).
- `IconEditorSheet` (Hub picture) and the Hub plan editor were not read in detail.
- The Bots tab's per-bot Heartbeat, Unrestricted and Apps switches are summarised here only for access confirmation copy; their layout is covered by the Bots settings analysis.
- Whether the Usage window's subtitle renders exactly as "8 Sep – 7 Oct 2026" depends on the system locale (inferred).
