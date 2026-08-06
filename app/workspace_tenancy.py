"""
Per-login data isolation via private workspace_id.

UI/assets are shared for every executive. Business data (customers, masters,
orders, targets, company profile, …) is filtered by workspace_id from the
authenticated user — never from the client.

Rule: each new sales_executive / distributor / retailer login gets its own
workspace unless an admin explicitly passes workspace_id (shared team case).
"""

from __future__ import annotations

import re

# Roles that own a private data silo by default.
_PRIVATE_WORKSPACE_ROLES = frozenset({
    'sales_executive',
    'distributor',
    'retailer',
})


def slug_for_username(username: str) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', (username or '').strip().lower())
    text = text.strip('_') or 'user'
    return text[:64]


def private_workspace_id(username: str) -> str:
    """Canonical private silo id for a login (stable for that username)."""
    return f'exec_{slug_for_username(username)}'


def resolve_workspace_id_for_new_user(
    username: str,
    role: str,
    explicit_workspace_id: str | None = None,
) -> str:
    """
    Pick workspace_id when creating a user.

    - Explicit non-empty workspace_id → use as-is (admin can share a silo).
    - sales_executive / distributor / retailer → private exec_<username>
    - otherwise → default
    """
    explicit = (explicit_workspace_id or '').strip()
    if explicit:
        return explicit

    role_key = (role or 'unassigned').strip().lower()
    if role_key in _PRIVATE_WORKSPACE_ROLES:
        return private_workspace_id(username)

    return 'default'
