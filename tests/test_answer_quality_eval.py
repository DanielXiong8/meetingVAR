"""Synthetic evaluator regression suite, not real model semantic evaluation."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_agent_eval import ev

FIXTURES = Path(__file__).parent / "fixtures/answer_quality"


class AnswerQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = ev.load_cases(FIXTURES / "cases.json")
        cls.by_id = {case.case_id: case for case in cls.cases}
        cls.manifest = json.loads((FIXTURES / "replay.json").read_text(encoding="utf-8"))

    def score(self, cid, variant="B", report=None, **kwargs):
        config = self.manifest["variants"][variant]
        if "execution" not in kwargs and cid in config["execution"]:
            kwargs["execution"] = ev.ExecutionEvidence(**config["execution"][cid])
        return ev.score_report(self.by_id[cid],
                               copy.deepcopy(config["reports"][cid]) if report is None else report, **kwargs)

    def test_twenty_unique_artificial_cases_and_program_gold(self):
        self.assertEqual(len(self.cases), 20)
        self.assertEqual(len(self.by_id), 20)
        self.assertTrue(all(c.label_origin == "synthetic" and c.oracle_answers for c in self.cases))
        self.assertTrue(all(s["source_id"].startswith("synthetic-") for c in self.cases for s in c.sources))
        for case in self.cases:
            for source in case.sources:
                self.assertEqual(source["revision"], ev.source_revision(source["pages"]))

    def test_all_adversarial_fixture_statuses(self):
        for cid, expected in self.manifest["expected_adversarial_status"].items():
            with self.subTest(case_id=cid):
                self.assertEqual(self.score(cid).status, expected)

    def test_project_overview_covers_goal_stage_tasks_and_unknown_assignment(self):
        row = self.score("01_project_overview")
        self.assertTrue(row.ok)
        self.assertEqual(row.missing_claims, [])
        self.assertEqual(row.verification_basis, "synthetic_exact_oracle")

    def test_invented_owner_and_date_are_forbidden(self):
        row = self.score("02_no_invented_owner_date")
        self.assertFalse(row.ok)
        self.assertEqual(row.forbidden_claims_found, ["负责人：小王", "截止日期：2026-12-01"])

    def test_scoped_enumeration_requires_all_items(self):
        row = self.score("03_scoped_count")
        self.assertTrue(row.ok)
        self.assertEqual(row.coverage_status, "passed")
        self.assertEqual(row.missing_items, [])

    def test_queue_variants_are_separate_from_base_structure_count(self):
        row = self.score("04_variant_count")
        self.assertTrue(row.ok)
        self.assertEqual(row.extra_items, [])

    def test_missing_list_item_not_hidden_by_report_items_or_count(self):
        row = self.score("05_missing_list_item")
        self.assertEqual(row.missing_items, ["链表"])
        self.assertFalse(row.ok)

    def test_extra_out_of_scope_item_is_reported(self):
        row = self.score("06_extra_out_of_scope_item")
        self.assertEqual(row.extra_items, ["堆"])
        self.assertFalse(row.ok)

    def test_false_premise_must_be_corrected_not_accepted(self):
        row = self.score("07_false_premise")
        self.assertFalse(row.ok)
        self.assertIn("前提不成立", row.missing_claims)

    def test_multi_turn_latest_user_scope_is_respected(self):
        good = self.score("08_follow_correction")
        bad = self.score("09_ignore_correction")
        self.assertTrue(good.ok)
        self.assertTrue(bad.constraint_violations)
        self.assertFalse(bad.ok)

    def test_source_update_accepts_current_revision_only(self):
        self.assertTrue(self.score("10_current_source_version").ok)
        stale = self.score("11_stale_source_version")
        self.assertIn("source_revision_mismatch", stale.citation_checks[0]["reasons"])
        self.assertFalse(stale.source_validated)

    def test_existing_id_does_not_validate_wrong_proposition(self):
        row = self.score("12_real_id_wrong_proposition")
        self.assertTrue(row.source_validated)
        self.assertTrue(row.reported_grounded)
        self.assertTrue(row.reported_semantic_verified)
        self.assertFalse(row.grounded)
        self.assertFalse(row.ok)

    def test_correct_quote_does_not_entail_opposite_conclusion(self):
        row = self.score("13_correct_quote_opposite_conclusion")
        self.assertTrue(row.source_validated)
        self.assertEqual(row.missing_claims, [])
        self.assertEqual(row.semantic_status, "failed")
        self.assertFalse(row.ok)

    def test_wrong_page_rejected_even_with_real_quote_and_id(self):
        row = self.score("14_quote_wrong_page")
        self.assertIn("quote_not_at_locator", row.citation_checks[0]["reasons"])
        self.assertFalse(row.ok)

    def test_wrong_line_rejected_even_with_real_quote_and_id(self):
        row = self.score("15_quote_wrong_line")
        self.assertIn("quote_not_at_locator", row.citation_checks[0]["reasons"])
        self.assertFalse(row.ok)

    def test_quote_must_match_independent_snapshot(self):
        row = self.score("16_invented_quote")
        self.assertIn("quote_not_at_locator", row.citation_checks[0]["reasons"])

    def test_infra_failure_without_answer_is_not_semantic_incorrect_trial(self):
        row = self.score("17_service_failure")
        self.assertEqual(row.status, "infra_failure")
        self.assertEqual(row.quality_status, "unverified")
        self.assertEqual(row.semantic_status, "unverified")

    def test_rate_limit_counted_once_even_in_receipts_and_errors(self):
        row = self.score("18_rate_limit_429")
        self.assertEqual(row.infra_failures, ["rate_limited"])
        summary = ev.summarize([row])
        self.assertEqual(summary["infra_failure_counts"], {"rate_limited": 1})
        self.assertIsNone(summary["evaluated_accuracy"])

    def test_empty_read_is_not_confirmed_absence(self):
        row = self.score("19_empty_read_not_absence")
        self.assertEqual(row.status, "infra_failure")
        self.assertIn("empty_read", row.infra_failures)
        self.assertFalse(row.ok)

    def test_partial_coverage_cannot_be_masked_by_complete_report_flag(self):
        row = self.score("20_partial_coverage")
        self.assertEqual(row.coverage_status, "failed")
        self.assertEqual(row.missing_units, ["synthetic-structures:p2", "synthetic-structures:p3"])
        self.assertFalse(row.ok)

    def test_missing_independent_coverage_receipts_is_unverified(self):
        row = self.score("03_scoped_count", execution=None)
        self.assertEqual(row.status, "unverified")
        self.assertFalse(row.ok)

    def test_duplicate_enumeration_is_not_inflated_count(self):
        cid = "03_scoped_count"
        report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
        report["answer"] += "\n- 队列"
        row = self.score(cid, report=report)
        self.assertEqual(row.duplicate_items, ["队列"])
        self.assertFalse(row.ok)

    def test_all_candidate_green_flags_are_non_authoritative(self):
        result = ev.compare_variants(self.cases, self.manifest["variants"])
        b, c = (result["variants"][name] for name in ("B", "C"))
        self.assertEqual(b["summary"], c["summary"])
        for x, y in zip(b["rows"], c["rows"]):
            self.assertEqual({k:v for k,v in x.items() if not k.startswith("reported_")},
                             {k:v for k,v in y.items() if not k.startswith("reported_")})

    def test_unlabeled_case_and_model_self_review_cannot_turn_green(self):
        cid = "01_project_overview"
        case = replace(self.by_id[cid], label_origin="unlabeled", annotation_id="", oracle_answers=())
        report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
        report["semantic_review"] = {"verdict": "passed", "method": "human"}
        row = ev.score_report(case, report)
        self.assertEqual(row.status, "unverified")
        self.assertTrue(row.source_validated)
        self.assertFalse(row.grounded)

    def human_case_report(self):
        cid = "01_project_overview"
        case = replace(self.by_id[cid], label_origin="human", annotation_id="unit-test-label-not-real-human-data", oracle_answers=())
        report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
        return case, report

    def review(self, case, report, verdict="passed", **kwargs):
        fields = dict(case_id=case.case_id, case_digest=ev.case_digest(case),
                      answer_digest=ev.answer_digest(report), reviewer="unit-test-reviewer",
                      method="human", verdict=verdict, rationale="Simulated review for evaluator contract tests only.")
        fields.update(kwargs)
        return ev.SemanticReview(**fields)

    def test_human_label_without_independent_review_is_unverified(self):
        case, report = self.human_case_report()
        self.assertEqual(ev.score_report(case, report).status, "unverified")

    def test_independent_review_can_establish_semantic_result(self):
        case, report = self.human_case_report()
        row = ev.score_report(case, report, review=self.review(case, report))
        self.assertEqual(row.status, "passed")
        bad = ev.score_report(case, report, review=self.review(case, report, "failed"))
        self.assertEqual(bad.status, "failed")

    def test_review_binding_rejects_changed_answer(self):
        case, report = self.human_case_report()
        review = self.review(case, report)
        report["answer"] += "相反结论。"
        row = ev.score_report(case, report, review=review)
        self.assertEqual(row.status, "unverified")
        self.assertIn("semantic_review_binding_mismatch", row.unverified_reasons)

    def test_review_binding_rejects_changed_history_scope_or_source(self):
        case, report = self.human_case_report()
        review = self.review(case, report)
        changed = replace(case, history=({"role": "user", "content": "改变范围"},))
        row = ev.score_report(changed, report, review=review)
        self.assertIn("semantic_review_binding_mismatch", row.unverified_reasons)

    def test_untrusted_review_method_or_empty_rationale_cannot_pass(self):
        case, report = self.human_case_report()
        for override in ({"method": "self_report"}, {"rationale": ""}, {"reviewer": ""}):
            with self.subTest(override=override):
                row = ev.score_report(case, report, review=self.review(case, report, **override))
                self.assertEqual(row.status, "unverified")

    def test_review_abstention_remains_unverified(self):
        case, report = self.human_case_report()
        row = ev.score_report(case, report, review=self.review(case, report, "unverified"))
        self.assertEqual(row.status, "unverified")

    def test_real_cases_cannot_use_synthetic_program_oracle(self):
        case, report = self.human_case_report()
        with self.assertRaisesRegex(ValueError, "synthetic"):
            ev.score_report(replace(case, oracle_answers=(report["answer"],)), report)

    def test_snapshot_hash_tampering_is_detected(self):
        case = copy.deepcopy(self.by_id["01_project_overview"])
        case.sources[0]["pages"][0]["lines"][0] = "altered"
        with self.assertRaisesRegex(ValueError, "revision"):
            ev.score_report(case, {})

    def test_unknown_id_and_candidate_source_refs_do_not_supply_truth(self):
        cid = "01_project_overview"
        report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
        report["citations"][0]["source_id"] = "invented"
        report["source_refs"] = [copy.deepcopy(report["citations"][0])]
        row = self.score(cid, report=report)
        self.assertIn("unknown_source", row.citation_checks[0]["reasons"])
        self.assertFalse(row.ok)

    def test_missing_revision_quote_and_invalid_ranges_fail_closed(self):
        cid = "01_project_overview"
        for patch_value in ({"revision": ""}, {"quote": ""}, {"page_start": True},
                            {"line_start": 0}, {"line_start": 4, "line_end": 2},
                            {"page_end": 10**12}, {"path": "../data/private.txt"}):
            with self.subTest(patch=patch_value):
                report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
                report["citations"][0].update(patch_value)
                self.assertFalse(self.score(cid, report=report).ok)

    def test_citation_cannot_escape_task_page_scope(self):
        cid = "03_scoped_count"
        report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
        c = report["citations"][0]
        c.update(page_start=3, page_end=3, line_start=1, line_end=1, quote="范围外补充：堆。")
        row = self.score(cid, report=report)
        self.assertIn("page_out_of_scope", row.citation_checks[0]["reasons"])

    def test_a_b_c_comparison_reports_no_real_semantic_measurements(self):
        result = ev.compare_variants(self.cases, self.manifest["variants"])
        self.assertFalse(result["external_model_called"])
        self.assertFalse(result["model_identity_verified"])
        for run in result["variants"].values():
            self.assertEqual(run["summary"]["synthetic_cases"], 20)
            self.assertEqual(run["summary"]["real_semantic_evaluated"], 0)
            self.assertIsNone(run["summary"]["real_semantic_accuracy"])
            self.assertFalse(run["summary"]["all_passed"])

    def test_comparison_rejects_different_models_and_generation_settings(self):
        for change in ({"model_id": "different"}, {"generation": {"temperature": 0.7}}):
            variants = copy.deepcopy(self.manifest["variants"])
            variants["B"].update(change)
            with self.assertRaisesRegex(ValueError, "identical"):
                ev.compare_variants(self.cases, variants)

    def test_missing_report_counted_not_dropped_from_comparison(self):
        variants = copy.deepcopy(self.manifest["variants"])
        variants["B"]["reports"].pop("01_project_overview")
        result = ev.compare_variants(self.cases, variants)["variants"]["B"]
        self.assertEqual(result["summary"]["total"], 20)
        self.assertIn("missing_report", result["rows"][0]["infra_failures"])

    def test_unknown_report_id_rejected(self):
        variants = copy.deepcopy(self.manifest["variants"])
        variants["B"]["reports"]["not-in-cases"] = {}
        with self.assertRaises(ValueError):
            ev.compare_variants(self.cases, variants)

    def test_runner_cannot_mutate_evaluator_owned_snapshot(self):
        case = copy.deepcopy(self.by_id["01_project_overview"])
        original = ev.case_digest(case)
        def runner(copy_of_case):
            copy_of_case.sources[0]["pages"][0]["lines"][0] = "changed"
            return self.manifest["variants"]["A"]["reports"][case.case_id]
        result = ev.evaluate([case], runner)
        self.assertEqual(ev.case_digest(case), original)
        self.assertTrue(result["rows"][0]["ok"])

    def test_evaluator_does_not_open_candidate_paths(self):
        cid = "01_project_overview"
        report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
        report["citations"][0]["path"] = "C:/private/forbidden/file.txt"
        with patch("builtins.open", side_effect=AssertionError("must not open files")):
            row = self.score(cid, report=report)
        self.assertFalse(row.ok)

    def test_offline_cli_uses_local_manifests_only(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = ev.main(["--cases", str(FIXTURES / "cases.json"),
                            "--reports", str(FIXTURES / "replay.json")])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(set(result["variants"]), {"A", "B", "C"})
        self.assertFalse(result["external_model_called"])

    def test_refusal_needs_gold_not_answered_false(self):
        case = ev.EvalCase("refuse", "unknown", expect_refusal=True)
        row = ev.score_report(case, {"answer": "No evidence", "answered": False, "ok": True})
        self.assertEqual(row.status, "unverified")
        self.assertEqual(ev.summarize([row])["refusal_evaluated"], 0)

    def test_step_budget_is_measured_separately_from_correctness(self):
        cid = "01_project_overview"
        report = copy.deepcopy(self.manifest["variants"]["A"]["reports"][cid])
        report["steps"] = 100
        row = self.score(cid, report=report)
        self.assertTrue(row.budget_exceeded)
        self.assertTrue(row.ok)
