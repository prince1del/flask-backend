"""
Platform Layer - Business Knowledge Graph Service (Persisted Implementation)
Provides entity and relationship lookup for decision support.
"""

from app.db import db
from app.models import KnowledgeGraphEntity, KnowledgeGraphRelationship


class BusinessKnowledgeGraph:
    """Workspace-scoped knowledge graph with persisted entity and relationship records."""

    @staticmethod
    def _ensure_app_context() -> None:
        from flask import current_app

        try:
            current_app._get_current_object()
        except RuntimeError:
            from app.web_app import create_app

            create_app().app_context().push()

    @staticmethod
    def add_entity(entity_type: str, entity_id: str, name: str, properties: dict | None = None, workspace_id: str = 'default') -> dict:
        BusinessKnowledgeGraph._ensure_app_context()
        entity = KnowledgeGraphEntity.query.filter_by(entity_type=entity_type, entity_id=entity_id, workspace_id=workspace_id).first()
        if entity is None:
            entity = KnowledgeGraphEntity(entity_type=entity_type, entity_id=entity_id, workspace_id=workspace_id)
        entity.name = name
        entity.properties = properties or {}
        db.session.add(entity)
        db.session.commit()
        return entity.to_dict()

    @staticmethod
    def add_relationship(entity_type: str, entity_id: str, relationship_type: str, target_type: str, target_id: str, properties: dict | None = None, workspace_id: str = 'default') -> dict:
        BusinessKnowledgeGraph._ensure_app_context()
        relationship = KnowledgeGraphRelationship(
            entity_type=entity_type,
            entity_id=entity_id,
            workspace_id=workspace_id,
            relationship_type=relationship_type,
            target_type=target_type,
            target_id=target_id,
            properties=properties or {},
        )
        db.session.add(relationship)
        db.session.commit()
        return relationship.to_dict()

    @staticmethod
    def get_entity(entity_type: str, entity_id: str, workspace_id: str = 'default') -> dict:
        BusinessKnowledgeGraph._ensure_app_context()
        entity = KnowledgeGraphEntity.query.filter_by(entity_type=entity_type, entity_id=entity_id, workspace_id=workspace_id).first()
        if entity is None:
            return {
                'entity_type': entity_type,
                'entity_id': entity_id,
                'name': f'{entity_type.title()} {entity_id}',
                'properties': {},
                'relationships': [],
                'workspace_id': workspace_id,
            }

        relationships = KnowledgeGraphRelationship.query.filter_by(
            entity_type=entity_type,
            entity_id=entity_id,
            workspace_id=workspace_id,
        ).order_by(KnowledgeGraphRelationship.created_at.asc()).all()

        return {
            'entity_type': entity.entity_type,
            'entity_id': entity.entity_id,
            'name': entity.name,
            'properties': entity.properties or {},
            'relationships': [
                {
                    'type': relation.relationship_type,
                    'target_type': relation.target_type,
                    'target': relation.target_id,
                    'properties': relation.properties or {},
                }
                for relation in relationships
            ],
            'workspace_id': workspace_id,
        }

    @staticmethod
    def search_entities(entity_type: str, query: str, workspace_id: str = 'default') -> dict:
        BusinessKnowledgeGraph._ensure_app_context()
        results = KnowledgeGraphEntity.query.filter(
            KnowledgeGraphEntity.entity_type == entity_type,
            KnowledgeGraphEntity.workspace_id == workspace_id,
        ).filter(
            (KnowledgeGraphEntity.name.ilike(f'%{query}%')) |
            (KnowledgeGraphEntity.properties.ilike(f'%{query}%'))
        ).order_by(KnowledgeGraphEntity.name).all()
        return {
            'entity_type': entity_type,
            'query': query,
            'results': [{'id': result.entity_id, 'name': result.name} for result in results],
        }
