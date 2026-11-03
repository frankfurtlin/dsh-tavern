"""Interpret isolated verdicts against the evidence actually supplied to the gate."""
from .knowledge import parse_json_local
from .schemas import ValidationOutput
from .errors import Problem


def inspect_verdict(result, known_ids, hidden_ids):
    diagnostic = {"status": "unknown", "reason": "incomplete_response"}
    if result.get("finishReason") not in ("stop", "completed"):
        return diagnostic
    try:
        verdict = ValidationOutput.model_validate(parse_json_local(result["text"]))
    except (Problem, ValueError, TypeError, KeyError):
        return {"status": "unknown", "reason": "invalid_format"}
    diagnostic.update(verdict=verdict.verdict, reasonCode=verdict.reasonCode,
                      severity=verdict.severity, evidenceCount=len(verdict.evidenceIds))
    # hiddenOriginal belongs only to the isolated gate. A spoiler verdict can
    # legitimately cite its block IDs; it must never authorize generated prose.
    if not set(verdict.evidenceIds).issubset(set(known_ids) | set(hidden_ids)):
        return {**diagnostic, "reason": "unknown_evidence"}
    if verdict.verdict == "pass" and verdict.reasonCode == "SAFE" and verdict.severity == "none":
        return {**diagnostic, "status": "passed", "reason": "model_passed"}
    if verdict.verdict == "unknown":
        return {**diagnostic, "reason": "model_uncertain"}
    return {**diagnostic, "status": "blocked", "reason": "model_blocked"}


def rejection_code(diagnostic):
    if diagnostic.get("status") == "unknown":
        return "VALIDATION_UNCERTAIN"
    reasons = {"SPOILER": "GENERATION_SPOILER", "HISTORY_CONFLICT": "GENERATION_HISTORY_CONFLICT",
               "CHARACTER_KNOWLEDGE": "GENERATION_CHARACTER_KNOWLEDGE"}
    return reasons.get(diagnostic.get("reasonCode"), "GENERATION_BLOCKED")
