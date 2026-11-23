"""Settings > Skills and Settings > Plugins read server content from the
client, so the two are held to each other here, reading the client files
off disk:

- the Discover folder the client names (``DISCOVER_SKILL_FOLDER`` in
  client/src/features/home/data/skills.ts) exists, is not disabled, and
  holds skills an AI employee can use as they are: owner-facing
  ``metadata.title`` and ``metadata.summary``, no tools to connect, no
  reserved name (``skill`` is the Skill tool's own entry, a
  ``*-personality`` skill replaces the whole system message), and a name no
  other folder uses;
- every starter bundle (client/src/features/home/hire/starters.json) uses
  only those skills, names apps the hire's app registry resolves, says so in
  its job (the job text is all the setup model reads), and fits the hire's
  job limit;
- every starter's own setup (its ``hire`` block, which "Hire now" sends as
  it stands through the client's ``starterHirePayload``) is read by the hire
  exactly as written, and builds with "ask me first" on without losing an
  app it names or the way it says it starts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from services.employees.apps import normalize_name, resolve_app
from services.employees.builder import SCHEDULE_TIMES, WEEKDAYS, BuildInputs, build_employee_graph
from services.employees.hire import _resolve_apps
from services.employees.hire_request import DESCRIPTION_MAX, JOB_MAX, NAME_MAX, ROLE_MAX, HireEmployeeRequest
from services.node_allowlist import is_hire_allowed

REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = REPO_ROOT / "client" / "src" / "features" / "home"
SKILLS_TS = HOME / "data" / "skills.ts"
STARTERS_JSON = HOME / "hire" / "starters.json"
SKILLS_DIR = REPO_ROOT / "server" / "skills"
ALLOWLIST = REPO_ROOT / "server" / "config" / "node_allowlist.json"


def _client(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"client source not present: {path}")
    return path.read_text(encoding="utf-8")


def _frontmatter(skill_md: Path) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{skill_md} has no frontmatter"
    return yaml.safe_load(match.group(1))


@pytest.fixture(scope="module")
def folder() -> str:
    match = re.search(r"export const DISCOVER_SKILL_FOLDER = '([^']+)';", _client(SKILLS_TS))
    assert match, "could not find DISCOVER_SKILL_FOLDER in skills.ts"
    return match.group(1)


@pytest.fixture(scope="module")
def discover_skills(folder) -> dict:
    return {path.parent.name: _frontmatter(path) for path in sorted((SKILLS_DIR / folder).glob("*/SKILL.md"))}


@pytest.fixture(scope="module")
def starters() -> list:
    return json.loads(_client(STARTERS_JSON))


def test_the_discover_folder_exists_and_is_not_disabled(folder, discover_skills):
    assert (SKILLS_DIR / folder).is_dir()
    assert discover_skills, f"no skills in server/skills/{folder}"
    disabled = json.loads(ALLOWLIST.read_text(encoding="utf-8")).get("disabled_skill_folders", [])
    assert folder not in disabled


def test_discover_skills_are_ready_for_an_employee(folder, discover_skills):
    elsewhere = {
        path.parent.name
        for path in SKILLS_DIR.glob("*/*/SKILL.md")
        if path.parent.parent.name != folder
    }
    for directory, frontmatter in discover_skills.items():
        name = frontmatter["name"]
        assert name == directory
        metadata = frontmatter.get("metadata") or {}
        assert str(metadata.get("title") or "").strip(), f"{name} has no metadata.title"
        assert str(metadata.get("summary") or "").strip(), f"{name} has no metadata.summary"
        assert "allowed-tools" not in frontmatter, f"{name} needs tools a hire may not have"
        assert name != "skill" and not name.endswith("-personality"), f"{name} is a reserved name"
        assert name not in elsewhere, f"{name} is also a skill in another folder"


def test_starters_use_discover_skills_and_name_their_apps(starters, discover_skills):
    assert starters
    ids = [starter["id"] for starter in starters]
    assert len(ids) == len(set(ids))
    for starter in starters:
        assert starter["skills"], f"{starter['id']} has no skills"
        for skill in starter["skills"]:
            assert skill in discover_skills, f"{starter['id']} uses {skill!r}, which Discover does not offer"
        assert 0 < len(starter["job"]) <= JOB_MAX
        job = normalize_name(starter["job"])
        for app_name in starter["apps"]:
            app = resolve_app(app_name, [])
            assert app is not None, f"{starter['id']}: the app registry does not know {app_name!r}"
            words = [normalize_name(word) for word in (app.name, *app.aliases)]
            assert any(word and word in job for word in words), f"{starter['id']}'s job never mentions {app.name}"


def _starter_request(starter: dict) -> HireEmployeeRequest:
    """What "Hire now" sends for a starter: its own setup, asking first."""
    hire = starter["hire"]
    return HireEmployeeRequest.model_validate(
        {
            "idempotency_key": f"starter-{starter['id']}",
            "job": starter["job"],
            "name": hire["names"][0],
            "role": hire["role"],
            "description": hire["description"],
            "apps": starter["apps"],
            "steps": hire["steps"],
            "team": hire["team"],
            "rules": {"ask_first": True, "items": []},
            "trigger": hire["trigger"],
            "sends_via": hire.get("sends_via"),
        }
    )


def test_every_starter_setup_is_read_as_written(starters):
    for starter in starters:
        hire = starter["hire"]
        request = _starter_request(starter)
        assert request.missing_identity() is None, starter["id"]
        # Nothing is cut short or swapped for a default on the way in.
        assert all(0 < len(name) <= NAME_MAX for name in hire["names"]), starter["id"]
        assert len(hire["role"]) <= ROLE_MAX and len(hire["description"]) <= DESCRIPTION_MAX, starter["id"]
        assert [
            {"title": step.title, "detail": step.detail, "role": step.role, "app": step.app} for step in request.steps
        ] == [
            {"title": step["title"], "detail": step.get("detail", ""), "role": step["role"], "app": step.get("app")}
            for step in hire["steps"]
        ], starter["id"]
        assert request.trigger is not None and request.trigger.model_dump(exclude_none=True) == hire["trigger"], starter["id"]
        assert request.sends_via == hire.get("sends_via"), starter["id"]
        if hire["trigger"]["kind"] == "schedule":
            # A time the schedule can run at, so it is never moved to another.
            assert hire["trigger"]["at"] in SCHEDULE_TIMES, starter["id"]
            if hire["trigger"]["every"] == "week":
                assert hire["trigger"]["day"] in WEEKDAYS, starter["id"]


def test_one_click_starters_keep_their_apps_while_asking_first(starters):
    """Hired as it stands with "ask me first" on, a starter must build with
    every app it names: one the builder leaves out (a tool that sends or
    spends) would promise work the employee cannot do."""
    for starter in starters:
        request = _starter_request(starter)
        named = [resolve_app(name, []) for name in starter["apps"]]
        connected = [app.id for app in named if app is not None]
        apps, unsupported = _resolve_apps(request, connected)
        assert unsupported == [], f"{starter['id']} names apps the registry does not know: {unsupported}"
        built = build_employee_graph(
            BuildInputs(
                workflow_id=f"starter-{starter['id']}",
                request=request,
                apps=apps,
                connected_app_ids=set(connected),
                allowed=is_hire_allowed,
            )
        )
        dropped = [app.name for app in apps if app.id not in built.app_ids]
        assert not dropped, f"{starter['id']} loses {dropped} while asking first: {built.warnings}"
        assert built.trigger["kind"] == request.trigger.kind, f"{starter['id']} does not start the way it says"
