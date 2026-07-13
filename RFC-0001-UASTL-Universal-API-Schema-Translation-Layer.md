# RFC-0001: Universal API Schema Translation Layer for LLM Code Generation

**Title:** UASTL — Universal API Schema Translation Layer  
**Status:** Draft  
**Author:** Rohith Thakurwar (Genie.ai / MachinaOS)  
**Created:** 2026-04-26  
**Target System:** OpenCompany Agentic OS Platform

---

## Abstract

This RFC specifies a system — the **Universal API Schema Translation Layer (UASTL)** — that ingests arbitrary connector API specifications (OpenAPI 3.x, AsyncAPI, GraphQL SDL, raw provider YAML) and compiles them into a **standardized, LLM-optimized Python interface** that a language model can use to author structured, executable code for real-world digital tasks. The system addresses three converging problems: (1) the N×M explosion of API surface that an LLM must memorize to write correct integration code, (2) the fragility of LLM-generated API calls when auth, pagination, rate-limiting, and error envelopes vary per provider, and (3) the absence of a self-improving feedback loop that refines the LLM's code-generation prompts against execution outcomes.

UASTL combines four subsystems:

- **Schema Compiler** — transforms heterogeneous API specs into a canonical `UniversalConnector` Python interface with typed stubs, brokered auth, uniform pagination, and standardized error handling.
- **Code Generation Engine** — a DSPy-based module that receives a natural-language task, retrieves relevant connector stubs, and emits executable Python (CodeAct paradigm) targeting the `UniversalConnector` interface.
- **Sandboxed Execution Runtime** — a Firecracker microVM (E2B) or WASM (Pyodide) environment with persistent Jupyter kernel, browser automation (Playwright), screenshot verification, and console introspection.
- **Reflective Prompt Optimizer** — a GEPA-based evolutionary compiler that uses execution traces (stdout, stderr, screenshots, HTTP responses) as rich feedback to iteratively improve code-generation prompts.

The end-to-end flow: `Task (NL) → Stub Retrieval → Code Generation → Sandboxed Execution → Trace Capture → GEPA Reflection → Prompt Improvement → Better Code Next Time`.

---

## 1. Motivation

### 1.1 The N×M API Surface Problem

A task like "check top trending reels on Instagram, then post about them on Twitter/X" requires the LLM to know:

- Instagram Graph API: OAuth2 app-level token, `GET /ig_hashtag_search`, `GET /{hashtag-id}/top_media`, pagination via `paging.cursors.after`, rate limit of 200 calls/hour per user, JSON envelope `{ "data": [...], "paging": {...} }`.
- Twitter/X API v2: OAuth2 PKCE or OAuth1.0a, `POST /2/tweets`, rate limit headers `x-rate-limit-remaining`, JSON envelope `{ "data": {...}, "errors": [...] }`.

Each additional connector multiplies the surface. Nango's `providers.yaml` catalogs **700+ APIs**, each with distinct auth modes (14 variants: `OAUTH2`, `API_KEY`, `BASIC`, `JWT`, `OAUTH2_CC`, `TBA`, `CUSTOM`, `TWO_STEP`, `SIGNATURE`, `OAUTH1`, `APP`, `APP_STORE`, `BILL`, `MCP_OAUTH2`), pagination types (`cursor`, `link`, `offset`), retry headers, base URLs, and response paths. An LLM cannot memorize this surface reliably.

### 1.2 Code-as-Action Outperforms JSON Tool Calling

CodeAct (Wang et al., ICML 2024) demonstrated across 17 LLMs that emitting Python code achieves **up to 20 percentage points higher success** with **~30% fewer interaction turns** than JSON tool-calling, especially for compositional tasks requiring loops, conditionals, and library reuse. OpenHands, Devin, and SWE-Agent all converged on code-as-action as the primary paradigm.

The implication: the right abstraction is not "define 700 JSON tools" but "give the LLM a Python SDK with 700 connectors behind one interface."

### 1.3 Self-Improving Code Generation is Unsolved at the Interface Level

DSPy provides the programming model (signatures, modules, optimizers). GEPA provides the optimization algorithm (reflective prompt evolution, Pareto candidate selection). RLM provides the long-context scaffold (REPL variables, `llm_query()`, truncated stdout). But no system today **composes these three with a unified API layer and closes the loop against real execution traces from real APIs**. This RFC specifies that composition.

---

## 2. Terminology

