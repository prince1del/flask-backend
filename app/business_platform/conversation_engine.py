"""Conversation engine backed by the SQLAlchemy models for persistence."""

from __future__ import annotations

from flask import current_app

from app.db import db
from app.models import Conversation, ConversationContext, ConversationMessage


class ConversationEngine:
    """Conversation management service backed by database models."""

    @staticmethod
    def _ensure_app_context() -> None:
        try:
            current_app._get_current_object()
        except RuntimeError:
            from app.web_app import create_app

            create_app().app_context().push()

    @staticmethod
    def start_conversation(workspace_id: str, title: str | None = None) -> dict:
        ConversationEngine._ensure_app_context()
        conversation = Conversation(workspace_id=workspace_id, title=title or 'New conversation', status='open')
        db.session.add(conversation)
        db.session.commit()
        return conversation.to_dict()

    @staticmethod
    def get_conversation(conversation_id: str) -> dict | None:
        ConversationEngine._ensure_app_context()
        conversation = db.session.get(Conversation, int(conversation_id)) if str(conversation_id).isdigit() else None
        return conversation.to_dict() if conversation else None

    @staticmethod
    def add_message(conversation_id: str, role: str, content: str) -> dict:
        ConversationEngine._ensure_app_context()
        conversation = db.session.get(Conversation, int(conversation_id)) if str(conversation_id).isdigit() else None
        if conversation is None:
            raise KeyError('Conversation not found')
        message = ConversationMessage(conversation_id=conversation.id, role=role, content=content)
        db.session.add(message)
        db.session.commit()
        return message.to_dict()

    @staticmethod
    def list_messages(conversation_id: str) -> dict:
        ConversationEngine._ensure_app_context()
        conversation = db.session.get(Conversation, int(conversation_id)) if str(conversation_id).isdigit() else None
        if conversation is None:
            raise KeyError('Conversation not found')
        return {
            'conversation_id': conversation.id,
            'messages': [message.to_dict() for message in conversation.messages],
            'status': conversation.status,
        }

    @staticmethod
    def close_conversation(conversation_id: str) -> dict:
        ConversationEngine._ensure_app_context()
        conversation = db.session.get(Conversation, int(conversation_id)) if str(conversation_id).isdigit() else None
        if conversation is None:
            raise KeyError('Conversation not found')
        conversation.status = 'closed'
        db.session.commit()
        return conversation.to_dict()

    @staticmethod
    def search_conversations(workspace_id: str, query: str) -> list:
        ConversationEngine._ensure_app_context()
        search = f'%{query}%'
        return [
            conversation.to_dict()
            for conversation in Conversation.query.filter(Conversation.workspace_id == workspace_id, Conversation.title.ilike(search)).all()
        ]

    @staticmethod
    def delete_conversation(conversation_id: str) -> bool:
        ConversationEngine._ensure_app_context()
        conversation = db.session.get(Conversation, int(conversation_id)) if str(conversation_id).isdigit() else None
        if conversation is None:
            return False
        db.session.delete(conversation)
        db.session.commit()
        return True

    @staticmethod
    def get_context(conversation_id: str) -> dict:
        ConversationEngine._ensure_app_context()
        conversation = db.session.get(Conversation, int(conversation_id)) if str(conversation_id).isdigit() else None
        if conversation is None:
            raise KeyError('Conversation not found')

        context_map = {
            entry.key: entry.value for entry in ConversationContext.query.filter_by(conversation_id=conversation.id).all()
        }
        if context_map:
            return context_map
        return conversation.context or {}

    @staticmethod
    def update_context(conversation_id: str, context: dict) -> dict:
        ConversationEngine._ensure_app_context()
        conversation = db.session.get(Conversation, int(conversation_id)) if str(conversation_id).isdigit() else None
        if conversation is None:
            raise KeyError('Conversation not found')

        for key, value in context.items():
            entry = ConversationContext.query.filter_by(conversation_id=conversation.id, key=key).first()
            if entry is None:
                entry = ConversationContext(conversation_id=conversation.id, key=key, value=value)
                db.session.add(entry)
            else:
                entry.value = value

        conversation.context = {**(conversation.context or {}), **context}
        db.session.commit()
        return ConversationEngine.get_context(conversation.id)
