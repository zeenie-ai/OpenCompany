# Noodle: creating and configuring bots and groups

Noodle's "hire" flow is a single fixed-width sheet (New Bot, 520 pt) whose header holds Cancel, a centred title and a plain-text primary action, and whose body is an identity row (64 pt avatar with a pencil badge plus a pre-filled, regenerable name field) above a five-segment tab picker: General (public description, private backstory, folders), Harness (a grouped card of harness, profile, model, effort and voice rows that open popovers), Tools, Computers and Browsers (assignment "wells" fed by search-and-add popovers). Edit Bot reuses the same sheet and adds sharing, the conversation background, Delete Bot, and a transactional save that snapshots the bot's settings files and restores them if any write fails. Groups use smaller sheets (New Group 480 pt, Group Info 460 pt) built from a name field, a description, an avatar-grid member picker with confirm-on-remove, an optional location tile row (This Mac or a joined Noodle Hub), folders and the background. Avatars are a circle that shows either an uploaded/generated JPEG or an SF Symbol on one of six two-stop gradients; backgrounds are per-conversation presets, photos, videos, multi-frame HEICs or system wallpapers, dimmed by a 25 % black scrim and forcing the window into dark appearance. Note that in this codebase "Kick" means recovering a failed bot, not removing a group member; removal is the confirm-on-remove action in the member picker.

Permalink base for every `path:line` citation below: https://github.com/pdparchitect/noodle/blob/702339e3153025c9fe9582783caff7a19b48ff97/

## 1. Scope

Read in full (assigned):
- `Sources/Noodle/`: `Overlays.swift`, `AgentConfigurationFields.swift`, `AgentPicker.swift`, `AgentProfileSheet.swift`, `AgentProfileButton.swift`, `GroupMemberPicker.swift`, `MCPAssignmentPicker.swift`, `CompanionAssignmentPicker.swift`, `BotFolderPicker.swift`, `AgentSettingsCheckpoint.swift`, `DestructiveActionButton.swift`, `GroupsSettingsView.swift`, `FirstBotSetupSheet.swift`.
- `Sources/NoodleRuntimeSettings/`: `BotsSettingsView.swift`, `BotAvatar.swift`, `BotSettingsHost.swift`, `HarnessProviderIcon.swift`, `AgentKickConfirmation.swift`.
- `Shared/Wallpaper/Sources/NoodleWallpaper/`: `IconEditorSheet.swift`, `IconAppearance.swift`, `BackgroundPicker.swift`, `ImageSourceMenu.swift`, `SystemWallpaperSheet.swift`, `NoodleImagePlaygroundButton.swift`, `CameraView.swift`, `BackgroundDropTarget.swift`, `CompanionContentPanel.swift`.
- `Shared/Wallpaper/Sources/NoodleWallpaperCore/*.swift` (AvatarIdea, BackgroundDrop, BackgroundMedia, BackgroundPhoto, ConversationBackground, ImagePlaygroundSetup, SystemWallpaper); `Shared/SettingsUI/Sources/NoodleSettingsUI/SheetSizing.swift`.

