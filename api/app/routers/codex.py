from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..schemas import CodexChatRequest, CodexChatResponse, CodexStatusResponse
from ..services.codex_service import (
    APPROVAL_POLICIES,
    REASONING_EFFORTS,
    SANDBOX_MODES,
    auth_path,
    codex_update_check_state,
    get_codex_version,
    run_codex_chat,
)


router = APIRouter(prefix="/codex", tags=["codex"])


@router.get("/status", response_model=CodexStatusResponse)
async def codex_status(settings: Settings = Depends(get_settings)) -> CodexStatusResponse:
    available, version, error = await get_codex_version(settings)
    path = auth_path(settings)
    update_state = codex_update_check_state()

    return CodexStatusResponse(
        available=available,
        version=version,
        auth_present=path.exists(),
        auth_path=str(path),
        workdir=settings.codex_workdir,
        default_model=settings.codex_default_model,
        models=settings.codex_model_list,
        sandbox_modes=SANDBOX_MODES,
        approval_policies=APPROVAL_POLICIES,
        reasoning_efforts=REASONING_EFFORTS,
        default_reasoning_effort=settings.codex_default_reasoning_effort,  # type: ignore[arg-type]
        last_update_check_at=update_state["last_update_check_at"],
        last_update_check_error=update_state["last_update_check_error"],
        error=error,
    )


@router.post("/chat", response_model=CodexChatResponse)
async def codex_chat(
    payload: CodexChatRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    return await run_codex_chat(
        settings=settings,
        message=payload.message,
        conversation_id=payload.conversation_id,
        model=payload.model,
        reasoning_effort=payload.reasoning_effort,
        sandbox=payload.sandbox,
        approval_policy=payload.approval_policy,
        attachments=payload.attachments,
    )
