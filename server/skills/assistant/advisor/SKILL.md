---
name: advisor
description: ALWAYS call the wired chat-model tool (anthropic_chat_model / openai_chat_model / gemini_chat_model) at the START of any complex task to get a plan from a stronger model. Also call when stuck and before declaring done. Pass only the `prompt` field — the operator configured `model` and `api_key`. Returns brief tactical advice; you do the work.
allowed-tools: anthropic_chat_model openai_chat_model gemini_chat_model
metadata:
  author: opencompany
  version: "1.0"
  category: general
---

# Advisor

A chat-model wired as a tool is your **advisor** — a stronger model configured by the operator. Consult it for strategy, not implementation.

## When to call

- **AT TASK START** — before writing, editing, or committing to an interpretation, ask for an approach. Orientation (reading files, fetching sources) is not substantive work; do that first, then call advisor.
- **WHEN STUCK** — errors recurring, approach not converging, results that don't fit.
- **BEFORE DECLARING DONE** — sanity-check completeness. Make your deliverable durable first (save the file, commit the change).

On short reactive tasks dictated by tool output you just read, skip subsequent calls — the advisor adds most of its value on the first call, before the approach crystallizes.

## How to call

- One focused question per call. The advisor has no memory of prior calls.
- Pass your question in `prompt`. **Do NOT set `model` or `api_key`** — the operator configured them.
- Include relevant context inside `prompt` (what you tried, what you observed). The advisor does not see your tool calls or memory.

## How to treat the response

- Tactical guidance, not a full solution. You do the work.
- Give the advice serious weight. If a step fails empirically, surface the conflict in a follow-up call ("I tried X, you suggested Y; here's the result — which constraint breaks the tie?").
- A passing self-test is not evidence the advice is wrong.

## Operator note (configuring the advisor node)

- **Model**: pick the provider's strongest current model. As of May 2026: `claude-opus-4-7`, `gpt-5.5-pro-2026-04-23`, `gemini-3.1-pro-preview`.
- **System prompt** (optional): "You are an advisor. Brief tactical guidance only — identify pitfalls, suggest changes, validate the plan. Do NOT write full solutions."
- **Cost**: advisor models are 3-10× the executor's per-token cost. Use sparingly.
