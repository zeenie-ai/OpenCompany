# Node Logic Flow Documentation

Frozen behavioural contract for every workflow node. One file per node, grouped
by category. After the upcoming full-stack refactor each handler must still
match its doc here, and each doc must still describe what the code does.

## How to use

- **Adding a node**: copy [`_TEMPLATE.md`](./_TEMPLATE.md) into the right
  category folder, name it `<nodeName>.md` (camelCase, matching the
  `type` field on the backend plugin at `server/nodes/<category>/<node>.py`).
- **Refactoring a node**: update the matching contract test in
  `server/tests/nodes/test_<category>.py` *first*, then change the handler,
  then update this doc.
- **The index below is maintained by hand** (there is no generator script
  currently). When you add, rename, or remove a card, update its entry below.

## Conventions

- Mermaid for flow diagrams (renders natively in GitHub & VS Code).
- Tables for inputs/outputs/parameters - greppable, diffable in PRs.
- Cross-link to the existing skill at `server/skills/.../SKILL.md` rather than
  duplicating tool-mode behaviour.
- Keep "Side Effects" honest: every DB write, broadcast, subprocess, and HTTP
  call must be listed.

## Index

<!-- INDEX-START (manually maintained; filenames are camelCase of the plugin `type`) -->

### ai_agents

- [AI Agent (`aiAgent`)](./ai_agents/aiAgent.md)
- [Zeenie (`chatAgent`)](./ai_agents/chatAgent.md)
- [Simple Memory (`simpleMemory`)](./ai_agents/simpleMemory.md)

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
- [OpenRouter Chat Model (`openrouterChatModel`)](./ai_chat_models/openrouterChatModel.md)

### ai_tools

- [Agent Builder (`agentBuilder`)](./ai_tools/agentBuilder.md)
- [Calculator Tool (`calculatorTool`)](./ai_tools/calculatorTool.md)
- [Current Time Tool (`currentTimeTool`)](./ai_tools/currentTimeTool.md)
- [DuckDuckGo Search (`duckduckgoSearch`)](./ai_tools/duckduckgoSearch.md)
- [Task Manager (`taskManager`)](./ai_tools/taskManager.md)
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
- [Create Map (`gmaps_create`)](./chat_utility/gmaps_create.md)
- [File Handler (`fileHandler`)](./chat_utility/fileHandler.md)
- [Team Monitor (`teamMonitor`)](./chat_utility/teamMonitor.md)
- [Text Generator (`textGenerator`)](./chat_utility/textGenerator.md)

### code_fs_process

- [File Modify (`fileModify`)](./code_fs_process/fileModify.md)
- [File Read (`fileRead`)](./code_fs_process/fileRead.md)
- [FS Search (`fsSearch`)](./code_fs_process/fsSearch.md)
- [JavaScript Executor (`javascriptExecutor`)](./code_fs_process/javascriptExecutor.md)
- [Monty Executor (`montyExecutor`)](./code_fs_process/montyExecutor.md)
- [Process Manager (`processManager`)](./code_fs_process/processManager.md)
- [Python Executor (`pythonExecutor`)](./code_fs_process/pythonExecutor.md)
- [Shell (`shell`)](./code_fs_process/shell.md)
- [TypeScript Executor (`typescriptExecutor`)](./code_fs_process/typescriptExecutor.md)

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

### search

- [Brave Search (`braveSearch`)](./search/braveSearch.md)
- [Perplexity Search (`perplexitySearch`)](./search/perplexitySearch.md)
- [Serper Search (`serperSearch`)](./search/serperSearch.md)

### specialized_agents

- [AI Employee (`ai_employee`)](./specialized_agents/aiEmployee.md)
- [Android Control Agent (`android_agent`)](./specialized_agents/androidAgent.md)
- [Autonomous Agent (`autonomous_agent`)](./specialized_agents/autonomousAgent.md)
- [Claude Code Agent (`claude_code_agent`)](./specialized_agents/claudeCodeAgent.md)
- [Codex (`codex_agent`)](./specialized_agents/codexAgent.md)
- [Coding Agent (`coding_agent`)](./specialized_agents/codingAgent.md)
- [Consumer Agent (`consumer_agent`)](./specialized_agents/consumerAgent.md)
- [Orchestrator Agent (`orchestrator_agent`)](./specialized_agents/orchestratorAgent.md)
- [Payments Agent (`payments_agent`)](./specialized_agents/paymentsAgent.md)
- [Productivity Agent (`productivity_agent`)](./specialized_agents/productivityAgent.md)
- [RLM Agent (`rlm_agent`)](./specialized_agents/rlmAgent.md)
- [Social Media Agent (`social_agent`)](./specialized_agents/socialAgent.md)
- [Task Management Agent (`task_agent`)](./specialized_agents/taskAgent.md)
- [Tool Agent (`tool_agent`)](./specialized_agents/toolAgent.md)
- [Travel Agent (`travel_agent`)](./specialized_agents/travelAgent.md)
- [Web Control Agent (`web_agent`)](./specialized_agents/webAgent.md)

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
- [Crawlee Scraper (`crawleeScraper`)](./web_automation/crawleeScraper.md)

### whatsapp

- [WhatsApp DB (`whatsappDb`)](./whatsapp/whatsappDb.md)
- [WhatsApp Receive (`whatsappReceive`)](./whatsapp/whatsappReceive.md)
- [WhatsApp Send (`whatsappSend`)](./whatsapp/whatsappSend.md)

### workflow_triggers

- [Chat Trigger (`chatTrigger`)](./workflow_triggers/chatTrigger.md)
- [Cron Scheduler (`cronScheduler`)](./workflow_triggers/cronScheduler.md)
- [Start (`start`)](./workflow_triggers/start.md)
- [Task Trigger (`taskTrigger`)](./workflow_triggers/taskTrigger.md)
- [Timer (`timer`)](./workflow_triggers/timer.md)
- [Webhook Response (`webhookResponse`)](./workflow_triggers/webhookResponse.md)
- [Webhook Trigger (`webhookTrigger`)](./workflow_triggers/webhookTrigger.md)

<!-- INDEX-END -->
