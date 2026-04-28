from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR


metadata = MetaData()

entity_types = Table(
    "entity_types",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text),
    Column("status", Text, nullable=False),
)

fact_types = Table(
    "fact_types",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("value_kind", Text, nullable=False),
    Column("series_mode", Text, nullable=False),
    Column("description", Text),
    Column("status", Text, nullable=False),
)

relation_types = Table(
    "relation_types",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("series_mode", Text, nullable=False),
    Column("description", Text),
    Column("directed", Boolean, nullable=False),
    Column("status", Text, nullable=False),
)

entities = Table(
    "entities",
    metadata,
    Column("id", Text, primary_key=True),
    Column("type_id", Text, ForeignKey("entity_types.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("description", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("merged_into_entity_id", Text, ForeignKey("entities.id")),
)

entity_names = Table(
    "entity_names",
    metadata,
    Column("id", Text, primary_key=True),
    Column("entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("language", Text),
    Column("source_id", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

fact_series = Table(
    "fact_series",
    metadata,
    Column("id", Text, primary_key=True),
    Column("entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("fact_type_id", Text, ForeignKey("fact_types.id"), nullable=False),
    Column("mode", Text, nullable=False),
    Column("current_fact_id", Text),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

facts = Table(
    "facts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("series_id", Text, ForeignKey("fact_series.id"), nullable=False),
    Column("entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("fact_type_id", Text, ForeignKey("fact_types.id"), nullable=False),
    Column("value_json", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("valid_from", DateTime(timezone=True)),
    Column("valid_to", DateTime(timezone=True)),
    Column("recorded_from", DateTime(timezone=True), nullable=False),
    Column("recorded_to", DateTime(timezone=True)),
    Column("last_verified_at", DateTime(timezone=True)),
    Column("replaces_fact_id", Text),
    Column("replaced_by_fact_id", Text),
    Column("primary_source_fragment_id", Text),
    Column("created_by", Text, nullable=False),
)

relation_series = Table(
    "relation_series",
    metadata,
    Column("id", Text, primary_key=True),
    Column("from_entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("relation_type_id", Text, ForeignKey("relation_types.id"), nullable=False),
    Column("to_entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("mode", Text, nullable=False),
    Column("current_relation_id", Text),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

relations = Table(
    "relations",
    metadata,
    Column("id", Text, primary_key=True),
    Column("series_id", Text, ForeignKey("relation_series.id"), nullable=False),
    Column("from_entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("relation_type_id", Text, ForeignKey("relation_types.id"), nullable=False),
    Column("to_entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("valid_from", DateTime(timezone=True)),
    Column("valid_to", DateTime(timezone=True)),
    Column("recorded_from", DateTime(timezone=True), nullable=False),
    Column("recorded_to", DateTime(timezone=True)),
    Column("last_verified_at", DateTime(timezone=True)),
    Column("replaces_relation_id", Text),
    Column("replaced_by_relation_id", Text),
    Column("primary_source_fragment_id", Text),
    Column("created_by", Text, nullable=False),
)

source_accounts = Table(
    "source_accounts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("module_type", Text, nullable=False),
    Column("account_label", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("settings_json", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

sources = Table(
    "sources",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source_type", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("source_account_id", Text, ForeignKey("source_accounts.id")),
    Column("url_or_path", Text),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("raw_text", Text),
    Column("raw_blob_ref", Text),
    Column("metadata_json", JSONB),
)

attachments = Table(
    "attachments",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source_id", Text, ForeignKey("sources.id"), nullable=False),
    Column("path", Text, nullable=False),
    Column("mime_type", Text, nullable=False),
    Column("size_bytes", BigInteger),
    Column("checksum_sha256", Text),
    Column("extracted_text", Text),
    Column("auto_summary", Text),
    Column("manual_title", Text),
    Column("manual_why_saved", Text),
    Column("manual_how_to_find", Text),
    Column("manual_main_point", Text),
    Column("manual_keywords", Text),
    Column("manual_review_after", Date),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

source_fragments = Table(
    "source_fragments",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source_id", Text, ForeignKey("sources.id"), nullable=False),
    Column("locator", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("normalized_text", Text),
    Column("fragment_type", Text, nullable=False),
    Column("embedding_ref", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

search_documents = Table(
    "search_documents",
    metadata,
    Column("id", Text, primary_key=True),
    Column("entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_tsv", TSVECTOR),
    Column("built_at", DateTime(timezone=True), nullable=False),
)

review_queue = Table(
    "review_queue",
    metadata,
    Column("id", Text, primary_key=True),
    Column("item_kind", Text, nullable=False),
    Column("item_id", Text, nullable=False),
    Column("risk_level", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("priority_score", Float),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),
    Column("resolved_by", Text),
)

source_import_jobs = Table(
    "source_import_jobs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("module_type", Text, nullable=False),
    Column("source_account_id", Text),
    Column("priority", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("items_found", Integer),
    Column("items_saved", Integer),
    Column("error_text", Text),
)
