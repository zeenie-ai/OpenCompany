# Node Logic Flow Documentation

Frozen behavioural contract for every workflow node. One file per node, grouped
by category. After the upcoming full-stack refactor each handler must still
match its doc here, and each doc must still describe what the code does.

## How to use

- **Adding a node**: copy [`_TEMPLATE.md`](./_TEMPLATE.md) into the right
  category folder and name it `<type>.md` -- the filename stem must equal the
  registered `type` string exactly (camelCase or snake_case, whatever the
  plugin declares) as set on the backend plugin at
  `server/nodes/<group>/<plugin>/__init__.py`.
- **Refactoring a node**: update the matching contract test in
  `server/tests/nodes/test_<category>.py` *first*, then change the handler,
  then update this doc.
- **The index below is generated.** Run `company docs nodes` after adding,
  renaming or removing a card; `company docs nodes --check` fails when a
  registered node has no card. Do not hand-edit between the
  `AUTO-GENERATED-INDEX` markers.

## Conventions

- Mermaid for flow diagrams (renders natively in GitHub & VS Code).
- Tables for inputs/outputs/parameters - greppable, diffable in PRs.
- Cross-link to the existing skill at `server/skills/.../SKILL.md` rather than
  duplicating tool-mode behaviour.
- Keep "Side Effects" honest: every DB write, broadcast, subprocess, and HTTP
  call must be listed.

## Index

For the current browser architecture, see [Browser runtime](../browser.md)
and [Browser workspace](../browser_workspace.md). The registered node is
`browser`; the `browserHarness` card is retained only as historical migration
documentation. Inclusion in this generated document index does not establish
that a retired node remains registered.

<!-- AUTO-GENERATED-INDEX-START -->

### ai_agents

- [AI Agent (`aiAgent`)](./ai_agents/aiAgent.md)
- [Zeenie (`chatAgent`)](./ai_agents/chatAgent.md)
- [Context (`context`)](./ai_agents/context.md)
- [Master Skill (`masterSkill`)](./ai_agents/masterSkill.md)
- [Memory (`simpleMemory`)](./ai_agents/simpleMemory.md)

### ai_chat_models

- [Anthropic Chat Model (`anthropicChatModel`)](./ai_chat_models/anthropicChatModel.md)
- [Cerebras Chat Model (`cerebrasChatModel`)](./ai_chat_models/cerebrasChatModel.md)
- [DeepSeek Chat Model (`deepseekChatModel`)](./ai_chat_models/deepseekChatModel.md)
- [Gemini Chat Model (`geminiChatModel`)](./ai_chat_models/geminiChatModel.md)
- [Groq Chat Model (`groqChatModel`)](./ai_chat_models/groqChatModel.md)
- [Kimi Chat Model (`kimiChatModel`)](./ai_chat_models/kimiChatModel.md)
- [LM Studio Chat Model (`lmstudioChatModel`)](./ai_chat_models/lmstudioChatModel.md)
- [Mistral Chat Model (`mistralChatModel`)](./ai_chat_models/mistralChatModel.md)
- [Ollama Chat Model (`ollamaChatModel`)](./ai_chat_models/ollamaChatModel.md)
- [OpenAI Chat Model (`openaiChatModel`)](./ai_chat_models/openaiChatModel.md)
- [OpenAI-compatible Chat Model (`openaiCompatibleChatModel`)](./ai_chat_models/openaiCompatibleChatModel.md)
- [OpenRouter Chat Model (`openrouterChatModel`)](./ai_chat_models/openrouterChatModel.md)
- [Sarvam AI Chat Model (`sarvamChatModel`)](./ai_chat_models/sarvamChatModel.md)

### ai_tools

- [Agent Builder (`agentBuilder`)](./ai_tools/agentBuilder.md)
- [Calculator Tool (`calculatorTool`)](./ai_tools/calculatorTool.md)
- [Canvas (`canvas`)](./ai_tools/canvas.md)
- [Current Time Tool (`currentTimeTool`)](./ai_tools/currentTimeTool.md)
- [Data (`dataSource`)](./ai_tools/dataSource.md)
- [DuckDuckGo Search (`duckduckgoSearch`)](./ai_tools/duckduckgoSearch.md)
- [Task Manager (`taskManager`)](./ai_tools/taskManager.md)
- [Vision Analyze (`visionAnalyze`)](./ai_tools/visionAnalyze.md)
- [Write Todos (`writeTodos`)](./ai_tools/writeTodos.md)

### android

- [Airplane Mode Control (`airplaneModeControl`)](./android/airplaneModeControl.md)
- [App Launcher (`appLauncher`)](./android/appLauncher.md)
- [App List (`appList`)](./android/appList.md)
- [Audio Automation (`audioAutomation`)](./android/audioAutomation.md)
- [Battery Monitor (`batteryMonitor`)](./android/batteryMonitor.md)
- [Bluetooth Automation (`bluetoothAutomation`)](./android/bluetoothAutomation.md)
- [Camera Control (`cameraControl`)](./android/cameraControl.md)
- [Device State (`deviceStateAutomation`)](./android/deviceStateAutomation.md)
- [Environmental Sensors (`environmentalSensors`)](./android/environmentalSensors.md)
- [Location (`location`)](./android/location.md)
- [Media Control (`mediaControl`)](./android/mediaControl.md)
- [Motion Detection (`motionDetection`)](./android/motionDetection.md)
- [Network Monitor (`networkMonitor`)](./android/networkMonitor.md)
- [Screen Control (`screenControlAutomation`)](./android/screenControlAutomation.md)
- [System Info (`systemInfo`)](./android/systemInfo.md)
- [WiFi Automation (`wifiAutomation`)](./android/wifiAutomation.md)

