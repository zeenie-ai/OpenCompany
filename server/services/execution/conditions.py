"""Condition evaluation for runtime conditional branching.

Evaluates edge conditions against node outputs to determine
which paths to follow in a workflow (Prefect-style dynamic branching).

Supported operators:
- eq: Equal (==)
- neq: Not equal (!=)
- gt: Greater than (>)
- lt: Less than (<)
- gte: Greater than or equal (>=)
- lte: Less than or equal (<=)
- contains: String/list contains value
- not_contains: String/list does not contain value
- exists: Field exists and is not None
- not_exists: Field does not exist or is None
- is_empty: Field is empty (None, "", [], {})
- is_not_empty: Field is not empty
- matches: Regex pattern match
- in: Value is in list
- not_in: Value is not in list
- starts_with: String starts with value
- ends_with: String ends with value
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List

from core.logging import get_logger

logger = get_logger(__name__)


# Type alias for condition dict
ConditionDict = Dict[str, Any]


def get_nested_value(data: Dict[str, Any], field_path: str) -> Any:
    """Get a nested value from a dictionary using dot notation.

    Args:
        data: Dictionary to extract value from
        field_path: Dot-separated path (e.g., "result.status", "items.0.name")

    Returns:
        Value at path or None if not found

    Examples:
        >>> get_nested_value({"result": {"status": "ok"}}, "result.status")
        "ok"
        >>> get_nested_value({"items": [{"name": "a"}]}, "items.0.name")
        "a"
    """
    if not data or not field_path:
        return None

    parts = field_path.split(".")
    current = data

    for part in parts:
        if current is None:
            return None

        # Handle array index
        if part.isdigit():
            index = int(part)
            if isinstance(current, (list, tuple)) and 0 <= index < len(current):
                current = current[index]
            else:
                return None
        # Handle dict key
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None

    return current


def evaluate_condition(condition: ConditionDict, output: Dict[str, Any]) -> bool:
    """Evaluate an edge condition against node output.

    Args:
        condition: Condition dict with field, operator, value
            {
                "field": "status",           # Output field to check
                "operator": "eq",            # Comparison operator
                "value": "success"           # Value to compare against
            }
        output: Node execution output dict

    Returns:
        True if condition matches, False otherwise
    """
    if not condition:
        return True  # No condition = always follow

    field = condition.get("field", "")
    operator = condition.get("operator", "eq")
    target_value = condition.get("value")

    # Get the actual value from output
    actual_value = get_nested_value(output, field)

    logger.debug("Evaluating condition", field=field, operator=operator, target=target_value, actual=actual_value)

    try:
        result = _evaluate_operator(operator, actual_value, target_value)
        logger.debug("Condition result", result=result)
        return result
    except Exception as e:
        logger.warning("Condition evaluation error", field=field, operator=operator, error=str(e))
        return False


# Fields that name the node-result envelope rather than the node's own data.
# Temporal keeps each node's whole activity result (``{success, result, ...}``)
# and evaluates edge conditions against it, so a condition written for the
# shipped path reads ``result.<field>`` (tests/temporal/test_conditional_edges.py
# locks that). The in-process executor and the sequential fallback keep only
# the inner result, so the same condition used to miss there.
ENVELOPE_FIELDS = frozenset({"success", "result"})


def evaluate_edge_condition(
    condition: ConditionDict,
    *,
    envelope: Dict[str, Any],
    inner: Any,
) -> bool:
    """Evaluate an edge condition the same way on every execution path.

    ``success``, ``result`` and ``result.*`` are read from the envelope, which
    is what Temporal evaluates against; every other field is read from the
    node's own result. The rule is decided by the field alone, never by trying
    both, so a path cannot silently change which value a condition sees.

    Args:
        condition: Condition dict with field, operator, value.
        envelope: The ``{success, result}`` envelope of the source node, or an
            empty dict when the source did not complete (skipped or failed).
        inner: The source node's own result.
    """
    if not condition:
        return True
    field = str(condition.get("field") or "")
    head = field.split(".", 1)[0]
    source = envelope if head in ENVELOPE_FIELDS else inner
    return evaluate_condition(condition, source)


def _evaluate_operator(operator: str, actual: Any, target: Any) -> bool:
    """Evaluate a single operator.

    Args:
        operator: Operator name
        actual: Actual value from output
        target: Target value to compare

    Returns:
        Comparison result
    """
    # Equality operators
    if operator == "eq":
        return _loose_eq(actual, target)

    elif operator == "neq":
        return not _loose_eq(actual, target)

    # Comparison operators (numeric)
    elif operator == "gt":
        return _safe_compare(actual, target, lambda a, b: a > b)

    elif operator == "lt":
        return _safe_compare(actual, target, lambda a, b: a < b)

    elif operator == "gte":
        return _safe_compare(actual, target, lambda a, b: a >= b)

    elif operator == "lte":
        return _safe_compare(actual, target, lambda a, b: a <= b)

    # String/list contains
    elif operator == "contains":
        if actual is None:
            return False
        if isinstance(actual, str):
            return str(target) in actual
        elif isinstance(actual, (list, tuple)):
            return target in actual
        elif isinstance(actual, dict):
            return target in actual
        return False

    elif operator == "not_contains":
        return not _evaluate_operator("contains", actual, target)

    # Existence checks
    elif operator == "exists":
        return actual is not None

    elif operator == "not_exists":
        return actual is None

    # Empty checks
    elif operator == "is_empty":
        if actual is None:
            return True
        if isinstance(actual, (str, list, dict, tuple)):
            return len(actual) == 0
        return False

    elif operator == "is_not_empty":
        return not _evaluate_operator("is_empty", actual, target)

    # Regex match
    elif operator == "matches":
        if actual is None or target is None:
            return False
        try:
            return bool(re.search(str(target), str(actual)))
        except re.error:
            logger.warning("Invalid regex pattern", pattern=target)
            return False

    # List membership
    elif operator == "in":
        if not isinstance(target, (list, tuple)):
            return actual == target
        return actual in target

    elif operator == "not_in":
        return not _evaluate_operator("in", actual, target)

    # String prefix/suffix
    elif operator == "starts_with":
        if actual is None or target is None:
            return False
        return str(actual).startswith(str(target))

    elif operator == "ends_with":
        if actual is None or target is None:
            return False
        return str(actual).endswith(str(target))

    # Boolean checks
    elif operator == "is_true":
        return actual is True or actual == "true" or actual == 1

    elif operator == "is_false":
        return actual is False or actual == "false" or actual == 0

    # Type checks
    elif operator == "is_string":
        return isinstance(actual, str)

    elif operator == "is_number":
        return isinstance(actual, (int, float)) and not isinstance(actual, bool)

    elif operator == "is_boolean":
        return isinstance(actual, bool)

    elif operator == "is_array":
        return isinstance(actual, (list, tuple))

    elif operator == "is_object":
        return isinstance(actual, dict)

    else:
        logger.warning("Unknown operator", operator=operator)
        return False


def _loose_eq(actual: Any, target: Any) -> bool:
    """Equality that bridges the editor's string target to a numeric output.

    The edge-condition editor types ``eq``/``neq`` targets as ``any`` and stores
    the raw input, so a numeric comparison arrives as ``"200"`` while the node
    output holds ``200``. Strict ``==`` makes that permanently false, which is
    indistinguishable from a mis-typed field name. The ordering operators
    already coerce via :func:`_safe_compare`; this brings equality in line.

    **The coercion is deliberately narrow: exactly one side must be a string
    and the other a real number.** Coercing string-vs-string looks harmless and
    is not -- ``float()`` rounds past 2**53, so two *distinct* 18-digit
    identifiers (WhatsApp group JIDs, snowflake ids, ns timestamps) would
    compare equal and silently take each other's branch. Two strings are
    compared as strings, which is what the user wrote.

    :class:`~decimal.Decimal` rather than ``float`` for the same reason: it is
    exact, and it swallows arbitrarily large ints instead of raising
    ``OverflowError`` (which is outside the caught tuple, so it would escape and
    make ``neq`` answer False for a pair that is plainly unequal).

    Booleans, ``None`` and every other type need no special case: they either
    satisfy plain ``==`` or fail the text/number guard, and ``Decimal("True")``
    is simply invalid. Note that ``eq 1`` still matches a ``True`` output --
    ``1 == True`` in Python, short-circuiting on the first line. That predates
    loose equality; tightening it here would be an unrelated behaviour change.
    """
    if actual == target:
        return True

    numeric = (int, float, Decimal)
    bridges_text_to_number = (isinstance(actual, str) and isinstance(target, numeric)) or (
        isinstance(target, str) and isinstance(actual, numeric)
    )
    if not bridges_text_to_number:
        return False

    try:
        return Decimal(str(actual)) == Decimal(str(target))
    except (InvalidOperation, ValueError, TypeError):
        return False


def _safe_compare(actual: Any, target: Any, comparator) -> bool:
    """Safely compare two values, handling type coercion.

    Args:
        actual: Actual value
        target: Target value
        comparator: Comparison function

    Returns:
        Comparison result, False if comparison impossible
    """
    if actual is None or target is None:
        return False

    # Try numeric comparison first
    try:
        return comparator(float(actual), float(target))
    except (ValueError, TypeError):
        pass

    # Fall back to string comparison
    try:
        return comparator(str(actual), str(target))
    except (ValueError, TypeError):
        return False


def evaluate_conditions(conditions: List[ConditionDict], output: Dict[str, Any], logic: str = "and") -> bool:
    """Evaluate multiple conditions with AND/OR logic.

    Args:
        conditions: List of condition dicts
        output: Node execution output
        logic: "and" (all must match) or "or" (any must match)

    Returns:
        Combined evaluation result
    """
    if not conditions:
        return True

    results = [evaluate_condition(c, output) for c in conditions]

    if logic == "or":
        return any(results)
    else:  # "and"
        return all(results)


def decide_next_edges(edges: List[Dict[str, Any]], source_node_id: str, output: Dict[str, Any]) -> List[str]:
    """Determine which edges to follow based on conditions.

    Args:
        edges: All edges in workflow
        source_node_id: ID of the node that just completed
        output: Output from the completed node

    Returns:
        List of target node IDs to execute next
    """
    next_nodes = []
    unconditional_edges = []
    conditional_edges = []

    # Separate edges by type
    for edge in edges:
        if edge.get("source") != source_node_id:
            continue

        condition = edge.get("data", {}).get("condition")
        if condition:
            conditional_edges.append(edge)
        else:
            unconditional_edges.append(edge)

    # If there are conditional edges, evaluate them
    if conditional_edges:
        for edge in conditional_edges:
            condition = edge.get("data", {}).get("condition")
            if evaluate_condition(condition, output):
                next_nodes.append(edge["target"])
                logger.info("Conditional edge matched", source=source_node_id, target=edge["target"], condition=condition)

        # If no conditional edges matched, fall through to unconditional
        if not next_nodes and unconditional_edges:
            logger.info("No conditional edges matched, using unconditional", source=source_node_id)
            for edge in unconditional_edges:
                next_nodes.append(edge["target"])
    else:
        # No conditions - follow all unconditional edges
        for edge in unconditional_edges:
            next_nodes.append(edge["target"])

    return next_nodes


# Operator metadata for frontend UI
OPERATORS = {
    "eq": {"label": "Equals", "description": "Value equals target", "requires_value": True},
    "neq": {"label": "Not Equals", "description": "Value does not equal target", "requires_value": True},
    "gt": {"label": "Greater Than", "description": "Value is greater than target", "requires_value": True},
    "lt": {"label": "Less Than", "description": "Value is less than target", "requires_value": True},
    "gte": {"label": "Greater or Equal", "description": "Value is greater than or equal to target", "requires_value": True},
    "lte": {"label": "Less or Equal", "description": "Value is less than or equal to target", "requires_value": True},
    "contains": {"label": "Contains", "description": "String/list contains value", "requires_value": True},
    "not_contains": {"label": "Does Not Contain", "description": "String/list does not contain value", "requires_value": True},
    "exists": {"label": "Exists", "description": "Field exists and is not null", "requires_value": False},
    "not_exists": {"label": "Does Not Exist", "description": "Field does not exist or is null", "requires_value": False},
    "is_empty": {"label": "Is Empty", "description": "Value is empty (null, '', [], {})", "requires_value": False},
    "is_not_empty": {"label": "Is Not Empty", "description": "Value is not empty", "requires_value": False},
    "matches": {"label": "Matches Regex", "description": "Value matches regex pattern", "requires_value": True},
    "in": {"label": "In List", "description": "Value is in list", "requires_value": True},
    "not_in": {"label": "Not In List", "description": "Value is not in list", "requires_value": True},
    "starts_with": {"label": "Starts With", "description": "String starts with value", "requires_value": True},
    "ends_with": {"label": "Ends With", "description": "String ends with value", "requires_value": True},
    "is_true": {"label": "Is True", "description": "Value is truthy", "requires_value": False},
    "is_false": {"label": "Is False", "description": "Value is falsy", "requires_value": False},
}


def get_available_operators() -> Dict[str, Dict[str, Any]]:
    """Get operator metadata for frontend UI."""
    return OPERATORS.copy()
