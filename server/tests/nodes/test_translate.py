"""Contract for the provider-abstracted text-language nodes.

The load-bearing assertions here are the capability-membership ones. DeepL
translates and does nothing else; if it could be *selected* for
transliteration the failure would surface at runtime, inside a paid call,
rather than being unrepresentable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx

from ._mocks import patched_container, patched_pricing

pytestmark = pytest.mark.node_contract

SERVER_DIR = Path(__file__).resolve().parents[2]

_KEYS = {
    "deepl": "dl-test-key",
    "sarvam": "sv-test",
    "openai": "sk-test",
}


# ============================================================================
# Capability membership IS the capability
# ============================================================================


class TestCapabilityMembership:
    def test_translate_only_provider_is_absent_from_the_others(self):
        from nodes.translate._registry import (
            detect_providers,
            transliterate_providers,
            translate_providers,
        )

        assert "deepl" in translate_providers()
        assert "deepl" not in transliterate_providers()
        assert "deepl" not in detect_providers()

    def test_multi_capability_providers_are_in_all_three(self):
        from nodes.translate._registry import (
            detect_providers,
            transliterate_providers,
            translate_providers,
        )

        for provider in ("sarvam", "openai"):
            assert provider in translate_providers()
            assert provider in transliterate_providers()
            assert provider in detect_providers()

    def test_every_provider_resolves_to_a_registered_credential(self):
        """Config names credential *ids*, never Credential classes."""
        from services.plugin.credential import CREDENTIAL_REGISTRY

        from nodes.translate import _config
        from nodes.translate._registry import CAPABILITIES

        for capability, (providers, _) in CAPABILITIES.items():
            for provider in providers():
                credential = _config.credential_id(provider)
                assert credential in CREDENTIAL_REGISTRY, (
                    f"{provider} ({capability}) maps to {credential!r}, "
                    "which is not registered"
                )

    def test_nodes_declare_a_credential_for_every_provider_they_offer(self):
        from nodes.translate import _config
        from nodes.translate._registry import (
            detect_providers,
            transliterate_providers,
            translate_providers,
        )
        from nodes.translate.detect_language import DetectLanguageNode
        from nodes.translate.translate_text import TranslateTextNode
        from nodes.translate.transliterate_text import TransliterateTextNode

        for node, providers in (
            (TranslateTextNode, translate_providers()),
            (TransliterateTextNode, transliterate_providers()),
            (DetectLanguageNode, detect_providers()),
        ):
            declared = {c.id for c in node.credentials}
            for provider in providers:
                assert _config.credential_id(provider) in declared, (
                    f"{node.type} cannot authenticate {provider}"
                )

    async def test_selecting_an_unsupported_provider_is_a_user_error(self, harness):
        """DeepL is not merely undocumented for transliteration — it is refused."""
        with patched_container(auth_api_keys=_KEYS), patched_pricing():
            result = await harness.execute(
                "transliterateText", {"provider": "deepl", "text": "namaste"}
            )

        harness.assert_envelope(result, success=False)
        assert "deepl" in result["error"]
        assert "sarvam" in result["error"]


# ============================================================================
# Wire shapes
# ============================================================================


class TestDeepLWireShape:
    URL = "https://api.deepl.com/v2/translate"

    @respx.mock
    async def test_auth_scheme_and_array_body(self, harness):
        route = respx.post(self.URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "translations": [
                        {
                            "text": "Hallo",
                            "detected_source_language": "EN",
                            "billed_characters": 5,
                        }
                    ]
                },
            )
        )

        with patched_container(auth_api_keys=_KEYS), patched_pricing():
            result = await harness.execute(
                "translateText",
                {"provider": "deepl", "text": "Hello", "target_language": "de"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["translated_text"] == "Hallo"
        assert result["result"]["detected_source_language"] == "EN"

        request = route.calls.last.request
        # "DeepL-Auth-Key", not Bearer.
        assert request.headers["authorization"] == "DeepL-Auth-Key dl-test-key"
        import json as _json

        body = _json.loads(request.content)
        # `text` is an array natively, and the target is upper-cased.
        assert body["text"] == ["Hello"]
        assert body["target_lang"] == "DE"

    @respx.mock
    async def test_a_free_key_is_routed_to_the_free_host(self, harness):
        """A ':fx' key on the pro host 403s with an unhelpful body."""
        route = respx.post("https://api-free.deepl.com/v2/translate").mock(
            return_value=httpx.Response(200, json={"translations": [{"text": "Bonjour"}]})
        )

        with patched_container(auth_api_keys={**_KEYS, "deepl": "abc:fx"}), patched_pricing():
            result = await harness.execute(
                "translateText",
                {"provider": "deepl", "text": "Hello", "target_language": "fr"},
            )

        harness.assert_envelope(result, success=True)
        assert route.called

    @respx.mock
    async def test_provider_reported_billing_is_preferred(self, harness, api_usage):
        """DeepL tells us what it billed; that beats counting input."""
        respx.post(self.URL).mock(
            return_value=httpx.Response(
                200,
                json={"translations": [{"text": "x", "billed_characters": 4242}]},
            )
        )

        with patched_container(auth_api_keys=_KEYS):
            await harness.execute(
                "translateText",
                {"provider": "deepl", "text": "short", "target_language": "de"},
            )

        api_usage.assert_awaited_once()
        assert api_usage.await_args.args == ("deepl", "translate", 4242)


class TestSarvamWireShape:
    @respx.mock
    async def test_translate_uses_the_native_header_and_single_input(self, harness):
        route = respx.post("https://api.sarvam.ai/translate").mock(
            return_value=httpx.Response(
                200,
                json={
                    "translated_text": "नमस्ते",
                    "source_language_code": "en-IN",
                    "request_id": "sv-1",
                },
            )
        )

        with patched_container(auth_api_keys=_KEYS), patched_pricing():
            result = await harness.execute(
                "translateText",
                {"provider": "sarvam", "text": "hello", "target_language": "hi-IN"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["translated_text"] == "नमस्ते"

        request = route.calls.last.request
        assert request.headers["api-subscription-key"] == "sv-test"
        import json as _json

        # Sarvam takes one string, not an array.
        assert _json.loads(request.content)["input"] == "hello"

    @respx.mock
    async def test_transliterate_hits_its_own_endpoint(self, harness):
        route = respx.post("https://api.sarvam.ai/transliterate").mock(
            return_value=httpx.Response(200, json={"transliterated_text": "नमस्ते"})
        )

        with patched_container(auth_api_keys=_KEYS), patched_pricing():
            result = await harness.execute(
                "transliterateText",
                {"provider": "sarvam", "text": "namaste", "target_language": "hi-IN"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["transliterated_text"] == "नमस्ते"
        assert route.called

    @respx.mock
    async def test_detect_returns_language_and_script(self, harness):
        respx.post("https://api.sarvam.ai/text-lid").mock(
            return_value=httpx.Response(
                200, json={"language_code": "hi-IN", "script_code": "Deva"}
            )
        )

        with patched_container(auth_api_keys=_KEYS), patched_pricing():
            result = await harness.execute(
                "detectLanguage", {"provider": "sarvam", "text": "नमस्ते"}
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["language"] == "hi-IN"
        assert result["result"]["script"] == "Deva"

    async def test_over_cap_input_is_refused_before_the_call(self, harness):
        with patched_container(auth_api_keys=_KEYS), patched_pricing():
            result = await harness.execute(
                "translateText",
                {
                    "provider": "sarvam",
                    "text": "x" * 3000,
                    "translate_model": "mayura:v1",
                },
            )

        harness.assert_envelope(result, success=False)
        assert "1000" in result["error"]


# ============================================================================
# Node contract
# ============================================================================


class TestNodeContract:
    def test_no_node_names_a_field_model_or_api_key(self):
        """Regression guard for the parameter-panel clobber."""
        from nodes.translate.detect_language import DetectLanguageParams
        from nodes.translate.translate_text import TranslateTextParams
        from nodes.translate.transliterate_text import TransliterateTextParams

        for params in (
            TranslateTextParams,
            TransliterateTextParams,
            DetectLanguageParams,
        ):
            assert "model" not in params.model_fields
            assert "api_key" not in params.model_fields

    def test_no_node_uses_declarative_routing(self):
        """`routing=` resolves credentials[0], ignoring `provider`."""
        from nodes.translate.detect_language import DetectLanguageNode
        from nodes.translate.translate_text import TranslateTextNode
        from nodes.translate.transliterate_text import TransliterateTextNode

        for node in (TranslateTextNode, TransliterateTextNode, DetectLanguageNode):
            for spec in node._operations.values():
                assert spec.routing is None, f"{node.type}.{spec.name} declares routing="

    def test_canvas_handles_stay_visible(self):
        from nodes.translate.detect_language import DetectLanguageNode
        from nodes.translate.translate_text import TranslateTextNode
        from nodes.translate.transliterate_text import TransliterateTextNode

        for node in (TranslateTextNode, TransliterateTextNode, DetectLanguageNode):
            assert node.hide_input_handle is False
            assert node.hide_output_handle is False

    @pytest.mark.parametrize(
        "node_type,payload",
        [
            ("translateText", {"text": "hi", "provider_options": "", "preserve_formatting": ""}),
            ("transliterateText", {"text": "hi", "provider_options": ""}),
            ("detectLanguage", {"text": "hi", "provider_options": ""}),
        ],
    )
    def test_blank_panel_values_fall_back_to_defaults(self, node_type, payload):
        from services.node_registry import get_node_class

        model = get_node_class(node_type).Params(**payload)
        assert model.provider_options == {}

    def test_json_object_string_is_parsed(self):
        from nodes.translate.translate_text import TranslateTextParams

        model = TranslateTextParams(
            text="hi", provider_options='{"glossary_id": "abc"}'
        )
        assert model.provider_options == {"glossary_id": "abc"}


class TestLazySdkImports:
    """Registering translate providers must not import a heavy SDK."""

    HEAVY = ("openai", "anthropic", "google.genai")

    def test_registering_providers_imports_no_sdk(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "import nodes.translate\n"
                f"leaked = [m for m in {self.HEAVY!r} if m in sys.modules]\n"
                "print('LEAKED=' + ','.join(leaked))\n",
            ],
            cwd=SERVER_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().endswith("LEAKED="), (
            f"translate provider registration imported heavy SDKs: {result.stdout!r}"
        )

    def test_every_declared_exception_ref_resolves(self):
        import nodes.translate  # noqa: F401

        from nodes.translate._registry import CAPABILITIES

        for providers, get_spec in CAPABILITIES.values():
            for name in providers():
                assert get_spec(name).sdk_exception_types
