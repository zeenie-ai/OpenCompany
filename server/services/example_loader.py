"""Example workflow loader - reuses existing database.save_workflow().

Most of the import business logic (validation, remap, requirements
extraction, credential cross-check, name conflict) lives in
``services.workflow_import`` and is shared with the WS ``import_workflow``
handler. This module is the first-launch-only convenience wrapper.
"""

import json
import logging
from typing import List, Dict, Any

from core.paths import example_workflows_dir

logger = logging.getLogger(__name__)


# Module-level alias so callers (and the workflow-validator contract
# test in ``tests/test_workflow_validator.py``) can ``glob(EXAMPLES_DIR /
# "*.json")`` without re-resolving the path on every call.
EXAMPLES_DIR = example_workflows_dir()


def get_example_workflows() -> List[Dict[str, Any]]:
    """Load all example workflow JSON files from disk."""
    examples = []
    examples_dir = example_workflows_dir()
    if not examples_dir.exists():
        logger.warning(f"Examples directory not found: {examples_dir}")
        return examples

    for file in sorted(examples_dir.glob("*.json")):
        try:
            with open(file, encoding="utf-8") as f:
                workflow = json.load(f)
                workflow["_filename"] = file.name  # Track source
                examples.append(workflow)
                logger.debug(f"Loaded example: {file.name}")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load {file}: {e}")

    return examples


async def import_examples_for_user(database) -> int:
    """Import all examples using shared ``services.workflow_import`` helpers.

    Dry-runs the workflow validator on each example, remaps node ids
    (mandatory: today's two example workflows share 6 node ids), then
    saves the workflow + per-node parameters. Errors skip the example
    (broken examples shipped on disk are bugs); warnings are logged but
    allowed through — first-launch missing credentials are expected.

    Returns the count of workflows imported.
    """
    from services.workflow_import import remap_node_ids
    from services.workflow_naming import new_workflow_id, next_available_slug
    from services.workflow_validator import validate_workflow

    examples = get_example_workflows()
    imported = 0

    for example in examples:
        # Fresh UUID for each example (pre-Wave-14 used "example_"
        # prefix + JSON id which produced ugly "example_example_workflow-..."
        # double-prefixed strings). Slug comes from the seed's display
        # name via the standard helper.
        workflow_id = new_workflow_id()
        example_name = example.get("name", "Example Workflow")
        slug = await next_available_slug(example_name, database)

        # Rewrite every node id so two examples that share ids don't
        # overwrite each other's parameters in the node_parameters table.
        nodes, edges, node_parameters = remap_node_ids(
            example.get("nodes", []),
            example.get("edges", []),
            example.get("nodeParameters", {}),
        )

        # Pre-save validation. parameters_by_id is the freshly-remapped
        # map so INVALID_PARAM check sees the configured defaults instead
        # of empty dicts.
        try:
            report = await validate_workflow(
                nodes=nodes,
                edges=edges,
                parameters_by_id=node_parameters,
            )
        except Exception as exc:
            logger.warning(
                "Skipping example %r: validator raised %s",
                example.get("name"),
                exc,
            )
            continue

        if report["errors"]:
            logger.warning(
                "Skipping example %r: %d validation errors %s",
                example.get("name"),
                len(report["errors"]),
                [iss.get("code") for iss in report["errors"]],
            )
            continue
        if report["warnings"]:
            # Expected on first launch — credentials not yet configured.
            logger.info(
                "Example %r has %d warnings (likely first-launch credential gaps)",
                example.get("name"),
                len(report["warnings"]),
            )

        # Reuse existing save_workflow method
        success = await database.save_workflow(
            workflow_id=workflow_id,
            name=example_name,
            slug=slug,
            description=example.get("description"),
            data={"nodes": nodes, "edges": edges},
        )

        if success:
            imported += 1
            logger.info(f"Imported example: {example.get('name')}")

            # Save embedded nodeParameters under the remapped node ids.
            for node_id, params in node_parameters.items():
                if params:
                    try:
                        await database.save_node_parameters(node_id, params)
                        logger.debug(f"Saved parameters for node {node_id}")
                    except Exception as e:
                        logger.error(f"Failed to save parameters for node {node_id}: {e}")

    return imported
