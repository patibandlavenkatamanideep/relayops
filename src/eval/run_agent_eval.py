"""Agent evaluation — adversarial cases + (optional) LLM-as-judge.

Run:  python3 -m src.eval.run_agent_eval

Runs each adversarial case end-to-end through the pipeline and reports:
  * deterministic checks — disposition, scope refusal, citations, forbidden text
    (the rigorous backbone; always runs, offline)
  * LLM-judge score — groundedness / safety / tone, only if ANTHROPIC_API_KEY set

Showing that you *test the agent* (not just ship it) is the point of this step.
"""

from __future__ import annotations

import os
import traceback

from .agent_cases import CASES, deterministic_failures, run_case


def main() -> None:
    judge = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from .judge import LLMJudge

            judge = LLMJudge()
        except Exception as e:
            print(f"[LLM judge unavailable: {type(e).__name__}: {e}]")
            if os.environ.get("RELAYOPS_DEBUG"):
                traceback.print_exc()

    det_pass = 0
    judge_pass = 0
    judge_scores: list[int] = []

    print(f"running {len(CASES)} adversarial agent cases\n")
    for case in CASES:
        resp = run_case(case)
        fails = deterministic_failures(case, resp)
        ok = not fails
        det_pass += ok
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {case.name}")
        print(f"       disposition={resp.disposition.value}  reply={resp.text[:70]!r}")
        for f in fails:
            print(f"       - {f}")

        if judge is not None:
            try:
                v = judge.judge(case, resp)
                judge_scores.append(v.score)
                judge_pass += v.verdict == "pass"
                print(f"       judge: {v.verdict} ({v.score}/5) — {v.rationale}")
            except Exception as e:
                print(f"       judge: error ({type(e).__name__}: {e})")

    print("\n----- summary -----")
    print(f"deterministic checks: {det_pass}/{len(CASES)} cases pass")
    if judge is not None:
        mean = sum(judge_scores) / len(judge_scores) if judge_scores else 0.0
        print(f"LLM-judge: {judge_pass}/{len(CASES)} pass, mean score {mean:.1f}/5")
    else:
        print("LLM-judge: skipped (set ANTHROPIC_API_KEY to enable)")


if __name__ == "__main__":
    main()
