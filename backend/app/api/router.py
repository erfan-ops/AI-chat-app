"""Aggregates all route modules into the single API router."""

from fastapi import APIRouter

from app.api.routes import (
    ai,
    auth,
    characters,
    cloudinary,
    conversations,
    memories,
    messages,
    otp,
    personas,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(otp.router)
api_router.include_router(personas.router)
api_router.include_router(characters.router)
api_router.include_router(cloudinary.router)
api_router.include_router(ai.router)
api_router.include_router(conversations.router)
api_router.include_router(messages.router)
api_router.include_router(memories.router)
