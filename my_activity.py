import asyncio
import os

from telethon import TelegramClient
from telethon.errors import RPCError


async def main():
    async with TelegramClient(
        "telegram_lab",
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"]
    ) as client:

        me = await client.get_me()

        print("\n=== MY TELEGRAM ACTIVITY ===")
        print(f"My account ID: {me.id}\n")

        total = 0
        checked = 0

        async for dialog in client.iter_dialogs():

            if not dialog.is_group:
                continue

            checked += 1

            print(f"\nChecking: {dialog.name}")

            try:
                async for message in client.iter_messages(
                    dialog.entity,
                    from_user="me",
                    limit=100
                ):
                    if message.sender_id != me.id:
                        continue

                    total += 1

                    print(
                        f"[{message.date}] "
                        f"Message #{message.id}: "
                        f"{(message.raw_text or '')[:120]}"
                    )

            except RPCError as error:
                print(
                    f"Could not inspect this group: "
                    f"{type(error).__name__}"
                )

            # Keep this initial experiment deliberately small.
            if checked >= 5:
                break

        print("\n=== SUMMARY ===")
        print(f"Groups checked: {checked}")
        print(f"Messages found: {total}")


if __name__ == "__main__":
    asyncio.run(main())
