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
- **Rebuilding the index below**: run `node scripts/build-node-docs-index.js`
  (also runs in CI to fail builds when a node is missing a doc).

## Conventions

- Mermaid for flow diagrams (renders natively in GitHub & VS Code).
- Tables for inputs/outputs/parameters - greppable, diffable in PRs.
- Cross-link to the existing skill at `server/skills/.../SKILL.md` rather than
  duplicating tool-mode behaviour.
- Keep "Side Effects" honest: every DB write, broadcast, subprocess, and HTTP
  call must be listed.

## Index

<!-- AUTO-GENERATED-INDEX-START -->

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
- [Mistral Chat Model (`mistralChatModel`)](./ai_chat_models/mistralChatModel.md)
- [OpenAI Chat Model (`openaiChatModel`)](./ai_chat_models/openaiChatModel.md)
- [OpenRouter Chat Model (`openrouterChatModel`)](./ai_chat_models/openrouterChatModel.md)

### ai_tools

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

### email

- [Email Read (`emailRead`)](./email/emailRead.md)
- [Email Receive (`emailReceive`)](./email/emailReceive.md)
- [Email Send (`emailSend`)](./email/emailSend.md)

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

<!-- AUTO-GENERATED-INDEX-END -->
