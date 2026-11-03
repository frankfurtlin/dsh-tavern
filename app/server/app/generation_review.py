"""Story checks advise the reader; durable prose and acceptance remain their choice."""
from .domain import digest

REVIEW_POLICY = "reader-choice-v1"
WARNING_CODES = {"SPOILER", "HISTORY_CONFLICT", "CHARACTER_KNOWLEDGE", "INJECTION", "UNCERTAIN", "CHECK_UNAVAILABLE", "WRITING_INTERRUPTED"}


def review_warning(diagnostic, chapter_index):
    if diagnostic.get("status") == "passed":
        return None
    code = diagnostic.get("reasonCode") if diagnostic.get("status") == "blocked" else "UNCERTAIN"
    return {"code": code if code in WARNING_CODES else "UNCERTAIN", "chapterIndex": chapter_index}


def saved_warnings(task):
    return [{"code": warning["code"], "chapterIndex": warning["chapterIndex"]}
            for warning in task.checkpoint.get("reviewWarnings", [])
            if isinstance(warning, dict) and warning.get("code") in WARNING_CODES
            and type(warning.get("chapterIndex")) is int and 1 <= warning["chapterIndex"] <= 10][:10]


def reviewed_status(warnings):
    return "unknown" if any(w["code"] in {"UNCERTAIN", "CHECK_UNAVAILABLE", "WRITING_INTERRUPTED"} for w in warnings) else "blocked" if warnings else "passed"


def candidate_acceptable(candidate, task):
    intact = bool(candidate and candidate.owner_id == task.owner_id and task.task_type == "generation"
        and candidate.persistence_state == "saved" and not candidate.partial and candidate.outcome == "complete"
        and candidate.text.strip() and candidate.content_hash == digest(candidate.text))
    advisory = task.checkpoint.get("generationReviewPolicy") == REVIEW_POLICY and bool(saved_warnings(task))
    return intact and (candidate.validation_status == "passed" or advisory and candidate.validation_status in {"blocked", "unknown"})
