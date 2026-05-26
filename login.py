import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

# Set TG_API_ID and TG_API_HASH in a .env file (see .env.example).
# NEVER hardcode credentials in source code.
API_ID = int(os.environ.get("TG_API_ID", "0"))
API_HASH = os.environ.get("TG_API_HASH", "")

if not API_ID or not API_HASH:
    raise ValueError(
        "Missing credentials. Set TG_API_ID and TG_API_HASH in a .env file. "
        "Get them at https://my.telegram.org"
    )

print("Starting one-time Telegram login activation...")
client = TelegramClient('sessions/tg_parser_session', API_ID, API_HASH)

async def main():
    print("Success! Your session is authorized and saved.")

# The `with client:` block calls client.start() implicitly, which prompts for
# phone number and OTP in the terminal.  main() runs after auth completes.
with client:
    client.loop.run_until_complete(main())