### chat_utility

- [Chat History (`chatHistory`)](./chat_utility/chatHistory.md)
- [Chat Send (`chatSend`)](./chat_utility/chatSend.md)
- [Console (`console`)](./chat_utility/console.md)
- [File Handler (`fileHandler`)](./chat_utility/fileHandler.md)
- [Create Map (`gmaps_create`)](./chat_utility/gmaps_create.md)
- [Geocoding (`gmaps_locations`)](./chat_utility/gmaps_locations.md)
- [Nearby Places (`gmaps_nearby_places`)](./chat_utility/gmaps_nearby_places.md)
- [Team Monitor (`teamMonitor`)](./chat_utility/teamMonitor.md)
- [Text Generator (`textGenerator`)](./chat_utility/textGenerator.md)

### cli_integrations

- [Cloudflare (`cloudflareAction`)](./cli_integrations/cloudflareAction.md)
- [Google Cloud (`gcloudAction`)](./cli_integrations/gcloudAction.md)
- [GitHub (`githubAction`)](./cli_integrations/githubAction.md)
- [Vercel (`vercelAction`)](./cli_integrations/vercelAction.md)

### code_fs_process

- [File Modify (`fileModify`)](./code_fs_process/fileModify.md)
- [File Read (`fileRead`)](./code_fs_process/fileRead.md)
- [FS Search (`fsSearch`)](./code_fs_process/fsSearch.md)
- [Gallery (`gallery`)](./code_fs_process/gallery.md)
- [JavaScript Executor (`javascriptExecutor`)](./code_fs_process/javascriptExecutor.md)
- [Monty Executor (`montyExecutor`)](./code_fs_process/montyExecutor.md)
- [Process Manager (`processManager`)](./code_fs_process/processManager.md)
- [Python Executor (`pythonExecutor`)](./code_fs_process/pythonExecutor.md)
- [Shell (`shell`)](./code_fs_process/shell.md)
- [TypeScript Executor (`typescriptExecutor`)](./code_fs_process/typescriptExecutor.md)

### discord

- [Discord API (`discordAction`)](./discord/discordAction.md)
- [Discord Interaction (`discordInteraction`)](./discord/discordInteraction.md)
- [Discord Receive (`discordReceive`)](./discord/discordReceive.md)
- [Discord Send (`discordSend`)](./discord/discordSend.md)

### document

- [Document Parser (`documentParser`)](./document/documentParser.md)
- [Embedding Generator (`embeddingGenerator`)](./document/embeddingGenerator.md)
- [File Downloader (`fileDownloader`)](./document/fileDownloader.md)
- [HTTP Scraper (`httpScraper`)](./document/httpScraper.md)
- [Text Chunker (`textChunker`)](./document/textChunker.md)
- [Vector Store (`vectorStore`)](./document/vectorStore.md)

### email

- [Email Read (`emailRead`)](./email/emailRead.md)
- [Email Receive (`emailReceive`)](./email/emailReceive.md)
- [Email Send (`emailSend`)](./email/emailSend.md)

### google_workspace

- [Calendar (`googleCalendar`)](./google_workspace/googleCalendar.md)
- [Contacts (`googleContacts`)](./google_workspace/googleContacts.md)
- [Drive (`googleDrive`)](./google_workspace/googleDrive.md)
- [Gmail (`googleGmail`)](./google_workspace/googleGmail.md)
- [Gmail Receive (`googleGmailReceive`)](./google_workspace/googleGmailReceive.md)
- [Sheets (`googleSheets`)](./google_workspace/googleSheets.md)
- [Tasks (`googleTasks`)](./google_workspace/googleTasks.md)

### http_proxy

- [HTTP Request (`httpRequest`)](./http_proxy/httpRequest.md)
- [Proxy Config (`proxyConfig`)](./http_proxy/proxyConfig.md)
- [Proxy Request (`proxyRequest`)](./http_proxy/proxyRequest.md)
- [Proxy Status (`proxyStatus`)](./http_proxy/proxyStatus.md)

### language

- [Detect Language (`detectLanguage`)](./language/detectLanguage.md)
- [Speech to Text (`speechToText`)](./language/speechToText.md)
- [Text to Speech (`textToSpeech`)](./language/textToSpeech.md)
- [Translate (`translateText`)](./language/translateText.md)
- [Transliterate (`transliterateText`)](./language/transliterateText.md)

### microsoft

- [Outlook Calendar (`msCalendar`)](./microsoft/msCalendar.md)
- [Outlook Mail (`msMail`)](./microsoft/msMail.md)
- [Outlook Mail Receive (`msMailReceive`)](./microsoft/msMailReceive.md)

