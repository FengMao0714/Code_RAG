"""Code Agent report serialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_rag.agent.models import AgentReport


def agent_report_to_dict(report: AgentReport) -> dict[str, Any]:
    """Serialize an :class:`AgentReport` to plain JSON-compatible data."""
    return {
        "task": report.task,
        "repo_path": str(report.repo_path),
        "source_type": report.resolved.identity.source_type,
        "source": report.resolved.identity.canonical_source,
        "understanding": report.understanding,
        "plan": {
            "steps": [
                {"question": step.question, "rationale": step.rationale}
                for step in report.plan.steps
            ],
        },
        "key_files": report.key_files,
        "suggested_changes": report.suggested_changes,
        "risks": report.risks,
        "suggested_tests": report.suggested_tests,
        "references": report.references,
        "insufficient_evidence": report.insufficient_evidence,
        "review_note": report.review_note,
        "evidence": [
            {
                "question": evidence.step.question,
                "file_paths": evidence.file_paths,
                "chunk_summaries": evidence.chunk_summaries,
            }
            for evidence in report.evidence
        ],
    }


def render_agent_markdown(report: AgentReport) -> str:
    """Render a human-readable Markdown report."""
    lines: list[str] = []
    lines.append("# Code Agent Report")
    lines.append("")
    lines.append(f"- Task: {report.task}")
    lines.append(f"- Repository: `{report.repo_path}`")
    lines.append(f"- Source type: `{report.resolved.identity.source_type}`")
    lines.append(f"- Insufficient evidence: `{report.insufficient_evidence}`")
    lines.append("")
    lines.append("## Understanding")
    lines.append("")
    lines.append(report.understanding or "-")
    lines.append("")
    lines.append("## Plan")
    lines.append("")
    for index, step in enumerate(report.plan.steps, start=1):
        lines.append(f"{index}. {step.question}")
        if step.rationale:
            lines.append(f"   - Rationale: {step.rationale}")
    lines.append("")
    lines.append("## Key Files")
    lines.append("")
    if report.key_files:
        lines.extend(f"- `{file_path}`" for file_path in report.key_files)
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Suggested Changes")
    lines.append("")
    lines.extend(f"- {item}" for item in report.suggested_changes)
    lines.append("")
    lines.append("## Risks")
    lines.append("")
    lines.extend(f"- {item}" for item in (report.risks or ["None"]))
    lines.append("")
    lines.append("## Suggested Tests")
    lines.append("")
    lines.extend(f"- `{item}`" for item in report.suggested_tests)
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    for evidence in report.evidence:
        lines.append(f"### {evidence.step.question}")
        if evidence.file_paths:
            lines.append("Files: " + ", ".join(f"`{fp}`" for fp in evidence.file_paths))
        if evidence.chunk_summaries:
            lines.extend(f"- `{summary}`" for summary in evidence.chunk_summaries)
        else:
            lines.append("- No evidence found")
        lines.append("")
    lines.append("## Reviewer Note")
    lines.append("")
    lines.append(report.review_note or "-")
    return "\n".join(lines).rstrip() + "\n"


def write_agent_report(report: AgentReport, output_path: str | Path, *, fmt: str) -> Path:
    """Write an Agent report as ``markdown`` or ``json``."""
    normalized = (fmt or "").strip().lower()
    if normalized not in {"markdown", "json"}:
        raise ValueError(f"不支持的 Agent 报告格式: {fmt}（应为 markdown/json）")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if normalized == "json":
        path.write_text(
            json.dumps(agent_report_to_dict(report), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        path.write_text(render_agent_markdown(report), encoding="utf-8")
    return path
