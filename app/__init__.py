from flask import Flask

from app.web_app import create_app as _create_app


def create_app() -> Flask:
    """Factory used by the modular package structure."""
    return _create_app()


__all__ = ["create_app"]
