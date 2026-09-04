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
    "\ndef _lm_studio_continuum_response_format(\n",
    '''\ndef _uses_lm_studio_continuum_structured_output(\n    *,\n    preset: str,\n    compatibility_profile: str,\n    assembled: dict[str, Any],\n) -> bool:\n    request_input = assembled.get("input", {})\n    return (\n        preset == "custom"\n        and compatibility_profile == "lm_studio"\n        and request_input.get("generation_target") == "continuum"\n        and request_input.get("continuum_stage") in {"plan", "plan_repair"}\n    )\n\n\ndef _lm_studio_continuum_response_format(\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''    if connection.preset != "custom" or connection.compatibility_profile != "lm_studio":\n        return None\n    request_input = assembled.get("input", {})\n    if (\n        request_input.get("generation_target") != "continuum"\n        or request_input.get("continuum_stage") not in {"plan", "plan_repair"}\n    ):\n        return None\n    settings = request_input.get("continuum")\n''',
    '''    if not _uses_lm_studio_continuum_structured_output(\n        preset=connection.preset,\n        compatibility_profile=connection.compatibility_profile,\n        assembled=assembled,\n    ):\n        return None\n    request_input = assembled.get("input", {})\n    settings = request_input.get("continuum")\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''            "api_preset": connection.preset,\n            "endpoint": connection.base_url,\n''',
    '''            "api_preset": connection.preset,\n            "api_compatibility_profile": connection.compatibility_profile,\n            "endpoint": connection.base_url,\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''        if thinking and model_info.get("thinking") is not True:\n            raise ModelError("API_THINKING_UNAVAILABLE", "This provider model does not report reasoning controls.")\n''',
    '''        structured_planner = _uses_lm_studio_continuum_structured_output(\n            preset=str(model_info.get("api_preset") or ""),\n            compatibility_profile=str(model_info.get("api_compatibility_profile") or "generic"),\n            assembled=assembled,\n        )\n        effective_thinking = False if structured_planner else thinking\n        if effective_thinking and model_info.get("thinking") is not True:\n            raise ModelError("API_THINKING_UNAVAILABLE", "This provider model does not report reasoning controls.")\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''        desired_output = THINKING_OUTPUT_TOKENS if thinking else standard_output_tokens\n''',
    '''        desired_output = THINKING_OUTPUT_TOKENS if effective_thinking else standard_output_tokens\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''        minimum_output = MINIMUM_OUTPUT_TOKENS if thinking else desired_output\n''',
    '''        minimum_output = MINIMUM_OUTPUT_TOKENS if effective_thinking else desired_output\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''            "thinking": thinking,\n            "estimated_text_tokens": estimated_text_tokens,\n''',
    '''            "thinking": effective_thinking,\n            "estimated_text_tokens": estimated_text_tokens,\n''',
)
replace_once(
    "backend/models/api_provider_backend.py",
    '''            "thinking_budget_reduced": thinking and max_output_tokens < THINKING_OUTPUT_TOKENS,\n''',
    '''            "thinking_budget_reduced": effective_thinking and max_output_tokens < THINKING_OUTPUT_TOKENS,\n''',
)

