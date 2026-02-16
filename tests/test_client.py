"""Tests for LLMClient declaration."""

import pytest
import mutagent
from mutagent.client import LLMClient
from mutobj.core import DeclarationMeta, _DECLARED_METHODS


class TestLLMClientDeclaration:

    def test_inherits_from_mutagent_declaration(self):
        assert issubclass(LLMClient, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(LLMClient, DeclarationMeta)

    def test_has_declared_attributes(self):
        client = LLMClient(
            model="test-model",
            api_key="test-key",
            base_url="https://api.example.com",
        )
        assert client.model == "test-model"
        assert client.api_key == "test-key"
        assert client.base_url == "https://api.example.com"

    def test_send_message_is_declared_method(self):
        declared = getattr(LLMClient, _DECLARED_METHODS, set())
        assert "send_message" in declared

    def test_send_message_is_callable(self):
        client = LLMClient(
            model="test-model",
            api_key="test-key",
            base_url="https://api.example.com",
        )
        # send_message should be callable (either stub or impl)
        assert hasattr(client, "send_message")
        assert callable(client.send_message)
