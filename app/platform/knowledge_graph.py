"""
Platform Layer - Business Knowledge Graph Service (Mock Implementation)
Provides entity and relationship lookup for decision support.
"""


class BusinessKnowledgeGraph:
    """Mock knowledge graph for business entities and relationships."""

    @staticmethod
    def get_entity(entity_type: str, entity_id: str) -> dict:
        return {
            'entity_type': entity_type,
            'entity_id': entity_id,
            'name': f'{entity_type.title()} {entity_id}',
            'properties': {
                'status': 'active',
                'score': 75,
            },
            'relationships': [
                {
                    'type': 'related_to',
                    'target': f'{entity_type}-related',
                    'confidence': 0.9,
                }
            ]
        }

    @staticmethod
    def search_entities(entity_type: str, query: str) -> dict:
        return {
            'entity_type': entity_type,
            'query': query,
            'results': [
                {
                    'id': f'{entity_type}-1',
                    'name': f'{entity_type.title()} Match 1',
                },
                {
                    'id': f'{entity_type}-2',
                    'name': f'{entity_type.title()} Match 2',
                }
            ]
        }
