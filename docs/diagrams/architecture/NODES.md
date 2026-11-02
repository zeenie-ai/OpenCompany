# Registered-node capability index

Source-backed declaration snapshot: `56f0b397d0b313ec550791c1e2772525a9f7cf12`. **157 registered types**.

Companion to views 06 and 07. Rows describe declarations, not successful provider/device execution.
Agent-tool eligibility comes from `iter_tool_node_classes`; direct Workspace tasks require the explicit
`workspace_task` flag, authorization, saved-node enablement and controller admission. Graph use can be
a trigger, action, agent, tool or passive binding; it does not make every node a main-flow action.
The Canvas column reports only the NodeSpec hide hint. Actual renderers and runtime prerequisites
can restrict Run independently. A hidden button does not establish backend refusal.

Credentials, connected nodes, live runtimes and per-operation limitations remain authoritative in
the linked node cards. Registered status does not certify availability, authorization or isolation.

Regenerate with the backend Python environment: `python scripts/architecture_diagrams.py export-nodes`.

## agent (20)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`aiAgent`](../../../docs-internal/node-logic-flows/ai_agents/aiAgent.md) · [source](../../../server/nodes/agent/ai_agent/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `ai-heavy` |
| [`ai_employee`](../../../docs-internal/node-logic-flows/specialized_agents/ai_employee.md) · [source](../../../server/nodes/agent/ai_employee/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`android_agent`](../../../docs-internal/node-logic-flows/specialized_agents/android_agent.md) · [source](../../../server/nodes/agent/android_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`autonomous_agent`](../../../docs-internal/node-logic-flows/specialized_agents/autonomous_agent.md) · [source](../../../server/nodes/agent/autonomous_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`browser_agent`](../../../docs-internal/node-logic-flows/specialized_agents/browser_agent.md) · [source](../../../server/nodes/agent/browser_agent/__init__.py) | registered | SpecializedAgentBase | eligible | eligible | renderer-dependent | `ai-heavy` |
| [`chatAgent`](../../../docs-internal/node-logic-flows/ai_agents/chatAgent.md) · [source](../../../server/nodes/agent/chat_agent/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `ai-heavy` |
| [`claude_code_agent`](../../../docs-internal/node-logic-flows/specialized_agents/claude_code_agent.md) · [source](../../../server/nodes/agent/claude_code_agent/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `ai-heavy` |
| [`codex_agent`](../../../docs-internal/node-logic-flows/specialized_agents/codex_agent.md) · [source](../../../server/nodes/agent/codex_agent/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `ai-heavy` |
| [`coding_agent`](../../../docs-internal/node-logic-flows/specialized_agents/coding_agent.md) · [source](../../../server/nodes/agent/coding_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`consumer_agent`](../../../docs-internal/node-logic-flows/specialized_agents/consumer_agent.md) · [source](../../../server/nodes/agent/consumer_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`mobile_use_agent`](../../../docs-internal/node-logic-flows/mobile/mobile_use_agent.md) · [source](../../../server/nodes/mobile/_node.py) | registered | ActionNode | eligible | eligible | renderer-dependent | `android` |
| [`orchestrator_agent`](../../../docs-internal/node-logic-flows/specialized_agents/orchestrator_agent.md) · [source](../../../server/nodes/agent/orchestrator_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`payments_agent`](../../../docs-internal/node-logic-flows/specialized_agents/payments_agent.md) · [source](../../../server/nodes/agent/payments_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`productivity_agent`](../../../docs-internal/node-logic-flows/specialized_agents/productivity_agent.md) · [source](../../../server/nodes/agent/productivity_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`rlm_agent`](../../../docs-internal/node-logic-flows/specialized_agents/rlm_agent.md) · [source](../../../server/nodes/agent/rlm_agent/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `ai-heavy` |
| [`social_agent`](../../../docs-internal/node-logic-flows/specialized_agents/social_agent.md) · [source](../../../server/nodes/agent/social_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`task_agent`](../../../docs-internal/node-logic-flows/specialized_agents/task_agent.md) · [source](../../../server/nodes/agent/task_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`tool_agent`](../../../docs-internal/node-logic-flows/specialized_agents/tool_agent.md) · [source](../../../server/nodes/agent/tool_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`travel_agent`](../../../docs-internal/node-logic-flows/specialized_agents/travel_agent.md) · [source](../../../server/nodes/agent/travel_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`web_agent`](../../../docs-internal/node-logic-flows/specialized_agents/web_agent.md) · [source](../../../server/nodes/agent/web_agent/__init__.py) | registered | SpecializedAgentBase | eligible | — | renderer-dependent | `ai-heavy` |

## android (16)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`airplaneModeControl`](../../../docs-internal/node-logic-flows/android/airplaneModeControl.md) · [source](../../../server/nodes/android/airplane_mode_control/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`appLauncher`](../../../docs-internal/node-logic-flows/android/appLauncher.md) · [source](../../../server/nodes/android/app_launcher/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`appList`](../../../docs-internal/node-logic-flows/android/appList.md) · [source](../../../server/nodes/android/app_list/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`audioAutomation`](../../../docs-internal/node-logic-flows/android/audioAutomation.md) · [source](../../../server/nodes/android/audio_automation/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`batteryMonitor`](../../../docs-internal/node-logic-flows/android/batteryMonitor.md) · [source](../../../server/nodes/android/battery_monitor/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`bluetoothAutomation`](../../../docs-internal/node-logic-flows/android/bluetoothAutomation.md) · [source](../../../server/nodes/android/bluetooth_automation/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`cameraControl`](../../../docs-internal/node-logic-flows/android/cameraControl.md) · [source](../../../server/nodes/android/camera_control/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`deviceStateAutomation`](../../../docs-internal/node-logic-flows/android/deviceStateAutomation.md) · [source](../../../server/nodes/android/device_state_automation/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`environmentalSensors`](../../../docs-internal/node-logic-flows/android/environmentalSensors.md) · [source](../../../server/nodes/android/environmental_sensors/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`location`](../../../docs-internal/node-logic-flows/android/location.md) · [source](../../../server/nodes/android/location/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`mediaControl`](../../../docs-internal/node-logic-flows/android/mediaControl.md) · [source](../../../server/nodes/android/media_control/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`motionDetection`](../../../docs-internal/node-logic-flows/android/motionDetection.md) · [source](../../../server/nodes/android/motion_detection/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`networkMonitor`](../../../docs-internal/node-logic-flows/android/networkMonitor.md) · [source](../../../server/nodes/android/network_monitor/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`screenControlAutomation`](../../../docs-internal/node-logic-flows/android/screenControlAutomation.md) · [source](../../../server/nodes/android/screen_control_automation/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`systemInfo`](../../../docs-internal/node-logic-flows/android/systemInfo.md) · [source](../../../server/nodes/android/system_info/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |
| [`wifiAutomation`](../../../docs-internal/node-logic-flows/android/wifiAutomation.md) · [source](../../../server/nodes/android/wifi_automation/__init__.py) | registered | AndroidServiceBase | eligible | — | renderer-dependent | `android` |

## api (1)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`apifyActor`](../../../docs-internal/node-logic-flows/web_automation/apifyActor.md) · [source](../../../server/nodes/scraper/apify_actor/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## browser (1)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`browser`](../../../docs-internal/node-logic-flows/web_automation/browser.md) · [source](../../../server/nodes/browser/browser/__init__.py) | registered | ToolNode | eligible | — | renderer-dependent | `browser` |

## chat (4)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`chatHistory`](../../../docs-internal/node-logic-flows/chat_utility/chatHistory.md) · [source](../../../server/nodes/chat/chat_history/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`chatReply`](../../../docs-internal/node-logic-flows/chat_utility/chatReply.md) · [source](../../../server/nodes/chat/chat_reply/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`chatSend`](../../../docs-internal/node-logic-flows/chat_utility/chatSend.md) · [source](../../../server/nodes/chat/chat_send/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`chatUi`](../../../docs-internal/node-logic-flows/chat_utility/chatUi.md) · [source](../../../server/nodes/chat/chat_ui/__init__.py) | registered | ToolNode | eligible | — | hidden | `machina-default` |

## code (4)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`javascriptExecutor`](../../../docs-internal/node-logic-flows/code_fs_process/javascriptExecutor.md) · [source](../../../server/nodes/code/javascript_executor/__init__.py) | registered | CodeExecutorBase | eligible | — | renderer-dependent | `code-exec` |
| [`montyExecutor`](../../../docs-internal/node-logic-flows/code_fs_process/montyExecutor.md) · [source](../../../server/nodes/code/monty_executor/__init__.py) | registered | CodeExecutorBase | eligible | — | renderer-dependent | `code-exec` |
| [`pythonExecutor`](../../../docs-internal/node-logic-flows/code_fs_process/pythonExecutor.md) · [source](../../../server/nodes/code/python_executor/__init__.py) | registered | CodeExecutorBase | eligible | — | renderer-dependent | `code-exec` |
| [`typescriptExecutor`](../../../docs-internal/node-logic-flows/code_fs_process/typescriptExecutor.md) · [source](../../../server/nodes/code/typescript_executor/__init__.py) | registered | CodeExecutorBase | eligible | — | renderer-dependent | `code-exec` |

## deployment (4)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`awsAction`](../../../docs-internal/node-logic-flows/cli_integrations/awsAction.md) · [source](../../../server/nodes/aws/aws_action.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`cloudflareAction`](../../../docs-internal/node-logic-flows/cli_integrations/cloudflareAction.md) · [source](../../../server/nodes/cloudflare/cloudflare_action.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`gcloudAction`](../../../docs-internal/node-logic-flows/cli_integrations/gcloudAction.md) · [source](../../../server/nodes/gcloud/gcloud_action.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`vercelAction`](../../../docs-internal/node-logic-flows/cli_integrations/vercelAction.md) · [source](../../../server/nodes/vercel/vercel_action.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## discord (4)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`discordAction`](../../../docs-internal/node-logic-flows/discord/discordAction.md) · [source](../../../server/nodes/discord/discord_action.py) | registered | AccountScopedNode | eligible | — | renderer-dependent | `rest-api` |
| [`discordInteraction`](../../../docs-internal/node-logic-flows/discord/discordInteraction.md) · [source](../../../server/nodes/discord/discord_interaction.py) | registered | TriggerNode | — | — | renderer-dependent | `triggers-event` |
| [`discordReceive`](../../../docs-internal/node-logic-flows/discord/discordReceive.md) · [source](../../../server/nodes/discord/discord_receive.py) | registered | TriggerNode | — | — | renderer-dependent | `triggers-event` |
| [`discordSend`](../../../docs-internal/node-logic-flows/discord/discordSend.md) · [source](../../../server/nodes/discord/discord_send.py) | registered | AccountScopedNode | eligible | — | renderer-dependent | `messaging` |

## document (6)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`documentParser`](../../../docs-internal/node-logic-flows/document/documentParser.md) · [source](../../../server/nodes/document/document_parser/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`embeddingGenerator`](../../../docs-internal/node-logic-flows/document/embeddingGenerator.md) · [source](../../../server/nodes/document/embedding_generator/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `ai-heavy` |
| [`fileDownloader`](../../../docs-internal/node-logic-flows/document/fileDownloader.md) · [source](../../../server/nodes/document/file_downloader/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `rest-api` |
| [`httpScraper`](../../../docs-internal/node-logic-flows/document/httpScraper.md) · [source](../../../server/nodes/document/http_scraper/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `rest-api` |
| [`textChunker`](../../../docs-internal/node-logic-flows/document/textChunker.md) · [source](../../../server/nodes/document/text_chunker/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`vectorStore`](../../../docs-internal/node-logic-flows/document/vectorStore.md) · [source](../../../server/nodes/document/vector_store/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |

## email (3)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`emailRead`](../../../docs-internal/node-logic-flows/email/emailRead.md) · [source](../../../server/nodes/email/email_read/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |
| [`emailReceive`](../../../docs-internal/node-logic-flows/email/emailReceive.md) · [source](../../../server/nodes/email/email_receive/__init__.py) | registered | PollingTriggerNode | — | — | renderer-dependent | `triggers-poll` |
| [`emailSend`](../../../docs-internal/node-logic-flows/email/emailSend.md) · [source](../../../server/nodes/email/email_send/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |

## filesystem (5)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`fileModify`](../../../docs-internal/node-logic-flows/code_fs_process/fileModify.md) · [source](../../../server/nodes/filesystem/file_modify/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`fileRead`](../../../docs-internal/node-logic-flows/code_fs_process/fileRead.md) · [source](../../../server/nodes/filesystem/file_read/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`fsSearch`](../../../docs-internal/node-logic-flows/code_fs_process/fsSearch.md) · [source](../../../server/nodes/filesystem/fs_search/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`gallery`](../../../docs-internal/node-logic-flows/code_fs_process/gallery.md) · [source](../../../server/nodes/filesystem/gallery/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`shell`](../../../docs-internal/node-logic-flows/code_fs_process/shell.md) · [source](../../../server/nodes/filesystem/shell/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |

## google (7)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`googleCalendar`](../../../docs-internal/node-logic-flows/google_workspace/googleCalendar.md) · [source](../../../server/nodes/google/calendar/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`googleContacts`](../../../docs-internal/node-logic-flows/google_workspace/googleContacts.md) · [source](../../../server/nodes/google/contacts/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`googleDrive`](../../../docs-internal/node-logic-flows/google_workspace/googleDrive.md) · [source](../../../server/nodes/google/drive/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`googleGmail`](../../../docs-internal/node-logic-flows/google_workspace/googleGmail.md) · [source](../../../server/nodes/google/gmail/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`googleGmailReceive`](../../../docs-internal/node-logic-flows/google_workspace/googleGmailReceive.md) · [source](../../../server/nodes/google/gmail_receive/__init__.py) | registered | PollingTriggerNode | — | — | renderer-dependent | `triggers-poll` |
| [`googleSheets`](../../../docs-internal/node-logic-flows/google_workspace/googleSheets.md) · [source](../../../server/nodes/google/sheets/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`googleTasks`](../../../docs-internal/node-logic-flows/google_workspace/googleTasks.md) · [source](../../../server/nodes/google/tasks/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## language (5)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`detectLanguage`](../../../docs-internal/node-logic-flows/language/detectLanguage.md) · [source](../../../server/nodes/translate/detect_language.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`speechToText`](../../../docs-internal/node-logic-flows/language/speechToText.md) · [source](../../../server/nodes/speech/speech_to_text.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`textToSpeech`](../../../docs-internal/node-logic-flows/language/textToSpeech.md) · [source](../../../server/nodes/speech/text_to_speech.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`translateText`](../../../docs-internal/node-logic-flows/language/translateText.md) · [source](../../../server/nodes/translate/translate_text.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`transliterateText`](../../../docs-internal/node-logic-flows/language/transliterateText.md) · [source](../../../server/nodes/translate/transliterate_text.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## location (3)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`gmaps_create`](../../../docs-internal/node-logic-flows/chat_utility/gmaps_create.md) · [source](../../../server/nodes/location/gmaps_create/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`gmaps_locations`](../../../docs-internal/node-logic-flows/chat_utility/gmaps_locations.md) · [source](../../../server/nodes/location/gmaps_locations/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`gmaps_nearby_places`](../../../docs-internal/node-logic-flows/chat_utility/gmaps_nearby_places.md) · [source](../../../server/nodes/location/gmaps_nearby_places/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## memory (1)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`context`](../../../docs-internal/node-logic-flows/ai_agents/context.md) · [source](../../../server/nodes/context/__init__.py) | registered | ActionNode | — | — | hidden | `machina-default` |

## microsoft (3)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`msCalendar`](../../../docs-internal/node-logic-flows/microsoft/msCalendar.md) · [source](../../../server/nodes/microsoft/calendar/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`msMail`](../../../docs-internal/node-logic-flows/microsoft/msMail.md) · [source](../../../server/nodes/microsoft/mail/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`msMailReceive`](../../../docs-internal/node-logic-flows/microsoft/msMailReceive.md) · [source](../../../server/nodes/microsoft/mail_receive/__init__.py) | registered | PollingTriggerNode | — | — | renderer-dependent | `triggers-poll` |

## model (13)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`anthropicChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/anthropicChatModel.md) · [source](../../../server/nodes/model/anthropic_chat_model/__init__.py) | registered | ChatModelBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`cerebrasChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/cerebrasChatModel.md) · [source](../../../server/nodes/model/cerebras_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`deepseekChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/deepseekChatModel.md) · [source](../../../server/nodes/model/deepseek_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`geminiChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/geminiChatModel.md) · [source](../../../server/nodes/model/gemini_chat_model/__init__.py) | registered | ChatModelBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`groqChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/groqChatModel.md) · [source](../../../server/nodes/model/groq_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`kimiChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/kimiChatModel.md) · [source](../../../server/nodes/model/kimi_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`lmstudioChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/lmstudioChatModel.md) · [source](../../../server/nodes/model/lmstudio_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`mistralChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/mistralChatModel.md) · [source](../../../server/nodes/model/mistral_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`ollamaChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/ollamaChatModel.md) · [source](../../../server/nodes/model/ollama_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`openaiChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/openaiChatModel.md) · [source](../../../server/nodes/model/openai_chat_model/__init__.py) | registered | ChatModelBase | eligible | — | renderer-dependent | `ai-heavy` |
| [`openaiCompatibleChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/openaiCompatibleChatModel.md) · [source](../../../server/nodes/model/openai_compatible_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`openrouterChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/openrouterChatModel.md) · [source](../../../server/nodes/model/openrouter_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |
| [`sarvamChatModel`](../../../docs-internal/node-logic-flows/ai_chat_models/sarvamChatModel.md) · [source](../../../server/nodes/model/sarvam_chat_model/__init__.py) | registered | ChatModelBase | — | — | renderer-dependent | `ai-heavy` |

## payments (4)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`dodoPaymentsAction`](../../../docs-internal/node-logic-flows/dodo_payments/dodoPaymentsAction.md) · [source](../../../server/nodes/dodo_payments/dodo_payments_action.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`dodoPaymentsReceive`](../../../docs-internal/node-logic-flows/dodo_payments/dodoPaymentsReceive.md) · [source](../../../server/nodes/dodo_payments/dodo_payments_receive.py) | registered | WebhookTriggerNode | — | — | renderer-dependent | `triggers-event` |
| [`stripeAction`](../../../docs-internal/node-logic-flows/stripe/stripeAction.md) · [source](../../../server/nodes/stripe/stripe_action.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`stripeReceive`](../../../docs-internal/node-logic-flows/stripe/stripeReceive.md) · [source](../../../server/nodes/stripe/stripe_receive.py) | registered | WebhookTriggerNode | — | — | renderer-dependent | `triggers-event` |

## proxy (3)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`proxyConfig`](../../../docs-internal/node-logic-flows/http_proxy/proxyConfig.md) · [source](../../../server/nodes/proxy/proxy_config/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`proxyRequest`](../../../docs-internal/node-logic-flows/http_proxy/proxyRequest.md) · [source](../../../server/nodes/proxy/proxy_request/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`proxyStatus`](../../../docs-internal/node-logic-flows/http_proxy/proxyStatus.md) · [source](../../../server/nodes/proxy/proxy_status/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |

## scheduler (2)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`cronScheduler`](../../../docs-internal/node-logic-flows/workflow_triggers/cronScheduler.md) · [source](../../../server/nodes/scheduler/cron_scheduler/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `triggers-poll` |
| [`timer`](../../../docs-internal/node-logic-flows/workflow_triggers/timer.md) · [source](../../../server/nodes/scheduler/timer/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |

## scraper (2)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`crawleeScraper`](../../../docs-internal/node-logic-flows/web_automation/crawleeScraper.md) · [source](../../../server/nodes/scraper/crawlee_scraper/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `browser` |
| [`tikhubAction`](../../../docs-internal/node-logic-flows/web_automation/tikhubAction.md) · [source](../../../server/nodes/scraper/tikhub_action/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## search (3)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`braveSearch`](../../../docs-internal/node-logic-flows/search/braveSearch.md) · [source](../../../server/nodes/search/brave_search/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`perplexitySearch`](../../../docs-internal/node-logic-flows/search/perplexitySearch.md) · [source](../../../server/nodes/search/perplexity_search/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`serperSearch`](../../../docs-internal/node-logic-flows/search/serperSearch.md) · [source](../../../server/nodes/search/serper_search/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## social (8)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`socialReceive`](../../../docs-internal/node-logic-flows/telegram_social/socialReceive.md) · [source](../../../server/nodes/social/social_receive/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`socialSend`](../../../docs-internal/node-logic-flows/telegram_social/socialSend.md) · [source](../../../server/nodes/social/social_send/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |
| [`telegramReceive`](../../../docs-internal/node-logic-flows/telegram_social/telegramReceive.md) · [source](../../../server/nodes/telegram/telegram_receive.py) | registered | TriggerNode | — | — | renderer-dependent | `triggers-event` |
| [`telegramSend`](../../../docs-internal/node-logic-flows/telegram_social/telegramSend.md) · [source](../../../server/nodes/telegram/telegram_send.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |
| [`twitterReceive`](../../../docs-internal/node-logic-flows/twitter/twitterReceive.md) · [source](../../../server/nodes/twitter/twitter_receive/__init__.py) | unavailable | TriggerNode | — | — | renderer-dependent | `triggers-poll` |
| [`twitterSearch`](../../../docs-internal/node-logic-flows/twitter/twitterSearch.md) · [source](../../../server/nodes/twitter/twitter_search/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`twitterSend`](../../../docs-internal/node-logic-flows/twitter/twitterSend.md) · [source](../../../server/nodes/twitter/twitter_send/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |
| [`twitterUser`](../../../docs-internal/node-logic-flows/twitter/twitterUser.md) · [source](../../../server/nodes/twitter/twitter_user/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## text (2)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`fileHandler`](../../../docs-internal/node-logic-flows/chat_utility/fileHandler.md) · [source](../../../server/nodes/text/file_handler/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`textGenerator`](../../../docs-internal/node-logic-flows/chat_utility/textGenerator.md) · [source](../../../server/nodes/text/text_generator/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |

## tool (13)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`agentBuilder`](../../../docs-internal/node-logic-flows/ai_tools/agentBuilder.md) · [source](../../../server/nodes/tool/agent_builder/__init__.py) | registered | ToolNode | eligible | — | hidden | `machina-default` |
| [`android_tool`](../../../docs-internal/node-logic-flows/mobile/android_tool.md) · [source](../../../server/nodes/mobile/_tool.py) | registered | ToolNode | eligible | eligible | renderer-dependent | `android` |
| [`calculatorTool`](../../../docs-internal/node-logic-flows/ai_tools/calculatorTool.md) · [source](../../../server/nodes/tool/calculator_tool/__init__.py) | registered | ToolNode | eligible | — | hidden | `rest-api` |
| [`canvas`](../../../docs-internal/node-logic-flows/ai_tools/canvas.md) · [source](../../../server/nodes/tool/canvas/__init__.py) | registered | ToolNode | eligible | — | renderer-dependent | `machina-default` |
| [`currentTimeTool`](../../../docs-internal/node-logic-flows/ai_tools/currentTimeTool.md) · [source](../../../server/nodes/tool/current_time_tool/__init__.py) | registered | ToolNode | eligible | — | hidden | `machina-default` |
| [`dataSource`](../../../docs-internal/node-logic-flows/ai_tools/dataSource.md) · [source](../../../server/nodes/tool/data_source/__init__.py) | registered | ToolNode | eligible | — | hidden | `machina-default` |
| [`duckduckgoSearch`](../../../docs-internal/node-logic-flows/ai_tools/duckduckgoSearch.md) · [source](../../../server/nodes/search/duckduckgo_search/__init__.py) | registered | ToolNode | eligible | — | hidden | `rest-api` |
| [`masterSkill`](../../../docs-internal/node-logic-flows/ai_agents/masterSkill.md) · [source](../../../server/nodes/skill/master_skill/__init__.py) | registered | ActionNode | — | — | hidden | `machina-default` |
| [`mcpConnector`](../../../docs-internal/node-logic-flows/ai_tools/mcpConnector.md) · [source](../../../server/nodes/mcp/mcp_connector.py) | registered | ToolNode | eligible | — | hidden | `rest-api` |
| [`simpleMemory`](../../../docs-internal/node-logic-flows/ai_agents/simpleMemory.md) · [source](../../../server/nodes/tool/simple_memory/__init__.py) | registered | ToolNode | eligible | — | hidden | `machina-default` |
| [`taskManager`](../../../docs-internal/node-logic-flows/ai_tools/taskManager.md) · [source](../../../server/nodes/tool/task_manager/__init__.py) | registered | ToolNode | eligible | — | hidden | `machina-default` |
| [`visionAnalyze`](../../../docs-internal/node-logic-flows/ai_tools/visionAnalyze.md) · [source](../../../server/nodes/vision/vision_analyze/__init__.py) | registered | ToolNode | eligible | — | hidden | `ai-heavy` |
| [`writeTodos`](../../../docs-internal/node-logic-flows/ai_tools/writeTodos.md) · [source](../../../server/nodes/tool/write_todos/__init__.py) | registered | ToolNode | eligible | — | hidden | `machina-default` |

## trigger (2)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`taskTrigger`](../../../docs-internal/node-logic-flows/workflow_triggers/taskTrigger.md) · [source](../../../server/nodes/trigger/task_trigger/__init__.py) | registered | TriggerNode | — | — | renderer-dependent | `triggers-event` |
| [`webhookTrigger`](../../../docs-internal/node-logic-flows/workflow_triggers/webhookTrigger.md) · [source](../../../server/nodes/trigger/webhook_trigger/__init__.py) | registered | TriggerNode | — | — | renderer-dependent | `triggers-event` |

## utility (6)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`chatTrigger`](../../../docs-internal/node-logic-flows/workflow_triggers/chatTrigger.md) · [source](../../../server/nodes/trigger/chat_trigger/__init__.py) | registered | TriggerNode | — | — | renderer-dependent | `triggers-event` |
| [`console`](../../../docs-internal/node-logic-flows/chat_utility/console.md) · [source](../../../server/nodes/utility/console/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`httpRequest`](../../../docs-internal/node-logic-flows/http_proxy/httpRequest.md) · [source](../../../server/nodes/utility/http_request/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |
| [`processManager`](../../../docs-internal/node-logic-flows/code_fs_process/processManager.md) · [source](../../../server/nodes/utility/process_manager/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`teamMonitor`](../../../docs-internal/node-logic-flows/chat_utility/teamMonitor.md) · [source](../../../server/nodes/utility/team_monitor/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |
| [`webhookResponse`](../../../docs-internal/node-logic-flows/workflow_triggers/webhookResponse.md) · [source](../../../server/nodes/utility/webhook_response/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `rest-api` |

## vcs (1)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`githubAction`](../../../docs-internal/node-logic-flows/cli_integrations/githubAction.md) · [source](../../../server/nodes/github/github_action.py) | registered | ActionNode | eligible | — | renderer-dependent | `rest-api` |

## whatsapp (3)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`whatsappDb`](../../../docs-internal/node-logic-flows/whatsapp/whatsappDb.md) · [source](../../../server/nodes/whatsapp/whatsapp_db.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |
| [`whatsappReceive`](../../../docs-internal/node-logic-flows/whatsapp/whatsappReceive.md) · [source](../../../server/nodes/whatsapp/whatsapp_receive.py) | registered | TriggerNode | — | — | renderer-dependent | `triggers-event` |
| [`whatsappSend`](../../../docs-internal/node-logic-flows/whatsapp/whatsappSend.md) · [source](../../../server/nodes/whatsapp/whatsapp_send.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |

## whatsapp_business (4)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`whatsappBusinessMedia`](../../../docs-internal/node-logic-flows/whatsapp_business/whatsappBusinessMedia.md) · [source](../../../server/nodes/whatsapp_business/whatsapp_business_media.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |
| [`whatsappBusinessReceive`](../../../docs-internal/node-logic-flows/whatsapp_business/whatsappBusinessReceive.md) · [source](../../../server/nodes/whatsapp_business/whatsapp_business_receive.py) | registered | _WhatsAppBusinessTrigger | — | — | renderer-dependent | `triggers-event` |
| [`whatsappBusinessSend`](../../../docs-internal/node-logic-flows/whatsapp_business/whatsappBusinessSend.md) · [source](../../../server/nodes/whatsapp_business/whatsapp_business_send.py) | registered | ActionNode | eligible | — | renderer-dependent | `messaging` |
| [`whatsappBusinessStatus`](../../../docs-internal/node-logic-flows/whatsapp_business/whatsappBusinessStatus.md) · [source](../../../server/nodes/whatsapp_business/whatsapp_business_receive.py) | registered | _WhatsAppBusinessTrigger | — | — | renderer-dependent | `triggers-event` |

## workflow (4)

| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |
|---|---|---|---|---|---|---|
| [`approvalGate`](../../../docs-internal/node-logic-flows/workflow_triggers/approvalGate.md) · [source](../../../server/nodes/workflow/approval_gate/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `triggers-event` |
| [`employeeJob`](../../../docs-internal/node-logic-flows/workflow/employeeJob.md) · [source](../../../server/nodes/workflow/employee_job/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`employeeJobSource`](../../../docs-internal/node-logic-flows/workflow/employeeJobSource.md) · [source](../../../server/nodes/workflow/employee_job_source/__init__.py) | registered | ActionNode | eligible | — | renderer-dependent | `machina-default` |
| [`start`](../../../docs-internal/node-logic-flows/workflow_triggers/start.md) · [source](../../../server/nodes/workflow/start/__init__.py) | registered | ActionNode | — | — | renderer-dependent | `machina-default` |

## Unavailable and retired

- `twitterReceive` remains registered but unavailable; its row is not a working ingress promise.
- [`browserHarness`](../../../docs-internal/node-logic-flows/web_automation/browserHarness.md) is retired and absent from the registry.

See the [core](../../../docs-internal/audits/CORE_NODES.md) and
[integration](../../../docs-internal/audits/INTEGRATION_NODES.md) audits for node-specific constraints.
