import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import crawler
import ingest_to_postgres


def split_seed_and_options(argv: list[str]) -> tuple[list[str], list[str]]:
    seed_parts: list[str] = []
    forward_args: list[str] = []
    parsing_options = False

    for arg in argv:
        if not parsing_options and arg.startswith("--"):
            parsing_options = True

        if parsing_options:
            forward_args.append(arg)
        else:
            seed_parts.append(arg)

    return seed_parts, forward_args


def snapshot_news_result_files(output_dir: str) -> set[Path]:
    output_path = Path(output_dir)
    if not output_path.exists():
        return set()
    return {path.resolve() for path in output_path.glob("*_news_results.json") if path.is_file()}


def resolve_generated_news_file(output_dir: str, before_files: set[Path]) -> Path | None:
    output_path = Path(output_dir)
    if not output_path.exists():
        return None

    after_files = [path.resolve() for path in output_path.glob("*_news_results.json") if path.is_file()]
    new_files = [path for path in after_files if path not in before_files]
    candidates = new_files or after_files
    if not candidates:
        return None

    return max(candidates, key=lambda path: path.stat().st_mtime)


def upload_news_results_to_postgres(news_file: Path) -> int:
    table_name = ingest_to_postgres.validate_table_name(
        os.getenv("POSTGRES_TABLE", ingest_to_postgres.DEFAULT_TABLE)
    )
    skip_create_table = os.getenv("POSTGRES_SKIP_CREATE_TABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )

    try:
        if not skip_create_table:
            with conn.cursor() as cur:
                ingest_to_postgres.create_table(cur, table_name)
            conn.commit()

        imported_count = ingest_to_postgres.import_files(conn, table_name, [news_file])
        conn.commit()
        print(f"PostgreSQL 自動上傳完成：{news_file.name} -> {table_name}")
        return imported_count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    crawler.configure_stdio()
    seed_parts, forward_args = split_seed_and_options(sys.argv[1:])

    if seed_parts:
        os.environ[crawler.SEED_ENV_VAR] = " ".join(seed_parts).strip()
    else:
        os.environ.pop(crawler.SEED_ENV_VAR, None)

    original_argv = sys.argv[:]
    try:
        sys.argv = ["crawler.py", *forward_args]
        args = crawler.parse_cli_args()
    finally:
        sys.argv = original_argv

    seed = crawler.resolve_seed(args)
    before_files = snapshot_news_result_files(args.output_dir)
    crawler.crawl_news_by_keyword(
        seed_keyword=seed,
        output_dir=args.output_dir,
        max_expanded_keywords=args.max_expanded_keywords,
        max_news_per_keyword=args.max_news_per_keyword,
        max_total_results=args.max_total_results,
        sleep_sec=args.sleep_sec,
        gemini_model=args.gemini_model,
    )

    news_file = resolve_generated_news_file(args.output_dir, before_files)
    if news_file is None:
        raise FileNotFoundError("找不到本次產生的 news_results.json，無法自動上傳 PostgreSQL。")

    upload_news_results_to_postgres(news_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