| Term | Definition |
|---|---|
| **Connector** | A third-party API or service (e.g., Twitter/X, Instagram, Slack, Notion) |
| **Provider Spec** | The raw API specification: OpenAPI 3.x JSON/YAML, AsyncAPI, GraphQL SDL, or Nango-style `providers.yaml` entry |
| **Universal Connector** | The UASTL-compiled Python class exposing a connector through a standardized interface |
| **Stub** | A minimal Python source string (type hints, docstrings, no implementation) injected into the LLM's context for code generation |
| **Broker** | The credential proxy that injects auth headers at request time without exposing tokens to the LLM or sandbox |
| **Task Program** | The LLM-generated Python code that uses `UniversalConnector` stubs to accomplish a digital task |
| **Trace** | The full execution record: code emitted, stdout/stderr per cell, HTTP request/response pairs, screenshots, timing |
| **Metric** | A decomposed scoring function over traces: parse success, import correctness, call pattern validity, sandbox execution RC, output quality |
| **Reflection** | GEPA's mutation operator: an LLM reads the trace + metric critique and rewrites the code-generation prompt |

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                          UASTL System                                │
│                                                                      │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────────────┐  │
│  │   Schema     │    │   Stub       │    │   Vector Index          │  │
│  │   Registry   │───▶│   Compiler   │───▶│   (per-connector stubs) │  │
│  │             │    │              │    │                         │  │
│  │ • OpenAPI   │    │ • Normalize  │    │ • Embedding search      │  │
│  │ • providers │    │ • Type-map   │    │ • Top-k retrieval       │  │
│  │   .yaml     │    │ • Pydantic   │    │ • Context budget        │  │
│  │ • GraphQL   │    │ • Docstrings │    │                         │  │
│  └─────────────┘    └──────────────┘    └────────────┬────────────┘  │
│                                                      │               │
│                                                      ▼               │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │                    Code Generation Engine                      │   │
│  │                                                               │   │
│  │  ┌─────────────┐   ┌──────────────┐   ┌──────────────────┐   │   │
│  │  │ Task Router │──▶│ DSPy Module  │──▶│ CodeAct / RLM    │   │   │
│  │  │ (NL → plan) │   │ (Signature)  │   │ (Python emission)│   │   │
│  │  └─────────────┘   └──────────────┘   └────────┬─────────┘   │   │
│  │                                                 │             │   │
│  └─────────────────────────────────────────────────┼─────────────┘   │
│                                                    │                 │
│                                                    ▼                 │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │                 Sandboxed Execution Runtime                    │   │
│  │                                                               │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐   │   │
│  │  │ Jupyter  │  │  Playwright  │  │  Credential Broker    │   │   │
│  │  │ Kernel   │  │  (browser)   │  │  (Nango proxy / MCP)  │   │   │
│  │  │          │  │              │  │                       │   │   │
│  │  │ • REPL   │  │ • AX-tree    │  │ • Token injection     │   │   │
│  │  │ • vars   │  │ • Screenshot │  │ • Refresh / rotate    │   │   │
│  │  │ • state  │  │ • Console    │  │ • Egress allowlist    │   │   │
│  │  └──────────┘  └──────────────┘  └───────────────────────┘   │   │
│  │                                                               │   │
│  │  Firecracker microVM (E2B) │ Pyodide (WASM) │ Modal          │   │
│  └───────────────────────────────┬───────────────────────────────┘   │
│                                  │                                   │
│                                  ▼                                   │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │              Reflective Prompt Optimizer (GEPA)                │   │
│  │                                                               │   │
│  │  Trace ──▶ Metric (decomposed) ──▶ Critique (textual)        │   │
│  │       ──▶ Reflection LM ──▶ Mutated Prompt ──▶ Pareto Pool   │   │
│  │       ──▶ Selection ──▶ Next Generation                      │   │
│  │                                                               │   │
│  │  DSPy optimizer: dspy.GEPA(metric, num_generations=40)        │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Schema Compiler

### 4.1 Input Formats

The Schema Compiler accepts four input formats:

```python
class SchemaSource(Enum):
    OPENAPI_3 = "openapi"        # OpenAPI 3.0/3.1 JSON or YAML
    ASYNCAPI  = "asyncapi"       # AsyncAPI 2.x/3.x for event-driven APIs
    GRAPHQL   = "graphql"        # GraphQL SDL + introspection result
    NANGO     = "nango_provider" # Nango providers.yaml entry
```

### 4.2 Canonical Internal Representation (CIR)

All input formats are first compiled to a **Canonical Internal Representation**, a Pydantic model that captures the union of information across formats:

```python
from pydantic import BaseModel, Field
from typing import Literal
from enum import Enum

class AuthMode(str, Enum):
    OAUTH2     = "oauth2"
    OAUTH2_CC  = "oauth2_cc"      # client credentials
    OAUTH1     = "oauth1"
    API_KEY    = "api_key"
    BASIC      = "basic"
    BEARER     = "bearer"
    JWT        = "jwt"
    CUSTOM     = "custom"
    NONE       = "none"

class PaginationType(str, Enum):
    CURSOR = "cursor"
    OFFSET = "offset"
    LINK   = "link"
    NONE   = "none"

class RateLimitConfig(BaseModel):
    """Rate limit headers to respect."""
    remaining_header: str | None = None        # e.g. "x-rate-limit-remaining"
    reset_header: str | None = None            # e.g. "x-rate-limit-reset"
    reset_format: Literal["unix", "seconds", "iso8601"] = "unix"
    retry_after_header: str | None = "retry-after"

class PaginationConfig(BaseModel):
    """Uniform pagination descriptor — derived from Nango's providers.yaml patterns."""
    type: PaginationType = PaginationType.NONE
    cursor_path_in_response: str | None = None   # JSONPath to next cursor
    cursor_name_in_request: str | None = None     # query param name
    limit_name_in_request: str | None = "limit"
    response_path: str | None = None              # JSONPath to data array
    link_path_in_response_body: str | None = None # for link-based pagination
    link_rel: str | None = None                   # for Link header pagination
    offset_calculation: Literal["per-page", "by-response-size"] = "by-response-size"

class EndpointSpec(BaseModel):
    """A single API endpoint, normalized."""
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str                                     # e.g. "/2/tweets"
    summary: str                                  # one-line description
    description: str | None = None
    parameters: list["ParameterSpec"] = []
    request_body: "RequestBodySpec | None" = None
    response_schema: dict | None = None           # JSON Schema of success response
    response_path: str | None = None              # JSONPath to unwrap envelope
    scopes_required: list[str] = []
    rate_limit_group: str | None = None
    tags: list[str] = []

class ParameterSpec(BaseModel):
    name: str
    location: Literal["query", "path", "header", "cookie"]
    required: bool = False
    schema_type: str = "string"
    description: str | None = None
    enum_values: list[str] | None = None

class RequestBodySpec(BaseModel):
    content_type: str = "application/json"
    schema: dict                                  # JSON Schema
    required: bool = True

class ConnectorCIR(BaseModel):
    """Canonical Internal Representation for one connector."""
    name: str                                     # e.g. "twitter_x"
    display_name: str                             # e.g. "Twitter / X"
    categories: list[str] = []                    # e.g. ["social", "marketing"]
    base_url: str                                 # e.g. "https://api.x.com"
    auth: AuthMode
    auth_config: dict = {}                        # auth-mode-specific config
    default_headers: dict[str, str] = {}
    pagination: PaginationConfig = PaginationConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    endpoints: list[EndpointSpec] = []
    docs_url: str | None = None
    version: str = "1.0.0"
```

### 4.3 Format-Specific Parsers

Each input format has a parser that emits `ConnectorCIR`:

