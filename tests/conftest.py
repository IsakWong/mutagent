"""Shared test fixtures and setup for mutagent tests."""

import mutagent.builtins

# Ensure all @impl registrations are loaded before any test runs.
mutagent.builtins.load()
