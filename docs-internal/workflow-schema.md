# Workflow JSON Schema

> **Related Documentation:**
> - [Node Creation Guide](./node_creation.md) - How to create new nodes (frontend definitions, backend handlers)
> - [CLAUDE.md](../CLAUDE.md) - Project overview, key files, architecture patterns

## Overview

The workflow JSON schema defines the structure and validation rules for workflow automation data. This document describes the schema format, validation, and usage examples.

## Schema Structure

A workflow JSON document contains:

- **Metadata**: ID, name, timestamps, version
- **Nodes**: Array of workflow nodes with their types, positions, and parameters
- **Edges**: Array of connections between nodes

## Complete Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Workflow",
  "description": "A workflow automation definition containing nodes and connections",
  "type": "object",
  "required": ["id", "name", "nodes", "edges", "createdAt", "lastModified"],
  "properties": {
    "id": {
      "type": "string",
      "pattern": "^workflow_[0-9]+$"
    },
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 100
    },
    "nodes": {
      "type": "array",
      "items": { "$ref": "#/definitions/node" }
    },
    "edges": {
      "type": "array",
      "items": { "$ref": "#/definitions/edge" }
    },
    "createdAt": {
      "type": "string",
      "format": "date-time"
    },
    "lastModified": {
      "type": "string",
      "format": "date-time"
    },
    "version": {
      "type": "string",
      "default": "1.0.0"
    }
  }
}
```

## Supported Node Types (96 Total)

> The canonical list of nodes lives in the backend plugin tree at `server/nodes/<category>/<node>.py`; this section is a human-readable index.

### Workflow Nodes (2 nodes)
- `start` - Workflow entry point with initial data
- `taskTrigger` - Event-driven trigger for delegated child agent completion

### Scheduler Nodes (2 nodes)
- `timer` - Delay/wait before continuing (seconds, minutes, hours)
- `cronScheduler` - Recurring scheduled execution (seconds to months, timezone support)

### AI Chat Model Nodes (9 nodes)
- `openaiChatModel` - OpenAI GPT 4.x/5.x + reasoning models (o1/o3/o4 series)
- `anthropicChatModel` - Anthropic Claude 4.x with extended thinking
- `geminiChatModel` - Google Gemini 2.5/3 with thinking support
- `openrouterChatModel` - OpenRouter unified API (200+ models)
- `groqChatModel` - Groq ultra-fast inference (Llama, Qwen3, GPT-OSS)
- `cerebrasChatModel` - Cerebras custom AI hardware (Llama, Qwen)
- `deepseekChatModel` - DeepSeek V3 (chat + reasoner with Chain-of-Thought)
- `kimiChatModel` - Moonshot Kimi K2.5 / K2-thinking
- `mistralChatModel` - Mistral Large / Small / Codestral

### AI Agents and Memory (3 nodes)
- `aiAgent` - Tool-calling agent loop
- `chatAgent` - Conversational agent (Zeenie) with skill support
- `simpleMemory` - Markdown-based conversation memory with optional vector DB

### Specialized AI Agents (15 nodes)
Pre-configured agents for specific domains. All inherit `AI_AGENT_PROPERTIES`. See [agent_architecture.md](agent_architecture.md):
- `android_agent` - Android device control
- `coding_agent` - Code execution (Python, JavaScript, TypeScript)
- `web_agent` - Web automation and scraping
- `task_agent` - Task management and scheduling
- `social_agent` - Social messaging (WhatsApp, Telegram)
- `travel_agent` - Location and maps
- `tool_agent` - Generic tool orchestration
- `productivity_agent` - Google Workspace workflows
- `payments_agent` - Payment processing
- `consumer_agent` - Customer support
- `autonomous_agent` - Code Mode with agentic loops
- `orchestrator_agent` - Team lead for multi-agent coordination
- `ai_employee` - Team lead (alternate UI/branding)
- `rlm_agent` - Recursive Language Model (REPL-based, dedicated handler)
- `claude_code_agent` - Claude Code SDK integration

### AI Tool Nodes (4 dedicated)
Connect to AI Agent's `input-tools` handle:
- `masterSkill` - Aggregates multiple skills with enable/disable toggles
- `calculatorTool` - Math operations
- `currentTimeTool` - Current date/time with timezone
- `duckduckgoSearch` - DuckDuckGo web search (free, no API key)
- `taskManager` - Task creation and tracking tool

### Search Nodes (3 dual-purpose)
Work as both workflow nodes and AI tools. Defined in `searchNodes.ts`:
- `braveSearch` - Brave Search API
- `serperSearch` - Google SERP via Serper API
- `perplexitySearch` - Perplexity Sonar with citations

### Location Nodes (3 nodes, 2 dual-purpose)
- `gmaps_create` - Google Maps creation with center, zoom, map type
- `gmaps_locations` - Geocoding (address to coordinates)
- `gmaps_nearby_places` - Google Places nearbySearch

### Google Workspace Nodes (7 nodes)
Consolidated operation-based nodes sharing one OAuth connection. See [new_service_integration.md](new_service_integration.md):
- `googleGmail` - send / search / read (dual-purpose)
- `googleGmailReceive` - Polling trigger for incoming emails
- `googleCalendar` - create / list / update / delete
- `googleDrive` - upload / download / list / share
- `googleSheets` - read / write / append
- `googleTasks` - create / list / complete / update / delete
- `googleContacts` - create / list / search / get / update / delete

### WhatsApp Nodes (3 nodes)
- `whatsappSend` - Dual-purpose: send text/media/location/contact to contacts, groups, channels
- `whatsappDb` - Dual-purpose: 18 operations covering chat history, contacts, groups, newsletters
- `whatsappReceive` - Event-driven trigger with filters (type, sender, group, keywords, channel)

### Twitter/X Nodes (4 nodes)
- `twitterSend` - Dual-purpose: tweet, reply, retweet, like, delete
- `twitterSearch` - Dual-purpose: rich search with expansions and citations
- `twitterUser` - Dual-purpose: profiles, followers, following
- `twitterReceive` - Polling trigger for mentions, DMs, timeline

### Telegram Nodes (2 nodes)
- `telegramSend` - Dual-purpose: text, photo, document, location, contact
- `telegramReceive` - Event-driven trigger via long-polling

### Email Nodes (3 nodes)
IMAP/SMTP integration via the [Himalaya CLI](https://github.com/pimalaya/himalaya). Supports Gmail, Outlook, Yahoo, iCloud, ProtonMail (Bridge), Fastmail, and custom/self-hosted servers. Credentials stored via `auth_service.store_api_key()` -- see [email_service.md](email_service.md).
- `emailSend` - Dual-purpose: send via SMTP (to, subject, body, cc, bcc, body_type text/html)
- `emailRead` - Dual-purpose: list / search / read / folders / move / delete / flag via IMAP
- `emailReceive` - Polling trigger with baseline detection (poll_interval 30-3600s)

### Social Nodes (2 nodes)
Unified multi-platform messaging (WhatsApp, Telegram, Discord, Slack, SMS, Email, Matrix, Teams):
- `socialReceive` - Normalize messages from platform triggers
- `socialSend` - Dual-purpose unified send

### Android Nodes (16 nodes)

> Android device connection is now configured via the Credentials modal (Android panel), not a workflow node. Service nodes can be connected directly to any agent's `input-tools` handle.

#### System Monitoring (4 nodes)
- `batteryMonitor` - Battery status, level, charging, temperature
- `networkMonitor` - Network connectivity and type
- `systemInfo` - Device/OS info, memory, hardware
- `location` - GPS tracking with accuracy

#### App Management (2 nodes)
- `appLauncher` - Launch applications by package name
- `appList` - List installed applications

#### Device Automation (6 nodes)
- `wifiAutomation` - WiFi enable/disable, scan, status
- `bluetoothAutomation` - Bluetooth enable/disable, paired devices
- `audioAutomation` - Volume control, mute/unmute
- `deviceStateAutomation` - Airplane mode, screen, power save, brightness
- `screenControlAutomation` - Brightness, wake, timeout
- `airplaneModeControl` - Airplane mode status and control

#### Sensors (2 nodes)
- `motionDetection` - Accelerometer, gyroscope, shake detection
- `environmentalSensors` - Temperature, humidity, pressure, light

#### Media (2 nodes)
- `cameraControl` - Camera info, take photos
- `mediaControl` - Media playback, volume

### Utility Nodes (6 nodes)
- `httpRequest` - HTTP requests (GET, POST, PUT, DELETE, PATCH) with optional proxy support (`useProxy: true`)
- `webhookTrigger` - Incoming HTTP webhook trigger at `/webhook/{path}`
- `webhookResponse` - Custom response to webhook caller
- `chatTrigger` - Console message input trigger
- `console` - Debug logging output
- `teamMonitor` - Real-time monitoring of Agent Team operations

### Proxy Nodes (3 nodes)
- `proxyRequest` - HTTP requests through residential proxy providers with geo-targeting and failover
- `proxyConfig` - Dual-purpose: configure providers, credentials, routing rules
- `proxyStatus` - View proxy provider health, scores, and usage statistics

### Code Nodes (3 nodes)
All dual-purpose (workflow node + AI tool):
- `pythonExecutor` - Python code execution (in-process)
- `javascriptExecutor` - JavaScript execution via persistent Node.js server
- `typescriptExecutor` - TypeScript execution via persistent Node.js server (tsx)

### Chat Nodes (2 nodes)
- `chatSend` - Send via JSON-RPC 2.0 WebSocket
- `chatHistory` - Retrieve chat message history

### Document Processing Nodes (6 nodes)
RAG pipeline nodes for document ingestion, processing, and vector storage:
- `httpScraper` - Scrape links from web pages (date/page pagination modes)
- `fileDownloader` - Parallel file downloads with semaphore concurrency
- `documentParser` - Parse documents to text (PyPDF, Marker OCR, Unstructured, BeautifulSoup)
- `textChunker` - Split text into overlapping chunks (recursive, markdown, token strategies)
- `embeddingGenerator` - Generate vector embeddings (HuggingFace, OpenAI, Ollama providers)
- `vectorStore` - Store/query vectors (ChromaDB, Qdrant, Pinecone backends)

### Web Scraping Nodes (2 nodes)
- `apifyActor` - Dual-purpose: run Apify pre-built actors (Instagram, TikTok, Twitter, LinkedIn, Google Search, Maps, etc.)
- `crawleeScraper` - Crawlee web scraping with static and Playwright modes

## Example Workflow JSON

### Basic Workflow (Start -> AI Agent)

```json
{
  "id": "workflow_1234567890",
  "name": "AI Agent with Start Node",
  "version": "1.0.0",
  "createdAt": "2025-01-06T12:00:00.000Z",
  "lastModified": "2025-01-06T12:30:00.000Z",
  "nodes": [
    {
      "id": "start-1",
      "type": "start",
      "position": { "x": 100, "y": 100 },
      "data": {
        "label": "Start",
        "initialData": "{\"message\": \"Hello World\", \"value\": 123}"
      }
    },
    {
      "id": "aiAgent-1",
      "type": "aiAgent",
      "position": { "x": 400, "y": 100 },
      "data": {
        "label": "AI Agent",
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "prompt": "{{start.message}}",
        "systemMessage": "You are a helpful assistant"
      }
    }
  ],
  "edges": [
    {
      "id": "edge-1",
      "source": "start-1",
      "target": "aiAgent-1",
      "sourceHandle": "output-main",
      "targetHandle": "input-main"
    }
  ]
}
```

### Workflow with Memory (Conversational AI)

```json
{
  "id": "workflow_1234567891",
  "name": "AI Agent with Memory",
  "version": "1.0.0",
  "createdAt": "2025-01-06T12:00:00.000Z",
  "lastModified": "2025-01-06T12:30:00.000Z",
  "nodes": [
    {
      "id": "start-1",
      "type": "start",
      "position": { "x": 100, "y": 100 },
      "data": {
        "label": "Start",
        "initialData": "{\"chatInput\": \"What did we discuss earlier?\"}"
      }
    },
    {
      "id": "simpleMemory-1",
      "type": "simpleMemory",
      "position": { "x": 400, "y": 250 },
      "data": {
        "label": "Memory",
        "sessionId": "user-session-123",
        "memoryType": "window",
        "windowSize": 20
      }
    },
    {
      "id": "aiAgent-1",
      "type": "aiAgent",
      "position": { "x": 400, "y": 100 },
      "data": {
        "label": "AI Agent",
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "prompt": "{{start.chatInput}}",
        "systemMessage": "You are a helpful assistant with memory of our conversation"
      }
    }
  ],
  "edges": [
    {
      "id": "edge-main",
      "source": "start-1",
      "target": "aiAgent-1",
      "sourceHandle": "output-main",
      "targetHandle": "input-main"
    },
    {
      "id": "edge-memory",
      "source": "simpleMemory-1",
      "target": "aiAgent-1",
      "sourceHandle": "output-memory",
      "targetHandle": "input-memory"
    }
  ]
}
```

**Memory Workflow Behavior:**
1. Start node provides the user's chat input
2. Simple Memory connects to AI Agent's memory handle (config connection)
3. When AI Agent runs, it loads conversation history from the memory session
4. AI Agent's response is automatically saved to the memory session
5. Simple Memory node can see Start node's outputs (via AI Agent) for parameter mapping

## Usage Examples

### Export Workflow

```typescript
import { useAppStore } from './store/useAppStore';