```python
class OpenAPIParser:
    """Parse OpenAPI 3.x spec into ConnectorCIR."""

    def parse(self, spec: dict, overrides: dict | None = None) -> ConnectorCIR:
        """
        Walks the OpenAPI spec:
        1. Extract info.title → display_name, servers[0].url → base_url
        2. Extract securitySchemes → auth mode detection
        3. Walk paths → EndpointSpec list
        4. For each operation:
           - parameters (query, path, header)
           - requestBody → RequestBodySpec
           - responses.200.content.application/json.schema → response_schema
        5. Apply overrides (pagination, rate_limit, response_path)
           because OpenAPI specs rarely encode these.
        """
        ...

class NangoProviderParser:
    """Parse a single entry from Nango's providers.yaml."""

    def parse(self, name: str, entry: dict) -> ConnectorCIR:
        """
        Direct mapping:
        - entry["auth_mode"] → AuthMode enum
        - entry["proxy"]["base_url"] → base_url
        - entry["proxy"]["paginate"] → PaginationConfig
        - entry["proxy"]["retry"] → RateLimitConfig
        - entry["proxy"]["headers"] → default_headers

        Note: Nango entries don't contain endpoint-level detail.
        Endpoints are discovered separately via:
        1. Fetching the provider's OpenAPI spec (if available)
        2. Scraping the provider's API docs
        3. Manual endpoint registry
        """
        ...

class GraphQLParser:
    """Parse GraphQL SDL + introspection into ConnectorCIR."""

    def parse(self, sdl: str, introspection: dict, 
              base_url: str, auth: AuthMode) -> ConnectorCIR:
        """
        Maps:
        - Query type fields → GET-equivalent EndpointSpecs
        - Mutation type fields → POST-equivalent EndpointSpecs
        - Input types → RequestBodySpec
        - Return types → response_schema
        """
        ...
```

### 4.4 Enrichment Pipeline

Raw specs are incomplete. The enrichment pipeline fills gaps:

```
Raw Spec → Parser → ConnectorCIR
  → Pagination Enricher (probe API or match known patterns)
  → Rate Limit Enricher (probe headers or match known patterns)
  → Response Envelope Enricher (detect common wrappers: { data, errors, meta })
  → Auth Scope Mapper (map endpoint tags to required scopes)
  → Example Generator (LLM-generate example request/response pairs)
  → Enriched ConnectorCIR
```

The Enrichment Pipeline can use an LLM to fill gaps:

```python
class SpecEnricher(dspy.Module):
    """Use an LLM to infer missing spec details from API docs."""

    def __init__(self):
        self.infer_pagination = dspy.ChainOfThought(
            "api_docs_snippet, known_endpoints -> pagination_config: PaginationConfig"
        )
        self.infer_envelope = dspy.ChainOfThought(
            "sample_response_json -> response_path: str, error_path: str"
        )
        self.generate_examples = dspy.ChainOfThought(
            "endpoint_spec -> example_request: dict, example_response: dict"
        )
```

### 4.5 Stub Generation

The final compilation step: `ConnectorCIR → Python stub string`. This is what the LLM sees in its context.

**Design principles for LLM-optimized stubs:**

1. **Typed, not schemaful** — Pydantic models for request/response, not raw JSON Schema. LLMs generate fewer errors against type hints than against schema objects.
2. **Docstrings over comments** — the docstring IS the prompt. Each method's docstring is a minimal, complete specification.
3. **Flat, not nested** — one class per connector with methods, not deep inheritance hierarchies.
4. **Example-bearing** — each method includes a `# Example:` block showing a minimal working call.
5. **Auth-invisible** — no auth parameters in method signatures. The broker handles it.
6. **Pagination-invisible** — all list endpoints return `AsyncIterator[T]`. The runtime paginates.
7. **Error-uniform** — all methods raise `ConnectorError(status, code, message, retry_after)`.

```python
# GENERATED STUB — twitter_x.pyi
# Do not edit. Regenerate with: uastl compile twitter_x

from uastl.types import ConnectorError, PageIterator
from pydantic import BaseModel
from datetime import datetime

class Tweet(BaseModel):
    id: str
    text: str
    author_id: str
    created_at: datetime
    public_metrics: "TweetMetrics | None" = None

class TweetMetrics(BaseModel):
    like_count: int
    retweet_count: int
    reply_count: int
    impression_count: int

class CreateTweetRequest(BaseModel):
    text: str
    reply_to: str | None = None
    media_ids: list[str] | None = None
    poll_options: list[str] | None = None
    poll_duration_minutes: int | None = None

class TwitterX:
    """Twitter/X API v2 connector.

    All methods are pre-authenticated. Pagination is automatic.
    Rate limits are handled with exponential backoff.
    """

    async def create_tweet(self, req: CreateTweetRequest) -> Tweet:
        """Post a new tweet.

        POST /2/tweets
        Scopes: tweet.write, users.read

        Example:
            tweet = await twitter.create_tweet(
                CreateTweetRequest(text="Hello from OpenCompany!")
            )
            print(tweet.id)  # "1849573028375..."
        """
        ...

    async def get_tweet(self, tweet_id: str, 
                        fields: list[str] | None = None) -> Tweet:
        """Retrieve a single tweet by ID.

        GET /2/tweets/{id}
        Scopes: tweet.read

        Example:
            tweet = await twitter.get_tweet("1849573028375")
            print(tweet.text, tweet.public_metrics.like_count)
        """
        ...

    async def search_recent(self, query: str, 
                            max_results: int = 100) -> PageIterator[Tweet]:
        """Search tweets from the last 7 days.

        GET /2/tweets/search/recent
        Scopes: tweet.read
        Pagination: automatic cursor-based

        Example:
            async for tweet in twitter.search_recent("trending reels"):
                print(tweet.text)
        """
        ...

    async def get_user_timeline(self, user_id: str,
                                max_results: int = 100) -> PageIterator[Tweet]:
        """Get tweets from a user's timeline.

        GET /2/users/{id}/tweets
        Scopes: tweet.read, users.read

        Example:
            async for tweet in twitter.get_user_timeline("12345"):
                print(tweet.created_at, tweet.text)
        """
        ...
```

```python
# GENERATED STUB — instagram.pyi

from uastl.types import ConnectorError, PageIterator
from pydantic import BaseModel

class IGMedia(BaseModel):
    id: str
    media_type: str          # "IMAGE", "VIDEO", "CAROUSEL_ALBUM"
    media_url: str
    caption: str | None = None
    timestamp: str
    like_count: int | None = None
    comments_count: int | None = None
    permalink: str

class Instagram:
    """Instagram Graph API connector.

    All methods are pre-authenticated via Instagram Business account OAuth2.
    Rate limit: 200 calls/user/hour — handled automatically.
    """

    async def search_hashtag(self, hashtag: str) -> str:
        """Find the hashtag ID for a given hashtag name.

        GET /ig_hashtag_search?q={hashtag}

        Example:
            hashtag_id = await instagram.search_hashtag("trending")
            # Returns: "17843853986012965"
        """
        ...

    async def get_top_media(self, hashtag_id: str,
                            fields: list[str] | None = None) -> PageIterator[IGMedia]:
        """Get top media for a hashtag.

        GET /{hashtag_id}/top_media

        Example:
            async for media in instagram.get_top_media("17843853986012965"):
                if media.media_type == "VIDEO":  # reels
                    print(media.caption, media.like_count)
        """
        ...
```

