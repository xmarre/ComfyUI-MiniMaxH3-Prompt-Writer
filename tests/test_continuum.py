from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from backend.continuum import (
    CONTINUUM_SCHEMA_VERSION,
    ContinuumError,
    apply_continuum_refinement,
    assemble_continuum_chunk_request,
    assemble_continuum_plan_repair_request,
    assemble_continuum_plan_request,
    assemble_continuum_refinement,
    generation_target,
    parse_chunk_prompts,
    parse_sequence_plan,
    prompt_hash,
    sequence_result,
    serialize_chunk_prompts,
    validate_continuum_settings,
    validate_generated_chunk,
    validate_sequence_plan,
)


def manifest(mode="T2VA", assets=None):
    return {
        "session_id": "11111111-2222-4333-8444-555555555555",
        "mode": mode,
        "assets": list(assets or []),
        "valid": True,
        "violations": [],
    }


def picture(number=1):
    return {
        "id": f"picture-{number}",
        "type": "image",
        "filename": f"picture-{number}.png",
        "reference": f"<Picture {number}>",
        "content_url": f"/picture-{number}",
        "frames": [],
        "prepared_width": 1024,
        "prepared_height": 768,
    }


def video(number=1):
    return {
        "id": f"video-{number}",
        "type": "video",
        "filename": f"video-{number}.mp4",
        "reference": f"<Video {number}>",
        "content_url": f"/video-{number}",
        "frames": [],
        "contact_sheet_width": 1152,
        "contact_sheet_height": 768,
    }


def settings(chunks=3, chunk_seconds=5.0):
    return {
        "schema_version": CONTINUUM_SCHEMA_VERSION,
        "chunks": chunks,
        "chunk_seconds": chunk_seconds,
        "total_seconds": chunks * chunk_seconds,
    }


def plan(chunks=3, references=(), *, break_at=None):
    return {
        "schema_version": CONTINUUM_SCHEMA_VERSION,
        "global": {
            "continuity_anchors": "Same subject, location, wardrobe, light, camera axis, and room tone.",
            "persistent_constraints": "Preserve the exact dialogue and use one continuous shot.",
            "reference_assignments": [
                {"tag": tag, "role": f"Stable role for {tag}."} for tag in references
            ],
            "subject_anchors": [
                {"id": "<Subject 1>", "meaning": "The same courier throughout the sequence."}
            ] if references else [],
        },
        "chunks": [
            {
                "index": index,
                "continuity": "initial" if index == 1 else "intentional_break" if index == break_at else "continuous",
                "transition": "Requested cut to dawn." if index == break_at else "",
                "start_state": f"Start state {index}.",
                "action": f"Action {index}.",
                "end_state": f"End state {index}.",
            }
            for index in range(1, chunks + 1)
        ],
    }


class ContinuumSettingsTests(unittest.TestCase):
    def test_generation_target_defaults_to_single_clip(self):
        self.assertEqual(generation_target({}), "single")
        self.assertEqual(generation_target({"generation_target": "continuum"}), "continuum")

    def test_native_boundary_values_are_valid(self):
        for chunks in (1, 16):
            for seconds in (4, 15):
                with self.subTest(chunks=chunks, seconds=seconds):
                    value = validate_continuum_settings({
                        "continuum": {"schema_version": 1, "chunks": chunks, "chunk_seconds": seconds}
                    })
                    self.assertEqual(value["chunks"], chunks)
                    self.assertEqual(value["chunk_seconds"], float(seconds))
                    self.assertEqual(value["total_seconds"], chunks * seconds)

    def test_invalid_native_values_are_rejected(self):
        invalid = (
            {"chunks": 0, "chunk_seconds": 5},
            {"chunks": 17, "chunk_seconds": 5},
            {"chunks": True, "chunk_seconds": 5},
            {"chunks": 3, "chunk_seconds": 3.9},
            {"chunks": 3, "chunk_seconds": 15.1},
            {"chunks": 3, "chunk_seconds": True},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ContinuumError):
                validate_continuum_settings({"continuum": {"schema_version": 1, **value}})

    def test_total_sequence_duration_is_not_limited_to_twenty_seconds(self):
        value = validate_continuum_settings({
            "continuum": {"schema_version": 1, "chunks": 12, "chunk_seconds": 5}
        })
        self.assertEqual(value["total_seconds"], 60)


