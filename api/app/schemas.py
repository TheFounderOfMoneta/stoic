from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EntityStatus = Literal["active", "merged", "hidden", "archived"]
SourceType = Literal[
    "note",
    "email",
    "chat_message",
    "voice_note",
    "file",
    "calendar_event",
    "web_page",
]
SourceOrigin = Literal[
    "manual_input",
    "gmail",
    "telegram",
    "google_calendar",
    "web_download",
    "file_drop",
]
SourceStatus = Literal["captured", "normalized", "processed", "error"]
FragmentType = Literal["sentence", "paragraph", "signature", "header", "metadata", "block"]
ReviewStatus = Literal["queued", "in_review", "approved", "rejected", "skipped"]
RiskLevel = Literal["low", "medium", "high"]
ReviewItemKind = Literal[
    "candidate_fact",
    "candidate_relation",
    "attachment_context",
    "merge_candidate",
    "new_entity",
]
CodexSandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
CodexApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
CodexReasoningEffort = Literal["low", "medium", "high", "xhigh"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


class CodexStatusResponse(BaseModel):
    available: bool
    version: str | None = None
    auth_present: bool
    auth_path: str
    workdir: str
    default_model: str
    models: list[str]
    sandbox_modes: list[CodexSandboxMode]
    approval_policies: list[CodexApprovalPolicy]
    reasoning_efforts: list[CodexReasoningEffort]
    default_reasoning_effort: CodexReasoningEffort
    last_update_check_at: str | None = None
    last_update_check_error: str | None = None
    error: str | None = None


class CodexChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    model: str | None = None
    reasoning_effort: CodexReasoningEffort = "medium"
    sandbox: CodexSandboxMode = "read-only"
    approval_policy: CodexApprovalPolicy = "on-request"
    attachments: list["CodexAttachment"] = Field(default_factory=list)


class CodexAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(default="application/octet-stream", max_length=100)
    data_base64: str | None = None
    text: str | None = None


class CodexUsage(BaseModel):
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None


class CodexChatResponse(BaseModel):
    conversation_id: str
    message: str
    model: str
    reasoning_effort: CodexReasoningEffort
    sandbox: CodexSandboxMode
    approval_policy: CodexApprovalPolicy
    usage: CodexUsage | None = None


MessageChannel = Literal["assistant", "telegram"]
MessageSenderRole = Literal["user", "assistant", "contact", "system"]


class MessageConversationRef(BaseModel):
    id: str
    title: str | None = None


class MessageItem(BaseModel):
    id: str
    conversation_id: str
    channel: MessageChannel
    sender_role: MessageSenderRole
    text: str
    captured_at: datetime
    sender_name: str | None = None
    external_message_id: str | None = None
    metadata_json: Any | None = None


class MessageListResponse(BaseModel):
    items: list[MessageItem]
    limit: int
    offset: int


class MessageConversationSummary(BaseModel):
    id: str
    channel: MessageChannel
    title: str
    last_message_text: str | None = None
    last_message_at: datetime
    message_count: int
    sender_name: str | None = None


class MessageConversationListResponse(BaseModel):
    items: list[MessageConversationSummary]
    limit: int
    offset: int


class AssistantMessageSendRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    model: str | None = None
    reasoning_effort: CodexReasoningEffort = "medium"
    sandbox: CodexSandboxMode = "read-only"
    approval_policy: CodexApprovalPolicy = "on-request"
    attachments: list["CodexAttachment"] = Field(default_factory=list)


class AssistantMessageSendResponse(CodexChatResponse):
    user_message_id: str
    assistant_message_id: str


class TelegramDialogSummaryResponse(BaseModel):
    id: int
    title: str
    entity: str
    entity_type: str
    kind: Literal["contact", "group"]
    username: str | None = None
    unread_count: int = 0
    message_count_hint: int | None = None
    conversation_id: str
    app_pinned: bool = False
    is_pinned: bool = False
    telegram_pinned: bool = False
    position: int | None = None
    last_message_at: datetime | None = None
    last_message_text: str | None = None


class TelegramDialogListResponse(BaseModel):
    items: list[TelegramDialogSummaryResponse]
    limit: int


class TelegramImportResponse(BaseModel):
    conversation_id: str
    imported: int
    skipped: int
    total: int


class TelegramAuthCodeRequest(BaseModel):
    phone: str = Field(min_length=3, max_length=32)


class TelegramAuthCodeResponse(BaseModel):
    phone: str
    phone_code_hash: str
    type: str
    timeout: int | None = None


class TelegramAuthSignInRequest(BaseModel):
    phone: str = Field(min_length=3, max_length=32)
    code: str = Field(min_length=1, max_length=32)
    phone_code_hash: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, max_length=128)


class TelegramAuthUserResponse(BaseModel):
    id: int
    username: str | None = None
    phone: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class TelegramSendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class TelegramDialogPinRequest(BaseModel):
    pinned: bool


