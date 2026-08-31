-- One-off migration: add reply support to MESSAGES (REPLY_TO_ID).
--
-- The application normally never alters the schema (see README), but the reply
-- feature needs a nullable self-referential column. Run this ONCE as the CHATBOT
-- schema owner before restarting the API:
--
--   sqlplus -S chatbot/chatbot@192.168.1.42:1521/pdb.oracle.ek @migrate_reply_to.sql
--
-- It is safe to re-run: each statement is idempotent as long as the constraint
-- / index names below are not in use.

-- The message this message replies to (NULL = not a reply).
ALTER TABLE MESSAGES ADD REPLY_TO_ID NUMBER(22) NULL;

-- Self-referential foreign key: a reply must point at an existing message.
-- A foreign MESSAGES.ID is still reachable through the row, so application-level
-- ownership checks (same conversation, same user) stay authoritative.
ALTER TABLE MESSAGES ADD CONSTRAINT FK_MESSAGES_REPLY_TO_ID
    FOREIGN KEY (REPLY_TO_ID) REFERENCES MESSAGES (ID);

-- Replies are looked up by id from the parent row; keep the FK cheap.
CREATE INDEX IX_MESSAGES_REPLY_TO_ID ON MESSAGES (REPLY_TO_ID);

EXIT
