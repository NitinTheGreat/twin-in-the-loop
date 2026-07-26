from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from ..actions.schema import NoOp, ParseError, parse_action


def extract_json(text: str) -> Optional[dict]:
    body = text.strip()
    if "```" in body:
        for chunk in body.split("```"):
            candidate = chunk.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                body = candidate
                break
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start : end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class StructuredResult:
    action: object
    attempts: int
    malformed: int
    rejected: int
    exhausted: bool
    responses: list = field(default_factory=list)


def enforce_action(client, messages, validate_fn, max_retries) -> StructuredResult:
    conversation = list(messages)
    malformed = 0
    rejected = 0
    responses: list[str] = []

    for attempt in range(max_retries + 1):
        text, _ = client.complete(conversation)
        responses.append(text)
        payload = extract_json(text)
        target = payload.get("action", payload) if isinstance(payload, dict) else None

        if target is None:
            malformed += 1
            conversation.append({"role": "assistant", "content": text})
            conversation.append(
                {
                    "role": "user",
                    "content": "Your response was not valid JSON matching the action schema. Reply with only the JSON action object.",
                }
            )
            continue

        parsed = parse_action(target)
        if isinstance(parsed, ParseError):
            malformed += 1
            conversation.append({"role": "assistant", "content": text})
            conversation.append(
                {
                    "role": "user",
                    "content": f"That action failed schema parsing: {parsed.message}. Reply with only a valid JSON action object.",
                }
            )
            continue

        verdict = validate_fn(parsed)
        if not verdict.valid:
            rejected += 1
            conversation.append({"role": "assistant", "content": text})
            conversation.append(
                {
                    "role": "user",
                    "content": f"That action was rejected by the validator: {verdict.reason}. Choose a different, valid action.",
                }
            )
            continue

        return StructuredResult(
            action=parsed,
            attempts=attempt + 1,
            malformed=malformed,
            rejected=rejected,
            exhausted=False,
            responses=responses,
        )

    return StructuredResult(
        action=NoOp(),
        attempts=max_retries + 1,
        malformed=malformed,
        rejected=rejected,
        exhausted=True,
        responses=responses,
    )