### 4.6 Stub Budget and Retrieval

Full stubs for 700 connectors would consume ~200k+ tokens. The system uses **retrieval** instead:

```python
class StubIndex:
    """Vector index over connector stubs for top-k retrieval."""

    def __init__(self, embedding_model: str = "text-embedding-3-small"):
        self.index: dict[str, StubEntry] = {}

    def add_connector(self, cir: ConnectorCIR, stub: str):
        """Index a connector with embedding of:
        - name + display_name + categories (weighted 2x)
        - all endpoint summaries
        - all model names
        """
        ...

    def retrieve(self, task: str, budget_tokens: int = 4000, 
                 max_connectors: int = 3) -> list[str]:
        """Retrieve top-k connector stubs that fit within token budget.

        Strategy:
        1. Embed task description
        2. Cosine similarity against connector embeddings
        3. For top-k: include full stub if within budget, else truncate
           to class signature + relevant methods only
        4. Return list of stub strings ready for prompt injection
        """
        ...
```

**Token budget allocation** (for a 200k context window):

| Component | Budget | Notes |
|---|---|---|
| System prompt + instructions | ~4k | Fixed |
| Retrieved connector stubs | ~4k | 2-3 connectors, methods relevant to task |
| Task description + examples | ~2k | From GEPA-optimized few-shot demos |
| REPL history (RLM mode) | ~8k | Truncated, rolling window |
| Code generation space | ~4k | LLM output budget |
| Safety margin | ~2k | Overhead |
| **Total active context** | **~24k** | Well within limits |

---

## 5. Code Generation Engine

### 5.1 DSPy Module Architecture

The code generation engine is a composed DSPy module:

```python
import dspy
from dspy.predict import CodeAct

class TaskDecomposer(dspy.Signature):
    """Decompose a digital task into ordered steps, each mapped to a connector."""
    task: str = dspy.InputField(desc="Natural language task description")
    available_connectors: str = dspy.InputField(desc="Comma-separated connector names")
    steps: list[str] = dspy.OutputField(desc="Ordered list of atomic steps")
    connectors_needed: list[str] = dspy.OutputField(desc="Connector name for each step")

class CodeGenerator(dspy.Signature):
    """Generate Python code using UniversalConnector stubs to accomplish a step."""
    step_description: str = dspy.InputField()
    connector_stubs: str = dspy.InputField(desc="Python stub code for available connectors")
    previous_results: str = dspy.InputField(desc="Variables from prior steps, as Python dict repr")
    code: str = dspy.OutputField(desc="Executable Python code using the connector API")

class VerificationGenerator(dspy.Signature):
    """Generate verification code: assertions, screenshots, console checks."""
    action_taken: str = dspy.InputField(desc="Description of what was just done")
    expected_outcome: str = dspy.InputField()
    verification_code: str = dspy.OutputField(desc="Python code for verification")

class DigitalTaskAgent(dspy.Module):
    """Top-level module: NL task → executable Python via connector stubs."""

    def __init__(self, stub_index: StubIndex, sandbox: CodeInterpreter):
        super().__init__()
        self.decompose = dspy.ChainOfThought(TaskDecomposer)
        self.generate = dspy.ChainOfThought(CodeGenerator)
        self.verify = dspy.ChainOfThought(VerificationGenerator)
        self.stub_index = stub_index
        self.sandbox = sandbox

    def forward(self, task: str) -> dspy.Prediction:
        # 1. Retrieve relevant connector stubs
        connector_names = self._identify_connectors(task)
        stubs = self.stub_index.retrieve(task, max_connectors=3)

        # 2. Decompose task into steps
        plan = self.decompose(
            task=task,
            available_connectors=", ".join(connector_names)
        )

        # 3. Execute each step
        results = {}
        traces = []
        for i, step in enumerate(plan.steps):
            # Generate code for this step
            code_pred = self.generate(
                step_description=step,
                connector_stubs="\n\n".join(stubs),
                previous_results=repr(results)
            )

            # Execute in sandbox
            exec_result = self.sandbox.execute(code_pred.code)
            traces.append({
                "step": step,
                "code": code_pred.code,
                "stdout": exec_result.stdout,
                "stderr": exec_result.stderr,
                "return_code": exec_result.return_code
            })

            # Store results for next step
            results[f"step_{i}"] = exec_result.stdout

            # Self-debug loop: if execution failed, retry with error context
            if exec_result.return_code != 0:
                for retry in range(3):
                    debug_code = self._self_debug(
                        code_pred.code, exec_result.stderr, stubs
                    )
                    exec_result = self.sandbox.execute(debug_code)
                    if exec_result.return_code == 0:
                        break

        # 4. Generate and run verification
        verify_pred = self.verify(
            action_taken=str(plan.steps),
            expected_outcome=f"Task '{task}' completed successfully"
        )
        verify_result = self.sandbox.execute(verify_pred.verification_code)

        return dspy.Prediction(
            plan=plan.steps,
            traces=traces,
            verification=verify_result,
            success=all(t["return_code"] == 0 for t in traces)
        )
```

### 5.2 RLM Mode for Long-Context Tasks

When the task involves large data (scraping many posts, processing message archives, analyzing trends across 1000+ items), the system switches to RLM mode:

```python
class LongContextTaskAgent(dspy.Module):
    """Uses RLM pattern: data stays in REPL variables, LLM processes via code."""

    def __init__(self):
        super().__init__()
        self.rlm = dspy.RLM(
            "task: str, connectors: str -> result: str",
            max_iterations=15,
            max_output_chars=5000
        )

    def forward(self, task: str, data_source: str) -> dspy.Prediction:
        return self.rlm(
            task=task,
            connectors="""
Available in REPL globals:
  - `twitter`: TwitterX connector instance (async methods, use await)
  - `instagram`: Instagram connector instance
  - `browser`: Playwright Browser instance
  - `llm_query(prompt: str) -> str`: Call sub-LLM for semantic tasks
  - `llm_query_batched(prompts: list[str]) -> list[str]`: Batch sub-LM calls

Write Python code to accomplish the task. Data stays in REPL variables.
Use llm_query() for semantic analysis. Use FINAL_VAR(name) when done.
"""
        )
```