replace_once(
    "tests/test_continuum_structured_output.py",
    "from backend.models.api_provider_backend import (\n",
    "from backend.models.contract import ModelError\nfrom backend.models.api_provider_backend import (\n",
)
replace_once(
    "tests/test_continuum_structured_output.py",
    "    def test_handler_sends_schema_and_disables_lm_studio_reasoning_for_constrained_planner(self) -> None:\n",
    '''    def test_lm_studio_planner_preflight_uses_non_thinking_8192_budget(self) -> None:\n        model = self.backend._model_info(self.connection(), "qwen-model")\n        plan = self.backend.preflight(\n            model,\n            _assembled("plan"),\n            context_profile="auto",\n            kv_cache="auto",\n            thinking=True,\n        )\n        self.assertFalse(plan["thinking"])\n        self.assertEqual(plan["context_tokens"], 15360)\n        self.assertEqual(plan["max_output_tokens"], 8192)\n\n        repair = self.backend.preflight(\n            model,\n            _assembled("plan_repair"),\n            context_profile="auto",\n            kv_cache="auto",\n            thinking=True,\n        )\n        self.assertFalse(repair["thinking"])\n        self.assertEqual(repair["max_output_tokens"], 8192)\n\n    def test_non_planner_lm_studio_and_generic_planner_keep_thinking_validation(self) -> None:\n        lm_studio_model = self.backend._model_info(self.connection(), "qwen-model")\n        with self.assertRaises(ModelError) as normal:\n            self.backend.preflight(\n                lm_studio_model,\n                {"input": {"generation_target": "single"}, "messages": [], "media_inputs": []},\n                context_profile="auto",\n                kv_cache="auto",\n                thinking=True,\n            )\n        self.assertEqual(normal.exception.code, "API_THINKING_UNAVAILABLE")\n\n        generic_model = self.backend._model_info(self.connection(profile="generic"), "qwen-model")\n        with self.assertRaises(ModelError) as generic:\n            self.backend.preflight(\n                generic_model,\n                _assembled("plan"),\n                context_profile="auto",\n                kv_cache="auto",\n                thinking=True,\n            )\n        self.assertEqual(generic.exception.code, "API_THINKING_UNAVAILABLE")\n\n    def test_lm_studio_structured_output_rejection_is_distinct_and_content_free(self) -> None:\n        class Response:\n            status = 400\n\n            @staticmethod\n            def getheaders():\n                return [("X-Request-ID", "structured-request")]\n\n            @staticmethod\n            def read():\n                return json.dumps({\n                    "error": {\n                        "message": "unsupported response_format",\n                        "type": "invalid_request_error",\n                    }\n                }).encode("utf-8")\n\n        class HttpConnection:\n            def request(self, *_args, **_kwargs):\n                return None\n\n            @staticmethod\n            def getresponse():\n                return Response()\n\n            @staticmethod\n            def close():\n                return None\n\n        response_format = _lm_studio_continuum_response_format(self.connection(), _assembled("plan"))\n        payload = {\n            "model": "qwen-model",\n            "messages": [{"role": "user", "content": "private creative brief"}],\n            "stream": True,\n            "response_format": response_format,\n        }\n        with (\n            patch.object(self.backend, "_http_connection", return_value=HttpConnection()),\n            self.assertRaises(ModelError) as raised,\n        ):\n            self.backend._request_chat_completion_stream(self.connection(), payload)\n        self.assertEqual(raised.exception.code, "API_STRUCTURED_OUTPUT_REJECTED")\n        serialized = json.dumps(raised.exception.details)\n        self.assertNotIn("private creative brief", serialized)\n        self.assertNotIn("properties", serialized)\n        self.assertIn("structured-request", serialized)\n\n    def test_handler_sends_schema_and_disables_lm_studio_reasoning_for_constrained_planner(self) -> None:\n''',
)

replace_once(
    "tests/test_continuum_routes.py",
    "    async def test_non_json_initial_then_empty_preamble_repair_recovers_without_third_model_call(self):\n",
    '''    async def test_double_non_json_plan_failure_reports_content_free_structural_metadata(self):\n        initial = "<think>private initial planner text with no JSON"\n        repair = "repair prose with one opening brace { but no complete object"\n        response, stage, _backend = await self.run_sequence([\n            {"prompt": initial, "primary_finish_reason": "stop"},\n            {"prompt": repair, "primary_finish_reason": "stop"},\n        ])\n        self.assertEqual(response.status, 502)\n        error = self.payload(response)["error"]\n        self.assertEqual(error["code"], "INVALID_CONTINUUM_PLAN")\n        self.assertEqual(stage.await_count, 2)\n        details = error["details"]\n        self.assertEqual(details["initial_response"]["chars"], len(initial))\n        self.assertEqual(details["initial_response"]["finish_reason"], "stop")\n        self.assertTrue(details["initial_response"]["starts_with_think"])\n        self.assertFalse(details["initial_response"]["contains_object_open"])\n        self.assertEqual(details["repair_response"]["chars"], len(repair))\n        self.assertTrue(details["repair_response"]["contains_object_open"])\n        self.assertFalse(details["repair_response"]["contains_object_close"])\n        serialized = json.dumps(details)\n        self.assertNotIn("private initial planner text", serialized)\n        self.assertNotIn("repair prose", serialized)\n\n    async def test_non_json_initial_then_empty_preamble_repair_recovers_without_third_model_call(self):\n''',
)

print("Applied structured-output preflight and diagnostic follow-up.")