class SequencePlanTests(unittest.TestCase):
    def test_valid_plan_is_normalized_without_losing_intentional_break(self):
        value = validate_sequence_plan(plan(3, break_at=2), settings(), expected_references=set())
        self.assertEqual([item["index"] for item in value["chunks"]], [1, 2, 3])
        self.assertEqual(value["chunks"][1]["continuity"], "intentional_break")
        self.assertEqual(value["chunks"][1]["transition"], "Requested cut to dawn.")

    def test_subject_ids_are_canonicalized_from_common_model_forms(self):
        forms = ("<Subject 1>", "Subject 1", "subject 1", "S1", "subject_1", "1", 1)
        for raw in forms:
            with self.subTest(raw=raw):
                value = plan()
                value["global"]["subject_anchors"] = [
                    {"id": raw, "meaning": "The same courier throughout the sequence."}
                ]
                validated = validate_sequence_plan(value, settings(), expected_references=set())
                self.assertEqual(
                    validated["global"]["subject_anchors"],
                    [{"id": "<Subject 1>", "meaning": "The same courier throughout the sequence."}],
                )

    def test_subject_id_canonicalization_still_rejects_invalid_or_duplicate_identities(self):
        invalid_values = ("", "Subject 0", "zero", -1, 0, True)
        for raw in invalid_values:
            with self.subTest(raw=raw):
                value = plan()
                value["global"]["subject_anchors"] = [
                    {"id": raw, "meaning": "The same courier throughout the sequence."}
                ]
                with self.assertRaises(ContinuumError):
                    validate_sequence_plan(value, settings(), expected_references=set())

        duplicate = plan()
        duplicate["global"]["subject_anchors"] = [
            {"id": "Subject 1", "meaning": "Courier."},
            {"id": "1", "meaning": "Same courier again."},
        ]
        with self.assertRaises(ContinuumError) as raised:
            validate_sequence_plan(duplicate, settings(), expected_references=set())
        self.assertIn("<Subject 1>", raised.exception.message)

    def test_missing_duplicate_and_noncontiguous_chunks_are_rejected(self):
        cases = []
        missing = plan()
        missing["chunks"].pop()
        cases.append(missing)
        duplicate = plan()
        duplicate["chunks"][1]["index"] = 1
        cases.append(duplicate)
        noncontiguous = plan()
        noncontiguous["chunks"][1]["index"] = 3
        cases.append(noncontiguous)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ContinuumError):
                validate_sequence_plan(value, settings(), expected_references=set())

    def test_continuity_break_requires_an_explicit_reason(self):
        value = plan(break_at=2)
        value["chunks"][1]["transition"] = ""
        with self.assertRaises(ContinuumError) as raised:
            validate_sequence_plan(value, settings(), expected_references=set())
        self.assertEqual(raised.exception.code, "INVALID_CONTINUUM_PLAN_CONTINUITY")

    def test_reference_inventory_is_injected_from_manifest_not_model_output(self):
        value = plan(references=("<Picture 1>", "<Video 1>"))
        value["global"]["reference_assignments"] = [
            {"tag": "", "role": "bad model output"},
            "<Picture 999>",
        ]
        validated = validate_sequence_plan(
            value,
            settings(),
            expected_references={"<Picture 1>", "<Video 1>"},
        )
        self.assertEqual(
            validated["global"]["reference_assignments"],
            [
                {
                    "tag": "<Picture 1>",
                    "role": "Preserve this exact media-reference identity and its creative role across every chunk.",
                },
                {
                    "tag": "<Video 1>",
                    "role": "Preserve this exact media-reference identity and its creative role across every chunk.",
                },
            ],
        )

    def test_json_fence_is_the_only_tolerated_wrapper(self):
        payload = __import__("json").dumps(plan())
        self.assertEqual(parse_sequence_plan(f"```json\n{payload}\n```", settings(), expected_references=set()), plan())
        with self.assertRaises(ContinuumError):
            parse_sequence_plan(f"Here is the plan:\n{payload}", settings(), expected_references=set())


