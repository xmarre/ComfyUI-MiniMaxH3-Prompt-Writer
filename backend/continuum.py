from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .assembly import assemble_refinement, assemble_request
from .references import reference_tags


GENERATION_TARGET_SINGLE = "single"
GENERATION_TARGET_CONTINUUM = "continuum"
CONTINUUM_SCHEMA_VERSION = 1
MIN_CHUNKS = 1
MAX_CHUNKS = 16
MIN_CHUNK_SECONDS = 4.0
MAX_CHUNK_SECONDS = 15.0

_CHUNK_HEADER = re.compile(r"^\s*\[\s*Chunk\s+(\d+)\s*\]\s*$", re.IGNORECASE)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)
_SUBJECT_TAG = re.compile(r"<\s*Subject\s+(\d+)\s*>", re.IGNORECASE)


class ContinuumError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def generation_target(body: dict[str, Any]) -> str:
    value = body.get("generation_target", GENERATION_TARGET_SINGLE)
    if value not in {GENERATION_TARGET_SINGLE, GENERATION_TARGET_CONTINUUM}:
        raise ContinuumError(
            "INVALID_GENERATION_TARGET",
            "Generation target must be Single clip or H3 Continuum.",
            {"generation_target": value},
        )
    return value


def validate_continuum_settings(source: dict[str, Any]) -> dict[str, Any]:
    value = source.get("continuum") if "continuum" in source else source
    if not isinstance(value, dict):
        raise ContinuumError("INVALID_CONTINUUM_SETTINGS", "H3 Continuum settings must be a JSON object.")
    schema_version = value.get("schema_version", CONTINUUM_SCHEMA_VERSION)
    if schema_version != CONTINUUM_SCHEMA_VERSION:
        raise ContinuumError(
            "UNSUPPORTED_CONTINUUM_SCHEMA",
            f"Unsupported H3 Continuum schema {schema_version}; expected {CONTINUUM_SCHEMA_VERSION}.",
        )
    chunks = value.get("chunks")
    if not isinstance(chunks, int) or isinstance(chunks, bool) or not MIN_CHUNKS <= chunks <= MAX_CHUNKS:
        raise ContinuumError(
            "INVALID_CONTINUUM_CHUNKS",
            f"H3 Continuum chunks must be an integer between {MIN_CHUNKS} and {MAX_CHUNKS}.",
            {"chunks": chunks},
        )
    chunk_seconds = value.get("chunk_seconds")
    if (
        not isinstance(chunk_seconds, (int, float))
        or isinstance(chunk_seconds, bool)
        or not MIN_CHUNK_SECONDS <= float(chunk_seconds) <= MAX_CHUNK_SECONDS
    ):
        raise ContinuumError(
            "INVALID_CONTINUUM_DURATION",
            f"H3 Continuum chunk duration must be between {MIN_CHUNK_SECONDS:g} and {MAX_CHUNK_SECONDS:g} seconds.",
            {"chunk_seconds": chunk_seconds},
        )
    return {
        "schema_version": CONTINUUM_SCHEMA_VERSION,
        "chunks": chunks,
        "chunk_seconds": float(chunk_seconds),
        "total_seconds": round(chunks * float(chunk_seconds), 6),
    }


def _reference_inventory(manifest: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "tag": str(asset["reference"]),
            "type": str(asset["type"]),
            "filename": str(asset["filename"]),
        }
        for asset in manifest.get("assets", [])
        if asset.get("reference")
    ]


def _planner_schema(settings: dict[str, Any], references: list[dict[str, str]]) -> str:
    del references
    chunks = [
        {
            "index": index,
            "continuity": "initial" if index == 1 else "continuous",
            "transition": "",
            "start_state": "compact state at the start of this chunk",
            "action": "events that happen during this chunk",
            "end_state": "compact state reached at the end of this chunk",
        }
        for index in range(1, settings["chunks"] + 1)
    ]
    return json.dumps(
        {
            "schema_version": CONTINUUM_SCHEMA_VERSION,
            "global": {
                "continuity_anchors": "persistent subjects, environment, style, camera and sound rules",
                "persistent_constraints": "constraints that apply to every chunk",
                "subject_anchors": [
                    {
                        "id": "<Subject 1>",
                        "meaning": "stable identity and role of this subject across chunks",
                    }
                ],
            },
            "chunks": chunks,
        },
        ensure_ascii=False,
        indent=2,
    )


