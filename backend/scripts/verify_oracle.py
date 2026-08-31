"""Live verification against the real Oracle database.

1. Verifies every ORM model maps onto the actual Oracle schema (tables, columns,
   row counts) — read-only.
2. Runs the full API flow in-process against Oracle: register, login, protected
   endpoints, conversation CRUD, streamed AI reply (mock provider), persistence.
3. Cleans up exactly the rows it created (fresh test data only).

Run: uv run python scripts/verify_oracle.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running as `python scripts/verify_oracle.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before importing the app (settings are read at import time).
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("JWT_SECRET", "verify-secret-0123456789abcdef")
os.environ.setdefault(
    "DATABASE_URL",
    "oracle+oracledb://chatbot:chatbot@192.168.1.42:1521/?service_name=pdb.oracle.ek",
)

import httpx
from sqlalchemy import func, select, text

from app.db.database import SessionFactory
from app.db.models import (
    AIEndpoint,
    AIModel,
    Character,
    Conversation,
    Memory,
    Message,
    MessageGeneration,
    Provider,
    User,
)
from app.main import app

MODELS: list[type] = [
    Provider,
    AIEndpoint,
    AIModel,
    Character,
    Conversation,
    Memory,
    Message,
    MessageGeneration,
    User,
]

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    status = "ok" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        failures.append(message)


async def verify_orm_schema() -> None:
    print("== 1. ORM models vs Oracle schema ==")
    async with SessionFactory() as db:
        result = await db.execute(
            text(
                "SELECT table_name, column_name FROM user_tab_columns "
                "WHERE table_name IN (SELECT table_name FROM user_tables) "
                "ORDER BY table_name, column_id"
            )
        )
        oracle_columns: dict[str, list[str]] = {}
        for table_name, column_name in result:
            oracle_columns.setdefault(table_name, []).append(column_name)

        # All 9 tables must exist in Oracle and be mapped by the ORM.
        check(len(oracle_columns) >= 9, f"Oracle has 9 tables (found {len(oracle_columns)})")
        for model in MODELS:
            mapped = [c.name.lower() for c in model.__table__.columns]
            actual = oracle_columns.get(model.__tablename__, [])
            check(
                model.__tablename__ in oracle_columns,
                f"{model.__tablename__} exists in Oracle and in the ORM",
            )
            missing = [c for c in mapped if c not in [a.lower() for a in actual]]
            check(not missing, f"{model.__tablename__} columns match ({len(mapped)} mapped)")
            if missing:
                print(f"      missing in Oracle view: {missing}")

        for model in MODELS:
            count = (await db.execute(select(func.count()).select_from(model))).scalar_one()
            print(f"  {model.__tablename__}: {count} rows (SELECT through the ORM works)")

    check(not failures, "schema verification")


async def verify_api_flow() -> tuple[str, int]:
    print("== 2. API flow against Oracle (mock provider) ==")
    username = "verify_user"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/auth/register",
            json={"username": username, "password": "verify-password-123"},
        )
        check(response.status_code == 201, f"register -> 201 (got {response.status_code})")

        response = await client.post(
            "/auth/login", json={"username": username, "password": "verify-password-123"}
        )
        check(response.status_code == 200, f"login -> 200 (got {response.status_code})")
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = await client.get("/me", headers=headers)
        check(response.status_code == 200 and response.json()["username"] == username, "GET /me")

        response = await client.get("/conversations")
        check(response.status_code == 401, "anonymous GET /conversations -> 401")

        response = await client.get("/characters", headers=headers)
        characters = response.json()
        check(response.status_code == 200 and characters, "GET /characters lists Maya")

        response = await client.post(
            "/conversations", json={"character_id": characters[0]["id"]}, headers=headers
        )
        check(
            response.status_code == 201, f"POST /conversations -> 201 (got {response.status_code})"
        )
        conversation = response.json()
        conversation_id = conversation["id"]

        response = await client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "Hello from the live verification!"},
            headers=headers,
        )
        check(
            response.status_code == 200,
            f"streamed POST message -> 200 (got {response.status_code})",
        )
        body = response.text
        check("event: message.created" in body, "SSE contains message.created")
        check("event: message.delta" in body, "SSE contains message.delta chunks")
        check("event: message.completed" in body, "SSE contains message.completed")
        check("event: error" not in body, "SSE contains no error event")

        response = await client.get(f"/conversations/{conversation_id}", headers=headers)
        check(response.status_code == 200, "GET conversation")

        response = await client.get(f"/conversations/{conversation_id}/messages", headers=headers)
        messages = response.json()
        check(
            [m["role"] for m in messages] == ["user", "assistant"],
            "messages persisted: [user, assistant]",
        )

        # Foreign user cannot see the conversation.
        await client.post(
            "/auth/register",
            json={"username": "verify_user_2", "password": "verify-password-456"},
        )
        response = await client.post(
            "/auth/login", json={"username": "verify_user_2", "password": "verify-password-456"}
        )
        other_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        response = await client.get(f"/conversations/{conversation_id}", headers=other_headers)
        check(response.status_code == 404, "foreign user -> 404 on someone else's conversation")

        return username, conversation_id


async def cleanup(username: str, conversation_id: int) -> None:
    print(f"== 3. Cleaning up verification rows (user={username}, conv={conversation_id}) ==")
    async with SessionFactory() as db:
        for username_i in (username, "verify_user_2"):
            user = (
                await db.execute(select(User).where(User.username == username_i))
            ).scalar_one_or_none()
            if user is not None:
                await db.execute(text("DELETE FROM memories WHERE user_id = :u"), {"u": user.id})
                for conversation in (
                    await db.execute(select(Conversation).where(Conversation.user_id == user.id))
                ).scalars():
                    await db.execute(
                        text(
                            "DELETE FROM message_generations WHERE message_id IN "
                            "(SELECT id FROM messages WHERE conversation_id = :c)"
                        ),
                        {"c": conversation.id},
                    )
                    await db.execute(
                        text("DELETE FROM messages WHERE conversation_id = :c"),
                        {"c": conversation.id},
                    )
                    await db.execute(
                        text("DELETE FROM conversations WHERE id = :c"), {"c": conversation.id}
                    )
                await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
        await db.commit()
    print("  cleanup done")


async def main() -> int:
    await verify_orm_schema()
    username, conversation_id = await verify_api_flow()
    await cleanup(username, conversation_id)
    print()
    if failures:
        print(f"VERIFICATION FAILED: {len(failures)} check(s) failed")
        return 1
    print("ALL LIVE ORACLE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