class CanonicalSequenceTests(unittest.TestCase):
    def test_serializer_round_trip_and_hashes_match_continuum_contract(self):
        prompts = ["First prompt.\nSecond line.", "Second prompt.", "Third prompt."]
        script = serialize_chunk_prompts(prompts)
        self.assertEqual(
            script,
            "[Chunk 1]\nFirst prompt.\nSecond line.\n\n[Chunk 2]\nSecond prompt.\n\n[Chunk 3]\nThird prompt.",
        )
        self.assertEqual(parse_chunk_prompts(script, expected_chunks=3), prompts)
        result = sequence_result(settings(), plan(), prompts)
        self.assertEqual([item["hash"] for item in result["chunks"]], [prompt_hash(value) for value in prompts])

    def test_parser_rejects_missing_duplicate_noncontiguous_or_empty_sections(self):
        values = (
            "[Chunk 1]\nOne\n\n[Chunk 3]\nThree",
            "[Chunk 1]\nOne\n\n[Chunk 1]\nAgain",
            "[Chunk 1]\n\n[Chunk 2]\nTwo",
            "preamble\n[Chunk 1]\nOne",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(ContinuumError):
                parse_chunk_prompts(value)

    def test_reserved_chunk_header_inside_a_prompt_is_rejected(self):
        with self.assertRaises(ContinuumError):
            serialize_chunk_prompts(["Normal", "Nested\n[Chunk 9]\nBad"])


class ContinuumAssemblyTests(unittest.TestCase):
    def body(self, mode="T2VA"):
        return {
            "session_id": "11111111-2222-4333-8444-555555555555",
            "mode": mode,
            "generation_target": "continuum",
            "continuum": {"schema_version": 1, "chunks": 3, "chunk_seconds": 5},
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "creative_brief": "Keep one continuous shot. (S1) says <d>[English] Stay with me.</d>",
        }

    def test_planner_preserves_mode_dialogue_and_native_duration_concepts(self):
        with patch("backend.assembly.STORE.manifest", return_value=manifest()):
            assembled = assemble_continuum_plan_request(self.body())
        user = assembled["messages"][-1]["content"]
        self.assertIn("Underlying H3 mode: T2VA", user)
        self.assertIn("<d>[English] Stay with me.</d>", user)
        self.assertIn("Chunks: 3", user)
        self.assertIn("Total sequence duration: 15 seconds", user)
        self.assertEqual(assembled["input"]["duration_seconds"], 5.0)
        self.assertEqual(assembled["input"]["continuum_stage"], "plan")
        self.assertEqual(assembled["media_inputs"], [])

    def test_all_underlying_h3_modes_keep_the_existing_assembly_path(self):
        mode_assets = {
            "T2VA": [],
            "I2VA": [picture(1)],
            "FL2VA": [picture(1), picture(2)],
            "L2VA": [picture(1)],
            "Reference": [picture(1), video(1)],
        }
        for mode, assets in mode_assets.items():
            refs = tuple(item["reference"] for item in assets)
            value = plan(references=refs)
            with self.subTest(mode=mode), patch(
                "backend.assembly.STORE.manifest",
                return_value=manifest(mode, assets),
            ):
                assembled = assemble_continuum_chunk_request(
                    self.body(mode), value, 2, previous_prompt="Previous complete H3 prompt."
                )
                self.assertEqual(assembled["input"]["mode"], mode)
                self.assertEqual(assembled["input"]["duration_seconds"], 5.0)
                self.assertEqual(assembled["input"]["continuum_chunk_index"], 2)
                self.assertIn("Previous complete H3 prompt.", assembled["messages"][-1]["content"])
                self.assertIn("Required previous terminal state: End state 1.", assembled["messages"][-1]["content"])

    def test_planner_schema_defines_subjects_and_keeps_reference_identity_out_of_model_contract(self):
        assets = [picture(1), video(1)]
        with patch("backend.assembly.STORE.manifest", return_value=manifest("Reference", assets)):
            assembled = assemble_continuum_plan_request(self.body("Reference"))
        system = assembled["messages"][0]["content"]
        user = assembled["messages"][1]["content"]
        self.assertIn('subject_anchors must be []', system)
        self.assertIn('{"id":"<Subject N>","meaning":"concise stable identity and role"}', system)
        self.assertIn('"id": "<Subject 1>"', user)
        self.assertIn('"meaning": "stable identity and role of this subject across chunks"', user)
        self.assertIn("<Picture 1>: picture-1.png (image)", user)
        self.assertIn("<Video 1>: video-1.mp4 (video)", user)
        self.assertIn("Do not add a reference_assignments field", user)
        schema = user.split("Schema:\n", 1)[1]
        self.assertNotIn('"reference_assignments"', schema)

    def test_plan_repair_is_narrow_and_retains_original_request(self):
        error = ContinuumError("INVALID_CONTINUUM_PLAN", "Chunk 2 is missing.", {"chunk": 2})
        with patch("backend.assembly.STORE.manifest", return_value=manifest()):
            assembled = assemble_continuum_plan_repair_request(self.body(), "{bad", error)
        self.assertEqual(assembled["input"]["continuum_stage"], "plan_repair")
        self.assertIn("Repair only the structural", assembled["messages"][0]["content"])
        self.assertIn("Do not emit reference_assignments", assembled["messages"][0]["content"])
        self.assertIn("subject_anchors must be [] or a list of objects", assembled["messages"][0]["content"])
        self.assertIn("never use bare strings", assembled["messages"][0]["content"])
        self.assertIn("Chunk 2 is missing", assembled["messages"][1]["content"])
        self.assertIn("Stay with me", assembled["messages"][1]["content"])

    def test_generated_chunk_rejects_reference_and_subject_identity_drift(self):
        assets = [picture(1)]
        with patch("backend.assembly.STORE.manifest", return_value=manifest("Reference", assets)):
            assembled = assemble_continuum_chunk_request(
                self.body("Reference"), plan(references=("<Picture 1>",)), 1, previous_prompt=None
            )
        self.assertEqual(
            validate_generated_chunk("<Subject 1> uses <Picture 1>.", assembled),
            "<Subject 1> uses <Picture 1>.",
        )
        with self.assertRaises(ContinuumError) as reference_error:
            validate_generated_chunk("Use <Picture 2>.", assembled)
        self.assertEqual(reference_error.exception.code, "CONTINUUM_REFERENCE_IDENTITY_DRIFT")
        with self.assertRaises(ContinuumError) as subject_error:
            validate_generated_chunk("<Subject 2> uses <Picture 1>.", assembled)
        self.assertEqual(subject_error.exception.code, "CONTINUUM_SUBJECT_IDENTITY_DRIFT")


class ContinuumRefinementTests(unittest.TestCase):
    def body(self):
        prompts = ["Chunk one prompt.", "Chunk two prompt.", "Chunk three prompt."]
        value = plan()
        return {
            "session_id": "11111111-2222-4333-8444-555555555555",
            "mode": "T2VA",
            "generation_target": "continuum",
            "continuum": {
                "schema_version": 1,
                "chunks": 3,
                "chunk_seconds": 5,
                "chunk_index": 2,
                "plan": value,
            },
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "creative_brief": "One continuous shot.",
            "current_prompt": serialize_chunk_prompts(prompts),
            "instruction": "Slow the hand movement.",
        }, prompts, value

    def test_chunk_local_refinement_keeps_other_chunks_byte_for_byte(self):
        body, prompts, value = self.body()
        with patch("backend.assembly.STORE.manifest", return_value=manifest()):
            assembled, parsed, index, saved_plan = assemble_continuum_refinement(body)
        self.assertEqual(parsed, prompts)
        self.assertEqual(index, 2)
        self.assertEqual(saved_plan, value)
        self.assertIn("Following unchanged chunk prompt", assembled["messages"][-1]["content"])
        result = apply_continuum_refinement(
            "Refined chunk two prompt.", assembled, parsed, index, saved_plan
        )
        updated = [item["prompt"] for item in result["chunks"]]
        self.assertEqual(updated[0], prompts[0])
        self.assertEqual(updated[1], "Refined chunk two prompt.")
        self.assertEqual(updated[2], prompts[2])
        self.assertEqual(result["chunks"][0]["hash"], prompt_hash(prompts[0]))
        self.assertEqual(result["chunks"][2]["hash"], prompt_hash(prompts[2]))


if __name__ == "__main__":
    unittest.main()