### 5.3 Browser Automation Integration

For tasks requiring browser interaction (posting to Twitter, checking UI state):

```python
class BrowserTaskSignature(dspy.Signature):
    """Generate Playwright Python code for browser-based tasks."""
    task: str = dspy.InputField()
    page_observation: str = dspy.InputField(
        desc="Current page: AX-tree summary + interactive element indices"
    )
    screenshot_description: str = dspy.InputField(
        desc="LLM-generated description of current screenshot"
    )
    code: str = dspy.OutputField(
        desc="Playwright Python: use page.get_by_role(), page.locator(), expect()"
    )

class BrowserAgent(dspy.Module):
    """Agent that alternates between observing and acting on a browser page."""

    def __init__(self):
        super().__init__()
        self.act = dspy.ChainOfThought(BrowserTaskSignature)

    async def execute_browser_task(self, task: str, page):
        """Execute a browser task with observation-action loop."""
        for step in range(20):  # max steps
            # Observe: get AX tree + screenshot
            ax_tree = await self._get_ax_tree(page)
            screenshot = await page.screenshot()
            description = await self._describe_screenshot(screenshot)

            # Generate action code
            pred = self.act(
                task=task,
                page_observation=ax_tree,
                screenshot_description=description
            )

            # Execute
            try:
                exec(pred.code, {"page": page, "expect": expect})
            except Exception as e:
                # Feed error back for self-correction
                continue

            # Check completion
            if await self._is_task_complete(page, task):
                # Final verification screenshot
                final_screenshot = await page.screenshot(
                    path="verification.png"
                )
                return {"success": True, "screenshot": final_screenshot}

        return {"success": False, "reason": "max_steps_exceeded"}

    async def _get_ax_tree(self, page) -> str:
        """Extract simplified accessibility tree with interactive indices."""
        snapshot = await page.accessibility.snapshot()
        return self._simplify_ax_tree(snapshot, max_depth=4)
```

---

## 6. Sandboxed Execution Runtime

### 6.1 Runtime Architecture

```python
from abc import ABC, abstractmethod

class ExecutionResult(BaseModel):
    stdout: str
    stderr: str
    return_code: int
    duration_ms: float
    http_log: list[dict] = []      # captured request/response pairs
    screenshots: list[bytes] = []   # captured screenshots
    variables: dict[str, str] = {}  # serialized REPL variable state

class SandboxRuntime(ABC):
    """Abstract sandbox interface. Implementations: E2B, Modal, Pyodide."""

    @abstractmethod
    async def execute(self, code: str) -> ExecutionResult: ...

    @abstractmethod
    async def install_packages(self, packages: list[str]) -> bool: ...

    @abstractmethod
    async def get_variable(self, name: str) -> str: ...

    @abstractmethod
    async def set_variable(self, name: str, value: str) -> None: ...

    @abstractmethod
    async def screenshot(self) -> bytes: ...

    @abstractmethod
    async def get_browser_page(self): ...
```

### 6.2 Sandbox Bootstrap

When a sandbox starts, it is pre-loaded with:

```python
SANDBOX_BOOTSTRAP = """
# === UASTL Runtime Bootstrap ===
import asyncio
import json
from uastl.runtime import UniversalConnectorProxy

# Credential broker proxy — talks to host via HTTP, never holds tokens
_broker = UniversalConnectorProxy(broker_url="http://host:9090/proxy")

# Pre-instantiated connectors (lazy-auth on first call)
twitter = _broker.get_connector("twitter_x")
instagram = _broker.get_connector("instagram")
slack = _broker.get_connector("slack")
notion = _broker.get_connector("notion")
# ... all connected connectors

# Browser (Playwright, pre-launched Chromium)
from playwright.async_api import async_playwright
_pw = await async_playwright().start()
browser = await _pw.chromium.launch(headless=True)
page = await browser.new_page()

# Sub-LLM for RLM pattern
def llm_query(prompt: str) -> str:
    \"\"\"Call sub-LM. Runs on host, not in sandbox.\"\"\"
    import requests
    resp = requests.post("http://host:9090/llm_query", json={"prompt": prompt})
    return resp.json()["result"]

def llm_query_batched(prompts: list[str]) -> list[str]:
    \"\"\"Batch sub-LM calls for efficiency.\"\"\"
    import requests
    resp = requests.post("http://host:9090/llm_query_batch", json={"prompts": prompts})
    return resp.json()["results"]

# Screenshot helper
async def take_screenshot(filename: str = "screen.png"):
    await page.screenshot(path=filename)
    return filename

# Console capture
_console_messages = []
page.on("console", lambda msg: _console_messages.append({
    "type": msg.type, "text": msg.text
}))
"""
```

### 6.3 Credential Broker (Host-Side)

The broker runs **outside** the sandbox and injects credentials at proxy time:

```python
class CredentialBroker:
    """Host-side credential proxy. The sandbox calls this via HTTP.
    Tokens never enter the sandbox or the LLM context."""

    def __init__(self, nango_secret_key: str):
        self.nango = NangoClient(secret_key=nango_secret_key)

    async def proxy_request(self, request: ProxyRequest) -> ProxyResponse:
        """
        1. Look up connection credentials for (connector, user_id)
        2. Inject auth header (Bearer, API-Key, Basic, etc.)
        3. Apply rate-limit backoff if needed
        4. Forward to upstream API
        5. Capture response for trace logging
        6. Return response to sandbox
        """
        # Get live credentials (auto-refreshed by Nango)
        connection = await self.nango.get_connection(
            provider_config_key=request.connector,
            connection_id=request.user_id
        )

        # Build upstream request
        headers = {**request.headers}
        if connection.credentials.type == "OAUTH2":
            headers["Authorization"] = f"Bearer {connection.credentials.access_token}"
        elif connection.credentials.type == "API_KEY":
            headers[connection.credentials.header_name] = connection.credentials.api_key

        # Forward with retry
        response = await self._forward_with_retry(
            method=request.method,
            url=f"{request.base_url}{request.path}",
            headers=headers,
            params=request.params,
            json=request.body
        )

        return ProxyResponse(
            status=response.status_code,
            headers=dict(response.headers),
            body=response.json()
        )
```