CONTINUUM_PLAN_SYSTEM_PROMPT = """Plan one coherent MiniMax H3 Continuum sequence. Return one JSON object and nothing else. The plan is a compact continuity contract used to write complete H3 prompts sequentially. Preserve the user's explicit dialogue, visible text, media-reference semantics, exclusions, camera rules, sound rules, and intended ending. A later chunk continues from the previous chunk's end_state unless continuity is explicitly intentional_break. Use intentional_break only for a requested cut, location or time change, wardrobe change, entrance or exit, camera reset, major framing change, or another deliberate discontinuity, and explain it in transition. Keep any media reference tags mentioned in creative content semantically stable, but do not emit a reference_assignments field: Prompt Writer injects the authoritative current media-reference identities from its manifest after planning. subject_anchors must be [] when no stable subject identity is needed; otherwise every entry must be an object exactly shaped {"id":"<Subject N>","meaning":"concise stable identity and role"}. Never emit bare strings in subject_anchors and never reuse one subject id for a different entity. Make every string compact enough that a 16-chunk plan fits in one response."""


def assemble_continuum_plan_request(body: dict[str, Any]) -> dict[str, Any]:
    if body.get("mode") == "Music3":
        raise ContinuumError("CONTINUUM_MUSIC_UNSUPPORTED", "H3 Continuum is available only for H3 Video.")
    settings = validate_continuum_settings(body)
    single_body = dict(body)
    single_body["duration_seconds"] = settings["chunk_seconds"]
    base = assemble_request(single_body)
    manifest = base["input"]["media_manifest"]
    references = _reference_inventory(manifest)
    reference_lines = "\n".join(
        f"- {item['tag']}: {item['filename']} ({item['type']})" for item in references
    ) or "- None"
    user = (
        f"Underlying H3 mode: {body['mode']}\n"
        f"Chunks: {settings['chunks']}\n"
        f"Seconds per chunk: {settings['chunk_seconds']:g}\n"
        f"Total sequence duration: {settings['total_seconds']:g} seconds\n\n"
        f"Stable reference inventory:\n{reference_lines}\n\n"
        f"Creative brief:\n{base['input']['creative_brief']}\n\n"
        "Return exactly this schema with substantive compact values. Keep all chunk indexes contiguous and one-based. "
        "Do not add a reference_assignments field; the exact stable reference inventory above is injected by Prompt Writer.\n\n"
        f"Schema:\n{_planner_schema(settings, references)}"
    )
    return {
        "schema_version": 1,
        "guide": {"id": "h3-continuum-plan-v1", "title": "H3 Continuum sequence plan"},
        "input": {
            **base["input"],
            "mode": "T2VA",
            "underlying_mode": body["mode"],
            "generation_target": GENERATION_TARGET_CONTINUUM,
            "continuum_stage": "plan",
            "continuum": settings,
        },
        "media_inputs": [],
        "supporting_guides": [],
        "system_prompt": {"custom": False, "content": CONTINUUM_PLAN_SYSTEM_PROMPT},
        "messages": [
            {"role": "system", "name": "h3_continuum_sequence_planner", "content": CONTINUUM_PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    }


def _json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    match = _FENCE.fullmatch(value)
    if match:
        value = match.group(1).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ContinuumError(
            "INVALID_CONTINUUM_PLAN",
            "The sequence planner did not return valid JSON.",
            {"line": error.lineno, "column": error.colno, "reason": error.msg},
        ) from error
    if not isinstance(parsed, dict):
        raise ContinuumError("INVALID_CONTINUUM_PLAN", "The sequence plan must be a JSON object.")
    return parsed


def _nonempty_string(value: Any, field: str, *, chunk_index: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        details = {"field": field}
        if chunk_index is not None:
            details["chunk_index"] = chunk_index
        raise ContinuumError("INVALID_CONTINUUM_PLAN", f"Sequence-plan {field} must be non-empty text.", details)
    return value.strip()


def _canonical_subject_id(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return f"<Subject {value}>"
    if not isinstance(value, str) or not value.strip():
        raise ContinuumError(
            "INVALID_CONTINUUM_PLAN",
            "Sequence-plan subject_anchors.id must identify a positive subject number.",
            {"field": "subject_anchors.id"},
        )
    raw = value.strip()
    patterns = (
        r"<\s*Subject\s+([1-9]\d*)\s*>",
        r"Subject\s+([1-9]\d*)",
        r"S(?:ubject)?[_-]?([1-9]\d*)",
        r"([1-9]\d*)",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, raw, re.IGNORECASE)
        if match:
            return f"<Subject {int(match.group(1))}>"
    raise ContinuumError(
        "INVALID_CONTINUUM_PLAN",
        f"Invalid stable subject id: {raw}.",
        {"field": "subject_anchors.id"},
    )


def validate_sequence_plan(
    plan: dict[str, Any],
    settings: dict[str, Any],
    *,
    expected_references: set[str],
) -> dict[str, Any]:
    if plan.get("schema_version") != CONTINUUM_SCHEMA_VERSION:
        raise ContinuumError(
            "INVALID_CONTINUUM_PLAN",
            f"Sequence-plan schema_version must be {CONTINUUM_SCHEMA_VERSION}.",
        )
    global_plan = plan.get("global")
    if not isinstance(global_plan, dict):
        raise ContinuumError("INVALID_CONTINUUM_PLAN", "Sequence-plan global must be an object.")
    continuity_anchors = _nonempty_string(global_plan.get("continuity_anchors"), "global.continuity_anchors")
    persistent_constraints = _nonempty_string(global_plan.get("persistent_constraints"), "global.persistent_constraints")
    normalized_assignments = [
        {
            "tag": tag,
            "role": "Preserve this exact media-reference identity and its creative role across every chunk.",
        }
        for tag in sorted(expected_references)
    ]
    subject_anchors = global_plan.get("subject_anchors", [])
    if not isinstance(subject_anchors, list):
        raise ContinuumError("INVALID_CONTINUUM_PLAN", "Sequence-plan subject_anchors must be a list.")
    normalized_subjects = []
    seen_subjects: set[str] = set()
    for subject in subject_anchors:
        if not isinstance(subject, dict):
            raise ContinuumError("INVALID_CONTINUUM_PLAN", "Every subject anchor must be an object.")
        canonical_id = _canonical_subject_id(subject.get("id"))
        meaning = _nonempty_string(subject.get("meaning"), "subject_anchors.meaning")
        if canonical_id in seen_subjects:
            raise ContinuumError("INVALID_CONTINUUM_PLAN", f"Sequence plan defines {canonical_id} more than once.")
        seen_subjects.add(canonical_id)
        normalized_subjects.append({"id": canonical_id, "meaning": meaning})

    chunks = plan.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != settings["chunks"]:
        raise ContinuumError(
            "INVALID_CONTINUUM_PLAN_CHUNKS",
            "Sequence-plan chunk count does not match the requested chunk count.",
            {"expected": settings["chunks"], "actual": len(chunks) if isinstance(chunks, list) else None},
        )
    normalized_chunks = []
    seen_indexes: set[int] = set()
    for expected_index, chunk in enumerate(chunks, start=1):
        if not isinstance(chunk, dict):
            raise ContinuumError("INVALID_CONTINUUM_PLAN", f"Sequence-plan Chunk {expected_index} must be an object.")
        index = chunk.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise ContinuumError("INVALID_CONTINUUM_PLAN", "Sequence-plan chunk indexes must be integers.")
        if index in seen_indexes:
            raise ContinuumError("INVALID_CONTINUUM_PLAN_CHUNKS", f"Sequence plan contains duplicate Chunk {index}.")
        if index != expected_index:
            raise ContinuumError(
                "INVALID_CONTINUUM_PLAN_CHUNKS",
                "Sequence-plan chunk indexes must be contiguous and one-based.",
                {"expected": expected_index, "actual": index},
            )
        seen_indexes.add(index)
        continuity = chunk.get("continuity")
        allowed = {"initial"} if index == 1 else {"continuous", "intentional_break"}
        if continuity not in allowed:
            raise ContinuumError(
                "INVALID_CONTINUUM_PLAN_CONTINUITY",
                f"Chunk {index} continuity must be one of: {', '.join(sorted(allowed))}.",
            )
        transition = chunk.get("transition", "")
        if not isinstance(transition, str):
            raise ContinuumError("INVALID_CONTINUUM_PLAN", f"Chunk {index} transition must be text.")
        transition = transition.strip()
        if continuity == "intentional_break" and not transition:
            raise ContinuumError(
                "INVALID_CONTINUUM_PLAN_CONTINUITY",
                f"Chunk {index} must explain its intentional continuity break.",
            )
        normalized_chunks.append({
            "index": index,
            "continuity": continuity,
            "transition": transition,
            "start_state": _nonempty_string(chunk.get("start_state"), "start_state", chunk_index=index),
            "action": _nonempty_string(chunk.get("action"), "action", chunk_index=index),
            "end_state": _nonempty_string(chunk.get("end_state"), "end_state", chunk_index=index),
        })
    return {
        "schema_version": CONTINUUM_SCHEMA_VERSION,
        "global": {
            "continuity_anchors": continuity_anchors,
            "persistent_constraints": persistent_constraints,
            "reference_assignments": normalized_assignments,
            "subject_anchors": normalized_subjects,
        },
        "chunks": normalized_chunks,
    }


def parse_sequence_plan(
    text: str,
    settings: dict[str, Any],
    *,
    expected_references: set[str],
) -> dict[str, Any]:
    return validate_sequence_plan(_json_object(text), settings, expected_references=expected_references)


def assemble_continuum_plan_repair_request(
    body: dict[str, Any],
    invalid_text: str,
    error: ContinuumError,
) -> dict[str, Any]:
    assembled = assemble_continuum_plan_request(body)
    assembled["input"]["continuum_stage"] = "plan_repair"
    assembled["messages"] = [
        {
            "role": "system",
            "name": "h3_continuum_sequence_plan_repair",
            "content": (
                "Repair only the structural sequence-plan error described by the user. Preserve every valid creative "
                "choice and return one complete JSON object with no Markdown fence or commentary. Follow the exact schema "
                "from the original planning request. Do not emit reference_assignments; Prompt Writer injects those from "
                "the current media manifest. In particular, subject_anchors must be [] or a list of objects with exactly "
                "id and meaning fields; never use bare strings."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Structural error: {error.message}\nDetails: {json.dumps(error.details, ensure_ascii=False)}\n\n"
                f"Invalid plan:\n{invalid_text}\n\n"
                f"Original planning request:\n{assembled['messages'][-1]['content']}"
            ),
        },
    ]
    return assembled


def _plan_context(plan: dict[str, Any], index: int, previous_prompt: str | None) -> str:
    chunk = plan["chunks"][index - 1]
    previous = plan["chunks"][index - 2] if index > 1 else None
    following = plan["chunks"][index] if index < len(plan["chunks"]) else None
    parts = [
        "H3 Continuum chunk contract:",
        f"Global anchors: {json.dumps(plan['global'], ensure_ascii=False, sort_keys=True)}",
        f"Current chunk plan: {json.dumps(chunk, ensure_ascii=False, sort_keys=True)}",
    ]
    if previous is not None:
        parts.append(f"Required previous terminal state: {previous['end_state']}")
    if previous_prompt:
        parts.append(
            "Previous generated chunk prompt (carry forward its supported terminal details and stable identities):\n"
            + previous_prompt
        )
    if following is not None:
        parts.append(f"Next planned start state: {following['start_state']}")
    parts.append(
        f"Write only the complete normal H3 prompt for Chunk {index}. Do not emit a [Chunk {index}] wrapper. "
        "A continuous chunk must begin from the previous terminal state without resetting location, subjects, wardrobe, "
        "lighting, camera, props, dialogue state, or sound. Apply a transition only when the plan marks intentional_break."
    )
    return "\n\n".join(parts)


def assemble_continuum_chunk_request(
    body: dict[str, Any],
    plan: dict[str, Any],
    index: int,
    *,
    previous_prompt: str | None,
) -> dict[str, Any]:
    settings = validate_continuum_settings(body)
    if not 1 <= index <= settings["chunks"]:
        raise ContinuumError("INVALID_CONTINUUM_CHUNK", "Chunk index is outside the configured sequence.")
    single_body = dict(body)
    single_body["duration_seconds"] = settings["chunk_seconds"]
    assembled = assemble_request(single_body)
    expected_references = {
        item["tag"] for item in _reference_inventory(assembled["input"]["media_manifest"])
    }
    plan = validate_sequence_plan(plan, settings, expected_references=expected_references)
    assembled["input"].update({
        "generation_target": GENERATION_TARGET_CONTINUUM,
        "continuum_stage": "chunk",
        "continuum": settings,
        "continuum_chunk_index": index,
        "continuum_plan": plan,
    })
    user_message = next(message for message in assembled["messages"] if message["role"] == "user")
    user_message["content"] += "\n\n" + _plan_context(plan, index, previous_prompt)
    return assembled


def validate_generated_chunk(prompt: str, assembled: dict[str, Any]) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ContinuumError("EMPTY_CONTINUUM_CHUNK", "The model returned an empty Continuum chunk prompt.")
    value = prompt.strip()
    for line in value.splitlines():
        if _CHUNK_HEADER.fullmatch(line):
            raise ContinuumError(
                "INVALID_CONTINUUM_CHUNK_FORMAT",
                "A generated chunk prompt contains a reserved [Chunk N] interchange header.",
                {"chunk_index": assembled["input"].get("continuum_chunk_index")},
            )
    manifest = assembled["input"].get("media_manifest", {})
    allowed = {
        str(asset["reference"])
        for asset in manifest.get("assets", [])
        if asset.get("reference")
    }
    actual = reference_tags(value)
    unexpected = actual - allowed
    if unexpected:
        raise ContinuumError(
            "CONTINUUM_REFERENCE_IDENTITY_DRIFT",
            "A generated chunk introduced reference labels outside the stable sequence inventory.",
            {"unexpected": sorted(unexpected), "allowed": sorted(allowed)},
        )
    planned_subjects = {
        item["id"] for item in assembled["input"].get("continuum_plan", {}).get("global", {}).get("subject_anchors", [])
    }
    actual_subjects = {f"<Subject {int(number)}>" for number in _SUBJECT_TAG.findall(value)}
    if planned_subjects and not actual_subjects.issubset(planned_subjects):
        raise ContinuumError(
            "CONTINUUM_SUBJECT_IDENTITY_DRIFT",
            "A generated chunk introduced a subject label outside the stable sequence plan.",
            {"unexpected": sorted(actual_subjects - planned_subjects), "planned": sorted(planned_subjects)},
        )
    return value


def serialize_chunk_prompts(prompts: list[str]) -> str:
    if not isinstance(prompts, list) or not prompts:
        raise ContinuumError("INVALID_CONTINUUM_SEQUENCE", "A Continuum sequence must contain at least one chunk prompt.")
    normalized = []
    for index, prompt in enumerate(prompts, start=1):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ContinuumError(
                "INVALID_CONTINUUM_SEQUENCE",
                f"Chunk {index} prompt must be non-empty text.",
                {"chunk_index": index},
            )
        value = prompt.strip()
        if any(_CHUNK_HEADER.fullmatch(line) for line in value.splitlines()):
            raise ContinuumError(
                "INVALID_CONTINUUM_CHUNK_FORMAT",
                f"Chunk {index} contains a reserved [Chunk N] header.",
            )
        normalized.append(f"[Chunk {index}]\n{value}")
    return "\n\n".join(normalized)


def parse_chunk_prompts(script: str, *, expected_chunks: int | None = None) -> list[str]:
    if not isinstance(script, str) or not script.strip():
        raise ContinuumError("INVALID_CONTINUUM_SEQUENCE", "Continuum sequence text is empty.")
    prompts: list[str] = []
    current_index: int | None = None
    body: list[str] = []

    def finish() -> None:
        nonlocal body
        if current_index is None:
            if any(line.strip() for line in body):
                raise ContinuumError(
                    "INVALID_CONTINUUM_SEQUENCE",
                    "Text before [Chunk 1] is not part of the canonical Continuum format.",
                )
            body = []
            return
        prompt = "\n".join(body).strip()
        if not prompt:
            raise ContinuumError(
                "INVALID_CONTINUUM_SEQUENCE",
                f"Chunk {current_index} prompt is empty.",
                {"chunk_index": current_index},
            )
        prompts.append(prompt)
        body = []

    for line_number, line in enumerate(script.splitlines(), start=1):
        match = _CHUNK_HEADER.fullmatch(line)
        if match:
            finish()
            index = int(match.group(1))
            expected_index = len(prompts) + 1
            if index != expected_index:
                raise ContinuumError(
                    "INVALID_CONTINUUM_SEQUENCE",
                    "Continuum chunk headers must be contiguous, unique, and one-based.",
                    {"line": line_number, "expected": expected_index, "actual": index},
                )
            current_index = index
            continue
        body.append(line)
    finish()
    if not prompts:
        raise ContinuumError("INVALID_CONTINUUM_SEQUENCE", "No [Chunk N] sections were found.")
    if expected_chunks is not None and len(prompts) != expected_chunks:
        raise ContinuumError(
            "INVALID_CONTINUUM_SEQUENCE",
            "Continuum sequence chunk count does not match the configured count.",
            {"expected": expected_chunks, "actual": len(prompts)},
        )
    return prompts


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def sequence_result(settings: dict[str, Any], plan: dict[str, Any], prompts: list[str]) -> dict[str, Any]:
    canonical = serialize_chunk_prompts(prompts)
    parsed = parse_chunk_prompts(canonical, expected_chunks=settings["chunks"])
    if parsed != [prompt.strip() for prompt in prompts]:
        raise ContinuumError("INVALID_CONTINUUM_SEQUENCE", "Canonical Continuum serialization did not round-trip.")
    return {
        "schema_version": CONTINUUM_SCHEMA_VERSION,
        "settings": settings,
        "plan": plan,
        "chunks": [
            {"index": index, "prompt": prompt, "hash": prompt_hash(prompt)}
            for index, prompt in enumerate(parsed, start=1)
        ],
        "prompt": canonical,
    }


def assemble_continuum_refinement(
    body: dict[str, Any],
) -> tuple[dict[str, Any], list[str], int, dict[str, Any]]:
    settings = validate_continuum_settings(body)
    continuum = body.get("continuum")
    if not isinstance(continuum, dict):
        raise ContinuumError("INVALID_CONTINUUM_SETTINGS", "H3 Continuum refinement settings are missing.")
    plan_value = continuum.get("plan")
    if not isinstance(plan_value, dict):
        raise ContinuumError("INVALID_CONTINUUM_PLAN", "H3 Continuum refinement requires its saved sequence plan.")
    plan_request = assemble_continuum_plan_request(body)
    expected_references = {
        item["tag"] for item in _reference_inventory(plan_request["input"]["media_manifest"])
    }
    plan = validate_sequence_plan(plan_value, settings, expected_references=expected_references)
    prompts = parse_chunk_prompts(str(body.get("current_prompt") or ""), expected_chunks=settings["chunks"])
    chunk_index = continuum.get("chunk_index")
    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or not 1 <= chunk_index <= settings["chunks"]:
        raise ContinuumError(
            "INVALID_CONTINUUM_CHUNK",
            "Select a valid one-based Continuum chunk to refine.",
            {"chunk_index": chunk_index},
        )
    refine_body = dict(body)
    refine_body["duration_seconds"] = settings["chunk_seconds"]
    refine_body["current_prompt"] = prompts[chunk_index - 1]
    assembled = assemble_refinement(refine_body, None)
    assembled["input"].update({
        "generation_target": GENERATION_TARGET_CONTINUUM,
        "continuum_stage": "refine_chunk",
        "continuum": settings,
        "continuum_chunk_index": chunk_index,
        "continuum_plan": plan,
    })
    user_message = next(message for message in assembled["messages"] if message["role"] == "user")
    previous_prompt = prompts[chunk_index - 2] if chunk_index > 1 else None
    user_message["content"] += "\n\n" + _plan_context(plan, chunk_index, previous_prompt)
    if chunk_index < len(prompts):
        user_message["content"] += "\n\nFollowing unchanged chunk prompt for boundary compatibility:\n" + prompts[chunk_index]
    user_message["content"] += (
        "\n\nRefine only the selected chunk. Return only its complete replacement H3 prompt. "
        "Every other chunk remains byte-for-byte unchanged."
    )
    return assembled, prompts, chunk_index, plan


def apply_continuum_refinement(
    result_prompt: str,
    assembled: dict[str, Any],
    prompts: list[str],
    chunk_index: int,
    plan: dict[str, Any],
) -> dict[str, Any]:
    refined = validate_generated_chunk(result_prompt, assembled)
    updated = list(prompts)
    updated[chunk_index - 1] = refined
    settings = assembled["input"]["continuum"]
    return sequence_result(settings, plan, updated)
