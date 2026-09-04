from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement target, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "backend/models/api_provider_backend.py",
    '''        content_parts: list[str] = []\n        finish_reason: str | None = None\n''',
    '''        content_parts: list[str] = []\n        structured_reasoning_parts: list[str] = []\n        structured_output_request = (\n            connection.compatibility_profile == "lm_studio"\n            and isinstance(payload.get("response_format"), dict)\n            and payload["response_format"].get("type") == "json_schema"\n        )\n        finish_reason: str | None = None\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''                text = delta.get("content")\n                if isinstance(text, str):\n                    content_parts.append(text)\n                elif isinstance(text, list):\n                    for part in text:\n                        if isinstance(part, dict) and isinstance(part.get("text"), str):\n                            content_parts.append(part["text"])\n                if choice.get("finish_reason") is not None:\n''',
    '''                text = delta.get("content")\n                if isinstance(text, str):\n                    content_parts.append(text)\n                elif isinstance(text, list):\n                    for part in text:\n                        if isinstance(part, dict) and isinstance(part.get("text"), str):\n                            content_parts.append(part["text"])\n                if structured_output_request:\n                    # LM Studio currently has Qwen reasoning-model cases where the JSON-schema\n                    # grammar is applied to the separated reasoning stream and content stays empty.\n                    # Capture that provider field only for constrained planner requests; promote it\n                    # below only when it is itself complete JSON.\n                    reasoning_text = delta.get("reasoning_content")\n                    if not isinstance(reasoning_text, str):\n                        reasoning_text = delta.get("reasoning")\n                    if isinstance(reasoning_text, str):\n                        structured_reasoning_parts.append(reasoning_text)\n                if choice.get("finish_reason") is not None:\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''        content = "".join(content_parts)\n        if not usage:\n''',
    '''        content = "".join(content_parts)\n        if structured_output_request and not content.strip() and structured_reasoning_parts:\n            reasoning_candidate = "".join(structured_reasoning_parts).strip()\n            try:\n                json.loads(reasoning_candidate)\n            except json.JSONDecodeError:\n                pass\n            else:\n                content = reasoning_candidate\n        if not usage:\n''',
)

replace_once(
    "tests/test_continuum_structured_output.py",
    '''    def test_handler_sends_schema_and_disables_lm_studio_reasoning_for_constrained_planner(self) -> None:\n''',
    '''    def test_lm_studio_structured_planner_promotes_valid_json_from_reasoning_stream(self) -> None:\n        response_format = _lm_studio_continuum_response_format(self.connection(), _assembled("plan"))\n\n        class Response:\n            status = 200\n\n            @staticmethod\n            def getheaders():\n                return []\n\n            def __init__(self, reasoning_field: str, *, content: str = "") -> None:\n                self.lines = iter([\n                    ("data: " + json.dumps({\n                        "choices": [{\n                            "delta": {\n                                reasoning_field: '{"schema_version":2}',\n                                "content": content,\n                            },\n                            "finish_reason": "stop",\n                        }]\n                    }) + "\\n").encode("utf-8"),\n                    b"data: [DONE]\\n",\n                ])\n\n            def readline(self):\n                return next(self.lines, b"")\n\n        class HttpConnection:\n            def __init__(self, response):\n                self.response = response\n\n            def request(self, *_args, **_kwargs):\n                return None\n\n            def getresponse(self):\n                return self.response\n\n            def close(self):\n                return None\n\n        for reasoning_field in ("reasoning_content", "reasoning"):\n            with self.subTest(reasoning_field=reasoning_field):\n                http = HttpConnection(Response(reasoning_field))\n                payload = {\n                    "model": "qwen-model",\n                    "messages": [],\n                    "stream": True,\n                    "response_format": response_format,\n                }\n                with patch.object(self.backend, "_http_connection", return_value=http):\n                    result = self.backend._request_chat_completion_stream(self.connection(), payload)\n                self.assertEqual(result["choices"][0]["message"]["content"], '{"schema_version":2}')\n\n    def test_lm_studio_reasoning_fallback_is_scoped_and_never_overrides_content(self) -> None:\n        response_format = _lm_studio_continuum_response_format(self.connection(), _assembled("plan"))\n\n        class Response:\n            status = 200\n\n            @staticmethod\n            def getheaders():\n                return []\n\n            def __init__(self, *, content: str, reasoning: str) -> None:\n                self.lines = iter([\n                    ("data: " + json.dumps({\n                        "choices": [{\n                            "delta": {"content": content, "reasoning_content": reasoning},\n                            "finish_reason": "stop",\n                        }]\n                    }) + "\\n").encode("utf-8"),\n                    b"data: [DONE]\\n",\n                ])\n\n            def readline(self):\n                return next(self.lines, b"")\n\n        class HttpConnection:\n            def __init__(self, response):\n                self.response = response\n\n            def request(self, *_args, **_kwargs):\n                return None\n\n            def getresponse(self):\n                return self.response\n\n            def close(self):\n                return None\n\n        cases = [\n            (self.connection(), "plain-content", '{"schema_version":2}', response_format, "plain-content"),\n            (self.connection(), "", "reasoning prose", response_format, ""),\n            (self.connection(profile="generic"), "", '{"schema_version":2}', response_format, ""),\n        ]\n        for connection, content, reasoning, fmt, expected in cases:\n            with self.subTest(profile=connection.compatibility_profile, content=content, reasoning=reasoning):\n                http = HttpConnection(Response(content=content, reasoning=reasoning))\n                payload = {\n                    "model": "qwen-model",\n                    "messages": [],\n                    "stream": True,\n                    "response_format": fmt,\n                }\n                with patch.object(self.backend, "_http_connection", return_value=http):\n                    result = self.backend._request_chat_completion_stream(connection, payload)\n                self.assertEqual(result["choices"][0]["message"]["content"], expected)\n\n    def test_handler_sends_schema_and_disables_lm_studio_reasoning_for_constrained_planner(self) -> None:\n''',
)

replace_once(
    "CHANGELOG.md",
    '''- Constrained LM Studio H3 Continuum `plan` and `plan_repair` responses with JSON Schema structured output while retaining Prompt Writer semantic validation, deterministic recovery, and the single bounded repair limit; constrained Qwen planner stages run without reasoning to avoid LM Studio routing schema output into reasoning-only content, and terminal planner failures now include content-free structural response diagnostics.\n''',
    '''- Constrained LM Studio H3 Continuum `plan` and `plan_repair` responses with JSON Schema structured output while retaining Prompt Writer semantic validation, deterministic recovery, and the single bounded repair limit; constrained planner stages use non-thinking application semantics, and the LM Studio adapter safely recovers valid schema JSON from the separated reasoning stream when affected Qwen runtimes leave `content` empty. Terminal planner failures now include content-free structural response diagnostics.\n''',
)

print("Applied LM Studio structured reasoning-stream compatibility fallback.")
