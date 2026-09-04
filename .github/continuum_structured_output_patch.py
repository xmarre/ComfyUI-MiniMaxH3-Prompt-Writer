from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one replacement target, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_regex_once(path: str, pattern: str, replacement: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one regex replacement target, found {count}")
    target.write_text(updated, encoding="utf-8")


replace_once(
    "backend/continuum.py",
    "from .assembly import assemble_refinement, assemble_request\n",
    "from .assembly import assemble_refinement, assemble_request\n"
    "from .continuum_schema import CONTINUUM_PLAN_SCHEMA_VERSION, continuum_plan_json_schema\n",
)
replace_once(
    "backend/continuum.py",
    'CONTINUUM_SCHEMA_VERSION = 2\n',
    'CONTINUUM_SCHEMA_VERSION = CONTINUUM_PLAN_SCHEMA_VERSION\n',
)
replace_regex_once(
    "backend/continuum.py",
    r"def _planner_schema\(settings: dict\[str, Any\]\) -> str:\n.*?\n\n\nCONTINUUM_PLAN_SYSTEM_PROMPT",
    "def _planner_schema(settings: dict[str, Any]) -> str:\n"
    "    return json.dumps(\n"
    "        continuum_plan_json_schema(settings),\n"
    "        ensure_ascii=False,\n"
    "        indent=2,\n"
    "    )\n\n\n"
    "CONTINUUM_PLAN_SYSTEM_PROMPT",
)
replace_once(
    "backend/continuum.py",
    '        f"Schema:\\n{_planner_schema(settings)}"\n',
    '        f"JSON Schema:\\n{_planner_schema(settings)}"\n',
)

replace_once(
    "backend/models/api_provider_backend.py",
    "from ..h3_pipeline import run_h3_pipeline, validate_media_capabilities\n",
    "from ..continuum_schema import continuum_plan_json_schema\n"
    "from ..h3_pipeline import run_h3_pipeline, validate_media_capabilities\n",
)
replace_once(
    "backend/models/api_provider_backend.py",
    "\n\nclass _ApiChatHandler:\n",
    '''\n\ndef _lm_studio_continuum_response_format(\n    connection: ApiConnection,\n    assembled: dict[str, Any],\n) -> dict[str, Any] | None:\n    if connection.preset != "custom" or connection.compatibility_profile != "lm_studio":\n        return None\n    request_input = assembled.get("input", {})\n    if (\n        request_input.get("generation_target") != "continuum"\n        or request_input.get("continuum_stage") not in {"plan", "plan_repair"}\n    ):\n        return None\n    settings = request_input.get("continuum")\n    if not isinstance(settings, dict):\n        raise ModelError(\n            "INVALID_CONTINUUM_SETTINGS",\n            "Continuum planner structured output requires validated Continuum settings.",\n        )\n    try:\n        schema = continuum_plan_json_schema(settings)\n    except ValueError as error:\n        raise ModelError(\n            "INVALID_CONTINUUM_SETTINGS",\n            "Continuum planner structured output requires a valid chunk count.",\n        ) from error\n    return {\n        "type": "json_schema",\n        "json_schema": {\n            "name": "h3_continuum_plan_v2",\n            "strict": True,\n            "schema": schema,\n        },\n    }\n\n\nclass _ApiChatHandler:\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    "        thinking: bool,\n        **_unused: Any,\n",
    "        thinking: bool,\n        response_format: dict[str, Any] | None = None,\n        **_unused: Any,\n",
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''        payload: dict[str, Any] = {\n            "model": self.model_id,\n            "messages": messages,\n            "stream": True,\n        }\n''',
    '''        payload: dict[str, Any] = {\n            "model": self.model_id,\n            "messages": messages,\n            "stream": True,\n        }\n        if response_format is not None:\n            payload["response_format"] = response_format\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''        elif preset == "custom" and self.connection.compatibility_profile == "lm_studio":\n            payload["reasoning_effort"] = "low" if thinking else "none"\n''',
    '''        elif preset == "custom" and self.connection.compatibility_profile == "lm_studio":\n            # LM Studio/Qwen reasoning can route constrained JSON into reasoning_content.\n            # Structured Continuum planning therefore runs as an explicit non-thinking request.\n            payload["reasoning_effort"] = "none" if response_format is not None else ("low" if thinking else "none")\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''                raise self._http_error(connection.preset, response.status, data, response_headers)\n''',
    '''                provider_error = self._http_error(connection.preset, response.status, data, response_headers)\n                response_format = payload.get("response_format")\n                if (\n                    connection.compatibility_profile == "lm_studio"\n                    and isinstance(response_format, dict)\n                    and response_format.get("type") == "json_schema"\n                    and provider_error.code == "API_INVALID_REQUEST"\n                ):\n                    details = dict(provider_error.details or {})\n                    details["structured_output"] = "json_schema"\n                    raise ModelError(\n                        "API_STRUCTURED_OUTPUT_REJECTED",\n                        "LM Studio rejected the Continuum planner structured-output request.",\n                        details,\n                    ) from provider_error\n                raise provider_error\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''                handler = _ApiChatHandler(self, connection, model_info["remote_model"])\n\n                def complete(**kwargs: Any) -> dict[str, Any]:\n                    kwargs.pop("purpose", None)\n                    return handler(**kwargs)\n\n                result = run_h3_pipeline(\n''',
    '''                handler = _ApiChatHandler(self, connection, model_info["remote_model"])\n                response_format = _lm_studio_continuum_response_format(connection, assembled)\n                structured_planner = response_format is not None\n\n                def complete(**kwargs: Any) -> dict[str, Any]:\n                    kwargs.pop("purpose", None)\n                    kwargs["response_format"] = response_format\n                    return handler(**kwargs)\n\n                result = run_h3_pipeline(\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''                    thinking=thinking,\n                    seed=seed,\n                    on_phase=on_phase,\n''',
    '''                    thinking=False if structured_planner else thinking,\n                    seed=seed,\n                    on_phase=on_phase,\n''',
)

replace_once(
    "backend/routes.py",
    "from .devlog import DEVELOPER_MODE, LOG_PATH, PeakVRAMMonitor, gpu_memory_snapshot, write_event\n",
    "from .continuum_schema import planner_response_metadata\n"
    "from .devlog import DEVELOPER_MODE, LOG_PATH, PeakVRAMMonitor, gpu_memory_snapshot, write_event\n",
)
replace_once(
    "backend/routes.py",
    '        "API_GENERATION_FAILED",\n',
    '        "API_GENERATION_FAILED",\n        "API_STRUCTURED_OUTPUT_REJECTED",\n',
)
replace_once(
    "backend/routes.py",
    '''                                "recovery_error": {\n                                    "code": recovery_error.code,\n                                    "message": recovery_error.message,\n                                    "details": recovery_error.details,\n                                },\n                            },\n''',
    '''                                "recovery_error": {\n                                    "code": recovery_error.code,\n                                    "message": recovery_error.message,\n                                    "details": recovery_error.details,\n                                },\n                                "initial_response": planner_response_metadata(planner_result),\n                                "repair_response": planner_response_metadata(repair_result),\n                            },\n''',
)

print("Continuum structured-output patch applied.")