### 6.4 Egress Network Policy

The sandbox's network is restricted to:

```yaml
# Firecracker VM network policy
egress_allowlist:
  # Credential broker (host-side)
  - host:9090

  # Sub-LLM endpoint (host-side)
  - host:9091

  # Allowed upstream APIs (via broker only — direct blocked)
  # Direct API access is DENIED. All API calls go through the broker.

  # Package registries (for pip install)
  - pypi.org
  - files.pythonhosted.org

  # Browser targets (for Playwright)
  - "*.twitter.com"
  - "*.x.com"
  - "*.instagram.com"
  # ... per-task allowlist

# Everything else is DENIED by default
default_policy: DENY
```

---

## 7. Reflective Prompt Optimizer (GEPA Integration)

### 7.1 Metric Design

The metric is **decomposed** and returns both a score and a **textual critique**:

```python
class CodeExecutionMetric:
    """Decomposed metric for evaluating LLM-generated connector code.
    
    Returns (score: float, critique: str) where critique is the
    "Actionable Side Information" that GEPA needs for reflective mutation.
    """

    WEIGHTS = {
        "parse":     0.15,  # Does the code parse as valid Python?
        "imports":   0.10,  # Are required connector imports present?
        "calls":     0.15,  # Are connector methods called correctly?
        "types":     0.10,  # Do arguments match Pydantic model types?
        "execution": 0.30,  # Does the code execute without errors?
        "output":    0.20,  # Does the output match expected result?
    }

    def __call__(self, example, prediction, trace=None) -> tuple[float, str]:
        code = prediction.code
        critiques = []
        total_score = 0.0

        # 1. Parse check
        try:
            import ast
            ast.parse(code)
            total_score += self.WEIGHTS["parse"]
        except SyntaxError as e:
            critiques.append(f"PARSE FAIL at line {e.lineno}: {e.msg}")

        # 2. Import check
        required_imports = self._extract_required_imports(example.task)
        found_imports = self._extract_imports(code)
        missing = required_imports - found_imports
        if not missing:
            total_score += self.WEIGHTS["imports"]
        else:
            critiques.append(f"MISSING IMPORTS: {missing}")

        # 3. Call pattern check
        expected_calls = example.expected_connector_calls  # e.g. ["twitter.create_tweet"]
        found_calls = self._extract_method_calls(code)
        call_match = len(set(expected_calls) & set(found_calls)) / max(len(expected_calls), 1)
        total_score += self.WEIGHTS["calls"] * call_match
        if call_match < 1.0:
            critiques.append(
                f"CALL MISMATCH: expected {expected_calls}, found {found_calls}"
            )

        # 4. Type check (static analysis against stubs)
        type_errors = self._check_types_against_stubs(code, example.stubs)
        if not type_errors:
            total_score += self.WEIGHTS["types"]
        else:
            critiques.append(f"TYPE ERRORS: {type_errors[:3]}")  # top 3

        # 5. Execution check (sandbox dry-run with mocked APIs)
        exec_result = self._sandbox_execute(code, mock=True)
        if exec_result.return_code == 0:
            total_score += self.WEIGHTS["execution"]
        else:
            critiques.append(
                f"EXECUTION FAIL (rc={exec_result.return_code}): "
                f"{exec_result.stderr[:500]}"
            )

        # 6. Output quality (LLM judge or string match)
        if exec_result.return_code == 0 and example.expected_output:
            output_score = self._score_output(
                exec_result.stdout, example.expected_output
            )
            total_score += self.WEIGHTS["output"] * output_score
            if output_score < 0.8:
                critiques.append(
                    f"OUTPUT MISMATCH: got '{exec_result.stdout[:200]}', "
                    f"expected pattern '{example.expected_output[:200]}'"
                )

        critique = " | ".join(critiques) if critiques else "All checks passed."
        return total_score, critique
```

### 7.2 GEPA Compilation

```python
import dspy
from dspy.teleprompt import GEPA

# Configure
dspy.configure(lm=dspy.LM("anthropic/claude-sonnet-4-6"))  # task LM
reflection_lm = dspy.LM("anthropic/claude-opus-4-6")        # reflection LM (can be stronger)

# Define the agent module
agent = DigitalTaskAgent(
    stub_index=stub_index,
    sandbox=E2BSandbox()
)

# Build training set (20-60 examples)
trainset = [
    dspy.Example(
        task="Search for tweets about #AI and print the top 5 by likes",
        stubs=stub_index.retrieve("search tweets AI"),
        expected_connector_calls=["twitter.search_recent"],
        expected_output="Tweet text with like counts"
    ).with_inputs("task"),
    dspy.Example(
        task="Find top trending reels on Instagram for #travel and post a summary tweet",
        stubs=stub_index.retrieve("instagram trending reels twitter post"),
        expected_connector_calls=["instagram.search_hashtag", "instagram.get_top_media", "twitter.create_tweet"],
        expected_output="Tweet posted successfully"
    ).with_inputs("task"),
    # ... 18-58 more examples spanning task categories
]

valset = trainset[::4]  # hold out every 4th for validation

# Compile with GEPA
optimizer = GEPA(
    metric=CodeExecutionMetric(),
    num_generations=40,         # evolutionary generations
    population_size=10,         # candidates per generation
    reflection_lm=reflection_lm,
    # GEPA-specific: Pareto selection, reflective mutation, rich textual feedback
)

optimized_agent = optimizer.compile(
    agent,
    trainset=trainset,
    valset=valset,
)

# Save compiled prompts (portable across LMs)
optimized_agent.save("compiled_digital_task_agent.json")
```

### 7.3 Continuous Improvement Loop

```
Production execution
  → Trace captured (code, stdout, stderr, screenshots, HTTP log)
  → Metric evaluated (decomposed score + textual critique)
  → If score < threshold:
      → Trace added to training set
      → GEPA re-compiled periodically (nightly or on N new traces)
      → New prompts deployed
  → If score >= threshold:
      → Trace added as positive example (few-shot demo candidate)
```

