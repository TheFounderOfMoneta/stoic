from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .client import TelegramUserClient
from .config import get_telegram_settings
from .transformers import telegram_message_to_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stoic Telegram user ingest CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show current authorization status")

    send_code = subparsers.add_parser("send-code", help="Request Telegram login code")
    send_code.add_argument("--phone", required=False)

    sign_in = subparsers.add_parser("sign-in", help="Complete Telegram login")
    sign_in.add_argument("--phone", required=False)
    sign_in.add_argument("--code", required=True)
    sign_in.add_argument("--password", required=False)

    dialogs = subparsers.add_parser("dialogs", help="List recent dialogs")
    dialogs.add_argument("--limit", type=int, default=25)

    messages = subparsers.add_parser("messages", help="List messages from a dialog")
    messages.add_argument("--entity", required=True, help="Username, phone, id or invite entity")
    messages.add_argument("--limit", type=int, default=50)
    messages.add_argument("--as-sources", action="store_true")

    export_cmd = subparsers.add_parser("export", help="Export messages to JSON")
    export_cmd.add_argument("--entity", required=True)
    export_cmd.add_argument("--limit", type=int, default=200)
    export_cmd.add_argument("--output", required=False)

    return parser


async def run_command(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_telegram_settings()
    client = TelegramUserClient(settings)
    await client.connect()

    try:
        if args.command == "status":
            return {
                "authorized": await client.is_authorized(),
                "session_path": str(settings.session_path),
                "me": await client.me(),
            }

        if args.command == "send-code":
            phone = args.phone or settings.phone
            if not phone:
                raise ValueError("Phone is required. Pass --phone or set TELEGRAM_PHONE.")
            return await client.request_login_code(phone)

        if args.command == "sign-in":
            phone = args.phone or settings.phone
            if not phone:
                raise ValueError("Phone is required. Pass --phone or set TELEGRAM_PHONE.")
            return await client.sign_in(phone=phone, code=args.code, password=args.password)

        if not await client.is_authorized():
            raise ValueError("Telegram session is not authorized. Run send-code and sign-in first.")

        if args.command == "dialogs":
            dialogs = await client.list_dialogs(limit=args.limit)
            return {
                "items": [item.model_dump(mode="json") for item in dialogs],
                "count": len(dialogs),
            }

        if args.command == "messages":
            items = await client.list_messages(entity=args.entity, limit=args.limit)
            payload = [item.model_dump(mode="json") for item in items]
            if args.as_sources:
                payload = [telegram_message_to_source(item) for item in items]
            return {"items": payload, "count": len(items)}

        if args.command == "export":
            output = Path(args.output) if args.output else None
            export_path = await client.export_messages(
                entity=args.entity,
                limit=args.limit,
                output=output,
            )
            return {"output": str(export_path)}

        raise ValueError(f"Unknown command: {args.command}")
    finally:
        await client.disconnect()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = asyncio.run(run_command(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