// Export to JSON string
const workflow = useAppStore.getState().currentWorkflow;
const jsonString = useAppStore.getState().exportWorkflowToJSON();
console.log(jsonString);

// Export to file download
useAppStore.getState().exportWorkflowToFile();
```

### Import Workflow

```typescript
import { useAppStore } from './store/useAppStore';

// Import from JSON string
const jsonString = '{ ... }';
useAppStore.getState().importWorkflowFromJSON(jsonString);

// Import from file
import { importWorkflowFromFile } from './utils/workflowExport';

const fileInput = document.createElement('input');
fileInput.type = 'file';
fileInput.accept = '.json';
fileInput.onchange = async (e) => {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) {
    const workflow = await importWorkflowFromFile(file);
    useAppStore.getState().setCurrentWorkflow(workflow);
  }
};
fileInput.click();
```

### Validate Workflow

```typescript
import { validateWorkflow } from './schemas/workflowSchema';

const workflow = {
  id: 'workflow_123',
  name: 'My Workflow',
  nodes: [...],
  edges: [...],
  createdAt: new Date().toISOString(),
  lastModified: new Date().toISOString()
};

const validation = validateWorkflow(workflow);

if (validation.valid) {
  console.log('Workflow is valid');
} else {
  console.error('Validation errors:', validation.errors);
}
```

## Config Node Architecture

Config nodes (memory, tools) are auxiliary nodes that connect to parent nodes via special handles instead of the main data flow.

### Characteristics of Config Nodes

| Property | Config Node | Standard Node |
|----------|-------------|---------------|
| Group | Contains 'memory' or 'tool' | Other groups |
| Input Handle | None (passive) | `input-main` |
| Output Handle | `output-memory`, `output-model` | `output-main` |
| Target Handle | Parent's `input-memory`, `input-tools` | Parent's `input-main` |
| Parent Visibility | NOT shown in parent's input panel | Shown in parent's input panel |
| Input Inheritance | Inherits parent's main inputs | Only direct connections |

### Config Node Detection

Nodes are identified as config nodes by their group membership:

```typescript
const isConfigNode = (nodeType: string): boolean => {
  const definition = resolveNodeDescription(nodeType);
  const groups = definition?.group || [];
  return groups.includes('memory') || groups.includes('tool');
};
```

### Input Inheritance for Config Nodes

When a config node is connected to a parent's config handle:
1. The parent node does NOT see the config node as an input
2. The config node DOES see the parent's main inputs (labeled "via Parent")

This allows memory nodes to access the same input data as the AI Agent they're connected to, enabling template variable mapping like `{{start.chatInput}}` in the memory node's session ID.

```
┌─────────────┐     main      ┌─────────────┐
│   Start     │──────────────▶│  AI Agent   │
└─────────────┘               └─────────────┘
                                    ▲
                                    │ input-memory
                              ┌─────────────┐
                              │   Memory    │
                              │ (sees Start │
                              │  via Agent) │
                              └─────────────┘
