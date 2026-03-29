from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

sys.dont_write_bytecode = True

from env_utils import load_dotenv_file


DEFAULT_TABLE = "news"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import JSON files into PostgreSQL as JSONB rows."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="JSON file path(s) or directory path(s). Directories will import all *.json files.",
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"Target PostgreSQL table name. Default: {DEFAULT_TABLE}",
    )
    parser.add_argument(
        "--dbname",
        default=os.getenv("POSTGRES_DB", "postgres"),
        help="PostgreSQL database name.",
    )
    parser.add_argument(
        "--user",
        default=os.getenv("POSTGRES_USER", "postgres"),
        help="PostgreSQL user.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("POSTGRES_PASSWORD", ""),
        help="PostgreSQL password.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("POSTGRES_HOST", "localhost"),
        help="PostgreSQL host.",
    )
    parser.add_argument(
        "--port",
        default=os.getenv("POSTGRES_PORT", "5432"),
        help="PostgreSQL port.",
    )
    parser.add_argument(
        "--skip-create-table",
        action="store_true",
        help="Skip CREATE TABLE IF NOT EXISTS before import.",
    )
    return parser.parse_args()


def collect_json_files(raw_paths: list[str]) -> list[Path]:
    files: list[Path] = []

    for raw_path in raw_paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(sorted(p for p in path.glob("*.json") if p.is_file()))
        elif path.is_file() and path.suffix.lower() == ".json":
            files.append(path)
        else:
            raise FileNotFoundError(f"找不到 JSON 檔案或資料夾：{raw_path}")

    unique_files: list[Path] = []
    seen: set[Path] = set()
    for file_path in files:
        resolved = file_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_files.append(file_path)

    if not unique_files:
        raise FileNotFoundError("沒有找到可匯入的 JSON 檔案。")

    return unique_files


def validate_table_name(table_name: str) -> str:
    if not table_name.replace("_", "").isalnum():
        raise ValueError(f"不合法的資料表名稱：{table_name}")
    return table_name


def create_table(cur, table_name: str) -> None:
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id BIGSERIAL PRIMARY KEY,
            file_name TEXT NOT NULL,
            data JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def load_json(file_path: Path):
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def import_files(conn, table_name: str, files: Iterable[Path]) -> int:
    import psycopg2.extras

    imported_count = 0
    with conn.cursor() as cur:
        for file_path in files:
            data = load_json(file_path)
            cur.execute(
                f"""
                INSERT INTO {table_name} (file_name, data)
                VALUES (%s, %s)
                """,
                (file_path.name, psycopg2.extras.Json(data)),
            )
            imported_count += 1
            print(f"已匯入：{file_path}")
    return imported_count


def main() -> None:
    load_dotenv_file()
    args = parse_args()
    table_name = validate_table_name(args.table)
    files = collect_json_files(args.paths)

    import psycopg2

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
    )

    try:
        if not args.skip_create_table:
            with conn.cursor() as cur:
                create_table(cur, table_name)
            conn.commit()

        imported_count = import_files(conn, table_name, files)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"完成，共匯入 {imported_count} 個 JSON 檔案到資料表 {table_name}")


if __name__ == "__main__":
    main()
