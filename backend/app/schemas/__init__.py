"""Pydantic schemas — the API's input/output contracts (never ORM models)."""

from app.schemas.ai import (
    CharacterCreate,
    CharacterRead,
    CharacterUpdate,
    ModelCreate,
    ModelRead,
    ModelUpdate,
)
from app.schemas.auth import (
    LoginOtpRequest,
    LoginRequest,
    LoginResponse,
    OtpRequiredResponse,
    RegisterRequest,
)
from app.schemas.cloudinary import UploadSignatureRead
from app.schemas.conversations import ConversationCreate, ConversationRead, ConversationUpdate
from app.schemas.memories import MemoryRead
from app.schemas.messages import MessageCreate, MessageRead
from app.schemas.otp import OtpChallengeRead, OtpEnableRequest, OtpVerifyRequest
from app.schemas.personas import PersonaCreate, PersonaRead, PersonaUpdate
from app.schemas.users import UserRead, UserUpdate

__all__ = [
    "CharacterCreate",
    "CharacterRead",
    "CharacterUpdate",
    "ConversationCreate",
    "ConversationRead",
    "ConversationUpdate",
    "LoginOtpRequest",
    "LoginRequest",
    "LoginResponse",
    "MemoryRead",
    "MessageCreate",
    "MessageRead",
    "ModelCreate",
    "ModelRead",
    "ModelUpdate",
    "OtpChallengeRead",
    "OtpEnableRequest",
    "OtpRequiredResponse",
    "OtpVerifyRequest",
    "PersonaCreate",
    "PersonaRead",
    "PersonaUpdate",
    "RegisterRequest",
    "UploadSignatureRead",
    "UserRead",
    "UserUpdate",
]
