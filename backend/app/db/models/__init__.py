"""ORM models mapping the existing Oracle schema (see docs/database.md)."""

from app.db.models.ai import AIEndpoint, AIModel, Provider
from app.db.models.character import Character
from app.db.models.conversation import Conversation
from app.db.models.memory import Memory
from app.db.models.message import Message, MessageGeneration
from app.db.models.otp_log import OtpLog
from app.db.models.persona import UserPersona
from app.db.models.user import User

__all__ = [
    "AIEndpoint",
    "AIModel",
    "Character",
    "Conversation",
    "Memory",
    "Message",
    "MessageGeneration",
    "OtpLog",
    "Provider",
    "User",
    "UserPersona",
]
