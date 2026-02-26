"""Tests for LLMClient declaration."""

import mutagent
from mutagent.client import LLMClient
from mutagent.provider import LLMProvider
from mutagent.builtins.anthropic_provider import AnthropicProvider
from mutobj.core import DeclarationMeta, _DECLARED_METHODS


class TestLLMClientDeclaration:

    def test_inherits_from_mutagent_declaration(self):
        assert issubclass(LLMClient, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(LLMClient, DeclarationMeta)

    def test_has_declared_attributes(self):
        provider = AnthropicProvider(
            base_url="https://api.example.com",
            api_key="test-key",
        )
        client = LLMClient(
            provider=provider,
            model="test-model",
        )
        assert client.model == "test-model"
        assert client.provider is provider

    def test_send_message_is_declared_method(self):
        declared = getattr(LLMClient, _DECLARED_METHODS, set())
        assert "send_message" in declared

    def test_send_message_is_callable(self):
        provider = AnthropicProvider(
            base_url="https://api.example.com",
            api_key="test-key",
        )
        client = LLMClient(
            provider=provider,
            model="test-model",
        )
        assert hasattr(client, "send_message")
        assert callable(client.send_message)
