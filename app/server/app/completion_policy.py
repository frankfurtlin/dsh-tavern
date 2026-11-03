"""Completion limits include MiMo's private thinking and the final answer."""
from urllib.parse import urlsplit


POLICY_VERSION = "completion-v3"


def is_mimo_go(profile):
    endpoint = urlsplit(profile.base_url)
    return (endpoint.scheme == "https" and endpoint.hostname == "opencode.ai"
            and endpoint.path.rstrip("/") == "/zen/go/v1"
            and profile.model_id == "mimo-v2.6-flash")


def completion_policy(profile, *, world=False):
    explicit_thinking = is_mimo_go(profile)
    # The finite enabled-thinking experiment had truncated analysis and unknown
    # generation responses. Keep the spec defaults and explicitly disable MiMo's
    # default thinking; authorization still freezes every stage's total cap.
    limits = {"analysis": 2048,
              "generation": 2048,
              "role_chat": 1024,
              "validation": 2048,
              "connection_test": 32,
              "breakdown": 16384}
    if world:
        # Entity receipts and multi-speaker JSONL need more room than a reader
        # paragraph. Reserve the larger cap before sending any world request.
        limits.update(analysis=4096, generation=4096)
        endpoint=urlsplit(profile.base_url)
        if endpoint.hostname=='apihub.agnes-ai.com' and profile.model_id in ('agnes-2.5-flash','agnes-3.0-flash'):
            # Real visual measurements spent all 4096 tokens on reasoning and
            # returned an empty length-limited answer. Reserve both reasoning
            # and final geometry before sending a new, independently approved job.
            limits['analysis']=16384
    return {"version": POLICY_VERSION + ("-world-v1" if world else ""), "stages": {
        stage: {"totalCompletionTokens": limit,
                "thinking": "disabled" if explicit_thinking else None}
        for stage, limit in limits.items()}}


def completion_limit(profile, stage, *, world=False):
    return completion_policy(profile, world=world)["stages"][stage]["totalCompletionTokens"]


def input_ceiling(profile, output_limit, *, world=False, script=False, vision=False):
    # Worlds carry an entity ledger, routes and character knowledge alongside
    # the director's schema. Count that whole request (including the system
    # prompt) within the finite allowance reserved by world.jobs.create_job.
    # Multi-camera reviews reserve 8192 tokens per image. Their textual
    # measurements plus owned identity references can exceed the prose 96k cap
    # while still fitting this profile's configured context window. Reserve
    # this bounded allowance in the new consent before sending anything.
    return min(240000 if script or vision else 96000 if world else 12000, profile.context_window - output_limit - 512)


def planned_stages(operation, analysis_count, chapter_count=1):
    if operation == "connection_test":
        return ["connection_test"]
    stages = ["analysis"] * analysis_count
    if operation == "role_chat":
        stages.extend(["role_chat", "validation"])
    elif operation != "analysis":
        stages.extend(["generation", "validation"] * chapter_count)
    return stages
