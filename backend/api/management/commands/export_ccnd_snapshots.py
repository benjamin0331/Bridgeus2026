"""Export offline CCND snapshot metrics for finished conversations.

Read-only research export. For every conversation it runs
``iter_subject_analyses`` (one analysis per test subject — H-H yields two rows,
one per participant; H-AI yields one) and writes:

  - a CSV  (one row per SUBJECT) for quick eyeballing / stats software, and
  - a JSON (the full structured analysis per subject) for deeper inspection.

Nothing is written back to the DB; no model / consumer / dialogue flow is
touched; no LLM is called.

Usage (from backend/):
    python manage.py export_ccnd_snapshots --out ./ccnd_export
    python manage.py export_ccnd_snapshots --out ./ccnd_export --status all --segments 3
"""

import csv
import json
import os
from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand

from apps.matching.services.ccnd_snapshot_analysis import iter_subject_analyses

CSV_COLUMNS = [
    "conversation_id",
    "source_type",
    "subject_owner_key",
    "final_macro_coverage",
    "final_macro_count",
    "final_micro_count",
    "new_macros_per_segment",
    "new_micros_per_segment",
    "jaccard_macro_T1T2",
    "jaccard_macro_T2T3",
    "jaccard_micro_T1T2",
    "jaccard_micro_T2T3",
    "first_appearance",
    "timed_hit_count",
    "untimed_hit_count",
    "partner_macro_count",
    "partner_micro_count",
    "anomalies",
]


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


def _pipe(seq) -> str:
    return "|".join(str(x) for x in (seq or []))


def serialize_first_appearance(novelty: dict[str, Any]) -> str:
    """"node_name@ordinal@iso" entries, in appearance order — the time series of
    how the subject's field of view unfolds."""
    parts = []
    for entry in novelty.get("first_appearance", []):
        parts.append(
            f"{entry.get('node_name')}@{entry.get('first_hit_ordinal')}@{_iso(entry.get('first_timestamp'))}"
        )
    return ";".join(parts)


def build_csv_row(conversation_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Flatten one subject analysis into a single CSV row (pure — unit-tested)."""
    summary = result.get("summary", {})
    partner = result.get("partner_side", {})
    return {
        "conversation_id": conversation_id,
        "source_type": result.get("source_type"),
        "subject_owner_key": result.get("subject_owner_key"),
        "final_macro_coverage": summary.get("final_macro_coverage"),
        "final_macro_count": summary.get("final_macro_count"),
        "final_micro_count": summary.get("final_micro_count"),
        "new_macros_per_segment": _pipe(summary.get("new_macros_per_segment")),
        "new_micros_per_segment": _pipe(summary.get("new_micros_per_segment")),
        "jaccard_macro_T1T2": summary.get("jaccard_macro_T1T2"),
        "jaccard_macro_T2T3": summary.get("jaccard_macro_T2T3"),
        "jaccard_micro_T1T2": summary.get("jaccard_micro_T1T2"),
        "jaccard_micro_T2T3": summary.get("jaccard_micro_T2T3"),
        "first_appearance": serialize_first_appearance(result.get("novelty", {})),
        "timed_hit_count": summary.get("timed_hit_count"),
        "untimed_hit_count": summary.get("untimed_hit_count"),
        "partner_macro_count": partner.get("macro_count"),
        "partner_micro_count": partner.get("micro_count"),
        "anomalies": _pipe(a.get("type") for a in result.get("anomalies", [])),
    }


def _json_default(obj: Any):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _has_content(result: dict[str, Any]) -> bool:
    summary = result.get("summary", {})
    partner = result.get("partner_side", {})
    return bool(
        summary.get("final_micro_count")
        or summary.get("untimed_hit_count")
        or partner.get("micro_count")
    )


class Command(BaseCommand):
    help = "Export offline CCND snapshot metrics (CSV + JSON) for finished conversations."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Output directory (created if missing).")
        parser.add_argument(
            "--status",
            default="closed",
            choices=["closed", "active", "all"],
            help="Which conversations to include (default: closed / finished).",
        )
        parser.add_argument(
            "--source",
            default="all",
            choices=["hh", "hai", "all"],
            help="Restrict to H-H (matches), H-AI (sessions), or both.",
        )
        parser.add_argument("--segments", type=int, default=3, help="T1/T2/T3 => 3 (default).")
        parser.add_argument(
            "--include-empty",
            action="store_true",
            help="Also emit rows for conversations with no lit tree.",
        )

    def handle(self, *args, **options):
        # Imported here so the module stays importable without app registry ready.
        from api.models import DialogueMatch, DialogueSessionRecord

        out_dir = options["out"]
        status = options["status"]
        source = options["source"]
        segments = options["segments"]
        include_empty = options["include_empty"]
        os.makedirs(out_dir, exist_ok=True)

        def _status_filter(qs):
            if status == "all":
                return qs
            return qs.filter(status=status)

        rows: list[dict[str, Any]] = []
        json_records: list[dict[str, Any]] = []

        if source in ("hh", "all"):
            for match in _status_filter(DialogueMatch.objects.all()).order_by("created_at"):
                conversation_id = match.room_id or f"match:{match.pk}"
                for result in iter_subject_analyses(match, n_segments=segments):
                    if not include_empty and not _has_content(result):
                        continue
                    rows.append(build_csv_row(conversation_id, result))
                    json_records.append({"conversation_id": conversation_id, **result})

        if source in ("hai", "all"):
            for record in _status_filter(DialogueSessionRecord.objects.all()).order_by("created_at"):
                conversation_id = record.session_id or f"session:{record.pk}"
                for result in iter_subject_analyses(record, n_segments=segments):
                    if not include_empty and not _has_content(result):
                        continue
                    rows.append(build_csv_row(conversation_id, result))
                    json_records.append({"conversation_id": conversation_id, **result})

        csv_path = os.path.join(out_dir, "ccnd_snapshots.csv")
        json_path = os.path.join(out_dir, "ccnd_snapshots.json")

        # utf-8-sig so Excel reads the Chinese node names correctly.
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(json_records, fh, ensure_ascii=False, indent=2, default=_json_default)

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(rows)} subject-row(s) -> {csv_path} and {json_path}"
            )
        )
