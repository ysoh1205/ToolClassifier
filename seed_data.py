import argparse
import os
from pathlib import Path

from supabase import create_client

from notion_tool_catalog import load_json, merge_tools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tools_json", type=Path)
    parser.add_argument("output_schema_json", type=Path)
    parser.add_argument("server_name")
    args = parser.parse_args()

    tools, warnings = merge_tools(
        load_json(args.tools_json),
        load_json(args.output_schema_json),
    )

    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )

    records = [
        {
            "server_name": args.server_name,
            "tool_name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
        }
        for tool in tools
    ]

    client.table("tool_catalog").upsert(
        records,
        on_conflict="server_name,tool_name",
    ).execute()

    print(f"{len(records)}개 도구를 저장했습니다.")
    for warning in warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()