```

## Node Data Structure

Each node contains:

- `id`: Unique identifier (string)
- `type`: Node type (see supported types)
- `position`: { x: number, y: number }
- `data`: Node-specific parameters (object)
  - `label`: Display label
  - Additional parameters based on node type

### Start Node Data

```json
{
  "initialData": "{\"key1\": \"value1\", \"key2\": 123}"
}
```

### AI Agent Node Data

```json
{
  "provider": "anthropic" | "openai" | "gemini" | "openrouter",
  "model": "model-name",
  "apiKey": "api-key-string",
  "prompt": "{{start.message}}",
  "systemMessage": "System instructions",
  "temperature": 0.7,
  "maxTokens": 1000
}
```

### OpenRouter Chat Model Node Data

```json
{
  "model": "openai/gpt-4o-mini",
  "prompt": "{{start.message}}",
  "options": {
    "systemMessage": "You are a helpful assistant",
    "temperature": 0.7,
    "maxTokens": 1000,
    "topP": 1,
    "frequencyPenalty": 0,
    "presencePenalty": 0,
    "timeout": 60000,
    "maxRetries": 2
  }
}
```

OpenRouter model IDs use the format `provider/model-name`:
- `openai/gpt-4o`, `openai/gpt-4o-mini`
- `anthropic/claude-3.5-sonnet`, `anthropic/claude-3-haiku`
- `google/gemini-pro`, `google/gemini-flash-1.5`
- `meta-llama/llama-3.1-70b-instruct`
- Free models are prefixed with `[FREE]` in the dropdown

### Simple Memory Node Data

```json
{
  "sessionId": "conversation-session-id",
  "memoryType": "buffer" | "window",
  "windowSize": 10
}
```

Memory nodes are config nodes that:
- Connect to AI Agent via `input-memory` handle
- Store conversation history in database (persisted across restarts)
- Support buffer mode (all messages) or window mode (last N messages)
- Inherit parent node's main inputs for parameter mapping

### Trigger Node Data (WhatsApp Trigger)

```json
{
  "messageTypeFilter": "all" | "text" | "image" | "video" | "audio",
  "filter": "all" | "contact" | "group" | "keywords",
  "contactPhone": "+1234567890",
  "groupId": "group-id",
  "keywords": "word1, word2",
  "ignoreOwnMessages": true,
  "includeMediaData": false
}
```

Trigger nodes:
- Wait for external events using asyncio.Future
- Have no main input (they start workflows)
- Output event data when triggered

## Edge Data Structure

Each edge contains:

- `id`: Unique identifier
- `source`: Source node ID
- `target`: Target node ID
- `sourceHandle`: Output handle ID (default: "output-main")
- `targetHandle`: Input handle ID (default: "input-main")
- `type`: Rendering type ("default", "straight", "step", etc.)

### Handle Naming Conventions

#### Main Data Flow Handles
- `output-main` / `input-main` - Primary data flow between nodes

#### Config Handles (for auxiliary nodes)
Config handles connect memory/tool nodes to parent nodes without being part of main data flow:
- `input-memory` - Memory configuration input (AI Agent)
- `input-tools` - Tools configuration input (AI Agent)
- `input-model` - Model configuration input
- `output-model` - Model/config output (circular nodes)
- `output-memory` - Memory output (simpleMemory node)

#### Handle Detection Pattern
Config handles follow the pattern `input-<type>` where type is NOT 'main':
```typescript
const isConfigHandle = (handle: string): boolean => {
  return handle.startsWith('input-') && handle !== 'input-main';
};
```

### Config Node Connection Example

```json
{
  "edges": [
    {
      "id": "edge-memory",
      "source": "simpleMemory-1",
      "target": "aiAgent-1",
      "sourceHandle": "output-memory",
      "targetHandle": "input-memory"
    },
    {
      "id": "edge-main",
      "source": "start-1",
      "target": "aiAgent-1",
      "sourceHandle": "output-main",
      "targetHandle": "input-main"
    }
  ]
}
```

In this example:
- The simpleMemory node connects to AI Agent's `input-memory` handle (config connection)
- The start node connects to AI Agent's `input-main` handle (main data flow)
- AI Agent does NOT see simpleMemory as an input (config handles are filtered)
- simpleMemory DOES see Start node's outputs (inherits parent's main inputs)

## Dynamic Parameter Resolution

Template variables in node parameters are resolved using the format `{{nodeName.property}}`:

```json
{
  "prompt": "{{start.message}}"
}
```

This resolves to the `message` property from the Start node's output data.

## Validation Rules

1. Workflow must have required fields: id, name, nodes, edges, createdAt, lastModified
2. Workflow ID must match pattern: `workflow_[0-9]+`
3. Each node must have: id, type, position (with x and y)
4. Each edge must have: id, source, target
5. Edge source and target must reference existing nodes
6. Node types must be one of the supported types

## Version History

- **1.4.0** (2026-01-27): Comprehensive node type expansion to 58 nodes
  - Added Groq and Cerebras AI chat model nodes (6 total AI models)
  - Added 9 AI Skill nodes for Chat Agent (claude, whatsapp, memory, maps, http, scheduler, android, code, custom)
  - Added 4 AI Tool nodes for AI Agent (calculator, currentTime, webSearch, androidTool)
  - Added whatsappChatHistory node (4 WhatsApp nodes total)
  - Added scheduler nodes: timer, cronScheduler (with timezone support)
  - Added chat nodes: chatSend, chatHistory (JSON-RPC 2.0 WebSocket)
  - Added utility nodes: chatTrigger, console (5 utility nodes total)
  - Added javascriptExecutor code node (2 code nodes total)
  - Updated WebSocket handlers count to 51

- **1.3.0** (2026-01-19): OpenRouter AI provider integration
  - Added openrouterChatModel to AI Nodes list
  - Added OpenRouter node data structure with model ID format
  - Updated AI Agent provider options to include openrouter

- **1.2.0** (2025-12-19): Config node architecture and expanded node types
  - Added Config Node Architecture section with input inheritance
  - Added 31 supported node types (AI, Android, WhatsApp, Location, Code)
  - Added handle naming conventions (main vs config handles)
  - Added Simple Memory and Trigger node data structures
  - Added workflow with memory example
  - Updated edge documentation with config handle patterns

- **1.1.0** (2025-01-10): Android and WhatsApp integration
  - Added 17 Android service nodes
  - Added WhatsApp messaging nodes
  - Added trigger node support

- **1.0.0** (2025-01-06): Initial schema definition
  - Basic workflow structure
  - Node and edge definitions
  - Validation rules
  - Export/import functionality