Consulted for exact copy or behaviour (partial reads): `Sources/Noodle/{NoodleStore,NoodleApp,SidebarView,ConversationBackgroundView,Components,ComputerController,BrowserController,EventKitController,BotNameGenerator,StarterTeam,FirstBotSetup,MessageContextMenu,ConversationWindow,NoodleSettingsView}.swift`; `Sources/NoodleCore/{ConversationName,Models,Harnesses,KeyboardShortcuts,AgentFolder,AgentConfiguration,ConversationBackground,BackgroundMedia,WorkspaceRepository,EventKitAccess}.swift`; `Sources/NoodleRuntime/{AgentRuntimeCoordinator,BotVoice}.swift`; `Sources/NoodleRuntimeSettings/{ToolCatalogView,MCPSettingsView,SettingsRowList,SettingsStatusLabel,ConversationRuntimeSettings}.swift`; `Shared/Wallpaper/Sources/NoodleWallpaper/{ConversationBackgroundView,AnimatedWallpaper}.swift`; `Shared/HubLink/Sources/HubLink/LinkProtocol.swift`; `Mobile/Sources/NoodleMobile/Chats.swift`; `.agents/skills/ui-copy/SKILL.md` and `AGENTS.md` (read as data describing Noodle's own conventions).

Screenshots viewed:
- `website/assets/mobile-new-bot.png`: despite the filename, the iOS bot editor in edit mode (Cancel/Save, 88 pt avatar with tinted "Edit", Name, Harness "Claude Code", Model "Default", Tools/Computers/Browsers rows, Background, red Delete Bot at the bottom). Matches `Mobile/Sources/NoodleMobile/Chats.swift:2158-2298`.
- `website/assets/mobile-new-tool.png`: iOS "New Tool" catalogue (brand icon, tinted name, one-line summary, bottom "Search tools" field).
- `website/assets/screenshot-chloe.png`: macOS direct conversation; illustrated portrait avatars in the sidebar with a green runtime dot; a large avatar and name over an illustrated, dimmed wallpaper.
- `website/assets/screenshot-group-chat.png`: macOS group "Launch week"; stacked three-member group avatar, group description and member list under the title, photo wallpaper (rocket launch) dimmed.

## 2. Findings

### 2.1 Entry points, presentation and sizing

- Toolbar Create menu: `Label("Create", systemImage: "plus")`, tooltip "Create Bot or Group"; items "New Bot" (`person.crop.circle.badge.plus`) and "New Group" (`person.3.fill`) (`Sources/Noodle/NoodleApp.swift:369-386`). File menu replaces New Item with the same two commands (`NoodleApp.swift:138-149`). Default shortcuts ⌘N and ⇧⌘N, user-rebindable (`Sources/NoodleCore/KeyboardShortcuts.swift:95-96`).
- New Bot is disabled unless storage is ready and either a local harness is installed or a joined Hub lends one (`Sources/Noodle/NoodleStore.swift:313-315`); New Group is disabled while there are no bots (`NoodleApp.swift:381`, `529-531`).
- Editing: the toolbar's primary action is an `ellipsis` button labelled "Edit Bot" (direct chat, not for shared bots) or "Group Info" (`NoodleApp.swift:466-485`). Sidebar context menu: "Edit Bot…" / "Edit Group…", "Change Background…", then for bots that run on this Mac "Show Activity", "Show Usage", "Show Workspace in Finder", "Kick" (only when failed), "New Session", and finally "Archive Bot" / "Archive Group" (`Sources/Noodle/SidebarView.swift:77-138`). The Apple Intelligence model managers in Settings open Edit Bot directly on the Harness tab for a bot that uses a model (`Sources/Noodle/NoodleSettingsView.swift:232-234`; `Sources/NoodleRuntimeSettings/AppleLocalModelsView.swift:98-100`).
- Every sheet has a fixed width and a content-fitted height:

| Sheet / popover | Width x height (pt) | Source |
|---|---|---|
| New Bot, Edit Bot | 520 x fitted (animated resize) | `Overlays.swift:182`, `426`; `NoodleApp.swift:496-512` |
| New Group | 480 x fitted | `Overlays.swift:981` |
| Group Info | 460 x fitted | `Overlays.swift:823` |
| Folders (sub-sheet) | 480 x fitted | `BotFolderPicker.swift:66` |
| Bot Icon editor | 440 x fitted | `IconEditorSheet.swift:104` |
| Conversation Background | 520 x fitted | `Sources/Noodle/ConversationBackgroundView.swift:60` |
| System Wallpapers | 720 x 560 | `SystemWallpaperSheet.swift:51` |
| Camera | 320 x 320 circle + 20 padding | `CameraView.swift:131-133` |
| New Computer / New Browser | 420 x fitted | `ComputerController.swift:363`, `BrowserController.swift:218` |
| New Tool catalogue | 480 x fitted (list 310 tall) | `ToolCatalogView.swift:51`, `61` |
| First-run setup | 640 x fitted | `FirstBotSetupSheet.swift:35` |
| Bot / group profile (popover) | 320 x fitted | `AgentProfileSheet.swift:72`, `290` |

- `noodleSheetSizing(animated:)` = `fixedSize(horizontal: false, vertical: true)` + `.presentationSizing(.fitted)` + an AppKit bridge that measures the SwiftUI content and resizes the sheet window outside the layout pass, coalescing measurements, ignoring changes under 0.5 pt, and, when animated (and Reduce Motion is off), keeping the sheet attached at its top edge while it grows or shrinks (`Shared/SettingsUI/Sources/NoodleSettingsUI/SheetSizing.swift:4-68`). The comment states why: macOS keeps a sheet's initial size, and recreating the editor would lose drafts, focus and nested presentations (`SheetSizing.swift:16-18`). New Bot and Edit Bot use the animated variant, so switching tabs smoothly resizes the sheet.

### 2.2 Shared sheet anatomy

All editors share one header pattern: `HStack { Button("Cancel") Spacer Text(title).font(.headline) Spacer Button(primary) }.padding(16)`, both buttons `.buttonStyle(.plain)` with `.foregroundStyle(.blue)`; the primary turns `.secondary` and disabled when invalid (`Overlays.swift:257-271`, `321-333`, `761-773`, `905-925`). A full-width `Divider()` follows, then the body padded 20 (`Overlays.swift:180`, `424`, `821`). The bot and group editors do not use `Form`; they compose caption-semibold labels, rounded-border fields, caption helper text and tinted "well" containers in a `VStack` (New Bot spacing 16, Edit Bot 18, Group Info 18). `Form` with `.formStyle(.grouped)` is used for Settings tabs and the small New Computer / New Browser sheets.

Keyboard: Return in the name field submits when valid (`Overlays.swift:105-107`, `360`, `790`). Cancel in the four bot/group editors has no `.cancelAction` shortcut attached; Folders and Conversation Background bind their Apply to `.defaultAction` (`BotFolderPicker.swift:61`, `ConversationBackgroundView.swift:43`); Camera binds Take Photo to `.defaultAction`; New Computer/Browser bind both Cancel and Create (`ComputerController.swift:345-349`).

### 2.3 New Bot sheet (`Overlays.swift:43-272`)

**Identity row** (`HStack(spacing: 14)`, `Overlays.swift:80-129`):
- Avatar button: `BotAvatar` 64 pt with `pencil.circle.fill` at 21 pt in the bottom-trailing corner, palette rendering white on accent, on a background-coloured circle; plain style; tooltip and accessibility label "Change Bot Icon"; opens the icon editor sheet (`Overlays.swift:81-95`, `194-202`).
- Name column `VStack(alignment: .leading, spacing: 5)`: `TextField("Bot name")`, `.roundedBorder`, 14 pt, one line, autocorrect on, focused on appear. A regenerate button sits inside the field's trailing edge: `arrow.triangle.2.circlepath`, 11 pt semibold, secondary, 22x22 hit area, 4 pt trailing inset, pointing-hand cursor, tooltip "Try Another Name", accessibility "Generate Another Name"; it replaces the name with a new random one (never the current one) and refocuses the field (`Overlays.swift:98-124`). Caption below, secondary: "You can rename this bot later without changing its workspace location." (`Overlays.swift:125-127`). The claim holds because workspaces are keyed by the bot's UUID, not its name (`Sources/NoodleCore/WorkspaceRepository.swift:126-127`).
- Validation line under the whole row (not under the field): `NameValidationMessage`, caption, red, wrapping (`Overlays.swift:31-41`, `131`).

**Name generation** (`Sources/Noodle/BotNameGenerator.swift:3-69`): the name is pre-filled on appear (`Overlays.swift:184`). Style is a global setting, "Generated bot names": Real (default) picks from 110 first names (Ada, Adrian, Aisha, ... Zara, Zoe); Playful combines one of 32 descriptors (Amber, Bright, Brisk, Calm, ...) with one of 32 companions (Badger, Beacon, Birch, ...) as "Clever Otter". Up to 8 draws avoid repeating the current name; fallbacks "Alex" / "Noodle".

**Name validation** (`Sources/NoodleCore/ConversationName.swift:4-31`), applied to bot and group names, on the trimmed value:

| Rule | Message |
|---|---|
| empty after trimming | "Enter a name." |
| contains a newline or control character | "Names must be a single line. Put longer text in Description or Backstory." |
| more than 100 characters | "Keep names to 100 characters or fewer." |

The message renders only while the raw field is non-empty (`Overlays.swift:34`), so an empty field shows nothing, but Create stays disabled. Create additionally requires a usable harness: a Hub-lent harness, or a local installation of the selected provider (`Overlays.swift:205-210`).

**Tab picker** (`Overlays.swift:11-29`): `Picker("Bot settings")`, segmented, label hidden, fixed to its intrinsic width and centred; selection changes animate with `easeInOut(0.22)` unless Reduce Motion. Segments in order: General, Harness, Tools, Computers, Browsers (enum raw values; the cases are `general`, `runtime`, `mcp`, `computers`, `browsers`). If the chosen harness moves between this Mac and a Hub (or between Hubs), New Bot clears the tool, computer and browser selections because IDs from one home mean nothing in another (`Overlays.swift:18-19`, `27`, `133-135`). Under every tab except Harness, New Bot repeats the experimental-harness warning (`Overlays.swift:176-178`).

**Field table, New Bot**

| # | Tab | Label | Control | Placeholder / value | Default | Validation / notes |
|---|---|---|---|---|---|---|
| 1 | header | Icon | 64 pt avatar button | "Change Bot Icon" tooltip | symbol `sparkles`, colour random 0-5 | (`Overlays.swift:56-57`) |
| 2 | header | Name | text field + regenerate | "Bot name" | random name | ConversationName rules |
| 3 | General | Description | multi-line text field, 2-3 lines | "Briefly describe what this bot does…" | empty | none; trimmed, empty saved as null |
| 4 | General | Backstory | 160 pt text editor | "Describe who this bot is, its role, tone, or priorities…" | empty | none; trimmed |
| 5 | General | Folders | disclosure row opening a sheet | "None" or count | none | max 16 folders; hidden for Hub harnesses |
| 6 | Harness | Harness | row + popover | provider name or "Choose a harness" | space's Hub harness, else first installed | must be installed or lent |
| 7 | Harness | Profile | row + popover | "System" or profile name | System | shown only when the provider supports profiles and has some |
| 8 | Harness | Model | row + searchable popover | "<Provider> default" | "" (harness default) | resets when harness changes |
| 9 | Harness | Effort | stepped slider | "Model default" | "" | snaps to the model's default if unsupported |
| 10 | Harness | Voice | row + tile popover | voice name or "<Provider> default" | follows the name | shown when the provider has voices |
| 11 | Tools | Tools | list well + chooser | "Add tools to this bot" | none | |
| 12 | Computers | Computers | grid well + chooser | "Add computers to this bot" | none | |
| 13 | Browsers | Browsers | grid well + chooser | "Add browsers to this bot" | none | |

**Description vs backstory** (`Overlays.swift:634-695`):
- Description: label "Description" (caption, semibold), `TextField(..., axis: .vertical)`, rounded border, `lineLimit(2...3)`; helper (caption, secondary): "Visible to other bots in shared groups. The private backstory below is never included." It is the bot's public record: profiles, participant lists and other bots see it.
- Backstory: label "Backstory"; a custom editor, because SwiftUI's `TextEditor` has no placeholder: a `ZStack` of a continuous 8 pt rounded rectangle filled `secondary` at 10 % opacity, a 13 pt tertiary placeholder (9 pt horizontal, 4 pt vertical inset, hit-testing off), and a 13 pt `TextEditor` with its scroll background hidden and 4 pt padding; fixed height 160; 1 pt stroke `secondary` at 18 %. No helper text. Stored privately in `agent.json` and deliberately never added to `AgentRecord` (`Sources/NoodleCore/AgentConfiguration.swift:15-34`); the profile sheet "Deliberately uses only the public record, never the workspace/backstory" (`AgentProfileSheet.swift:8`). Changing the backstory on an existing bot starts a fresh harness session on save (`NoodleStore.swift:1074-1075`; same path as New Session, `AgentRuntimeCoordinator.swift:764-768`).

**Harness tab** (`AgentConfigurationFields.swift:85-216`): a caption-semibold "Harness" label over a grouped card: continuous 12 pt radius, fill `secondary` at 7.5 %, 1 pt stroke `secondary` at 11 %; rows separated by dividers inset 44 pt (`:170-174`, `108`). Each `RuntimeSelectionRow` is 50 pt tall, 12 pt horizontal padding, `HStack(spacing: 11)`: a 20x20 secondary icon, then a caption secondary title over a 13 pt medium value (one line, optional model tag capsule), a 12 pt minimum spacer, and a trailing `chevron.right` (caption semibold, tertiary) or a small spinner while capabilities load (`:218-257`). Rows and their SF Symbols: Harness (provider brand mark via `HarnessProviderIcon`, fallback `terminal`), Profile (`person.crop.circle`), Model (`cube.transparent`), Effort (`bolt.fill`), Voice (`waveform`). Popovers attach with `arrowEdge: .leading`.
- Value strings: harness "Choose a harness" when none; a Hub harness reads "<Provider> · <Hub name>", with " (<profile>)" after the provider when the Hub lends a named profile, and "Noodle Hub" when the Hub has no name (`:42-49`). Model: the model's display name, else "<Provider> default", else "Harness default" (`:78-83`). Voice: the voice's name or "<Provider> default" (`:158-160`).
- Cascades: changing harness resets model to the Hub's initial model (or ""), effort to "" and profile to nil; changing model resets effort to the model's default if the current effort is unsupported (`:192-205`).
- Notes under the card, all caption: the experimental warning in orange with `exclamationmark.triangle.fill`, "<Provider> is experimental. Responses may be slow or unreliable." (only Apple Intelligence is experimental, `Sources/NoodleCore/Harnesses.swift:16`); "<Provider> always uses unrestricted access and can work beyond this bot's private workspace." (no current provider triggers it, `Harnesses.swift:21-25`); and the provider's capability error as an orange triangle label (`:176-190`).
- Harness chooser popover: "Choose Harness" headline (14 pt padding), divider, inset list, sections "This Mac" and one per Hub when both exist; rows: 22x22 brand icon, provider name, an orange caption "Experimental" when applicable, trailing tinted `checkmark`; choosing dismisses. Size 300 x clamp(62 + rows x 44, 110, 420) (`:265-346`).
- Profile chooser: "Choose Profile", rows "System" (`house`) then profiles (`person.crop.circle`), 300 x clamp(62 + (n + 1) x 44, 110, 320) (`:348-390`).
- Model chooser, 390 x 420 (`:490-593`): a search bar (magnifying glass, plain field "Search models", `xmark.circle.fill` clear button labelled "Clear Search"), 34 pt tall, `secondary` 10 % fill, 8 pt radius, 12 pt outer padding; an inset list whose first row is "<Provider> default" with subtitle "Use the harness default model." and icon `wand.and.stars` (omitted when the catalogue default is used, as for Apple or a Hub that restricts models); model rows with `cube.transparent`, medium name, optional tag capsule, two-line caption description, trailing checkmark; no matches shows the system search empty state; a 34 pt footer, caption, trailing: "1 known model" / "N known models". Search matches name, id, description and tag.
- Model tag: caption2 medium secondary text, 6/1 pt padding, capsule filled `secondary` 14 % (`:596-607`). Claude Code models: Fable, Opus, Sonnet (default), Haiku; efforts low, medium, high, Extra High (`xhigh`), max, default high (`Harnesses.swift:108-122`).
- Effort control (`:609-813`): `bolt.fill` icon column aligned with the rows; "Effort" caption on the left and the selection on the right (13 pt medium, accent when set, secondary "Model default" when empty); with no model: "Choose a model to tune its effort" (caption, tertiary). The track is 22 pt tall: a capsule at `primary` 8 %, 3 pt stop dots at 20 %, a fill masked to the knob position using a five-stop gradient (#CC5C29 at 0, #C76B7A at 0.3, #6B7AF5 at 0.58, #E6F5FF at 0.8, #FA8FBD at 1; converted from the RGB fractions at `:688-700`), a white glow ellipse whose opacity grows with effort (0.2 + 0.5 x fraction, blur 9), and a 54-particle sparkle field at 30 fps that crowds toward the high end and pauses at index 0 or under Reduce Motion; the knob is a 22 pt gradient circle with 50 % white fill, 75 % white 1 pt stroke and a 30 % black shadow (radius 2.5, y 1). Dragging snaps to stops with an alignment haptic; changes animate `snappy(0.28)`. Assistive tech sees a standard stepped Slider "Reasoning Effort" whose value is the selection name. Choices are "" (Model default) followed by the model's efforts.
- Voice chooser (`:407-474`), 340 wide, 16 padding: "Choose Voice" headline, then "Feminine" and "Masculine" groups (caption semibold secondary) of 3-column tiles: `waveform` 17 pt (animated variable colour while that sample plays) over the name at 12.5 pt (semibold when selected), minimum height 58, 10 pt radius; selected = accent 16 % fill, 1.5 pt accent stroke and a 12 pt `checkmark.circle.fill` top-trailing; unselected = `secondary` 8 %. Clicking selects and plays the sample but keeps the popover open, so voices can be compared; tooltip "Choose <name> and hear it"; playback stops when the popover closes.
- Voice follows the name: until a voice is picked, 400 ms after the name or harness changes, the store guesses a feminine or masculine presentation from the name and selects that provider's default voice for it (`:392-405`; `Sources/NoodleRuntime/BotVoice.swift:53-57`).

**Tools tab** (`MCPAssignmentPicker.swift:7-108`): header "Tools" (headline, unlike the caption labels elsewhere) with a bordered "Add Tools…" button (`plus`). Empty: a full-width plain button with `puzzlepiece.extension` (large title) and "Add tools to this bot", minimum height 220 (room for five 44 pt rows, so the sheet does not jump), `quaternary` 25 % fill, radius 10. Otherwise a fixed 220 pt scroll list on the same fill: rows (8 pt padding, spacing 10) with a 26 pt connection icon, name, an orange maturity badge (caption2 on orange 12 % capsule, `ToolCatalogView.swift:65-74`), and trailing `pencil` (opens the connection editor sheet, tooltip "Edit <name>") and `minus.circle.fill` (tooltip "Remove <name> from this bot") buttons. Calendar and Reminders are built-in tools in the same list: icon = rounded square (radius 0.22 x size) filled with the tint at 18 %, symbol `calendar` / `checklist` at 0.62 x size; subtitle summarises scope ("No calendars chosen yet", the chosen list names, "Checking access…", "Allow access to choose calendars", "No access — choose Calendar in System Settings"); an `ellipsis.circle` button opens a 260 x 240 scope popover with "This bot may use:" and one checkbox per calendar or list (coloured dot, title, "read-only" caption) or an access prompt with "Allow Access…"/"Asking…" or "Open Settings" (`EventKitController.swift:365-466`).
- Chooser popover, 330 x 260: "Search connections" field, built-in tools not yet added ("Calendar" / "Reminders" with subtitles "Calendars on this Mac, chosen per bot" / "Reminder Lists on this Mac, chosen per bot"), then saved connections not yet assigned (28 pt icon, name, badge, one-line description, blue `plus.circle.fill`); clicking adds without closing; empty: "No saved connections. Choose New Tool to add one."; footer "New Tool…" (`MCPAssignmentPicker.swift:110-169`). The New Tool sheet is presented only after the popover has closed (a `wantsNewTool` flag read in `onDisappear`) to avoid nesting a sheet inside a popover (`:47-51`).
- New Tool catalogue (`ToolCatalogView.swift:6-62`): Cancel / "New Tool" / an invisible balancing "Cancel"; "Search tools"; caption "Choose a service to add it and sign in. You can customize it afterward."; rows (9 pt padding): 32 pt brand icon (fallback: first letter on a quaternary rounded square), name, one-line summary, maturity badge, kind label (caption2), blue plus; tooltip "Add <name> and sign in"; empty: "No matching tools. You can add a custom MCP below."; footer "Custom MCP…" with "Connect your own MCP server". Choosing a preset adds it, closes, and 250 ms later starts sign-in (`ToolCatalogView.swift:145-153`). The iOS screenshot shows the same catalogue with tinted names and a bottom search field.
- Hub-kept bots get `HubConnectionPicker`: same layout, plus an orange warning triangle with the problem as tooltip and a small "Sign In" button on rows not signed in; empty chooser text "No connections on this Hub." (`MCPAssignmentPicker.swift:173-300`).

**Computers and Browsers tabs** (`CompanionAssignmentPicker.swift:24-137`, wrapped by `ComputerController.swift:233-317` and `BrowserController.swift:126-183`): header (caption semibold) "Computers"/"Browsers" with "Add Computers"/"Add Browsers" (`plus`); an optional notice (Computer update prompts: "Update Noodle Computer to enable file transfers." / "...native attachment previews.", "In Noodle Computer, choose Check for Updates from the app menu.", on accent 8 % with radius 10); a well (min 140, max 280, `quaternary` 35 %, radius 12) showing either a creation prompt when nothing exists ("No computers yet" / "No computers available", "Open Noodle Computer to create one, then return here to add it." or "Install Noodle Computer to create your first computer.", "Get Noodle Computer", "Apple silicon · macOS 26 or later"; browsers: "No browsers yet", "Open Noodle Browser" / "Get Noodle Browser"), an add prompt ("Add computers to this bot" under a large `desktopcomputer` / `globe`), or an adaptive grid (min 84, column spacing 12, row spacing 16, padding 12) of 48 pt avatars with a remove badge (`xmark.circle.fill` 17 pt, white on dark grey, 4 pt padding, offset 10/-8) and two-line centred caption names; each tile's tooltip is "<name> · <state>" plus its description; context menu "Delete Computer". Selected IDs that no longer exist render as "Unavailable computer" with a `questionmark` symbol.
- Chooser popover 300 x 280: "Search computers", rows with 32 pt avatar, name and state ("Ready", "Paused", "Unavailable" or the VM state), blue plus; empty "All computers added" / "No matching computers"; footer "New Computer…" and "Open Noodle Computer" (`CompanionAssignmentPicker.swift:139-192`).
- New Computer sheet: Cancel / "New Computer" / Create; grouped form with Picker "Kind" (templates with symbols), "Name" pre-filled "<Bot>’s Computer", "Description" (prompt "Optional", 2-3 lines), progress "Creating…" with tooltip "A new computer may first download its image." New Browser: "Name" "<Bot>’s Browser" and "Description" (`ComputerController.swift:320-387`, `BrowserController.swift:186-235`, naming rule `CompanionAssignmentPicker.swift:17-21`).

**Folders** (`BotFolderPicker.swift`): the General-tab row is a plain button styled as a card: `Label("Folders", systemImage: "folder")`, trailing "None" or the count and a caption `chevron.right`, 12 pt padding, `quaternary` 30 %, radius 10; for groups the tooltip is "Every bot in this group can use these folders in all its conversations." (`:13-31`). It opens a 480 pt Folders sheet that edits a draft: Cancel / "Folders" / Apply (disabled until changed, Return applies) (`:33-68`). Inside: "Folders" label, "Add Folders" (`plus`), a well (min 140, max 280) with the empty button `folder.badge.plus` "Add folders to this bot"/"group", or rows (12/8 padding, dividers inset 44): `folder.fill` 18 pt blue, name with a `lock.fill` caption2 when read-only, tilde-abbreviated path middle-truncated, an `ellipsis.circle.fill` "Access and Description" popover and a `minus.circle.fill` remove (`:73-165`). Adding uses the system open panel (directories only, multiple selection, prompt "Add"), de-duplicated by path. The options popover (300 wide): "Access" segmented "Read & Write" / "Read Only", and "Description" ("Describe what this folder is for…", 3-5 lines, capped at 500 characters) (`:167-190`). Model rules: default writable, at most 16 folders, absolute paths only, never the whole disk or Noodle's own storage, descriptions collapsed to one line (`Sources/NoodleCore/AgentFolder.swift:14-61`).

**Create** (`Overlays.swift:229-245`; `NoodleStore.swift:873-963`): validates every assignment, checkpoints settings, creates the bot's package (UUID folder, `agent.json`, `memory.md` seeded "# Memory\n\n", `WorkspaceRepository.swift:138-189`) and its direct conversation, writes folders, profile, voice and assignments, and on any failure restores the checkpoint, deletes the half-made bot and shows the error in the app's "Noodle" alert. On success the sheet closes, the new chat is selected and the bot starts. Without an installed harness: "Set up a supported harness in Settings before creating a bot."

### 2.4 Edit Bot sheet (`Overlays.swift:274-545`)

Header "Edit Bot" with Save. Identity row: same 64 pt avatar button; the name field has no regenerate button, no 14 pt override and no caption (`:338-361`). The field is focused only when the sheet opens on General. Tabs and contents:

| Tab | Contents (in order) |
|---|---|
| General | Description; Backstory; Folders row (local harness only); "Conversation Background" row; Sharing picker(s); divider; "Delete Bot" (leading) |
| Harness | the same Harness card; caption "Saving restarts the bot. Its workspace and history stay unchanged." |
| Tools / Computers / Browsers | same pickers, pre-filled from current assignments |

- Background row: `Label("Conversation Background", systemImage: "photo")`, value "Default" or "Custom", chevron; same card styling as Folders; opens the background sheet in draft mode, so the choice is applied only by Save (`Sources/Noodle/ConversationBackgroundView.swift:73-96`).
- Sharing (Noodle Hub only): shown for a bot kept on a Hub that allows sharing and not owned by someone else, and once per Hub a local bot is shared through, titled "Sharing" or "Sharing on <Hub>" when there are several (`Overlays.swift:376-384`, `493-502`). It loads the Hub's people (small spinner; red error caption; "Nobody else is on this Hub"), then shows an adaptive grid (min 64, spacing 8, rows 10) of 44 pt person badges with 3 pt padding; selected people get a 2 pt accent ring and a 16 pt `checkmark.circle.fill`, unselected ones 55 % opacity; names in caption; tapping toggles with `snappy(0.2)`; tooltip "Share with <name>" / "Stop sharing with <name>". A summary line updates with a cross-fade: "Only you can talk to <bot>." or "<people> can talk to <bot> too.", where <people> is a locale-formatted "and" list (`Intl.ListFormat` with `type: "conjunction"` on the web) and <bot> falls back to "this bot" (`Overlays.swift:549-632`; `LinkProtocol.swift:629-634`).
- Save is enabled whenever the name and harness are valid, without a dirty check (`Overlays.swift:504-509`), unlike Group Info.
- Save semantics: background and settings commit together; the background metadata is written first, and if the settings save fails the previous background is restored without deleting its media (`NoodleStore.swift:2005-2028`). Settings save restarts the bot; a changed backstory restarts it with a fresh session (`NoodleStore.swift:1072-1075`).

### 2.5 Settings checkpointing (`AgentSettingsCheckpoint.swift:4-50`)

Not a user-facing undo. Before create or update, the store snapshots the app-owned settings files: `computers.json`, `browsers.json`, `calendars.json`, `reminders.json` at the root, `MCP/connections.json`, the bot's `agent.json` and its direct conversation's `conversation.json` (32 MiB read limit each, absent files recorded as absent). If any step throws, `restore()` walks the entries in reverse, rewrites only files whose bytes differ, deletes files that did not exist, and rethrows the first error; transcripts, runtime state and the bot's working files are never copied. Directory descriptors keep restoration inside the original folders even if a path changes. The user sees the original error in the "Noodle" alert; only if the restore itself fails is a suffix appended: " Previous settings were restored where possible; workspace repair is still needed: ..." on edit, " Previous settings could not be fully restored: ..." on create (`NoodleStore.swift:935-946`, `1056-1068`).

### 2.6 Destructive actions and confirmations

`DestructiveActionButton` wraps an AppKit `NSButton` (push bezel, `hasDestructiveAction`, `bezelColor = .systemRed`, `tintProminence = .secondary`, capsule border, system font, hugging its content) because SwiftUI's bordered style drops macOS 26's subdued red tint outside alerts (`DestructiveActionButton.swift:4-24`).

| Trigger | Title | Buttons | Message | Source |
|---|---|---|---|---|
| Delete Bot | "Delete Bot?" | "Delete Bot" (destructive), "Cancel" | "“<name>”, its workspace, and its direct conversation will be permanently deleted. It will also be removed from every group. This cannot be undone." | `Overlays.swift:469-483`; `NoodleStore.swift:1313-1319` |
| Delete Group | "Delete Group?" | "Delete Group", "Cancel" | "“<name>”, its messages, and its attachments will be permanently deleted. The bots in the group will not be deleted. This cannot be undone." | `Overlays.swift:824-835` |
| Remove member | "Remove <name> from group?" | "Remove from Group", "Cancel" | none | `GroupMemberPicker.swift:84-95` |
| Remove tool | "Remove “<name>”?" | "Remove Tool", "Cancel" | "This bot loses access to it when you save. The tool connection itself is not deleted." | `MCPAssignmentPicker.swift:99-106` |
| Remove computer/browser | "Remove “<name>”?" | "Remove Computer" / "Remove Browser" | "This bot loses access to it when you save. The computer itself is not deleted." ("browser" for browsers) | `CompanionAssignmentPicker.swift:120-127` |
| Delete computer/browser | "Delete “<name>”?" | "Delete Computer" / "Delete Browser" | "It moves to the Trash, and every bot using it loses it." | `CompanionAssignmentPicker.swift:128-135` |
| Remove folder | "Remove “<name>”?" | "Remove Folder" | bot: "This bot loses access to the folder when you save. The folder and its contents stay on your Mac." group: "The group stops sharing the folder when you save. The folder and its contents stay on your Mac." | `BotFolderPicker.swift:155-161` |
| New Session | "Start a new session for <name>?" | "New Session", "Cancel" | "<name> will start with a fresh context. Its workspace, memory and messages are kept." | `ConversationRuntimeSettings.swift:46-66` |

All removals inside an editor only change the draft; nothing happens until Save. Confirmations use `confirmationDialog(..., titleVisibility: .visible)`.

### 2.7 Groups

**New Group** (`Overlays.swift:888-996`), 480 wide, header "New Group" / Create (valid name and at least one member). Body pieces are padded individually (16 horizontal): `TextField("Group name")` (top 16); the validation line; the description editor (top 12); Location (only if at least one Hub is joined); the member picker (top 12, bottom 16); the centred caption "Add at least one bot. You can change the members later." (bottom 14); the Folders row when the location is this Mac. No avatar and no background row at creation. The sheet can open with members pre-selected, and starts on the Hub whose space is shown when all pre-selected bots belong to it (`NoodleStore.swift:457-461`).
- Location means where the group is kept: "This Mac" (`laptopcomputer`) or a joined Noodle Hub (`server.rack`, Hub name or "Noodle Hub"); a group is all this Mac's bots or all one Hub's, never mixed (`Overlays.swift:943`; `NoodleStore.swift:1145-1149`). Tiles sit in an adaptive grid (min 130, spacing 8): `HStack(spacing: 8)` of an 18 pt-wide icon (accent when selected, else secondary) and a one-line, tail-truncated title; 10/8 padding; continuous radius 8; selected = accent 15 % fill and 1.5 pt accent stroke, unselected = `secondary` 8 % (`Overlays.swift:998-1028`). Changing location drops selected members that are not candidates there (`:962`).
- Group description (`Overlays.swift:1030-1052`): "Description", placeholder "Describe the purpose and context of this group…", 2-4 lines, helper "Shared with every bot in this group so they understand its purpose." This is the group's goal/brief; changing it (or the membership) notifies the bots (`NoodleStore.swift:1234-1239`).

**Group Info** (`Overlays.swift:739-861`), 460 wide, header "Group Info" / Save. Body `VStack(alignment: .leading, spacing: 18)`: a 64 pt group avatar beside the "Group name" field and a caption "1 bot" / "N bots"; validation line; description; member picker; caption "Add at least one bot. Membership changes apply to future messages." (secondary, turning red when no bot is selected); Folders row (not for Hub groups: "A Hub's bots run there, out of reach of this Mac's folders."); "Conversation Background" row; divider; "Delete Group". Save requires a valid name, at least one member, and an actual change to name, description, members, folders or background (`:852-860`).

**Member picker** (`GroupMemberPicker.swift:6-144`): header "Members" (caption semibold) with a bordered "Add Bots" (`plus`) button, disabled when every addable bot is already in. The well (min 140, max 280, `quaternary` 35 %, radius 12) grows with a few rows and then scrolls so the sheet's header and buttons stay on screen (`:79-82`). Empty: `person.crop.circle.badge.plus` (large title) "Add bots to this group" (32 pt vertical padding). Members: adaptive grid (min 84, spacing 12, rows 16, padding 12) of `AgentProfileButton` avatars at 48 pt with shadow (each opens the bot's profile), greyscale for archived bots, a dark-grey `xmark.circle.fill` remove badge (17 pt, offset 10/-8, tooltip "Remove <name> from group"), and two-line centred caption names. Archived bots remain members until removed but can never be added (`:14-15`). Chooser popover 300 x 280: "Search bots", rows (8 pt padding, spacing 12) with 32 pt avatar, name and blue `plus.circle.fill`; adding keeps the popover open; empty "All bots added" / "No matching bots".

**Group avatar** (`Sources/Noodle/Components.swift:6-61`): with two or more members, a `quaternary` circle with up to three member avatars at 0.62 x size, each with a 2 pt window-coloured ring, offset (-0.18, -0.14), (0.18, -0.14) and (0, 0.19) x size; with one member, a `quaternary` circle at 0.88 x offset (+0.06, +0.06) behind the avatar at 0.86 x offset (-0.06, -0.06). The group screenshot shows this composition above the title, description and comma-separated member names.

**Groups settings** (`GroupsSettingsView.swift:6-86`): a grouped form listing every group (profile button, name, caption "<Hub> · <members>"), an "Archived" column of mini switches (64 pt column; "—" for Hub groups), header button "Archived" opening an info popover: "An archived group keeps its messages and attachments, but leaves the sidebar and takes no new messages. Its bots keep running in their other conversations." / "Turning this off brings the group back as it was."; empty "No groups".

### 2.8 "Kick" means recovery, not removal

"Kick" is a sidebar context-menu item shown only when a bot's runtime has failed (`SidebarView.swift:118-123`). An ordinary kick restarts immediately; failures that need consent return a request shown by `AgentKickConfirmation` as an alert (`AgentKickConfirmation.swift:6-39`; copy in `Sources/NoodleRuntime/AgentRuntimeCoordinator.swift:53-95`):

| Failure | Title | Confirm | Extra buttons | Message (abridged only where marked) |
|---|---|---|---|---|
| missing session | "Recover <name>?" | "Recover Bot" | Cancel | "The previous session is unavailable. Noodle can start a replacement and help <name> continue using your conversation history." + "Your messages, files, and bot settings will be kept. Details remembered only within the previous session may be lost." |
| usage limit | "Usage limit reached" | "Retry Now" | Cancel | "The harness's usage limit has been reached. Once usage is available again, retry to continue. Restarting cannot restore usage. Your session and unfinished work will be kept." |
| sign-in needed | "Sign in to reconnect <name>" | "Retry Now" | "Open Harness Settings", Cancel | "The harness needs you to sign in again. Open Harness settings for sign-in options, then retry. Your session and unfinished work will be kept." |
| recovery failed | "Retry recovery?" | "Retry Now" | Cancel | "Noodle will retry recovery using your conversation history. Your messages and files will be kept." |
| safety stop | "Safeguards stopped <name>" | "Resume" | "New Session", Cancel | "The model's safeguards stopped a response. Resume continues the same session. New Session starts with a fresh context and keeps the workspace, memory and messages." |

The modifier is attached to the containing view "so closing a context menu cannot dismiss the alert" (`AgentKickConfirmation.swift:5`). Removing a member from a group is covered in 2.7.

### 2.9 Profiles

- `AgentProfileButton` (`AgentProfileButton.swift:6-71`): the bot's avatar as a plain button (default 32 pt; tooltip "Show <name>’s profile", accessibility "Show profile for <name>") that opens the profile in a popover (`arrowEdge: .leading`). Its Message and Edit actions record a pending action, close the popover, and run on the next main-loop turn so the popover detaches before an editor sheet or window appears (`:50-70`). `GroupProfileButton` mirrors it for groups (`:74-125`).
- `AgentProfileSheet` (`AgentProfileSheet.swift:8-182`), 320 wide, 20 padding, `VStack(spacing: 16)`: a trailing `xmark.circle.fill` close button ("Close bot profile"); 88 pt avatar; name in `title2` semibold, centred, selectable; then either an "Archived" capsule or the bot's self-set status (one line, at most 60 characters, `Models.swift:18-21`) as a capsule (callout, 12/6 padding, `quaternary` fill); the public description in a scroll view capped at 120 pt (secondary, centred, selectable; fallback "No description yet."); and an action row. Actions, in order, when available: Reply (`arrowshape.turn.up.left`, "Reply in Group"), Message (`bubble.left`, "Direct Message", disabled when no direct chat exists), Call (`phone`, "Call <name>"), Computer (`desktopcomputer`; opens the one assigned computer or a chooser popover), Browser (`globe`), Edit (`pencil`, "Edit Bot"), Usage (`chart.bar`, "Show Usage"). Bots shared with the user by someone else omit Edit and Usage ("A bot someone shared is only talked with", `:97-98`). Up to four actions per row, split evenly, separated by 32 pt vertical dividers (`:102-108`). Each action label: 18 pt icon in a 20 pt frame over a caption title, full width, 6 pt vertical padding (`:302-318`).
- `GroupProfileSheet` (`:233-299`): same layout with the group avatar at 88 pt, title, "Archived" capsule if archived, description plus a caption list of member names, and actions Message (`bubble.left.and.bubble.right`, "Open Conversation") and Edit ("Edit Group").
- Profiles dismiss on an outside click or when the app deactivates; this is deliberately local to profiles because "clicking outside an editor must not discard unsaved changes" (`:320-372`).

### 2.10 Pickers

| Picker | Purpose | Presentation | Search | Selection | Rows / tiles | Empty states |
|---|---|---|---|---|---|---|
| `AgentPicker` | global conversation switcher, not member selection | borderless panel 560 wide, material, radius 22 | "Search", 18 pt, focused | single; Return picks | 4-column tiles 104 tall | "No Conversations" |
| `GroupMemberChooser` | add bots to a group | popover 300 x 280 | "Search bots" | multi-add, stays open | 32 pt avatar row | "All bots added" / "No matching bots" |
| `MCPConnectionChooser` | add tools | popover 330 x 260 | "Search connections" | multi-add | 28 pt icon, name, description | "No saved connections. Choose New Tool to add one." |
| `CompanionAssignmentChooser` | add computers / browsers | popover 300 x 280 | "Search computers" / "Search browsers" | multi-add | 32 pt avatar, name, state | "All ... added" / "No matching ..." |
| `BotFolderPicker` | add folders | system open panel | none | multi (panel) | folder list rows | "Add folders to this bot/group" |
| Harness / Profile / Model | single-value choice | popovers | Model only | single, closes | list rows + checkmark | system search empty state |
| Voice | single-value choice | popover 340 wide | none | single, stays open, plays sample | 3-column tiles | none |

`AgentPicker` details (`AgentPicker.swift`): opened by a system-wide hot key (default ⌃⌥Space, "From any app, pick a bot or group to float over your work.", `KeyboardShortcuts.swift:89`, `104`) registered through Carbon so no Input Monitoring permission is needed (`:100-125`). Layout constants: 4 columns, tile height 104, spacing 10, at most 3 visible rows before scrolling, 18 padding, 24 pt search row, 14 pt section spacing, 2 pt grid inset; the panel height is fixed when it opens so filtering does not make it wobble, and it hangs from its top edge at 72 % of the screen's visible height, centred horizontally (`:47-76`, `213-220`). Tiles: 56 pt conversation avatar, 12.5 pt medium title, an 8 pt blue unread dot, a `pip.fill` badge (10 pt semibold white on an accent circle, offset 6/-4) for conversations already floating; selected fill accent 28 %, otherwise `primary` 4 %, radius 14 (`:363-402`). Floating conversations sort first, then most recent; arrow keys move without wrapping; Return picks; Escape or losing key focus closes; the Call and Record shortcuts open the selection and act in it (`:27-44`, `276-311`). Picking floats that conversation.

### 2.11 Avatars

**Model** (`IconAppearance.swift:7-71`; `Models.swift:14-17`): `iconSymbol: String?`, `iconColour: Int`, `iconImage: Data?`. Bots store an arbitrary integer seed; the rendered gradient is `|seed| mod 6` (`:30-34`), while browsers and computers clamp to 0-5. Bots persist `avatarSymbolName`, `avatarColorIndex` (falls back to `accentSeed`, a random 0-5 assigned at creation) and `avatarImageData` inside `agent.json` with the rest of the public record (`AgentConfiguration.swift:17-60`; the JSON read limit is 32 MiB).

**Palette** (six two-stop linear gradients, top-leading to bottom-trailing): 0 blue to cyan, 1 purple to pink, 2 orange to yellow, 3 mint to teal, 4 indigo to blue, 5 pink to orange (`IconAppearance.swift:25-28`). These are dynamic system colours; see section 5 for approximate hex values.

**Rendering** (`IconBadge`, `IconAppearance.swift:42-71`): a circle-clipped `size x size` frame showing the image scaled to fill, or the gradient with the symbol at 0.38 x size, semibold, white (people on a Hub can show initials at 0.36 x size, rounded semibold). Shadow black 20 %, radius 3, y 1, unless disabled. Default symbol `sparkles` (`BotAvatar.swift:12`). Native menus get a 16 pt avatar rendered at 2x (`BotAvatar.swift:30-38`). Sizes seen: 16 (menus), 22 (companion chooser), 32 (lists, popover rows), 42 (sidebar), 44 (people), 48 (member grids), 56 (picker), 64 (editors, first-run team), 88 (profiles), 104 (icon editor).

**Icon editor** (`IconEditorSheet.swift:9-153`, configured for bots at `Overlays.swift:697-737`), 440 wide, edits a draft: Cancel / "Bot Icon" / Done (disabled while an image loads). Body `VStack(spacing: 18)`, padding 20: a 104 pt preview; GroupBox "Image" with two equal-width bordered buttons, "Choose Image…" (a menu, see below) and "Create Image…" (`apple.intelligence`, Image Playground, macOS 15.1+, only when the device supports it), a divider, a full-width bordered button "Use Symbol Instead" (`square.grid.2x2`) that reads "Using Symbol" (`checkmark`, disabled) while no image is set, and a spinner while loading; a red caption for failures; GroupBox "Colour" with six 34 pt gradient circles (spacing 12) and a 13 pt bold white checkmark on the selected one (only while no image); GroupBox "Symbol", a 6-column grid (spacing 10) of 42 pt-tall tiles (radius 10; selected accent fill with white glyph, else `secondary` 14 %; glyph 18 pt semibold). Picking a colour or symbol clears the image. The bot symbol set: `sparkles`, `bolt.fill`, `brain.head.profile`, `hammer.fill`, `terminal.fill`, `magnifyingglass`, `shippingbox.fill`, `paintbrush.fill`, `checkmark.seal.fill`, `ladybug.fill`, `wand.and.stars`, `gearshape.2.fill`. Done always records a symbol for bots (the image wins when present). There is no emoji avatar.

**Image sources**:
- "Choose Image…" opens an AppKit menu under a SwiftUI bordered button (`photo` icon plus a caption2 `chevron.down`, 20 pt label height): "Choose File…" (`folder`), "Photos Library…" (`photo.on.rectangle`); people's pictures also get "Take Photo…" (`camera`) first (`ImageSourceMenu.swift:26-66`). Bots do not offer the camera (`takesPhotos` defaults to false, `IconEditorSheet.swift:27-30`).
- Image Playground ("Create Image…"): concepts from `AvatarIdea`, the phrases "avatar portrait" and the bot's name plus a concept extracted from the description, or the backstory when the description is empty (`NoodleWallpaperCore/AvatarIdea.swift:6-27`); illustration style, personalization disabled so it never offers people from Photos, square output; the current image seeds it (`ImagePlaygroundSetup.swift:5-35`; `NoodleImagePlaygroundButton.swift:7-41`). The illustrated portraits in both macOS screenshots are consistent with this style (inferred).
- From the chat: an image attachment in a direct conversation offers "Use as Icon" (`person.crop.circle`) with the confirmation "Use as Icon?" / "Replace <name>’s icon with this image? Their icon will change everywhere in Noodle." / "Use as Icon" (`MessageContextMenu.swift:93-98`; `Components.swift:391-420`).
- Camera (people only): Cancel / "Camera" / "Take Photo" (default action, disabled until the first frame arrives), live preview 320 x 320 clipped to a circle; the shot is mirrored as previewed, centre-square cropped and scaled to at most 512 px; errors "Allow Noodle to use the camera in System Settings > Privacy & Security > Camera.", "No camera is available.", "The camera cannot be read." (`CameraView.swift:51-57`, `94-158`; `IconAppearance.swift:108-122`).
- Processing: sources over 50 MB are refused; images are thumbnailed to 512 px on the long side with EXIF orientation applied; bots encode JPEG at quality 0.86 (`Overlays.swift:733`; `IconAppearance.swift:93-147`). Errors: "That image could not be used.", "Choose an image smaller than 50 MB.", "The image is too large. Choose a smaller image.", "Photos could not provide this image. Try Choose File instead." (`IconAppearance.swift:80-91`).

**Starter team** (first run): three bots on the chosen account, named by the generator, in a group "Team" ("Ask the team anything; the personal assistant brings in whoever fits."): Personal Assistant (`sparkles`, colour 0), Full-Stack Developer (`terminal.fill`, colour 2), Researcher (`magnifyingglass`, colour 3), each with a public description and a backstory; leaving the welcome sends "Welcome to the team! Please introduce yourselves, and tell me what you are best at and how you can help me." (`Sources/Noodle/StarterTeam.swift:15-31`).

### 2.12 Conversation backgrounds

**Model** (`NoodleWallpaperCore/ConversationBackground.swift:5-33`): `preset` (sunset, ocean, forest, dusk), `imageFilename`, `mediaKind` (image, video, dynamicImage). "Local appearance only: kept out of message delivery and the agent workspace." Stored per conversation as `background.json` plus `Backgrounds/<uuid>.<jpg|heic|heif|mov|mp4|m4v>` in the conversation folder; replacing a background deletes the old file and its phone-sized copy only after the new metadata commits (`Sources/NoodleCore/ConversationBackground.swift:7-95`).

**Presets** (`NoodleWallpaper/ConversationBackgroundView.swift:82-115`): a three-colour linear gradient (top-leading to bottom-trailing) plus an ellipse of the middle colour at 50 % opacity, 1.4 x width by 1.1 x height, rotated -35°, offset 0.35 x width, blurred 50. Colours (from RGB fractions): Sunset #F58526, #C46EAB, #7559BF; Ocean #0A4280, #1499A8, #244D9E; Forest #143D2E, #57874D, #245763; Dusk #2E2963, #784A99, #BA5E78.

**Rendering**: any non-default background gets a black 25 % scrim (`:92`) and switches the window to dark appearance with a 0.35 s cross-fade (`:118-156`); changing wallpapers cross-fades 0.35 s ease-in-out after the next image has loaded, so image-backed chats never flash the default canvas (`:5-7`, `43-61`). Videos loop muted with aspect fill and pause when the window is hidden, minimized or occluded, or under Reduce Motion; multi-frame HEICs advance every 8 s with a 1 s fade (`AnimatedWallpaper.swift:54-138`).

**Background sheet** (`Sources/Noodle/ConversationBackgroundView.swift:4-71`), 520 wide, 24 padding, spacing 20: Cancel / "Conversation Background" / Apply (default action; disabled while busy or unchanged); the conversation title in secondary; a 210 pt live preview with radius 16 showing two sample bubbles on regular material capsules (10 pt padding), "Make this space your own." on the left and "Looks good!" on the right; the shared `BackgroundPicker`; a spinner while busy; a red caption on failure. The sheet cannot be dismissed while busy. Opened standalone (sidebar "Change Background…") Apply saves immediately; opened from Edit Bot or Group Info it only updates that editor's draft.

**Picker** (`BackgroundPicker.swift:25-150`): a row of five swatches, "Default", "Sunset", "Ocean", "Forest", "Dusk" (spacing 12; 48 pt tall live previews, radius 8, 2 pt accent stroke when selected, caption title; accessibility value "Selected"/"Not selected"), then a row (spacing 8) of "Choose Background…" (menu: "Choose File…", "Photos Library…", and "System Wallpapers…" only while this Mac has some downloaded) and "Create Image…" (Image Playground sized like the main screen on macOS 27+, seeded with the still being previewed).

**Sources and limits**:
- Files (picker or drop): images up to 512 MB (re-encoded to JPEG at quality 0.9, max 2560 px), multi-frame HEIC kept as dynamic (at most 120 frames), MP4/M4V/MOV up to 1 GB that must prove playable within 12 s (`NoodleWallpaperCore/BackgroundMedia.swift:37-104`).
- Photos library and generated images: up to 50 MB, converted to JPEG (`BackgroundPhoto.swift:6-23`; `BackgroundPicker.swift:119-126`).
- Drag and drop onto the preview: Finder files, image or movie data, or a URL; remote links download (30 s request and 120 s resource timeouts, 1 GB cap, extension recovered from the MIME type); videos are preferred over posters and TIFF last; while targeted the preview shows a 3 pt accent border (radius 16); tooltip "Drop an image or video to use as the background" (`BackgroundDropTarget.swift:4-48`; `BackgroundDrop.swift:5-161`).
- System wallpapers: only wallpapers already on disk (downloaded catalogue stills, hidden catalogue folders and aerial videos) are offered; sheet 720 x 560: Cancel / "System Wallpapers" / "Wallpaper Settings" (opens System Settings), a 4-column grid (spacing 12, rows 14, padding 20) of 16:10 thumbnails (radius 8, caption name), reloading when the app is reactivated (`SystemWallpaperSheet.swift:9-74`; `SystemWallpaper.swift:9-120`).
- From the chat: "Use as Background" on an image attachment, confirmed with "Use as Background?" / "Replace the background for <conversation> with this image?" (`MessageContextMenu.swift:84-92`).
- Errors: "Choose a readable image smaller than 50 MB."; "Choose a readable image or HEIC up to 512 MB, or a playable MP4, M4V or MOV video up to 1 GB. Wallpaper packages and streaming playlists are not supported."; "Drop a readable image or MP4, M4V or MOV video, or a direct link to one."; "The background couldn’t be downloaded. Try dropping the image or video itself."; Photos: "Photos couldn’t provide this image. If it’s in iCloud, open it in Photos and let it download, then try again. You can also use Choose Background → Choose File." (`ConversationBackground.swift:35-43`; `BackgroundDrop.swift:142-149`; `BackgroundPicker.swift:94`).

### 2.13 Bots settings

`BotsSettingsView` (`BotsSettingsView.swift:6-234`), a grouped form: a section with Toggle "Wake idle agents" and menu Picker "Wake after" (5, 10, 15, 30, 45 minutes, 1 hour, 2, 4, 8, 12 hours, 1 day, plus the current value); then one row per bot: profile button, name, a caption line with the access status as a link-like button ("restricted" in secondary or "unrestricted" in orange, "Restarting runtime…" while changing) and "· apps" in orange when enabled, and "Last heartbeat <relative time>" / "No heartbeat yet"; trailing mini switches in fixed columns Heartbeat (64), Unrestricted (100), Apps (64, "—" for providers without account apps) and Archived (64, only when some bot can be archived). Column headers are plain buttons opening 360-wide info popovers (20 padding). Turning on Unrestricted or Apps asks first: "Allow Unrestricted Access for <name>?" / "Allow Unrestricted Access" / "<name> will be able to read and change files and use services beyond its private workspace, with the access available to your Mac account. This restarts the bot." and "Allow Apps for <name>?" / "Allow Apps" / "... apps connected to your ChatGPT|Claude.ai account, such as Gmail, Google Drive, and Calendar, with the permissions you granted there. This restarts the bot." (`:262-281`). Archive info: "An archived bot keeps its workspace, memory and conversations, but does not run, wake or receive messages, and its chat leaves the sidebar. It stays a member of its groups but is left out of their messages." (`:236-247`).

### 2.14 First-run setup (form only)

`FirstBotSetupSheet` (`FirstBotSetupSheet.swift:7-183`), 640 wide, 20 padding. Step 1: four equal tiles in a row (spacing 10): Codex "by OpenAI", Claude "by Anthropic", Muse "by Meta", Grok "by xAI" (`FirstBotSetup.swift:17-38`), each a 36 pt brand icon, headline name, caption maker and a status label ("Checking…" `ellipsis.circle`; "Ready" green `checkmark.circle.fill`; "Sign-in required" `person.crop.circle.badge.questionmark`; "Unavailable" `minus.circle`), 18 pt vertical padding, `primary` 4 % fill, radius 12; choosing a tile starts install or sign-in; "Not Now" link (cancel action). Step 2: icon, name, status, a single linear progress bar that never changes position, sign-in challenge views, red selectable errors, and link-style "Install" / "Sign In…" and "Back". Step 3: the three starter bots (64 pt avatar, headline name, caption role, 140 pt columns) and a large prominent "Continue" (default action) that greets the team.

### 2.15 Form and copy conventions

- Labels are `caption` semibold above the control; helper text is `caption` secondary below; errors are `caption` red, and warnings orange with `exclamationmark.triangle.fill`. One requirement line (Group Info members) changes from secondary to red instead of adding a new message (`Overlays.swift:804-806`).
- Containers: input wells use `quaternary` at 25-35 % with radius 10-12; disclosure rows use `quaternary` 30 %, 12 pt padding, radius 10; the harness card uses `secondary` 7.5 % with an 11 % hairline; tiles use accent 15-16 % plus a 1.5 pt accent stroke when selected; list-row remove badges are `xmark.circle.fill` / `minus.circle.fill` in white on dark grey; add affordances are blue `plus.circle.fill`.
- Large empty states are whole-area buttons (large-title symbol over a sentence such as "Add tools to this bot") that open the same chooser as the header button.
- Fixed heights where content changes (220 pt tool list, 140-280 pt wells) prevent sheets from jumping as items are added (`MCPAssignmentPicker.swift:24-25`).
- Noodle's own copy rules (data from `.agents/skills/ui-copy/SKILL.md:6-40`): no persistent instructional hints or shortcut legends unless requested, optional guidance in tooltips; a trailing "…" (single character) only when the command needs more input ("Add Tools…", "New Computer…", "Choose Image…"), never for "New Bot" or for actions that merely confirm ("Delete Bot"); row secondary actions go in a `…` menu with the destructive action last. British "Colour" appears in UI (`IconEditorSheet.swift:74`).
- Approximate macOS text styles (inferred from Apple's macOS type scale, not in the repo): headline 13 pt bold, body 13, callout 12, caption 10, caption2 10, title2 17, large title 26.

### 2.16 iOS editor (screenshot)

The mobile editor is a standard inset-grouped `Form` in a navigation stack: avatar 88 pt with tinted "Edit" (opens a picture editor), "Name", a Harness menu picker ("Choose" until set; "Your plan lends no harnesses" when empty), Model ("Default" or "Choose" when the Hub restricts models), Effort ("Default"), Voice navigation row ("Automatic" until chosen), Tools / Computers / Browsers navigation rows, "Sharing" ("Only You", "1 Person", "N People"), "Background", then "Kick" (failed only) and "New Session", then red "Delete Bot" with "Delete <name>?" / "Delete" / "The bot and its conversation are deleted from the Hub for all your devices." The toolbar shows Cancel and "Create" or "Save" (`Mobile/Sources/NoodleMobile/Chats.swift:2158-2298`). The screenshot's avatar uses palette 5 (pink to orange) with `sparkles`.

## 3. Web translation

### 3.1 Schemas (zod)

```ts
import { z } from "zod";

const graphemes = (s: string) =>
  [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(s)].length; // Swift counts graphemes
const NOT_SINGLE_LINE = /[\n\r\u000B\u000C\u0085  \p{Cc}]/u; // add \p{Cf} for exact parity (see 5)

export const conversationName = z.string().transform((s) => s.trim()).superRefine((s, ctx) => {
  if (!s) ctx.addIssue({ code: "custom", message: "Enter a name." });
  else if (NOT_SINGLE_LINE.test(s))
    ctx.addIssue({ code: "custom", message: "Names must be a single line. Put longer text in Description or Backstory." });
  else if (graphemes(s) > 100) ctx.addIssue({ code: "custom", message: "Keep names to 100 characters or fewer." });
});

export const folderSchema = z.object({
  path: z.string().startsWith("/"),
  writable: z.boolean().default(true),
  description: z.string().max(500).optional(), // collapsed to one line on save
});

export const avatarSchema = z.object({
  symbol: z.string().nullable(),   // icon id; null = default "sparkles"
  colour: z.number().int(),        // any int; palette index = Math.abs(colour) % 6
  image: z.string().nullable(),    // URL of a stored 512 px JPEG (q 0.86)
});

export const botSchema = z.object({
  name: conversationName,
  harness: z.string().min(1),      // installed locally or lent by a Hub
  model: z.string(),               // "" = harness default
  effort: z.string(),              // "" = model default
  profileId: z.string().uuid().nullable(), // null = "System"
  voice: z.string(),               // "" = follows the name until chosen
  publicDescription: z.string(),   // trimmed; "" stored as null
  backstory: z.string(),           // private; never sent to profiles or other bots
  avatar: avatarSchema,
  toolIds: z.array(z.string().uuid()),
  calendarIds: z.array(z.string()),
  reminderListIds: z.array(z.string()),
  computerIds: z.array(z.string().uuid()),
  browserIds: z.array(z.string().uuid()),
  folders: z.array(folderSchema).max(16, "At most 16 folders can be shared."),
  sharedWith: z.array(z.string().uuid()).optional(), // edit only
});

export const groupSchema = z.object({
  name: conversationName,
  publicDescription: z.string(),
  memberIds: z.array(z.string().uuid()).min(1), // shown by turning the helper line red, not a new message
  folders: z.array(folderSchema).max(16),
  location: z.string(),            // "this-device" or a joined Hub id; members must match it
});

export const backgroundSchema = z.object({
  preset: z.enum(["sunset", "ocean", "forest", "dusk"]).nullable(),
  imageFilename: z.string().nullable(),
  mediaKind: z.enum(["image", "video", "dynamicImage"]).nullable(),
});
```

Form wiring: one react-hook-form instance per sheet spanning all tabs (Noodle keeps every field in the sheet so switching tabs, which rebuilds the tab view, loses nothing; it even lifts the built-in tool toggles into the sheet for that reason, `MCPAssignmentPicker.swift:15-17`). Use `mode: "onChange"` so the primary button's disabled state tracks validity; render the name error only when the raw value is non-empty. Cascades (harness to model/effort/profile; model to effort) belong in `useEffect`/`watch` subscriptions or a reducer, not in field components. The voice auto-follow is a 400 ms debounced effect that stops once `voiceChosen` is set.

### 3.2 Component structure

```
BotEditorDialog (mode: create | edit)        ~ NewBotSheet / EditBotSheet
  SheetHeader  [Cancel] title [Create|Save]   text buttons, primary disabled when invalid
  Separator
  Body (p-5, gap-4 / gap-[18px])
    IdentityRow: AvatarEditButton(64) + NameField(regenerate in trailing slot) + caption (create only)
    FieldError (name)
    SegmentedTabs: General | Harness | Tools | Computers | Browsers   (Radix Tabs or ToggleGroup)
    General: DescriptionField, BackstoryField, DisclosureRow(Folders), DisclosureRow(Background, edit),
             SharingGrid (edit, hub only), Separator, DestructiveButton("Delete Bot") (edit)
    Harness: HarnessCard{ SelectionRow(Harness), SelectionRow(Profile)?, SelectionRow(Model),
             EffortSlider, SelectionRow(Voice)? } + notes
    Tools:   AssignmentWell(list) + ChooserPopover + NewToolDialog
    Computers / Browsers: AssignmentWell(grid) + ChooserPopover + NewCompanionDialog
GroupEditorDialog (create 480 / info 460), IconEditorDialog (440), BackgroundDialog (520),
SystemWallpapersDialog (720 x 560), ProfilePopover (320), ConversationSwitcher (560, Command palette)
```

shadcn mapping: Dialog for sheets; Tabs (styled as a segmented control, centred, intrinsic width); Popover + Command for choosers; AlertDialog for every confirmation in 2.6; Tooltip for every `.help`; DropdownMenu for "Choose Image…" / "Choose Background…"; Slider customised for Effort (Radix keeps the slider semantics Noodle recreates with `accessibilityRepresentation`); ToggleGroup for "Read & Write / Read Only"; Avatar for the circle badge; `lucide-react` icons in place of SF Symbols (`sparkles`, `bolt`, `brain`, `hammer`, `terminal`, `search`, `package`, `paintbrush`, `badge-check`, `bug`, `wand-sparkles`, `cog` are close equivalents; inferred mapping).

### 3.3 Dialog sizing and motion

- Fixed widths from the table in 2.1 with `max-w-[calc(100vw-2rem)]`; height fits content up to the viewport, with internal scrolling in the wells rather than the dialog when possible.
- Anchor the dialog's top edge (for example `top-[8vh] translate-y-0`) so it grows downward like a macOS sheet; animate height changes on tab switch with a ResizeObserver-driven height transition (about 220 ms, matching the 0.22 s tab animation) and disable it under `prefers-reduced-motion`.
- Close behaviour: editors must not close on outside click or Escape while dirty (Noodle's profiles close on outside click; editors deliberately do not). Profiles may close on outside click and on window blur.
- Present nested dialogs (New Tool, New Computer, Folders, Icon, Background) only after the triggering popover has closed, mirroring the `wantsNew`/`onDisappear` pattern, to avoid focus-trap conflicts.

### 3.4 Picker patterns

- AssignmentWell: header (label + outline "Add X" button) over a well with min 140 / max 280 px height (tools: fixed 220), empty state as a full-area button, selected items as a grid (84 px minimum columns, 48 px avatars, corner remove badge offset 10/-8) or a list (26 px icon, edit and remove icons). Removal opens an AlertDialog whose copy says the change applies on save and the underlying item is not deleted.
- ChooserPopover: search input on top, scrollable list of not-yet-selected items each with a trailing add icon, multi-add without closing, distinct empty texts for "everything added" versus "no matches", a footer for "New X…" and "Open <companion>". Fixed popover sizes (300 x 280, 330 x 260) keep it stable while filtering.
- Single-select popovers (harness, profile, model): list with trailing check, select-and-close, height `clamp(110px, 62px + rows x 44px, 420px)`; the model list adds search and a count footer.
- Tile choosers (voice, location, sharing): grid of rounded tiles; selected = accent-soft fill + 1.5 px accent ring + check badge; voice tiles play a preview and stay open.
- Conversation switcher: a Command palette rendered as a 4-column grid with arrow-key grid navigation (no wrap), Enter to open, Escape to close, fixed height chosen when opened.

### 3.5 Avatars and backgrounds on the web

- Avatar pipeline: accept file, paste, drag-drop and generated images; decode with `createImageBitmap(file, { imageOrientation: "from-image" })`, draw to a canvas scaled so the long side is at most 512 px, export `image/jpeg` at 0.86; refuse sources over 50 MB. Render as `object-cover` in a circle; otherwise the gradient plus a white icon at 38 % of the size. Image generation can reuse AvatarIdea as a prompt: "avatar portrait, {name}, illustration" plus the description (else backstory).
- Background pipeline: still images to JPEG at 0.9 with a 2560 px long side; videos as `<video muted loop playsinline autoplay>` with `object-fit: cover`, paused on `visibilitychange` and under reduced motion; a fixed 25 % black overlay; switch the conversation surface to the dark theme while a background is set; cross-fade 350 ms once the next image has loaded. Presets render as CSS: a 135° three-stop linear gradient plus a rotated, 50 px-blurred ellipse of the middle colour at 50 %.

### 3.6 Tokens rather than opacity arithmetic

To respect the "no opacity arithmetic at call sites" rule, define named tokens for the recurring tints: `--surface-well` (quaternary 35 %), `--surface-well-subtle` (25 %), `--surface-row` (30 %), `--surface-card` (secondary 7.5 %) with `--border-card` (11 %), `--field-editor` (secondary 10 %) with `--border-editor` (18 %), `--tile` (secondary 8 %), `--tile-selected` (accent 15-16 %), `--picker-tile` (primary 4 %), `--picker-tile-selected` (accent 28 %), `--scrim-wallpaper` (black 25 %), `--badge-remove` (white glyph on dark grey), and effort gradient stops `--effort-0` to `--effort-4`.

## 4. Patterns worth copying, and pitfalls

Copy:
- Pre-filling a plausible name with a one-click regenerate makes "hiring" a one-step action; the caption promising that renaming is safe removes hesitation.
- Splitting the public description (shared with other bots and profiles) from a private backstory, with the helper text saying so, is a clear privacy model; restarting with a fresh session when the backstory changes keeps behaviour consistent with the prompt.
- Transactional saves (checkpoint, write, restore on failure; background committed together with settings) so a failed edit never leaves a half-applied bot.
- Assignment wells with stable heights, whole-area empty-state buttons, multi-add choosers and "loses access when you save; the item itself is not deleted" confirmations.
- Profiles that use only public data, dismiss on outside click, and expose at most four actions per row.
- Sheets anchored at the top that resize to content without remounting, so drafts and focus survive tab switches.
- Media limits and error messages that name the accepted formats and sizes.

Pitfalls:
- Edit Bot's Save has no dirty check (Group Info does): saving an unchanged bot still restarts it. Add a dirty check on the web.
- The name error appears under the whole identity row, separate from its field, and not at all while the field is empty; place errors next to the field and use `aria-describedby`.
- Header primary buttons are plain blue text with no keyboard shortcut except Return in the name field; on the web, make the primary a real button and bind Enter and Escape deliberately.
- Some labels are inconsistent: "Tools" is a headline while other section labels are captions; Settings says "Wake idle agents" while the rest of the UI says "bots".
- Popovers for single choices close on selection, but the voice popover does not; make that difference visible (for example a Done affordance) on the web.
- Swift's control-character set includes Unicode format characters, so names containing zero-width-joiner emoji are likely rejected as "not a single line" (inferred); decide deliberately whether to copy that.
- Very small caption sizes (10 pt) suit macOS but are hard to read on the web; scale the type ramp up.

## 5. Open questions and inferences

- Approximate hex values for the avatar palette, from Apple's 2025 system colours (inferred, not in the repo; verify in light and dark): blue #0088FF, cyan #00C0E8, purple #CB30E0, pink #FF2D55, orange #FF8D28, yellow #FFCC00, mint #00C8B3, teal #00C3D0, indigo #6155F5. The iOS screenshot's pink-to-orange avatar is consistent with these.
- Whether Escape dismisses the bot and group sheets on macOS was not verified; no `.cancelAction` is bound on their Cancel buttons.
- `avatarImageData` is a `Data` field in `agent.json`; its JSON form is assumed to be Foundation's default base64 string (inferred).
- The popover `arrowEdge: .leading` placement implies choosers open beside the row rather than below it (inferred from the API; not visible in the screenshots).
- The macOS text-style point sizes in 2.15 are inferred from Apple's documented scale.
- Hub-specific flows (harness lending, Hub pickers, sharing) were read only as far as they change the editors; their server behaviour is out of scope here.
- The illustrated avatars and wallpaper in the screenshots are assumed to come from Image Playground or user images; the screenshots do not say which.
