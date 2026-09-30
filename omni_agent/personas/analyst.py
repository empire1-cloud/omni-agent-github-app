"""Analyst persona: produces a mini-spec for a task."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from omni_agent.llm_client import LLMClient, LLMUnavailable


logger = logging.getLogger("omni_agent.analyst")


SYSTEM_PROMPT = (
    "You are the Analyst persona inside an Omni-Agent. "
    "Given a single task description and triage info, produce a strict JSON mini-spec with keys: "
    "scope (string), assumptions (array of strings), acceptance_criteria (array of strings), "
    "impacted_files (array of relative path strings). "
    "Return JSON ONLY, no prose."
)


def _task_acceptance(task: Dict[str, Any]) -> List[str]:
    """Acceptance criteria written by a person under the task (see scanner.ACCEPTANCE_RE)."""
    raw = task.get("acceptance_criteria")
    if not raw:
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [str(i) for i in items if i] if isinstance(items, list) else []


def _rule_based_spec(task: Dict[str, Any], triage: Dict[str, Any]) -> Dict[str, Any]:
    text = task["normalized_text"]
    ttype = triage.get("task_type", "backend")
    path_hints = triage.get("path_hints", []) or []
    missing = triage.get("missing_context", []) or []

    scope = f"[{ttype}] {text}"

    assumptions = [
        "Operate strictly within allowed paths defined by guardrails.",
        "No hidden architecture is assumed; only public, observable behavior.",
    ]
    if path_hints:
        assumptions.append(f"Target file path(s) inferred: {', '.join(path_hints)}")

    acceptance_criteria = _task_acceptance(task)
    if path_hints:
        for p in path_hints:
            acceptance_criteria.append(f"File '{p}' exists and is syntactically valid.")
    if ttype == "backend":
        acceptance_criteria.append("New backend module has at least one public callable defined.")
        acceptance_criteria.append("No imports of forbidden modules; lint passes (ruff or py_compile).")
    elif ttype == "docs":
        acceptance_criteria.append("Documentation file is updated and contains the requested section.")
    elif ttype == "tests":
        acceptance_criteria.append("New tests run and pass under pytest.")
    elif ttype == "frontend":
        acceptance_criteria.append("Frontend source builds without lint errors.")
    elif ttype == "infra":
        acceptance_criteria.append("Infra/config file is valid (e.g., yaml.safe_load passes).")
    if not acceptance_criteria:
        acceptance_criteria.append("Task description is implemented end-to-end without modifying forbidden paths.")

    acceptance_criteria = list(dict.fromkeys(acceptance_criteria))
    impacted_files = list(path_hints)
    if not impacted_files:
        if ttype == "backend":
            impacted_files = ["backend/app/services/<derived>.py"]
        elif ttype == "docs":
            impacted_files = ["omni_agent/README.md"]

    spec = {
        "scope": scope,
        "assumptions": assumptions,
        "acceptance_criteria": acceptance_criteria,
        "impacted_files": impacted_files,
        "missing_context": missing,
    }
    return spec


def run(task: Dict[str, Any], triage: Dict[str, Any], llm: LLMClient, persona_mode: str) -> Dict[str, Any]:
    """Returns dict: {spec: {...}, mode_used: 'llm'|'rule'}"""
    if persona_mode in ("llm", "hybrid"):
        try:
            user_payload = {
                "task_id": task["id"],
                "task_text": task["normalized_text"],
                "stated_acceptance_criteria": _task_acceptance(task),
                "triage": {
                    "task_type": triage.get("task_type"),
                    "priority": triage.get("priority"),
                    "path_hints": triage.get("path_hints", []),
                    "missing_context": triage.get("missing_context", []),
                    "dependencies": triage.get("dependencies", []),
                },
            }
            res = llm.call(
                SYSTEM_PROMPT,
                "Task payload:\n" + json.dumps(user_payload, indent=2),
                session_id=f"analyst-{task['id']}",
            )
            parsed = LLMClient.extract_json(res.text)
            if parsed and "acceptance_criteria" in parsed:
                # ensure missing_context preserved
                parsed.setdefault("missing_context", triage.get("missing_context", []))
                return {"spec": parsed, "mode_used": "llm"}
            logger.warning("analyst: LLM output did not parse to expected JSON; falling back to rule mode")
        except LLMUnavailable as e:
            if persona_mode == "llm":
                raise
            logger.info("analyst: LLM unavailable (%s); falling back to rule mode", e)
    return {"spec": _rule_based_spec(task, triage), "mode_used": "rule"}
