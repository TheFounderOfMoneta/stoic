import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
from shutil import which
import tempfile
from typing import Any

from fastapi import HTTPException, status

from ..config import Settings
from ..schemas import CodexApprovalPolicy, CodexAttachment, CodexReasoningEffort, CodexSandboxMode, CodexUsage


SANDBOX_MODES: list[CodexSandboxMode] = ["read-only", "workspace-write", "danger-full-access"]
APPROVAL_POLICIES: list[CodexApprovalPolicy] = ["untrusted", "on-failure", "on-request", "never"]
REASONING_EFFORTS: list[CodexReasoningEffort] = ["low", "medium", "high", "xhigh"]
_last_update_check_at: str | None = None
_last_update_check_error: str | None = None


async def get_codex_version(settings: Settings) -> tuple[bool, str | None, str | None]:
    command_path = which(settings.codex_command) or settings.codex_command
    try:
        process = await asyncio.create_subprocess_exec(
            command_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    except Exception as exc:
        return False, None, str(exc)

    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="replace").strip() or "Codex CLI failed."
        return False, None, error

    return True, stdout.decode("utf-8", errors="replace").strip(), None


def auth_path(settings: Settings) -> Path:
    return Path(settings.codex_home).expanduser() / "auth.json"


def codex_update_check_state() -> dict[str, str | None]:
    return {
        "last_update_check_at": _last_update_check_at,
        "last_update_check_error": _last_update_check_error,
    }


async def check_codex_updates(settings: Settings) -> None:
    global _last_update_check_at, _last_update_check_error

    available, _, error = await get_codex_version(settings)
    _last_update_check_at = datetime.now(timezone.utc).isoformat()
    _last_update_check_error = None if available else error


async def codex_update_check_loop(settings: Settings) -> None:
    while True:
        await check_codex_updates(settings)
        await asyncio.sleep(settings.codex_update_check_interval_seconds)


async def run_codex_chat(
    *,
    settings: Settings,
    message: str,
    conversation_id: str | None,
    model: str | None,
    reasoning_effort: CodexReasoningEffort,
    sandbox: CodexSandboxMode,
    approval_policy: CodexApprovalPolicy,
    attachments: list[CodexAttachment] | None = None,
) -> dict[str, Any]:
    selected_model = model or settings.codex_default_model
    if selected_model not in settings.codex_model_list:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Model '{selected_model}' is not allowed.",
        )

    command_path = which(settings.codex_command) or settings.codex_command
    prompt, image_paths, temp_dir = _prepare_attachments(settings.codex_workdir, message, attachments or [])
    command = [
        command_path,
        "--ask-for-approval",
        approval_policy,
        "--sandbox",
        sandbox,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "exec",
    ]

    if conversation_id:
        command.extend([
            "resume",
            "--json",
            "--skip-git-repo-check",
            "-m",
            selected_model,
        ])
        for image_path in image_paths:
            command.extend(["-i", image_path])
        command.extend([
            conversation_id,
            prompt,
        ])
    else:
        command.extend([
            "--json",
            "--skip-git-repo-check",
            "-C",
            settings.codex_workdir,
            "-m",
            selected_model,
        ])
        for image_path in image_paths:
            command.extend(["-i", image_path])
        command.append(prompt)

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.codex_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Codex did not finish before the timeout.",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Codex CLI was not found. Set CODEX_COMMAND or install codex on this host.",
        ) from exc
    finally:
        temp_dir.cleanup()

    events = _parse_jsonl(stdout.decode("utf-8", errors="replace"))
    thread_id = _thread_id(events) or conversation_id
    answer = _last_agent_message(events)
    usage = _usage(events)
    error = _error_message(events) or stderr.decode("utf-8", errors="replace").strip()

    if process.returncode != 0 or not thread_id or not answer:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error or "Codex did not return a complete response.",
        )

    return {
        "conversation_id": thread_id,
        "message": answer,
        "model": selected_model,
        "reasoning_effort": reasoning_effort,
        "sandbox": sandbox,
        "approval_policy": approval_policy,
        "usage": usage,
    }


def _prepare_attachments(
    workdir: str,
    message: str,
    attachments: list[CodexAttachment],
) -> tuple[str, list[str], tempfile.TemporaryDirectory[str]]:
    workspace = Path(workdir).expanduser().resolve()
    temp_dir = tempfile.TemporaryDirectory(prefix=".stoic-codex-", dir=str(workspace))
    image_paths: list[str] = []
    file_notes: list[str] = []
    text_blocks: list[str] = []

    for index, attachment in enumerate(attachments, start=1):
        name = _safe_filename(attachment.name, index)
        mime_type = attachment.mime_type or "application/octet-stream"
        path = Path(temp_dir.name) / name

        if attachment.text is not None:
            if len(attachment.text) > 200_000:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Attachment '{name}' is too large.",
                )
            path.write_text(attachment.text, encoding="utf-8")
            text_blocks.append(f"### {name} ({mime_type})\n{attachment.text}")
            continue

        if not attachment.data_base64:
            continue

        try:
            data = base64.b64decode(attachment.data_base64, validate=True)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Attachment '{name}' has invalid base64 data.",
            ) from exc

        if len(data) > 8_000_000:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Attachment '{name}' is too large.",
            )

        path.write_bytes(data)
        file_notes.append(f"- {name}: {path} ({mime_type})")
        if mime_type.startswith("image/"):
            image_paths.append(str(path))

    extra_sections: list[str] = []
    if text_blocks:
        extra_sections.append(
            "Text attachments have already been read by Stoic. Use their contents directly:\n\n"
            + "\n\n".join(text_blocks)
        )
    if file_notes:
        extra_sections.append(
            "Uploaded binary/image files are available during this turn at these local paths:\n"
            + "\n".join(file_notes)
            + "\nUse these paths when you need to inspect the uploaded files."
        )

    if not extra_sections:
        return message, image_paths, temp_dir

    prompt = message + "\n\n" + "\n\n".join(extra_sections)
    return prompt, image_paths, temp_dir


def _safe_filename(name: str, index: int) -> str:
    raw = Path(name).name or f"attachment-{index}"
    safe = "".join(char if char.isalnum() or char in ".-_ " else "_" for char in raw).strip()
    return safe or f"attachment-{index}"


def _parse_jsonl(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _thread_id(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") == "thread.started":
            value = event.get("thread_id")
            return value if isinstance(value, str) else None
    return None


def _last_agent_message(events: list[dict[str, Any]]) -> str | None:
    messages: list[str] = []
    for event in events:
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
    return messages[-1] if messages else None


def _usage(events: list[dict[str, Any]]) -> CodexUsage | None:
    for event in reversed(events):
        usage = event.get("usage")
        if event.get("type") == "turn.completed" and isinstance(usage, dict):
            return CodexUsage(**usage)
    return None


def _error_message(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("type") == "error" and isinstance(event.get("message"), str):
            return event["message"]
        turn_error = event.get("error")
        if isinstance(turn_error, dict) and isinstance(turn_error.get("message"), str):
            return turn_error["message"]
    return None
