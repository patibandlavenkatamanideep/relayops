"""Step 5 tests — adversarial agent cases + LLM-judge parsing.

The deterministic agent suite runs fully offline and is the safety backbone: every
adversarial case must satisfy its expectations (correct escalation, scope refusal,
grounded citations, guardrail block). The LLM judge needs an API key, so only its
pure verdict parser is unit-tested here.
"""

from __future__ import annotations

import unittest

from src.core.models import Disposition
from src.eval.agent_cases import CASES, deterministic_failures, run_case
from src.eval.judge import parse_verdict


class AgentCaseTests(unittest.TestCase):
    def test_all_cases_pass_deterministic_checks(self):
        failures = {}
        for case in CASES:
            resp = run_case(case)
            f = deterministic_failures(case, resp)
            if f:
                failures[case.name] = f
        self.assertEqual(failures, {}, f"deterministic failures: {failures}")

    def test_scope_violation_escalates_and_hides_other_customer(self):
        case = next(c for c in CASES if c.name == "cross_customer_scope_refusal")
        resp = run_case(case)
        self.assertEqual(resp.disposition, Disposition.ESCALATE)
        self.assertIn("scope_violation", (resp.handoff_context or {}).get("reason", ""))
        self.assertNotIn("bob", resp.text.lower())

    def test_invented_offer_is_blocked(self):
        case = next(c for c in CASES if c.name == "guardrail_blocks_invented_offer")
        resp = run_case(case)
        self.assertEqual(resp.guardrail_action, "block")
        self.assertNotIn("$9.99", resp.text)

    def test_faq_is_cited(self):
        case = next(c for c in CASES if c.name == "faq_grounded_with_citations")
        resp = run_case(case)
        self.assertTrue(resp.citations)


class VerdictParserTests(unittest.TestCase):
    def test_strict_json(self):
        v = parse_verdict('{"verdict": "pass", "score": 5, "rationale": "grounded"}')
        self.assertEqual(v.verdict, "pass")
        self.assertEqual(v.score, 5)

    def test_noisy_wrapping(self):
        v = parse_verdict('Sure: {"verdict":"fail","score":2,"rationale":"leaked"} done')
        self.assertEqual(v.verdict, "fail")
        self.assertEqual(v.score, 2)

    def test_score_clamped(self):
        v = parse_verdict('{"verdict":"pass","score":9,"rationale":"x"}')
        self.assertEqual(v.score, 5)

    def test_garbage_defaults_to_fail(self):
        self.assertEqual(parse_verdict("no json here").verdict, "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
