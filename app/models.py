from dataclasses import dataclass, field
from typing import Any


@dataclass
class Workspace:
    id: int | None = None
    name: str = ""
    description: str = ""
    created_at: str | None = None


@dataclass
class Schema:
    id: int | None = None
    name: str = ""
    definition: dict[str, Any] = field(default_factory=dict)


@dataclass
class Record:
    id: int | None = None
    workspace_id: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Verification:
    id: int | None = None
    workspace_id: int | None = None
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    id: int | None = None
    workspace_id: int | None = None
    title: str = ""
    content: str = ""
