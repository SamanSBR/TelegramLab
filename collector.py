import asyncio
import os
import sqlite3
from pathlib import Path

from telethon import TelegramClient

DB_PATH = Path(__file__).parent / "telegram_lab.db"


def initialize_database():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                peer_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                text TEXT,
                has_media INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (peer_id, message_id)
            )
        """)


def get_last_message_id(peer_id):
    with sqlite3.connect(DB_PATH) as db:
        result = db.execute(
            """
            SELECT COALESCE(MAX(message_id), 0)
            FROM messages
            WHERE peer_id = ?
            """,
            (peer_id,)
        ).fetchone()

        return result[0]


def save_message(peer_id, message):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            INSERT OR IGNORE INTO messages
            (peer_id, message_id, date, text, has_media)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                peer_id,
                message.id,
                message.date.isoformat(),
                message.raw_text,
                int(message.media is not None)
            )
        )


async def main():
    initialize_database()

    async with TelegramClient(
        "telegram_lab",
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"]
    ) as client:

        me = await client.get_me()
        peer_id = me.id

        last_id = get_last_message_id(peer_id)

        print("=== TelegramLab Collector ===")
        print(f"Last stored message ID: {last_id}")

        collected = 0

        async for message in client.iter_messages(
            "me",
            min_id=last_id,
            reverse=True
        ):
            if not message.date:
                continue

            save_message(peer_id, message)
            collected += 1

            print(f"Stored message: {message.id}")

        print(f"\nCollection complete. Processed: {collected}")


if __name__ == "__main__":
    asyncio.run(main())
