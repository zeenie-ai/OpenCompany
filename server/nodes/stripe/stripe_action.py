"""Stripe Action — pass-through over the Stripe CLI."""

from __future__ import annotations

import shlex
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.events import run_cli_command
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from ._credentials import StripeCredential


class StripeActionParams(BaseModel):
    command: str = Field(
        default="",
        description=(
            "Stripe CLI command, exactly as you would type after 'stripe '. "
            "Examples: 'customers create --email a@b.com', 'charges list --limit 10', "
            "'trigger charge.succeeded'. Reference: https://stripe.com/docs/cli"
        ),
    )

    model_config = ConfigDict(extra="ignore")


class StripeActionOutput(BaseModel):
    command: Optional[str] = None
    success: Optional[bool] = None
    result: Optional[Any] = None
    stdout: Optional[str] = None
    error: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class StripeActionNode(ActionNode):
    type = "stripeAction"
    display_name = "Stripe"
    subtitle = "CLI Command"
    group = ("payments", "tool")
    description = "Run any Stripe CLI command (customers, charges, payment_intents, refunds, invoices, trigger, …)"
    component_kind = "square"
    tool_name = "stripe_action"
    tool_description = "Run any Stripe CLI command and return the parsed JSON response. Pass a 'command' field exactly as you would type after 'stripe ' (e.g. 'customers create --email a@b.com', 'charges list --limit 10', 'payment_intents create --amount 2000 --currency usd', 'refunds create --payment-intent pi_xxx', 'trigger charge.succeeded'). Covers every Stripe resource: customers, charges, payment_intents, refunds, invoices, products, prices, subscriptions, payment_methods, setup_intents, transfers, payouts, plus 'trigger <event>' for synthetic test events."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (StripeCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = StripeActionParams
    Output = StripeActionOutput

    @Operation("run", cost={"service": "stripe", "action": "run", "count": 1})
    async def run(self, ctx: NodeContext, params: StripeActionParams) -> Any:
        from ._install import ensure_stripe_cli

        cmd = params.command.strip()
        if not cmd:
            raise RuntimeError("command is required (e.g. 'customers create --email a@b.com')")
        try:
            binary = str(await ensure_stripe_cli())
        except Exception as e:
            raise RuntimeError(f"Stripe CLI install failed: {e}")
        # No credential= — Stripe CLI reads its own creds from
        # ~/.config/stripe/config.toml after `stripe login`.
        result = await run_cli_command(binary=binary, argv=shlex.split(cmd))
        if not result["success"]:
            raise RuntimeError(result.get("error") or "Stripe CLI invocation failed")
        return {
            "command": cmd,
            "success": True,
            "result": result.get("result"),
            "stdout": result.get("stdout"),
        }
