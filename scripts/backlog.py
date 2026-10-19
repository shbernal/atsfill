"""Writes docs/adapters/README.md from the registry."""

from pathlib import Path

from atsfill.core import registry

Path(__file__).parents[1].joinpath("docs/adapters/README.md").write_text(registry.backlog())
