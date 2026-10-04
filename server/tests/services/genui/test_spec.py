"""Checking the interface an employee shows in a chat reply
(services/genui/spec.py): the design handoff's examples pass as written;
structural problems are refused with every reason; unknown types, unreachable
elements, stray fields and props are left out and reported; expressions and
paths come back canonical; and the tool description is the catalog's,
stable byte for byte."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.genui.spec import SpecError, canonical_path, check_spec, describe_catalog, load_catalog

FIXTURES = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "chat" / "__fixtures__"
EXAMPLES = ("saturday-booking", "reply-insights", "reminders")


def fixture(name: str):
    return json.loads((FIXTURES / f"{name}.spec.json").read_text(encoding="utf-8"))


def spec(**elements):
    return {"root": "root", "state": {}, "elements": {"root": {"type": "Stack", "children": list(elements)}, **elements}}


def refused(raw) -> list:
    with pytest.raises(SpecError) as error:
        check_spec(raw)
    return error.value.problems


@pytest.mark.parametrize("name", EXAMPLES)
def test_the_handoff_examples_pass_as_written(name):
    raw = fixture(name)
    checked = check_spec(raw)
    assert checked.dropped == [] and checked.notes == []
    for element_id, element in raw["elements"].items():
        assert checked.spec["elements"][element_id] == {"props": {}, **element}
    assert checked.spec["root"] == raw["root"] and checked.spec["state"] == raw.get("state", {})


# ----- refused -----


def test_a_spec_that_is_not_one_is_refused():
    assert refused("not a spec")
    assert refused({"root": "root"})
    assert refused({"root": "root", "elements": {}})
    assert refused({"root": "../x", "elements": {"x": {"type": "Text", "props": {"text": "hi"}}}})


def test_a_root_that_is_not_an_element_is_refused():
    problems = refused({"root": "missing", "elements": {"a": {"type": "Text", "props": {"text": "hi"}}}})
    assert any("root 'missing'" in problem for problem in problems)
    problems = refused({"root": "a", "elements": {"a": {"type": "Marquee", "props": {}}}})
    assert any("known type" in problem for problem in problems)


def test_every_problem_is_listed_at_once():
    raw = spec(
        pick={"type": "Select", "props": {"label": "Service", "options": ["Only one"]}},
        note={"type": "Text", "props": {}},
        go={"type": "Button", "props": {"label": "Go"}, "on": {"press": {"action": "push"}}},
    )
    problems = refused(raw)
    assert any("'pick' (Select)" in problem and "options" in problem for problem in problems)
    assert any("'note' (Text)" in problem and "text" in problem for problem in problems)
    assert any("push is not available" in problem for problem in problems)


def test_children_must_be_elements_the_parent_may_hold():
    assert any("is not an element" in problem for problem in refused(spec(row={"type": "Row", "children": ["ghost"]})))
    raw = spec(card={"type": "Card", "props": {"title": "Details"}, "children": ["go"]}, go={"type": "Button", "props": {"label": "Go"}})
    raw["elements"]["root"]["children"] = ["card"]
    assert any("cannot hold Button" in problem for problem in refused(raw))
    assert any("cannot hold children" in problem for problem in refused(spec(t={"type": "Text", "props": {"text": "x"}, "children": ["root"]})))


def test_cycles_and_shared_children_are_refused():
    raw = {
        "root": "a",
        "elements": {"a": {"type": "Stack", "children": ["b"]}, "b": {"type": "Stack", "children": ["a"]}},
    }
    assert any("contains itself" in problem for problem in refused(raw))
    shared = spec(s1={"type": "Stack", "children": ["t"]}, s2={"type": "Stack", "children": ["t"]}, t={"type": "Text", "props": {"text": "x"}})
    shared["elements"]["root"]["children"] = ["s1", "s2"]
    assert any("two parents" in problem for problem in refused(shared))


def test_limits_are_refused():
    limits = load_catalog()["limits"]
    deep = {"root": "s0", "elements": {}}
    for depth in range(limits["max_depth"] + 1):
        deep["elements"][f"s{depth}"] = {"type": "Stack", "children": [f"s{depth + 1}"] if depth < limits["max_depth"] else []}
    assert any("nested more than" in problem for problem in refused(deep))

    many = {f"t{n}": {"type": "Text", "props": {"text": "x"}} for n in range(limits["max_elements"])}
    wide = {"root": "root", "elements": {"root": {"type": "Stack", "children": []}, **many}}
    # More children than one element may hold.
    wide["elements"]["root"]["children"] = list(many)
    assert any("children" in problem for problem in refused(wide))

    big = spec(t={"type": "Text", "props": {"text": "x" * (limits["max_bytes"] + 1)}})
    assert any("bytes" in problem for problem in refused(big))


def test_a_ui_with_too_many_elements_is_refused():
    limits = load_catalog()["limits"]
    # Two groups of texts under the root: no element holds more children than
    # it may, but together they are one more element than a UI may have.
    per_group = -(-(limits["max_elements"] - 2) // 2)
    assert per_group <= limits["max_children"]
    raw = {"root": "root", "elements": {"root": {"type": "Stack", "children": ["a", "b"]}}}
    for group in ("a", "b"):
        texts = [f"{group}{n}" for n in range(per_group)]
        raw["elements"][group] = {"type": "Stack", "children": texts}
        raw["elements"].update({text: {"type": "Text", "props": {"text": "x"}} for text in texts})
    assert len(raw["elements"]) == limits["max_elements"] + 1
    assert refused(raw) == [f"the UI has {limits['max_elements'] + 1} elements; at most {limits['max_elements']}"]
    # At the limit it passes.
    del raw["elements"][raw["elements"]["a"]["children"].pop()]
    assert len(check_spec(raw).spec["elements"]) == limits["max_elements"]


def test_prototype_paths_and_reserved_names_are_refused():
    raw = spec(f={"type": "TextField", "props": {"label": "Name", "value": {"$bindState": "/__proto__/x"}}})
    assert any("not a usable state path" in problem for problem in refused(raw))
    raw = spec(t={"type": "Text", "props": {"text": {"$state": "/a/constructor"}}})
    assert refused(raw)
    raw = spec(t={"type": "Text", "props": {"text": "hi"}, "visible": {"$state": "/prototype"}})
    assert refused(raw)
    raw = spec(go={"type": "Button", "props": {"label": "Go"}, "on": {"press": {"action": "hold", "params": {"__oc_element": "x"}}}})
    assert any("not allowed" in problem for problem in refused(raw))
    raw = spec(go={"type": "Button", "props": {"label": "Go"}, "on": {"press": {"action": "9lives"}}})
    assert any("not an action name" in problem for problem in refused(raw))
    raw = {"root": "root", "state": {"__proto__": {"x": 1}}, "elements": spec()["elements"]}
    assert any("not allowed" in problem for problem in refused(raw))


def test_only_a_bound_prop_binds_state():
    raw = spec(pick={"type": "Select", "props": {"label": {"$bindState": "/x"}, "options": ["a", "b"]}})
    assert any("only an input's bound prop" in problem for problem in refused(raw))


def test_ask_needs_its_text():
    raw = spec(go={"type": "Button", "props": {"label": "Ask"}, "on": {"press": {"action": "ask"}}})
    assert any("(ask)" in problem and "text" in problem for problem in refused(raw))
    # A text read from the state at the press counts.
    raw = spec(go={"type": "Button", "props": {"label": "Ask"}, "on": {"press": {"action": "ask", "params": {"text": {"$state": "/q"}}}}})
    assert check_spec(raw).spec["elements"]["go"]["on"]["press"]["params"] == {"text": {"$state": "/q"}}


# ----- dropped and reported -----


def test_unknown_types_and_unreachable_elements_are_left_out():
    raw = spec(t={"type": "Text", "props": {"text": "hi"}}, marquee={"type": "Marquee", "props": {}})
    raw["elements"]["orphan"] = {"type": "Text", "props": {"text": "nobody holds me"}}
    checked = check_spec(raw)
    assert set(checked.spec["elements"]) == {"root", "t"}
    assert checked.spec["elements"]["root"]["children"] == ["t"]
    assert {item["id"]: item["reason"] for item in checked.dropped} == {
        "marquee": "unknown type 'Marquee'",
        "orphan": "not reachable from the root",
    }


def test_stray_fields_props_and_repeat_expressions_are_dropped_with_a_note():
    raw = spec(
        t={
            "type": "Text",
            "props": {"text": "hi", "color": "red"},
            "watch": {"/x": {"action": "ask"}},
            "repeat": {"statePath": "/items"},
        },
        f={"type": "TextField", "props": {"label": "Name", "placeholder": {"$item": "name"}}},
    )
    checked = check_spec(raw)
    assert checked.spec["elements"]["t"] == {"type": "Text", "props": {"text": "hi"}}
    assert checked.spec["elements"]["f"]["props"] == {"label": "Name"}
    notes = " | ".join(checked.notes)
    assert "'color'" in notes and "repeat, watch" in notes and "$item" in notes


def test_expressions_and_paths_come_back_canonical():
    raw = spec(
        t={"type": "Text", "props": {"text": {"$template": "Hi ${ name/first }"}}, "visible": [{"$state": "remind"}, {"$state": "/count", "gt": 2}]},
        f={"type": "Toggle", "props": {"label": "Remind", "checked": {"$bindState": "remind//"}}},
        c={"type": "Text", "props": {"text": {"$cond": {"$state": "/a", "eq": "x"}, "$then": "yes", "$else": {"$state": "b"}}}},
    )
    checked = check_spec(raw).spec["elements"]
    assert checked["t"]["props"]["text"] == {"$template": "Hi ${/name/first}"}
    assert checked["t"]["visible"] == {"$and": [{"$state": "/remind"}, {"$state": "/count", "gt": 2}]}
    assert checked["f"]["props"]["checked"] == {"$bindState": "/remind"}
    assert checked["c"]["props"]["text"] == {"$cond": {"$state": "/a", "eq": "x"}, "$then": "yes", "$else": {"$state": "/b"}}


def test_the_input_is_not_changed():
    raw = fixture("saturday-booking")
    before = copy.deepcopy(raw)
    check_spec(raw)
    assert raw == before


def test_paths():
    assert canonical_path("/a/b", 6) == "/a/b"
    assert canonical_path("a//b/", 6) == "/a/b"
    assert canonical_path("/a~1b", 6) == "/a~1b"
    assert canonical_path("", 6) is None
    assert canonical_path("/__proto__", 6) is None
    assert canonical_path("/a/b/c", 2) is None
    assert canonical_path(7, 6) is None


# ----- the tool description -----


def test_the_description_is_the_catalogs_and_stable():
    text = describe_catalog()
    assert text == describe_catalog()
    for name in load_catalog()["components"]:
        assert f"- {name} (" in text
    assert "SlotPicker (input; binds value)" in text
    assert "Card (layout; holds Select, Toggle, TextField, Text)" in text
    assert "[ui-event]" in text
