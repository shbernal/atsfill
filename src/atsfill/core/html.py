"""A page's HTML as a tree, for an adapter that reads a server-rendered form by one parser in
both its request read and its browser read (ADR 0011)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = {"input", "br", "img", "hr", "meta", "link", "source", "wbr", "col", "area"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: Node | None = None
    children: list[Node | str] = field(default_factory=list)

    def walk(self) -> list[Node]:
        """Every element under this one, in document order."""
        out = []
        for child in self.children:
            if isinstance(child, Node):
                out += [child, *child.walk()]
        return out

    def find(self, match: Callable[[Node], bool]) -> Node | None:
        return next((n for n in self.walk() if match(n)), None)

    def closest(self, match: Callable[[Node], bool]) -> Node | None:
        """This element or its nearest ancestor that matches."""
        node: Node | None = self
        while node is not None and not match(node):
            node = node.parent
        return node

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = self.open = Node("", {})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs}, self.open)
        self.open.children.append(node)
        if tag not in VOID:
            self.open = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.open.children.append(Node(tag, {k: v or "" for k, v in attrs}, self.open))

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self.open
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.open = node.parent

    def handle_data(self, data: str) -> None:
        self.open.children.append(data)


def parse(html: str) -> Node:
    tree = _Tree()
    tree.feed(html)
    return tree.root
