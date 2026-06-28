from .analytics import analytics_blueprint
from .auth import auth_blueprint
from .data import data_blueprint
from .reports import reports_blueprint
from .schemas import schemas_blueprint
from .workspaces import workspaces_blueprint

__all__ = [
    "analytics_blueprint",
    "auth_blueprint",
    "data_blueprint",
    "reports_blueprint",
    "schemas_blueprint",
    "workspaces_blueprint",
]
