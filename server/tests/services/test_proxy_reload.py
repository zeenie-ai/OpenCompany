"""Concurrency and handler-routing contracts for proxy snapshots."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nodes.proxy import proxy_config
from services.proxy.service import ProxyService


def _provider_row(name: str):
    return {
        "name": name,
        "enabled": True,
        "gateway_host": "proxy.example",
        "gateway_port": 8080,
        "url_template": "{}",
        "geo_coverage": "[]",
    }


def _rule_row(rule_id: int, pattern: str):
    return {
        "id": rule_id,
        "domain_pattern": pattern,
        "preferred_providers": "[]",
        "session_type": "rotating",
    }


async def test_provider_reload_serializes_fetch_build_and_swap():
    old_started = asyncio.Event()
    allow_old = asyncio.Event()

    class Auth:
        async def get_api_key(self, key: str):
            if "proxy_old_username" in key:
                old_started.set()
                await allow_old.wait()
            return "credential"

    database = MagicMock()
    database.get_proxy_providers = AsyncMock(
        side_effect=[[_provider_row("old")], [_provider_row("new")]]
    )
    service = ProxyService(Auth(), database, SimpleNamespace())

    older = asyncio.create_task(service.reload_providers())
    await old_started.wait()
    newer = asyncio.create_task(service.reload_providers())
    await asyncio.sleep(0)

    # The newer fetch cannot pass the older build and later be overwritten.
    assert database.get_proxy_providers.await_count == 1
    allow_old.set()
    await asyncio.gather(older, newer)
    assert list(service._providers) == ["new"]


async def test_routing_reload_serializes_fetch_and_swap():
    old_started = asyncio.Event()
    allow_old = asyncio.Event()
    calls = 0

    async def get_rules():
        nonlocal calls
        calls += 1
        if calls == 1:
            old_started.set()
            await allow_old.wait()
            return [_rule_row(1, "old.example")]
        return [_rule_row(2, "new.example")]

    database = MagicMock()
    database.get_proxy_routing_rules = AsyncMock(side_effect=get_rules)
    service = ProxyService(MagicMock(), database, SimpleNamespace())

    older = asyncio.create_task(service.reload_routing_rules())
    await old_started.wait()
    newer = asyncio.create_task(service.reload_routing_rules())
    await asyncio.sleep(0)
    assert database.get_proxy_routing_rules.await_count == 1

    allow_old.set()
    await asyncio.gather(older, newer)
    assert [rule.domain_pattern for rule in service.get_routing_rules()] == [
        "new.example"
    ]


async def test_provider_handler_reloads_only_provider_snapshot():
    database = MagicMock()
    database.save_proxy_provider = AsyncMock(return_value=True)
    service = MagicMock()
    service.reload_providers = AsyncMock()
    service.reload_routing_rules = AsyncMock()

    with patch("services.plugin.deps.get_database", return_value=database):
        result = await proxy_config._add_provider(
            {
                "name": "provider-a",
                "gateway_host": "proxy.example",
                "gateway_port": 8080,
                "url_template": "{}",
            },
            service,
        )

    assert result["success"] is True
    service.reload_providers.assert_awaited_once()
    service.reload_routing_rules.assert_not_awaited()


async def test_routing_handlers_reload_only_routing_snapshot():
    database = MagicMock()
    database.save_proxy_routing_rule = AsyncMock(return_value=True)
    database.delete_proxy_routing_rule = AsyncMock(return_value=True)
    service = MagicMock()
    service.reload_providers = AsyncMock()
    service.reload_routing_rules = AsyncMock()

    with patch("services.plugin.deps.get_database", return_value=database):
        added = await proxy_config._add_routing_rule(
            {
                "domain_pattern": "*.example.com",
                "preferred_providers": '["provider-a"]',
            },
            service,
        )
        removed = await proxy_config._remove_routing_rule(
            {"rule_id": 7},
            service,
        )

    assert added["success"] is True
    assert removed["success"] is True
    assert service.reload_routing_rules.await_count == 2
    service.reload_providers.assert_not_awaited()
