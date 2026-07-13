"""RLM service constants."""

# OpenCompany provider -> RLM backend mapping
PROVIDER_TO_BACKEND = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "groq": "openai",
    "openrouter": "openrouter",
    "cerebras": "openai",
}

# Base URLs for OpenAI-compatible providers
PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "cerebras": "https://api.cerebras.ai/v1",
}

# Default RLM execution parameters
DEFAULT_MAX_ITERATIONS = 30
DEFAULT_MAX_DEPTH = 1
DEFAULT_VERBOSE = False
