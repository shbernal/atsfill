"""Writes docs/adapters/README.md, the README's adapter badges and the docs site's carriers
from the registry."""

from pathlib import Path

from atsfill.core import registry

ROOT = Path(__file__).parents[1]
CARRIERS = ROOT / "overrides/partials/carriers.html"
LOGOS = ROOT / "docs/assets/logos"


def carriers(systems: dict[str, registry.Source] | None = None) -> str:
    """The home page's carriers: every supported system, its name from its doc's title, its
    logo where one is stored, as a Jinja module `home.html` imports."""
    systems = registry.sources() if systems is None else systems
    supported = sorted(
        (s for s in systems.values() if s.adapter and s.scope.value == "supported"),
        key=lambda s: _title(s.adapter or s.name).lower(),
    )
    logos = {}
    for s in supported:
        logo = next((p.name for p in sorted(LOGOS.glob(f"{s.adapter}.*")) if p.suffix != ".md"), "")
        logos[s.adapter] = logo
    items = [
        f"  <li><a href=\"{{{{ 'adapters/{s.adapter}/' | url }}}}\">"
        + (
            f'<img src="{{{{ \'assets/logos/{logos[s.adapter]}\' | url }}}}" alt=""'
            ' width="32" height="32">'
            if logos[s.adapter]
            else f'<span class="no-mark" aria-hidden="true">{_title(s.adapter or s.name)[0]}</span>'
        )
        + f"<span>{_title(s.adapter or s.name)}</span></a></li>"
        for s in supported
    ]
    return "\n".join(
        [
            "{#- Generated from the registry by `uv run python -m scripts.backlog` -#}",
            f"{{% set count = {len(supported)} %}}",
            f"{{% set logos = {dict(sorted(logos.items()))!r} %}}",
            "{% macro wall() -%}",
            '<ul class="carrier-wall">',
            *items,
            "</ul>",
            "{%- endmacro %}",
            "",
        ]
    )


def _title(adapter: str) -> str:
    doc = ROOT / "docs/adapters" / f"{adapter}.md"
    return doc.read_text().splitlines()[0].removeprefix("# ") if doc.exists() else adapter


if __name__ == "__main__":
    ROOT.joinpath("docs/adapters/README.md").write_text(registry.backlog())
    readme = ROOT / "README.md"
    readme.write_text(registry.with_badges(readme.read_text()))
    CARRIERS.parent.mkdir(exist_ok=True)
    CARRIERS.write_text(carriers())
