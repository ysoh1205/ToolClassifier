"""Pure helpers and Supabase persistence for the Streamlit tool catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from notion_tool_catalog import CatalogError, merge_tools


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SERVER_NAME_LENGTH = 200
MAX_TOOLS_PER_UPLOAD = 5_000


@dataclass(frozen=True)
class CatalogUpload:
    server_name: str
    records: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ImportResult:
    created_count: int
    updated_count: int
    total_count: int


def _load_uploaded_json(payload: bytes, *, label: str) -> Any:
    if not payload:
        raise CatalogError(f"{label} is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise CatalogError(
            f"{label} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CatalogError(f"{label} must be UTF-8 encoded.") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(
            f"Invalid JSON in {label} at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc


def parse_catalog_upload(
    tools_payload: bytes,
    output_schema_payload: bytes,
    server_name: str,
) -> CatalogUpload:
    normalized_server_name = server_name.strip()
    if not normalized_server_name:
        raise CatalogError("Server name must not be empty.")
    if len(normalized_server_name) > MAX_SERVER_NAME_LENGTH:
        raise CatalogError(
            f"Server name must be at most {MAX_SERVER_NAME_LENGTH} characters."
        )

    tools_data = _load_uploaded_json(tools_payload, label="tools JSON")
    output_data = _load_uploaded_json(
        output_schema_payload,
        label="output-schema JSON",
    )
    tools, warnings = merge_tools(tools_data, output_data)

    if len(tools) > MAX_TOOLS_PER_UPLOAD:
        raise CatalogError(
            f"An upload may contain at most {MAX_TOOLS_PER_UPLOAD} tools."
        )

    records = tuple(
        {
            "server_name": normalized_server_name,
            "tool_name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "output_keys": "",
        }
        for tool in tools
    )
    return CatalogUpload(
        server_name=normalized_server_name,
        records=records,
        warnings=tuple(warnings),
    )


def preserve_classifications(
    records: Sequence[Mapping[str, Any]],
    existing_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_name = {
        str(row["tool_name"]): row
        for row in existing_rows
        if row.get("tool_name") is not None
    }

    prepared: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item.setdefault("output_keys", "")
        existing = existing_by_name.get(str(record["tool_name"]))
        if existing:
            item["operation"] = existing.get("operation")
            item["resource_type"] = existing.get("resource_type")
            item["public_injection_point"] = existing.get(
                "public_injection_point"
            )
            if existing.get("output_keys") is not None:
                item["output_keys"] = str(existing["output_keys"])
        else:
            item["operation"] = None
            item["resource_type"] = None
            item["public_injection_point"] = None
        prepared.append(item)
    return prepared


def _response_data(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, Mapping):
        data = response.get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise CatalogError("Supabase returned an unexpected response shape.")
    return [dict(row) for row in data]


def upsert_catalog(
    admin_client: Any,
    catalog: CatalogUpload,
    *,
    batch_size: int = 200,
) -> ImportResult:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    response = (
        admin_client.table("tool_catalog")
        .select(
            "tool_name,operation,resource_type,"
            "public_injection_point,output_keys"
        )
        .eq("server_name", catalog.server_name)
        .execute()
    )
    existing_rows = _response_data(response)
    existing_names = {str(row["tool_name"]) for row in existing_rows}
    prepared = preserve_classifications(catalog.records, existing_rows)

    for start in range(0, len(prepared), batch_size):
        batch = prepared[start : start + batch_size]
        (
            admin_client.table("tool_catalog")
            .upsert(batch, on_conflict="server_name,tool_name")
            .execute()
        )

    uploaded_names = {str(record["tool_name"]) for record in catalog.records}
    updated_count = len(uploaded_names & existing_names)
    created_count = len(uploaded_names - existing_names)
    return ImportResult(
        created_count=created_count,
        updated_count=updated_count,
        total_count=len(catalog.records),
    )
