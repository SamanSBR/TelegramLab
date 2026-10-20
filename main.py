import asyncio
import os

from telethon import TelegramClient


async def main():
    client = TelegramClient(
        "telegram_lab",
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"]
    )

    async with client:
        me = await client.get_me()

        print("\n=== TelegramLab ===")
        print("Connection: SUCCESS")
        print(f"User ID: {me.id}")
        print(f"Username: {me.username}")

        print("\n=== Your Last 5 Saved Messages ===")

        async for message in client.iter_messages("me", limit=5):
            print(f"[{message.id}] {message.text}")


if __name__ == "__main__":
    asyncio.run(main())
