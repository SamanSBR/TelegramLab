import asyncio
import os
import json

from telethon import TelegramClient
from telethon.tl.types import MessageEntityUrl, MessageEntityTextUrl


async def main():
    async with TelegramClient(
        "telegram_lab",
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"]
    ) as client:

        print("\n=== TELEGRAM MESSAGE RESEARCH ===\n")

        async for message in client.iter_messages("me", limit=10):

            links = []

            for entity in message.entities or []:

                if isinstance(entity, MessageEntityTextUrl):
                    links.append(entity.url)

                elif isinstance(entity, MessageEntityUrl):
                    # Telegram offsets use UTF-16 code units.
                    raw = (message.raw_text or "").encode("utf-16-le")
                    start = entity.offset * 2
                    end = (entity.offset + entity.length) * 2
                    url = raw[start:end].decode("utf-16-le")
                    links.append(url)

            record = {
                "id": message.id,
                "date": message.date.isoformat(),
                "text": message.raw_text,
                "has_media": message.media is not None,
                "views": message.views,
                "forwards": message.forwards,
                "links": links
            }

            print(json.dumps(
                record,
                indent=2,
                ensure_ascii=False
            ))

            print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())