---

## 8. End-to-End Example

**Task:** "Check top trending reels on Instagram for #fitness and generate a Twitter post summarizing them."

### Step 1: Stub Retrieval

The StubIndex retrieves `instagram.pyi` and `twitter_x.pyi` stubs (§4.5).

### Step 2: Task Decomposition

```python
plan = decompose(
    task="Check top trending reels on Instagram for #fitness and generate a Twitter post summarizing them",
    available_connectors="instagram, twitter_x"
)
# plan.steps = [
#   "Search Instagram for hashtag ID of 'fitness'",
#   "Fetch top media for that hashtag, filter to VIDEO (reels)",
#   "Extract captions and engagement metrics from top 5 reels",
#   "Generate a summary tweet text",
#   "Post the tweet via Twitter/X API",
#   "Verify the tweet was posted"
# ]
```

### Step 3: Code Generation (single unified block)

The LLM emits:

```python
# Step 1-2: Get trending reels
hashtag_id = await instagram.search_hashtag("fitness")

top_reels = []
async for media in instagram.get_top_media(hashtag_id):
    if media.media_type == "VIDEO":
        top_reels.append(media)
    if len(top_reels) >= 5:
        break

# Step 3: Extract data
summaries = []
for reel in top_reels:
    summaries.append(
        f"- {reel.caption[:80]}... ({reel.like_count} likes)"
    )

# Step 4: Generate tweet text
summary_text = "\n".join(summaries)
tweet_text = llm_query(
    f"Write a concise, engaging tweet (under 280 chars) summarizing "
    f"these trending #fitness reels:\n{summary_text}"
)

# Step 5: Post tweet
from uastl.stubs.twitter_x import CreateTweetRequest
tweet = await twitter.create_tweet(
    CreateTweetRequest(text=tweet_text)
)
print(f"Tweet posted: {tweet.id}")

# Step 6: Verify
assert tweet.id is not None
print(f"SUCCESS: Posted tweet {tweet.id}")
```

### Step 4: Sandbox Execution

The code runs in E2B. The broker intercepts `instagram.*` and `twitter.*` calls, injects OAuth tokens, handles pagination, and returns responses. `llm_query()` calls the host-side sub-LM.

### Step 5: Trace Capture

```json
{
  "task": "Check top trending reels on Instagram for #fitness...",
  "steps": [
    {"code": "hashtag_id = await instagram.search_hashtag('fitness')",
     "stdout": "17843853986012965", "stderr": "", "rc": 0},
    {"code": "async for media in instagram.get_top_media(...)...",
     "stdout": "5 reels found", "stderr": "", "rc": 0},
    {"code": "tweet = await twitter.create_tweet(...)",
     "stdout": "Tweet posted: 1849573028375", "stderr": "", "rc": 0}
  ],
  "http_log": [
    {"method": "GET", "url": "https://graph.instagram.com/ig_hashtag_search?q=fitness",
     "status": 200, "latency_ms": 234},
    {"method": "GET", "url": "https://graph.instagram.com/17843.../top_media",
     "status": 200, "latency_ms": 456},
    {"method": "POST", "url": "https://api.x.com/2/tweets",
     "status": 201, "latency_ms": 312}
  ],
  "metric_score": 0.95,
  "metric_critique": "All checks passed."
}
```

---

## 9. Security Model

### 9.1 Threat Model

| Threat | Mitigation |
|---|---|
| LLM exfiltrates credentials | Credentials never enter LLM context or sandbox. Broker-only access. |
| Generated code accesses unauthorized APIs | Default-deny egress allowlist at VM network layer. Only broker HTTP allowed. |
| Prompt injection via tool outputs | Tool outputs are treated as untrusted data. Plan-then-execute: outputs cannot rewrite plan. |
| Runaway cost (infinite loops, API abuse) | Per-task step budget (30), per-session token budget, per-API call budget, hard kill at 2× expected cost. |
| Sandbox escape | Firecracker microVM: separate kernel per sandbox. Hardware-level isolation. |
| Data leakage between users | Per-user sandbox instances. No shared state. Sandbox destroyed after task completion. |

### 9.2 Policy-as-Code

```python
class ActionPolicy:
    """OPA/Cedar-style policy checked before every mutating action."""

    REQUIRES_APPROVAL = {
        "twitter.create_tweet",
        "twitter.delete_tweet",
        "slack.send_message",
        "notion.create_page",
        # ... any write operation
    }

    ALWAYS_ALLOWED = {
        "twitter.search_recent",
        "instagram.get_top_media",
        "instagram.search_hashtag",
        # ... all read operations
    }

    async def check(self, action: str, args: dict, user_id: str) -> PolicyDecision:
        if action in self.ALWAYS_ALLOWED:
            return PolicyDecision.ALLOW
        if action in self.REQUIRES_APPROVAL:
            # Check if user has pre-approved this action type
            if await self._user_has_standing_approval(user_id, action):
                return PolicyDecision.ALLOW
            return PolicyDecision.REQUIRE_HUMAN_APPROVAL
        return PolicyDecision.DENY
```

---

## 10. Schema Registry Operations

### 10.1 Adding a New Connector

```bash
# From OpenAPI spec
uastl add-connector \
  --name "notion" \
  --source openapi \
  --spec-url "https://raw.githubusercontent.com/.../notion-openapi.json" \
  --auth-mode oauth2 \
  --base-url "https://api.notion.com" \
  --pagination-type cursor \
  --pagination-cursor-path "next_cursor" \
  --pagination-cursor-param "start_cursor" \
  --response-path "results"

# From Nango providers.yaml entry
uastl add-connector \
  --name "hubspot" \
  --source nango \
  --nango-provider-key "hubspot"

# Auto-discovery: scrape docs + LLM enrichment
uastl add-connector \
  --name "linear" \
  --source auto \
  --docs-url "https://developers.linear.app/docs"
```

### 10.2 Connector Versioning

```python
class ConnectorVersion(BaseModel):
    connector: str
    version: str                    # semver
    cir_hash: str                   # SHA256 of serialized CIR
    stub_hash: str                  # SHA256 of generated stub
    compiled_at: datetime
    breaking_changes: list[str]     # e.g. ["removed endpoint GET /v1/old"]
    migration_notes: str | None
```

