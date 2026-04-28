from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_session, get_write_session
from ..schemas import (
    AssistantMessageSendRequest,
    AssistantMessageSendResponse,
    MessageConversationListResponse,
    MessageItem,
    MessageListResponse,
    TelegramAuthCodeRequest,
    TelegramAuthCodeResponse,
    TelegramAuthSignInRequest,
    TelegramAuthStatusResponse,
    TelegramAuthUserResponse,
    TelegramDialogListResponse,
    TelegramDialogSummaryResponse,
    TelegramDialogPinRequest,
    TelegramImportResponse,
    TelegramSendMessageRequest,
)
from ..services.messages_service import (
    import_telegram_messages,
    list_conversation_messages,
    list_message_conversations,
    list_telegram_dialogs,
    list_telegram_messages,
    send_assistant_message,
    send_telegram_message,
    set_telegram_dialog_pin,
    telegram_auth_status,
    telegram_request_code,
    telegram_sign_in_and_store,
)


router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("/conversations", response_model=MessageConversationListResponse)
async def message_conversations(
    channel: str | None = Query(default=None, pattern="^(assistant|telegram)$"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> MessageConversationListResponse:
    items = await list_message_conversations(session, channel=channel, limit=limit, offset=offset)
    return MessageConversationListResponse(items=items, limit=limit, offset=offset)


@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def conversation_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    after: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> MessageListResponse:
    items = await list_conversation_messages(
        session,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
        after=after,
    )
    return MessageListResponse(items=items, limit=limit, offset=offset)


@router.post("/assistant", response_model=AssistantMessageSendResponse)
async def assistant_message(
    payload: AssistantMessageSendRequest,
    session: AsyncSession = Depends(get_write_session),
    settings: Settings = Depends(get_settings),
) -> AssistantMessageSendResponse:
    return await send_assistant_message(session, settings=settings, payload=payload)


@router.get("/telegram/dialogs", response_model=TelegramDialogListResponse)
async def telegram_dialogs(
    limit: int = Query(default=30, ge=1, le=500),
    changed_after: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> TelegramDialogListResponse:
    items = await list_telegram_dialogs(session, limit=limit, changed_after=changed_after)
    return TelegramDialogListResponse(items=items, limit=limit)


@router.get("/telegram/dialogs/{entity}/messages", response_model=MessageListResponse)
async def telegram_dialog_messages(
    entity: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> MessageListResponse:
    items = await list_telegram_messages(entity=entity, limit=limit)
    return MessageListResponse(items=items, limit=limit, offset=0)


@router.post("/telegram/dialogs/{entity}/import", response_model=TelegramImportResponse)
async def telegram_dialog_import(
    entity: str,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_write_session),
) -> TelegramImportResponse:
    return await import_telegram_messages(session, entity=entity, limit=limit)


@router.post("/telegram/dialogs/{entity}/messages", response_model=MessageItem)
async def telegram_dialog_send_message(
    entity: str,
    payload: TelegramSendMessageRequest,
    session: AsyncSession = Depends(get_write_session),
) -> MessageItem:
    return await send_telegram_message(session, entity=entity, payload=payload)


@router.post("/telegram/dialogs/{chat_id}/pin", response_model=TelegramDialogSummaryResponse)
async def telegram_dialog_pin(
    chat_id: int,
    payload: TelegramDialogPinRequest,
    session: AsyncSession = Depends(get_write_session),
) -> TelegramDialogSummaryResponse:
    return await set_telegram_dialog_pin(session, chat_id=chat_id, payload=payload)


@router.get("/telegram/auth/status", response_model=TelegramAuthStatusResponse)
async def telegram_status() -> TelegramAuthStatusResponse:
    return await telegram_auth_status()


@router.post("/telegram/auth/request-code", response_model=TelegramAuthCodeResponse)
async def telegram_request_login_code(payload: TelegramAuthCodeRequest) -> TelegramAuthCodeResponse:
    return await telegram_request_code(payload)


@router.post("/telegram/auth/sign-in", response_model=TelegramAuthUserResponse)
async def telegram_auth_sign_in(
    payload: TelegramAuthSignInRequest,
    session: AsyncSession = Depends(get_write_session),
) -> TelegramAuthUserResponse:
    return await telegram_sign_in_and_store(session, payload=payload)
