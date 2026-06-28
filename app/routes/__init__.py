from .analytics import analytics_blueprint
from .auth import auth_blueprint
from .data import data_blueprint
from .reports import reports_blueprint
from .schemas import schemas_blueprint
from .storage import bp as storage_blueprint
from .workspaces import workspaces_blueprint

__all__ = [
    "analytics_blueprint",
    "auth_blueprint",
    "data_blueprint",
    "reports_blueprint",
    "schemas_blueprint",
    "storage_blueprint",
    "workspaces_blueprint",
]
