"""Writes docs/adapters/README.md and the README's adapter badges from the registry."""

from pathlib import Path

from atsfill.core import registry

ROOT = Path(__file__).parents[1]
ROOT.joinpath("docs/adapters/README.md").write_text(registry.backlog())
readme = ROOT / "README.md"
readme.write_text(registry.with_badges(readme.read_text()))
