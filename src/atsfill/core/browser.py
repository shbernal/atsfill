"""The browser launcher, the only module that imports camoufox and playwright (ADR 0002)."""

from playwright.sync_api import Page

__all__ = ["Page"]