When a connector's upstream API changes:
1. Re-parse spec → new CIR
2. Diff against previous CIR → detect breaking changes
3. Re-generate stub
4. Re-index in StubIndex
5. Flag affected GEPA training examples for re-evaluation

---

## 11. Connector Categories and Task Templates

### 11.1 Category-Specific Patterns

```python
CONNECTOR_CATEGORIES = {
    "social": {
        "common_operations": ["post", "search", "get_timeline", "get_trending"],
        "browser_fallback": True,    # some operations need browser (e.g., reels)
        "rate_limit_sensitivity": "high",
    },
    "crm": {
        "common_operations": ["create_contact", "update_deal", "search", "list"],
        "browser_fallback": False,
        "rate_limit_sensitivity": "medium",
    },
    "productivity": {
        "common_operations": ["create_task", "update_status", "list_items", "search"],
        "browser_fallback": False,
        "rate_limit_sensitivity": "low",
    },
    "communication": {
        "common_operations": ["send_message", "list_channels", "search_messages"],
        "browser_fallback": False,
        "rate_limit_sensitivity": "medium",
    },
}
```

### 11.2 Task Templates

Pre-compiled task patterns that the GEPA optimizer can specialize:

```python
TASK_TEMPLATES = {
    "cross_post": {
        "description": "Read from source platform, transform, post to target",
        "pattern": "read(source) → transform(llm) → write(target) → verify",
        "connectors_needed": 2,
        "requires_browser": False,
    },
    "trend_analysis": {
        "description": "Aggregate trending data, analyze, report",
        "pattern": "search(source) → collect(paginate) → analyze(llm) → format",
        "connectors_needed": 1,
        "requires_browser": False,
        "prefers_rlm": True,  # potentially large data
    },
    "browser_action": {
        "description": "Navigate, interact, verify via browser",
        "pattern": "navigate(url) → observe(ax_tree+screenshot) → act(click/type) → verify(screenshot)",
        "connectors_needed": 0,
        "requires_browser": True,
    },
    "multi_step_workflow": {
        "description": "Chain multiple API operations with data dependencies",
        "pattern": "step1(api_a) → transform → step2(api_b) → step3(api_c) → verify",
        "connectors_needed": "2+",
        "requires_browser": False,
    },
}
```

---

## 12. Implementation Roadmap

### Phase 1: Foundation (Weeks 1-4)

- [ ] Implement `ConnectorCIR` Pydantic models
- [ ] Build `OpenAPIParser` and `NangoProviderParser`
- [ ] Stub generator: CIR → Python `.pyi` with typed models and docstrings
- [ ] Manual stubs for 5 connectors: Twitter/X, Instagram, Slack, Notion, GitHub
- [ ] StubIndex with simple embedding-based retrieval
- [ ] Basic E2B sandbox integration with bootstrap script

### Phase 2: Code Generation (Weeks 5-8)

- [ ] DSPy module: `DigitalTaskAgent` with decompose → generate → execute loop
- [ ] Credential Broker: host-side proxy with Nango integration
- [ ] Self-debug loop (3 retries with error feedback)
- [ ] Playwright browser integration in sandbox
- [ ] Screenshot capture and AX-tree extraction
- [ ] 20 training examples across task templates

### Phase 3: Optimization (Weeks 9-12)

- [ ] `CodeExecutionMetric` with decomposed scoring and textual critique
- [ ] GEPA compilation pipeline: train → optimize → evaluate → deploy
- [ ] RLM integration for long-context tasks
- [ ] Continuous improvement loop (trace → retrain)
- [ ] A/B testing framework for prompt versions

### Phase 4: Scale (Weeks 13-16)

- [ ] Auto-discovery pipeline: docs URL → OpenAPI spec → CIR → stub
- [ ] Scale to 50+ connectors
- [ ] Connector versioning and breaking-change detection
- [ ] Policy-as-code enforcement (OPA integration)
- [ ] Production monitoring: trace SIEM, cost tracking, success rate dashboards

---

## 13. Open Questions

1. **Stub granularity:** Should stubs include ALL endpoints or only the top-20 most-used per connector? Full stubs improve coverage but cost tokens. Current design favors retrieval-based selection of relevant methods only.

2. **Mock vs. live execution during GEPA training:** Mock APIs are faster and cheaper but don't catch real-world edge cases (rate limits, auth failures, schema drift). Recommendation: mock for initial compilation, live for periodic re-evaluation.

3. **Browser vs. API for social media:** Instagram's official API is limited (no reels trending endpoint for most apps). Should the system prefer browser automation for discovery + API for posting? Current design: hybrid, with `browser_fallback: True` per category.

4. **Connector stub language:** Python stubs are chosen because the LLM generates Python. But Nango's integration code is TypeScript. Should UASTL compile to both? Current design: Python-only, with TypeScript as a future extension.

5. **Multi-user credential isolation:** When multiple users connect the same connector, the broker must correctly route `user_id → connection`. This is handled by Nango's `connectionId` but needs careful integration with the sandbox's per-user isolation.

---

## 14. References

- **CodeAct:** Wang et al., "Executable Code Actions Elicit Better LLM Agents," ICML 2024. arXiv:2402.01030
- **RLM:** Zhang, Kraska, Khattab, "Recursive Language Models," MIT CSAIL, 2025. alexzhang13.github.io/blog/2025/rlm/
- **GEPA:** Agrawal et al., "Reflective Prompt Evolution Can Outperform Reinforcement Learning," ICLR 2026 Oral. arXiv:2507.19457
- **DSPy:** Khattab et al., Stanford NLP. github.com/stanfordnlp/dspy
- **Nango:** NangoHQ. github.com/NangoHQ/nango — `providers.yaml` schema, proxy architecture, unified auth
- **SWE-Agent:** Yang et al., "Agent-Computer Interfaces Enable Automated Software Engineering," NeurIPS 2024. arXiv:2405.15793
- **OpenHands:** Wang et al., "OpenHands: An Open Platform for AI Software Developers as Generalist Agents," ICLR 2025.
- **browser-use:** github.com/browser-use/browser-use — DOM service, AX-tree indexing, screenshot observation
- **E2B:** e2b.dev — Firecracker microVM sandbox for AI agents
- **Gorilla:** Patil et al., "Gorilla: Large Language Model Connected with Massive APIs." arXiv:2305.15334

---

*End of RFC-0001.*
