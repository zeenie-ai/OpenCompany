"""Shipped skill examples must use real schemas, without executing external tools."""

from __future__ import annotations

import json
from pathlib import Path
import re

from markdown_it import MarkdownIt
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "server" / "skills"
PARSER = MarkdownIt("commonmark")


def test_active_skill_frontmatter_names_are_valid_and_unique():
    names = {}
    files = sorted(SKILLS.rglob("SKILL.md"))
    assert files, "An empty shipped skill tree cannot pass metadata coverage"
    for path in files:
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
        assert match, f"{path.relative_to(ROOT)} needs YAML frontmatter"
        fields = yaml.safe_load(match.group(1))
        assert isinstance(fields, dict), str(path)
        name, description = fields.get("name"), fields.get("description")
        assert isinstance(name, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name), str(path)
        assert isinstance(description, str) and description.strip(), str(path)
        assert name not in names, f"Duplicate skill name {name}: {path} and {names.get(name)}"
        names[name] = path


def test_runtime_v1_skill_bodies_are_active_overrides_and_preserve_user_copies():
    from services.skill_defaults import _VARIANTS, v1_default
    from services.skill_loader import Skill, SkillMetadata

    declared = {SKILLS / relative / "SKILL.v1.md" for relative in _VARIANTS.values()}
    assert declared == set(SKILLS.rglob("SKILL.v1.md")), "Versioned defaults must be inventoried explicitly"
    for name, relative in _VARIANTS.items():
        directory = SKILLS / relative
        assert (directory / "SKILL.md").is_file()
        body = (directory / "SKILL.v1.md").read_text(encoding="utf-8")
        assert body.strip(), f"Empty runtime-v1 default: {name}"
        original = Skill(SkillMetadata(name=name, description="fixture", path=directory), "legacy body")
        selected = v1_default(name, original)
        assert selected.instructions == body and original.instructions == "legacy body"
        customized = Skill(SkillMetadata(name=name, description="fixture", path=ROOT / "user-skills" / name), "owner's body")
        assert v1_default(name, customized) is customized


def _fences(path: Path, language: str):
    return [token.content for token in PARSER.parse(path.read_text(encoding="utf-8"))
            if token.type == "fence" and token.info.strip() == language]


def _argument_fences(path: Path):
    """Responses can be illustrative JSON; only input sections define arguments."""
    tokens = PARSER.parse(path.read_text(encoding="utf-8"))
    headings = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            level = int(token.tag[1:])
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, tokens[index + 1].content.lower()))
        if token.type == "fence" and token.info.strip() == "json":
            if not any(re.search(r"\b(response|responses|output|error response)\b", title) for _, title in headings):
                yield token.content


_INPUT_SKILLS = {
    "assistant/memory-skill/SKILL.md": "simpleMemory",
    "terminal/process-manager-skill/SKILL.md": "processManager",
    "coding_agent/python-skill/SKILL.md": "pythonExecutor",
    "web_agent/crawlee-scraper-skill/SKILL.md": "crawleeScraper",
    "social_agent/twitter-send-skill/SKILL.md": "twitterSend",
    "travel_agent/nearby-places-skill/SKILL.md": "gmaps_nearby_places",
    "travel_agent/geocoding-skill/SKILL.md": "gmaps_locations",
    "web_agent/http-request-skill/SKILL.md": "httpRequest",
    "web_agent/brave-search-skill/SKILL.md": "braveSearch",
    "web_agent/serper-search-skill/SKILL.md": "serperSearch",
    "web_agent/perplexity-search-skill/SKILL.md": "perplexitySearch",
    "web_agent/duckduckgo-search-skill/SKILL.md": "duckduckgoSearch",
    "web_agent/apify-skill/SKILL.md": "apifyActor",
    "web_agent/treg-skill/SKILL.md": "tregAction",
}
# Output examples are deliberately not interpreted as tool arguments.
_RESPONSE_KEYS = {"success", "error", "results", "result_count", "status", "output", "console_output", "proxied"}


def _validate_input(model, payload, context):
    unknown = payload.keys() - model.model_fields.keys()
    assert not unknown, f"{context}: unknown input keys {sorted(unknown)}"
    # Several real Params models ignore extras, so the explicit key check above
    # must happen before Pydantic validation (the old process `text` typo passed).
    model.model_validate(payload)


@pytest.mark.parametrize("relative,node_type", _INPUT_SKILLS.items(), ids=_INPUT_SKILLS.keys())
def test_selected_skill_argument_examples_match_actual_schema(relative, node_type):
    import nodes  # noqa: F401 -- register actual plugin contracts, no services
    from services.node_registry import registered_node_classes

    cls = registered_node_classes()[node_type]
    model = getattr(cls, "ToolInput", cls.Params)
    examples = []
    for code in _argument_fences(SKILLS / relative):
        payload = json.loads(code)
        if isinstance(payload, dict) and not (_RESPONSE_KEYS & payload.keys()):
            examples.append(payload)
    assert examples, f"{relative} needs at least one executable argument example"
    for index, payload in enumerate(examples, 1):
        _validate_input(model, payload, f"{relative}, input {index}")


def test_proxy_skill_wrapped_call_examples_match_actual_schema():
    from services.node_registry import registered_node_classes

    classes = {cls.tool_name: cls for cls in registered_node_classes().values()
               if cls.__module__.startswith("nodes.") and getattr(cls, "tool_name", None)}
    examples = []
    for token in PARSER.parse((SKILLS / "web_agent/proxy-config-skill/SKILL.md").read_text(encoding="utf-8")):
        if token.type != "fence":
            continue
        match = re.fullmatch(r"(proxy_config|proxy_request|proxy_status)\((\{.*\})\)\s*", token.content.strip(), re.S)
        if match:
            name, encoded = match.groups()
            cls = classes[name]
            _validate_input(getattr(cls, "ToolInput", cls.Params), json.loads(encoded), f"proxy skill {name}")
            examples.append(name)
    assert {"proxy_config", "proxy_request"} <= set(examples)


@pytest.mark.asyncio
async def test_python_skill_examples_execute_in_the_actual_restricted_sandbox():
    from nodes.code._base import CodeExecutorParams
    from nodes.code.python_executor import PythonExecutorNode
    from services.plugin import NodeContext

    examples = [json.loads(code)["code"] for code in _fences(SKILLS / "coding_agent/python-skill/SKILL.md", "json")
                if isinstance(json.loads(code), dict) and "code" in json.loads(code)]
    examples.extend(_fences(SKILLS / "autonomous/code-mode-skill/SKILL.md", "python"))
    examples.extend(_fences(SKILLS / "web_agent/proxy-config-skill/SKILL.md", "python"))
    assert examples
    node = PythonExecutorNode()
    context = NodeContext(node_id="skill-doc-test", node_type="pythonExecutor")
    for index, code in enumerate(examples, 1):
        try:
            result = await node.execute_op(context, CodeExecutorParams(code=code))
        except Exception as error:
            pytest.fail(f"Python skill example {index} failed in the real sandbox:\n{code}\n{error}")
        assert isinstance(result, dict) and "output" in result