### search

- [Brave Search (`braveSearch`)](./search/braveSearch.md)
- [Perplexity Search (`perplexitySearch`)](./search/perplexitySearch.md)
- [Serper Search (`serperSearch`)](./search/serperSearch.md)

### specialized_agents

- [AI Employee (`ai_employee`)](./specialized_agents/ai_employee.md)
- [Android Control Agent (`android_agent`)](./specialized_agents/android_agent.md)
- [Autonomous Agent (`autonomous_agent`)](./specialized_agents/autonomous_agent.md)
- [Claude Code Agent (`claude_code_agent`)](./specialized_agents/claude_code_agent.md)
- [Codex (`codex_agent`)](./specialized_agents/codex_agent.md)
- [Coding Agent (`coding_agent`)](./specialized_agents/coding_agent.md)
- [Consumer Agent (`consumer_agent`)](./specialized_agents/consumer_agent.md)
- [Orchestrator Agent (`orchestrator_agent`)](./specialized_agents/orchestrator_agent.md)
- [Payments Agent (`payments_agent`)](./specialized_agents/payments_agent.md)
- [Productivity Agent (`productivity_agent`)](./specialized_agents/productivity_agent.md)
- [RLM Agent (`rlm_agent`)](./specialized_agents/rlm_agent.md)
- [Social Media Agent (`social_agent`)](./specialized_agents/social_agent.md)
- [Task Management Agent (`task_agent`)](./specialized_agents/task_agent.md)
- [Tool Agent (`tool_agent`)](./specialized_agents/tool_agent.md)
- [Travel Agent (`travel_agent`)](./specialized_agents/travel_agent.md)
- [Vertex Agent Admin (`vertex_agent_admin`)](./specialized_agents/vertex_agent_admin.md)
- [Vertex Agent (`vertex_managed_agent`)](./specialized_agents/vertex_managed_agent.md)
- [Cloud Tool (`vertexCloudTool`)](./specialized_agents/vertexCloudTool.md)
- [Web Control Agent (`web_agent`)](./specialized_agents/web_agent.md)

### stripe

- [Stripe (`stripeAction`)](./stripe/stripeAction.md)
- [Stripe Receive (`stripeReceive`)](./stripe/stripeReceive.md)

### telegram_social

- [Social Receive (`socialReceive`)](./telegram_social/socialReceive.md)
- [Social Send (`socialSend`)](./telegram_social/socialSend.md)
- [Telegram Receive (`telegramReceive`)](./telegram_social/telegramReceive.md)
- [Telegram Send (`telegramSend`)](./telegram_social/telegramSend.md)

### twitter

- [Twitter Receive (`twitterReceive`)](./twitter/twitterReceive.md)
- [Twitter Search (`twitterSearch`)](./twitter/twitterSearch.md)
- [Twitter Send (`twitterSend`)](./twitter/twitterSend.md)
- [Twitter User (`twitterUser`)](./twitter/twitterUser.md)

### web_automation

- [Apify Actor (`apifyActor`)](./web_automation/apifyActor.md)
- [Browser (`browser`)](./web_automation/browser.md)
- [Browser Harness (`browserHarness`) — retired](./web_automation/browserHarness.md)
- [Crawlee Scraper (`crawleeScraper`)](./web_automation/crawleeScraper.md)
- [TikHub (`tikhubAction`)](./web_automation/tikhubAction.md)

### whatsapp

- [WhatsApp DB (`whatsappDb`)](./whatsapp/whatsappDb.md)
- [WhatsApp Receive (`whatsappReceive`)](./whatsapp/whatsappReceive.md)
- [WhatsApp Send (`whatsappSend`)](./whatsapp/whatsappSend.md)

### whatsapp_business

- [WhatsApp Business Media (`whatsappBusinessMedia`)](./whatsapp_business/whatsappBusinessMedia.md)
- [WhatsApp Business Receive (`whatsappBusinessReceive`)](./whatsapp_business/whatsappBusinessReceive.md)
- [WhatsApp Business Send (`whatsappBusinessSend`)](./whatsapp_business/whatsappBusinessSend.md)
- [WhatsApp Business Status (`whatsappBusinessStatus`)](./whatsapp_business/whatsappBusinessStatus.md)

### workflow_triggers

- [Approval (`approvalGate`)](./workflow_triggers/approvalGate.md)
- [Chat Trigger (`chatTrigger`)](./workflow_triggers/chatTrigger.md)
- [Cron Scheduler (`cronScheduler`)](./workflow_triggers/cronScheduler.md)
- [Start (`start`)](./workflow_triggers/start.md)
- [Task Trigger (`taskTrigger`)](./workflow_triggers/taskTrigger.md)
- [Timer (`timer`)](./workflow_triggers/timer.md)
- [Webhook Response (`webhookResponse`)](./workflow_triggers/webhookResponse.md)
- [Webhook Trigger (`webhookTrigger`)](./workflow_triggers/webhookTrigger.md)

<!-- AUTO-GENERATED-INDEX-END -->
