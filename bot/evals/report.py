"""Report emission for the eval ladder.

Every rung writes the same pair of artifacts to ``reports/evals/``:

* ``<name>.json`` — the machine-readable payload (what CI asserts on);
* ``<name>.md``   — the same payload rendered for a human.

The Markdown is generated from an explicit section list rather than by
introspecting the JSON, so a rung can order and caption its own numbers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

__all__ = ["reports_dir", "md_table", "write_report"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def reports_dir() -> Path:
    d = Path(os.environ.get("EVAL_REPORTS_DIR", repo_root() / "reports" / "evals"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v != v:  # NaN
            return "nan"
        if abs(v) >= 1000 or (v != 0 and abs(v) < 1e-4):
            return f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.2e}"
        return f"{v:.4f}"
    return str(v)


def md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Render a GitHub-flavoured Markdown table."""
    out = ["| " + " | ".join(str(h) for h in headers) + " |"]
    out.append("|" + "|".join("---" for _ in headers) + "|")
    for r in rows:
        out.append("| " + " | ".join(_fmt(c) for c in r) + " |")
    return "\n".join(out)


def write_report(
    name: str,
    title: str,
    payload: dict[str, Any],
    sections: Sequence[tuple[str, str]],
    verdict: str | None = None,
) -> tuple[Path, Path]:
    """Write ``<name>.json`` and ``<name>.md``; return both paths.

    ``sections`` is a list of ``(heading, markdown_body)`` pairs.
    """
    d = reports_dir()
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {"eval": name, "generated_at": generated, **payload}
    if verdict is not None:
        payload["verdict"] = verdict

    jpath = d / f"{name}.json"
    jpath.write_text(json.dumps(payload, indent=2, default=str) + "\n")

    lines = [f"# {title}", "", f"*Generated {generated} — `python -m bot.evals.{name}`*", ""]
    if verdict is not None:
        lines += [f"**Verdict: {verdict}**", ""]
    for heading, body in sections:
        lines += [f"## {heading}", "", body.rstrip(), ""]
    mpath = d / f"{name}.md"
    mpath.write_text("\n".join(lines))
    return jpath, mpath
