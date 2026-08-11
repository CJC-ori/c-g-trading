"""Write a row to the append surface — ``research_notes`` (migration 106).

This module IS the API for appending to the case-notes surface. Every skill
that answers a scoped question (an investigation, a registered forecast, a
BOTEC registration, a correction finding, a durable observation) appends
through here so every row is validated identically and every org-anchored note
lands on the org timeline (``events``).

Semantics
---------
* One row per answered scoped question. ``kind`` discriminates the renderer;
  ``details`` carries the kind-specific structured payload (shapes documented
  in ``db/migrations/106_research_notes.sql``).
* ``status`` is DERIVED, not chosen: a note with ``answer_md`` is stored
  ``answered`` (with ``answered_at = now()``); without it, ``open``. You can
  never insert a new note as ``superseded`` — supersession happens to the OLD
  note, below.
* REPLACE / SUPERSEDE: pass ``supersedes_note_id`` to replace an earlier note.
  In the SAME transaction the old note gets ``status='superseded'`` and the new
  row is inserted pointing back at it. Superseded notes are never deleted —
  the UI dims them and links to the successor. A forecast is *resolved* the
  same way: a superseding note whose ``details.resolution`` records the
  outcome.
* Forecast notes must carry ``details.resolution_criteria``,
  ``details.probability_or_range`` and ``details.review_by_date`` (ISO date);
  the date is mirrored into the indexed ``review_by`` column so the
  future forecasts-due sweep can query it without a jsonb scan. NOTE: no
  consumer exists yet — nothing today actions a review date, so never tell
  Chris a forecast will resurface on its own.
* BOTEC notes must carry ``details.headlines``, ``details.scenarios`` and
  ``details.workbook_path`` (the web-readable extraction of the workbook —
  Vercel cannot read repo files at runtime, so extraction happens here, at
  registration time).
* Org-anchored notes emit an ``events`` row (``research_note_added``) so the
  note is recorded against the org. NOTE: no page renders ``events`` yet, so
  the note's visible home is the org / person / grant page Case Notes section
  and ``/notes`` — not a timeline. Entity-less notes emit no event at all
  (``events.organization_id`` is NOT NULL).
* A grant- or person-anchored note gets its ``organization_id`` DERIVED at
  write time when the caller omitted it (from ``grants.organization_id`` /
  the person's current org), so it also lands in that org's Case Notes instead
  of being visible only on the grant/person page.

Failures are LOUD: every validation problem raises :class:`StoreNoteError`
(CLI: message on stderr, exit 1). Nothing is silently defaulted except the
documented ``kind='investigation'``.

CLI usage (JSON on stdin, mirrors tools/research/store_report.py)::

    echo '{"question": "...", "answer_md": "...", "source_urls": [...],
           "kind": "investigation", "organization_id": "...",
           "source_tool": "correct-record", "created_by": "fable-5"}' \\
        | uv run python tools/notes/store_note.py

On success prints the stored row summary as JSON to stdout.

Public API:
  - ``store_note(payload, conn=None, commit=True) -> dict``
  - ``list_notes(...) -> list[dict]`` (also exposed as the MCP list_notes tool)
  - ``validate_note(payload) -> dict`` (pure — no DB; unit-tested)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re as _re
import sys
import uuid as _uuid
from typing import Any

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
from tools.shared.db import get_conn, rows_to_dicts  # noqa: E402

KINDS = ("investigation", "forecast", "botec", "correction", "observation")

# details keys that MUST be present per kind (loud contract — the renderer and
# the calibration sweep both rely on them).
REQUIRED_DETAILS: dict[str, tuple[str, ...]] = {
    "forecast": ("resolution_criteria", "probability_or_range", "review_by_date"),
    "botec": ("headlines", "scenarios", "workbook_path"),
}

_ANCHOR_FIELDS = ("organization_id", "person_id", "grant_id")
_ISO_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StoreNoteError(ValueError):
    """A validation or lookup failure. Message is the whole story."""


def _require_uuid(value: Any, field: str) -> str:
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise StoreNoteError(f"{field} is not a valid UUID: {value!r}")


def validate_note(payload: dict) -> dict:
    """Validate + normalize a note payload. Pure — no DB access.

    Returns a normalized dict with exactly the columns to insert. Raises
    :class:`StoreNoteError` on the first problem found.
    """
    if not isinstance(payload, dict):
        raise StoreNoteError("payload must be a JSON object")

    question = (payload.get("question") or "").strip()
    if not question:
        raise StoreNoteError("question is required and must be non-empty")

    source_tool = (payload.get("source_tool") or "").strip()
    if not source_tool:
        raise StoreNoteError("source_tool is required (which skill/tool wrote this?)")

    kind = payload.get("kind") or "investigation"
    if kind not in KINDS:
        raise StoreNoteError(f"kind must be one of {KINDS}, got {kind!r}")

    answer_md = payload.get("answer_md")
    if answer_md is not None and not isinstance(answer_md, str):
        raise StoreNoteError("answer_md must be a string (markdown) or omitted")
    if answer_md is not None and not answer_md.strip():
        raise StoreNoteError("answer_md is empty — omit it for an open note")

    # status is derived; an explicit status may only confirm the derivation.
    derived_status = "answered" if answer_md else "open"
    explicit = payload.get("status")
    if explicit is not None and explicit != derived_status:
        raise StoreNoteError(
            f"status is derived from answer_md ({derived_status!r}); "
            f"got explicit {explicit!r}. New notes are never 'superseded' — "
            "pass supersedes_note_id to supersede an OLD note."
        )

    source_urls = payload.get("source_urls") or []
    if not isinstance(source_urls, list) or not all(
        isinstance(u, str) and u.strip() for u in source_urls
    ):
        raise StoreNoteError("source_urls must be a list of non-empty URL strings")

    details = payload.get("details") or {}
    if not isinstance(details, dict):
        raise StoreNoteError("details must be a JSON object")
    for key in REQUIRED_DETAILS.get(kind, ()):
        if key not in details or details[key] in (None, "", [], {}):
            raise StoreNoteError(
                f"kind={kind!r} requires details.{key} "
                f"(required keys: {REQUIRED_DETAILS[kind]})"
            )

    review_by = None
    if kind == "forecast":
        raw = str(details["review_by_date"]).strip()
        # date.fromisoformat is LOOSER than the contract on py>=3.11 (it takes
        # "20271231" and full datetimes), so pin the shape the message states.
        if not _ISO_DATE_RE.match(raw):
            raise StoreNoteError(
                f"details.review_by_date must be an ISO date (YYYY-MM-DD), got {raw!r}"
            )
        try:
            review_by = _dt.date.fromisoformat(raw)
        except ValueError:
            raise StoreNoteError(
                f"details.review_by_date must be an ISO date (YYYY-MM-DD), got {raw!r}"
            )
        if review_by < _dt.date.today():
            raise StoreNoteError(
                f"details.review_by_date {raw} is in the past — a forecast "
                "cannot be born overdue. Set the date you actually want to "
                "revisit this."
            )

    anchors = {}
    for field in _ANCHOR_FIELDS:
        val = payload.get(field)
        if val is None:
            anchors[field] = None
            continue
        if isinstance(val, str) and not val.strip():
            # "" used to be coerced to NULL, turning a typo into an invisible
            # anchor-less ecosystem note instead of a loud refusal.
            raise StoreNoteError(
                f"{field} is an empty string — omit the key entirely for an "
                "anchor-less ecosystem note, or pass a real UUID"
            )
        anchors[field] = _require_uuid(val, field)

    supersedes = payload.get("supersedes_note_id")
    supersedes = _require_uuid(supersedes, "supersedes_note_id") if supersedes else None

    unknown = (
        set(payload)
        - {
            "question",
            "answer_md",
            "source_urls",
            "kind",
            "details",
            "status",
            "supersedes_note_id",
            "source_tool",
            "created_by",
        }
        - set(_ANCHOR_FIELDS)
    )
    if unknown:
        raise StoreNoteError(f"unknown fields (typo?): {sorted(unknown)}")

    return {
        "question": question,
        "answer_md": answer_md,
        "source_urls": source_urls,
        "kind": kind,
        "details": details,
        "status": derived_status,
        "review_by": review_by,
        "supersedes_note_id": supersedes,
        "source_tool": source_tool,
        "created_by": payload.get("created_by"),
        **anchors,
    }


def store_note(payload: dict, conn=None, commit: bool = True) -> dict:
    """Validate and insert a research_notes row (+ supersession + event).

    All writes happen in ONE transaction:
      1. derive ``organization_id`` from ``grant_id`` / ``person_id`` when the
         caller supplied one of those and no org (so the note is not visible
         only on the grant/person page);
      2. if ``supersedes_note_id``: lock the old note, verify it exists, is not
         already superseded, is the same kind, and that the new note keeps the
         old note's anchors; set ``status='superseded'``;
      3. INSERT the new note;
      4. if org-anchored: INSERT the ``research_note_added`` events row
         (idempotent via the events unique-source index).

    ``conn``/``commit`` exist for tests and transactional smoke checks; normal
    callers pass neither. Raises :class:`StoreNoteError` loudly on any
    problem; psycopg2 errors (bad FK etc.) propagate untouched.
    """
    if conn is None and not commit:
        # An own-connection call with commit=False opens a connection, skips
        # the commit, and rolls everything back in `finally` — while still
        # returning a success dict carrying an id that does not exist. The
        # combination only makes sense with a caller-supplied conn.
        raise StoreNoteError(
            "commit=False requires a caller-supplied conn — otherwise the "
            "transaction is rolled back on close and the returned id is a lie"
        )
    row = validate_note(payload)
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        cur = conn.cursor()

        # Derive the org anchor. migration 106's header asserts "a
        # grant-specific note is also about its org", but nothing enforced it:
        # a /forecast invoked from /grant-writeup sets grant_id only, and the
        # note then never appeared in the org's Case Notes (which filter on
        # organization_id) and emitted no event.
        if not row["organization_id"]:
            if row["grant_id"]:
                cur.execute(
                    "SELECT organization_id FROM grants WHERE id = %s",
                    (row["grant_id"],),
                )
                got = cur.fetchone()
                if got and got[0]:
                    row["organization_id"] = str(got[0])
            elif row["person_id"]:
                cur.execute(
                    "SELECT current_organization_id FROM people WHERE id = %s",
                    (row["person_id"],),
                )
                got = cur.fetchone()
                if got and got[0]:
                    row["organization_id"] = str(got[0])

        if row["supersedes_note_id"]:
            cur.execute(
                "SELECT status, kind, organization_id, person_id, grant_id "
                "FROM research_notes WHERE id = %s FOR UPDATE",
                (row["supersedes_note_id"],),
            )
            old = cur.fetchone()
            if old is None:
                raise StoreNoteError(
                    f"supersedes_note_id {row['supersedes_note_id']} does not exist"
                )
            if old[0] == "superseded":
                raise StoreNoteError(
                    f"note {row['supersedes_note_id']} is already superseded — "
                    "supersede the CURRENT note in the chain instead"
                )
            if old[1] != row["kind"]:
                raise StoreNoteError(
                    f"kind mismatch: a {row['kind']!r} note cannot supersede a "
                    f"{old[1]!r} note — supersession replaces a note with a newer "
                    "version of the SAME kind"
                )
            # The successor must keep every anchor the old note had. Dropping
            # one leaves that page showing a dimmed, struck-through note whose
            # replacement is nowhere on the page — a retracted finding with no
            # visible correction. Adding anchors is fine.
            dropped = [
                name
                for name, old_val in zip(_ANCHOR_FIELDS, old[2:5])
                if old_val is not None and str(old_val) != (row[name] or "")
            ]
            if dropped:
                raise StoreNoteError(
                    f"the superseding note drops anchor(s) {dropped} that note "
                    f"{row['supersedes_note_id']} carries. The successor must keep "
                    "them (it may add more), or the old note is left retracted on "
                    "a page where its replacement never appears."
                )
            cur.execute(
                "UPDATE research_notes SET status = 'superseded', updated_at = NOW() "
                "WHERE id = %s",
                (row["supersedes_note_id"],),
            )

        cur.execute(
            """
            INSERT INTO research_notes (
                question, answer_md, source_urls, kind, details, status,
                answered_at, review_by, organization_id, person_id, grant_id,
                supersedes_note_id, source_tool, created_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                CASE WHEN %s = 'answered' THEN NOW() END,
                %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id, status, created_at
            """,
            (
                row["question"],
                row["answer_md"],
                row["source_urls"],
                row["kind"],
                json.dumps(row["details"]),
                row["status"],
                row["status"],
                row["review_by"],
                row["organization_id"],
                row["person_id"],
                row["grant_id"],
                row["supersedes_note_id"],
                row["source_tool"],
                row["created_by"],
            ),
        )
        note_id, status, created_at = cur.fetchone()

        if row["organization_id"]:
            cur.execute(
                """
                INSERT INTO events (
                    organization_id, person_id, grant_id, event_type, event_date,
                    title, summary, severity, source_tool,
                    source_record_table, source_record_id
                ) VALUES (%s, %s, %s, 'research_note_added', NOW(),
                          %s, %s, 'info', %s, 'research_notes', %s)
                ON CONFLICT (source_record_table, source_record_id, event_type)
                DO NOTHING
                """,
                (
                    row["organization_id"],
                    row["person_id"],
                    row["grant_id"],
                    f"Case note ({row['kind']}): {row['question'][:120]}",
                    (row["answer_md"] or "")[:300] or None,
                    row["source_tool"],
                    note_id,
                ),
            )

        if commit:
            conn.commit()
        return {
            "id": str(note_id),
            "kind": row["kind"],
            "status": status,
            "superseded_note_id": row["supersedes_note_id"],
            "organization_id": row["organization_id"],
            "person_id": row["person_id"],
            "grant_id": row["grant_id"],
            "created_at": str(created_at),
        }
    except Exception:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def list_notes(
    organization_id: str | None = None,
    person_id: str | None = None,
    grant_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    include_superseded: bool = True,
    limit: int = 50,
    conn=None,
) -> list[dict]:
    """List research_notes rows, newest first. Read-only.

    Filters AND together; no filter returns the newest notes across the whole
    surface. ``include_superseded=False`` hides replaced notes (the UI shows
    them dimmed; agents usually want them hidden).
    """
    clauses, params = [], []
    if organization_id:
        clauses.append("organization_id = %s")
        params.append(_require_uuid(organization_id, "organization_id"))
    if person_id:
        clauses.append("person_id = %s")
        params.append(_require_uuid(person_id, "person_id"))
    if grant_id:
        clauses.append("grant_id = %s")
        params.append(_require_uuid(grant_id, "grant_id"))
    if kind:
        if kind not in KINDS:
            raise StoreNoteError(f"kind must be one of {KINDS}, got {kind!r}")
        clauses.append("kind = %s")
        params.append(kind)
    if status:
        clauses.append("status = %s")
        params.append(status)
    if not include_superseded:
        clauses.append("status != 'superseded'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, question, answer_md, source_urls, kind, details, status,
                   answered_at, review_by, organization_id, person_id, grant_id,
                   supersedes_note_id, source_tool, created_by, created_at
            FROM research_notes {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (*params, max(1, min(int(limit), 200))),
        )
        return rows_to_dicts(cur)
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"store_note: stdin is not valid JSON: {exc}", file=sys.stderr)
        return 1
    try:
        result = store_note(payload)
    except StoreNoteError as exc:
        print(f"store_note: REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
