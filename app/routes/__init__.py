from .analytics import analytics_blueprint
from .auth import auth_blueprint
from .data import data_blueprint
from .reports import reports_blueprint
from .schemas import schemas_blueprint
from .storage import storage_bp
from .target_achievement import target_achievement_bp
from .workspaces import workspaces_blueprint
from .party_matching import party_matching_bp

__all__ = [
    'target_achievement_bp',
    'party_matching_bp',
    'storage_bp',
    'analytics_blueprint',
    'auth_blueprint',
    'data_blueprint',
    'reports_blueprint',
    'schemas_blueprint',
    'workspaces_blueprint'
]