class TelegramAuthStatusResponse(BaseModel):
    authorized: bool
    user: TelegramAuthUserResponse | None = None


class MetaEntityType(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str


class MetaFactType(BaseModel):
    id: str
    name: str
    value_kind: str
    series_mode: str
    description: str | None = None
    status: str


class MetaRelationType(BaseModel):
    id: str
    name: str
    series_mode: str
    description: str | None = None
    directed: bool
    status: str


class MetaResponse(BaseModel):
    entity_types: list[MetaEntityType]
    fact_types: list[MetaFactType]
    relation_types: list[MetaRelationType]


class EntitySummary(BaseModel):
    id: str
    type_id: str
    status: str
    description: str | None = None
    primary_name: str | None = None
    created_at: datetime
    updated_at: datetime
    merged_into_entity_id: str | None = None


class EntityListResponse(BaseModel):
    items: list[EntitySummary]
    limit: int
    offset: int


class EntityName(BaseModel):
    id: str
    name: str
    kind: str
    language: str | None = None
    source_id: str | None = None
    created_at: datetime


class EntityFact(BaseModel):
    id: str
    series_id: str
    fact_type_id: str
    fact_type_name: str
    value_kind: str
    value_json: Any
    status: str
    confidence: float
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    recorded_from: datetime
    recorded_to: datetime | None = None
    last_verified_at: datetime | None = None
    primary_source_fragment_id: str | None = None
    created_by: str


class EntityRelation(BaseModel):
    id: str
    series_id: str
    relation_type_id: str
    relation_type_name: str
    directed: bool
    direction: Literal["outgoing", "incoming"]
    from_entity_id: str
    from_entity_name: str | None = None
    to_entity_id: str
    to_entity_name: str | None = None
    status: str
    confidence: float
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    recorded_from: datetime
    recorded_to: datetime | None = None
    last_verified_at: datetime | None = None
    primary_source_fragment_id: str | None = None
    created_by: str


class EntityCardResponse(EntitySummary):
    names: list[EntityName]
    facts: list[EntityFact]
    relations: list[EntityRelation]


class SearchResult(BaseModel):
    kind: Literal["entity"]
    id: str
    entity_id: str
    title: str | None = None
    snippet: str | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    items: list[SearchResult]
    limit: int
    offset: int
    source: Literal["search_documents", "entity_names"]


class IncomingSourceSummary(BaseModel):
    id: str
    source_type: str
    origin: str
    source_account_id: str | None = None
    url_or_path: str | None = None
    captured_at: datetime
    status: str
    raw_blob_ref: str | None = None
    metadata_json: Any | None = None
    raw_text_preview: str | None = None


class IncomingSourceListResponse(BaseModel):
    items: list[IncomingSourceSummary]
    limit: int
    offset: int


class Attachment(BaseModel):
    id: str
    source_id: str
    path: str
    mime_type: str
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    auto_summary: str | None = None
    manual_title: str | None = None
    manual_why_saved: str | None = None
    manual_how_to_find: str | None = None
    manual_main_point: str | None = None
    manual_keywords: str | None = None
    manual_review_after: date | None = None
    created_at: datetime


class IncomingSourceDetail(IncomingSourceSummary):
    raw_text: str | None = None
    fragments_count: int = 0
    attachments_count: int = 0
    attachments: list[Attachment] = Field(default_factory=list)


class SourceFragment(BaseModel):
    id: str
    source_id: str
    locator: str
    text: str
    normalized_text: str | None = None
    fragment_type: str
    embedding_ref: str | None = None
    created_at: datetime


class SourceFragmentDetail(SourceFragment):
    source: IncomingSourceSummary | None = None


class SourceFragmentListResponse(BaseModel):
    items: list[SourceFragment]
    limit: int
    offset: int


class ReviewQueueItem(BaseModel):
    id: str
    item_kind: str
    item_id: str
    risk_level: str
    status: str
    reason: str
    priority_score: float | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class ReviewQueueResponse(BaseModel):
    items: list[ReviewQueueItem]
    limit: int
    offset: int


class TaskDeadlineCountResponse(BaseModel):
    count: int
    date: date
    timezone: str


class TaskDeadlineItem(BaseModel):
    id: str
    text: str
    deadline_at: datetime
    date: date
    time: str
    done: bool = False


class TaskDeadlineListResponse(BaseModel):
    items: list[TaskDeadlineItem]
    date: date
    timezone: str


class TaskBacklogItem(BaseModel):
    id: str
    text: str
    updated_at: datetime


class TaskBacklogResponse(BaseModel):
    items: list[TaskBacklogItem]
    limit: int
    offset: int


class CalendarEvent(BaseModel):
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    date: date
    time: str
    color: str
    location: str | None = None


class CalendarEventsResponse(BaseModel):
    items: list[CalendarEvent]
    date_from: date
    date_to: date
    timezone: str


class ErrorResponse(BaseModel):
    detail: str


class OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
