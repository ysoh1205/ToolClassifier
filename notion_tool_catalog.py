#!/usr/bin/env python3
"""Generate a Notion-importable tool catalog from MCP tool JSON files.

The generated bundle contains:

* a Markdown index with one row per tool;
* one Markdown detail page per tool, containing its input/output schemas;
* a CSV file that can be imported as an actual Notion database; and
* a ZIP archive that preserves the Markdown page hierarchy.

Only the tool name and description are pre-filled. Operation and Resource Type
are deliberately left blank for human classification in Notion.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote


OPERATION_CHOICES = ("Read", "Write", "Modify")
RESOURCE_TYPE_CHOICES = ("Private", "Open-public", "Targeted-access")


class CatalogError(ValueError):
    """Raised when an input file cannot be converted into a tool catalog."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: Any
    output_schema: Any | None


@dataclass(frozen=True)
class BuildResult:
    output_dir: Path
    index_file: Path
    csv_file: Path
    zip_file: Path | None
    tool_count: int
    warnings: tuple[str, ...]


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as exc:
        raise CatalogError(f"File not found: {path}") from exc
    except PermissionError as exc:
        raise CatalogError(f"Cannot read file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        ) from exc


def _records_from_mapping(data: Mapping[str, Any], *, source: str) -> list[dict[str, Any]]:
    """Support common MCP wrappers and name-keyed schema mappings."""

    for key in ("tools", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return _as_record_list(value, source=source)

    result = data.get("result")
    if isinstance(result, Mapping):
        return extract_records(result, source=source)

    records: list[dict[str, Any]] = []
    for name, value in data.items():
        if not isinstance(value, Mapping):
            raise CatalogError(
                f"{source} must be a list of objects, an MCP tools wrapper, "
                "or an object keyed by tool name."
            )
        record = dict(value)
        record.setdefault("name", name)
        records.append(record)
    return records


def _as_record_list(values: Sequence[Any], *, source: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise CatalogError(f"{source}[{index}] must be a JSON object.")
        records.append(dict(value))
    return records


def extract_records(data: Any, *, source: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return _as_record_list(data, source=source)
    if isinstance(data, Mapping):
        return _records_from_mapping(data, source=source)
    raise CatalogError(f"{source} must contain a JSON array or object.")


def _required_name(record: Mapping[str, Any], *, source: str, index: int) -> str:
    name = record.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CatalogError(f"{source}[{index}] has no non-empty string 'name'.")
    return name.strip()


def _schema(record: Mapping[str, Any], keys: Iterable[str]) -> Any | None:
    for key in keys:
        if key in record:
            return record[key]
    return None


def merge_tools(tool_data: Any, output_data: Any) -> tuple[list[Tool], list[str]]:
    tool_records = extract_records(tool_data, source="tools JSON")
    output_records = extract_records(output_data, source="output-schema JSON")

    output_by_name: dict[str, Any] = {}
    for index, record in enumerate(output_records):
        name = _required_name(record, source="output-schema JSON", index=index)
        if name in output_by_name:
            raise CatalogError(f"Duplicate output schema for tool: {name}")
        output_by_name[name] = _schema(
            record,
            ("output_schema", "outputSchema", "schema"),
        )

    warnings: list[str] = []
    tools: list[Tool] = []
    seen_names: set[str] = set()

    for index, record in enumerate(tool_records):
        name = _required_name(record, source="tools JSON", index=index)
        if name in seen_names:
            raise CatalogError(f"Duplicate tool name: {name}")
        seen_names.add(name)

        description_value = record.get("description", "")
        if description_value is None:
            description = ""
        elif isinstance(description_value, str):
            description = description_value.strip()
        else:
            description = str(description_value)

        input_schema = _schema(record, ("inputSchema", "input_schema"))
        if input_schema is None:
            warnings.append(f"No input schema for tool: {name}")

        if name not in output_by_name:
            warnings.append(f"No output schema record for tool: {name}")
            output_schema = None
        else:
            output_schema = output_by_name[name]
            if output_schema is None:
                warnings.append(f"Output schema is empty for tool: {name}")

        tools.append(
            Tool(
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
            )
        )

    for extra_name in sorted(output_by_name.keys() - seen_names):
        warnings.append(f"Unused output schema for unknown tool: {extra_name}")

    if not tools:
        raise CatalogError("The tools JSON contains no tools.")

    return tools, warnings


def safe_filename(value: str, *, fallback: str = "catalog") -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", normalized)
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._ ")
    return normalized[:100] or fallback


def tool_page_filename(index: int, name: str, used: set[str]) -> str:
    base = safe_filename(name, fallback="tool")
    candidate = f"{index:03d}_{base}.md"
    casefolded = candidate.casefold()
    if casefolded in used:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        candidate = f"{index:03d}_{base}_{digest}.md"
        casefolded = candidate.casefold()
    used.add(casefolded)
    return candidate


def escape_markdown_table(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )


def escape_link_label(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
    )


def json_code_block(value: Any) -> str:
    if value is None:
        return "_Not provided._\n"
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)
    longest_run = max((len(run) for run in re.findall(r"`+", rendered)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}json\n{rendered}\n{fence}\n"


def description_block(description: str) -> str:
    if not description:
        return "_No description provided._\n"
    return "\n".join(f"> {line}" if line else ">" for line in description.splitlines()) + "\n"


def render_tool_page(server_name: str, tool: Tool) -> str:
    operation_text = " / ".join(OPERATION_CHOICES)
    resource_text = " / ".join(RESOURCE_TYPE_CHOICES)
    return "".join(
        [
            f"# {tool.name}\n\n",
            f"**Server:** `{server_name}`\n\n",
            "## Description\n\n",
            description_block(tool.description),
            "\n## Human classification\n\n",
            f"- Operation: _(choose one: {operation_text})_\n",
            f"- Resource Type: _(choose one: {resource_text})_\n",
            "\n## Input Schema\n\n",
            json_code_block(tool.input_schema),
            "\n## Output Schema\n\n",
            json_code_block(tool.output_schema),
        ]
    )


def render_index(
    server_name: str,
    tools: Sequence[Tool],
    page_filenames: Sequence[str],
) -> str:
    lines = [
        f"# {server_name} Tools\n",
        f"Server: `{server_name}`\n",
        f"Tool count: {len(tools)}\n",
        "## Classification choices\n",
        f"- Operation: {', '.join(OPERATION_CHOICES)}",
        f"- Resource Type: {', '.join(RESOURCE_TYPE_CHOICES)}",
        "- Operation and Resource Type are intentionally blank for human review.\n",
        "## Tool database\n",
        "| Tool Name | Description | Operation | Resource Type |",
        "|---|---|---|---|",
    ]

    for tool, filename in zip(tools, page_filenames, strict=True):
        target = f"tools/{quote(filename)}"
        name = escape_link_label(tool.name)
        description = escape_markdown_table(tool.description)
        lines.append(f"| [{name}]({target}) | {description} |  |  |")

    lines.append("")
    return "\n".join(lines)


def write_csv(path: Path, tools: Sequence[Tool]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("Tool Name", "Description", "Operation", "Resource Type"))
        for tool in tools:
            writer.writerow((tool.name, tool.description, "", ""))


def _validate_output_target(path: Path) -> None:
    resolved = path.resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
    if resolved in forbidden:
        raise CatalogError(f"Refusing to use unsafe output directory: {resolved}")


def _remove_exact_target(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _create_zip(output_dir: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path in sorted(output_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, Path(output_dir.name) / path.relative_to(output_dir))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_catalog(
    tools_path: Path,
    output_schema_path: Path,
    server_name: str,
    output_dir: Path,
    *,
    force: bool = False,
    make_zip: bool = True,
) -> BuildResult:
    if not server_name.strip():
        raise CatalogError("Server name must not be empty.")

    tools_data = load_json(tools_path)
    output_data = load_json(output_schema_path)
    tools, warnings = merge_tools(tools_data, output_data)

    output_dir = output_dir.expanduser()
    _validate_output_target(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    zip_path = output_dir.parent / f"{output_dir.name}.zip" if make_zip else None
    existing = [path for path in (output_dir, zip_path) if path is not None and path.exists()]
    if existing and not force:
        joined = ", ".join(str(path) for path in existing)
        raise CatalogError(f"Output already exists (use --force to replace it): {joined}")

    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-build-", dir=output_dir.parent)
    )
    try:
        tools_dir = temporary_dir / "tools"
        tools_dir.mkdir()

        used_filenames: set[str] = set()
        page_filenames = [
            tool_page_filename(index, tool.name, used_filenames)
            for index, tool in enumerate(tools, start=1)
        ]

        for tool, filename in zip(tools, page_filenames, strict=True):
            (tools_dir / filename).write_text(
                render_tool_page(server_name, tool),
                encoding="utf-8",
            )

        stem = safe_filename(server_name, fallback="server")
        index_name = f"{stem}.md"
        csv_name = f"{stem}.csv"
        (temporary_dir / index_name).write_text(
            render_index(server_name, tools, page_filenames),
            encoding="utf-8",
        )
        write_csv(temporary_dir / csv_name, tools)

        if output_dir.exists():
            _remove_exact_target(output_dir)
        os.replace(temporary_dir, output_dir)

        if zip_path is not None:
            if zip_path.exists():
                _remove_exact_target(zip_path)
            _create_zip(output_dir, zip_path)

        return BuildResult(
            output_dir=output_dir.resolve(),
            index_file=(output_dir / index_name).resolve(),
            csv_file=(output_dir / csv_name).resolve(),
            zip_file=zip_path.resolve() if zip_path is not None else None,
            tool_count=len(tools),
            warnings=tuple(warnings),
        )
    finally:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Notion-ready tool database and schema page bundle.",
    )
    parser.add_argument("tools_json", type=Path, help="JSON containing tool definitions")
    parser.add_argument(
        "output_schema_json",
        type=Path,
        help="JSON containing output schemas, matched by tool name",
    )
    parser.add_argument("server_name", help="Server name shown in generated pages")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Output directory (default: ./<server_name>_notion)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the exact output directory and ZIP if they already exist",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Do not create a ZIP archive",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir or Path(
        f"{safe_filename(args.server_name, fallback='server')}_notion"
    )

    try:
        result = build_catalog(
            tools_path=args.tools_json,
            output_schema_path=args.output_schema_json,
            server_name=args.server_name,
            output_dir=output_dir,
            force=args.force,
            make_zip=not args.no_zip,
        )
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Created catalog for {result.tool_count} tools")
    print(f"Markdown index: {result.index_file}")
    print(f"Notion CSV: {result.csv_file}")
    print(f"Detail pages: {result.output_dir / 'tools'}")
    if result.zip_file is not None:
        print(f"Import ZIP: {result.zip_file}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
