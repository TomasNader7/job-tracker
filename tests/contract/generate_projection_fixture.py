"""Regenerates tests/contract/projection_cases_v1.json, the cross-language golden fixture.

Run from the repo root:   python tests/contract/generate_projection_fixture.py
The same file is checked by the C# tests (ProjectionContractTests) and by the Databricks notebook's self-test, so
the C# projection and the Spark pipeline are held to one definition. The expected results below are written BY HAND
(including an independent copy of the transition table), not computed by either implementation.
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "projection_cases_v1.json"
def ts(day): return f"2026-{day}T00:00:00.000000Z" if len(day) == 5 else f"{day}T00:00:00.000000Z"
def ev(seq, app, prev, new, eff, rec=None, src="Manual", sup=None, note=None):
    return {"event_sequence": seq, "application_id": app, "previous_status": prev, "new_status": new,
            "effective_at": ts(eff), "recorded_at": ts(rec or eff), "source": src,
            "supersedes_event_sequence": sup, "note": note}
def corr(seq, app, prev, new, eff, sup, rec=None):
    return ev(seq, app, prev, new, eff, rec, "Correction", sup, "synthetic correction")
def exp(status, nr=0, codes=(), idate=None):
    return {"status": status, "is_no_reply": nr, "problem_codes": sorted(codes), "interview_date": idate}
A, IS, I, R, W, X, NS = "Applied", "InterviewScheduled", "Interviewed", "Rejected", "Withdrawn", "Accepted", "NotStarted"
cases = [
 ("simple_chain", [ev(1,1,None,A,"11-01"), ev(2,1,A,IS,"11-05"), ev(3,1,IS,I,"11-08")], {1: exp(I, 0, [], "2026-11-08")}),
 ("same_timestamp_numeric_sequence_order",  # input order 10 then 9; sequence 9 is recorded LATER than 10, so neither text order nor recorded_at may be used
  [ev(10,1,A,IS,"11-01","11-01"), ev(9,1,None,A,"11-01","11-02")], {1: exp(IS)}),
 ("illegal_transition_stops_at_last_valid", [ev(1,1,None,A,"11-01"), ev(2,1,A,X,"11-05"), ev(3,1,X,W,"11-06")], {1: exp(A, 0, ["ILLEGAL_TRANSITION"])}),
 ("illegal_first_event", [ev(1,1,None,I,"11-01")], {1: exp(None, 0, ["ILLEGAL_FIRST"])}),
 ("first_event_with_previous_status", [ev(1,1,IS,A,"11-01")], {1: exp(A, 0, ["FIRST_PREVIOUS_NOT_NULL"])}),
 ("previous_status_mismatch_chain_wins", [ev(1,1,None,A,"11-01"), ev(2,1,IS,R,"11-05")], {1: exp(R, 0, ["PREVIOUS_MISMATCH"])}),
 ("correction_replaces_wrong_event", [ev(1,1,None,A,"11-01"), ev(2,1,A,R,"11-05"), corr(3,1,A,IS,"11-05",2,"11-06")], {1: exp(IS)}),
 ("correction_of_a_correction", [ev(1,1,None,A,"11-01"), ev(2,1,A,R,"11-05"), corr(3,1,A,IS,"11-05",2,"11-06"), corr(4,1,A,W,"11-05",3,"11-07")], {1: exp(W)}),
 ("correction_target_missing_is_ignored", [ev(1,1,None,A,"11-01"), corr(2,1,A,R,"11-05",99)], {1: exp(A, 0, ["CORRECTION_TARGET_INVALID"])}),
 ("correction_target_in_other_application_is_ignored",
  [ev(1,1,None,A,"11-01"), corr(2,1,A,R,"11-05",3), ev(3,2,None,A,"11-01")],
  {1: exp(A, 0, ["CORRECTION_TARGET_INVALID"]), 2: exp(A)}),
 ("two_corrections_supersede_each_other_both_dropped",
  [ev(1,1,None,A,"11-01"), ev(2,1,A,R,"11-05"), corr(3,1,A,W,"11-06",4,"11-06"), corr(4,1,A,IS,"11-07",3,"11-07")], {1: exp(R)}),
 ("reopen_after_rejection", [ev(1,1,None,A,"11-01"), ev(2,1,A,R,"11-05"), ev(3,1,R,IS,"11-10")], {1: exp(IS)}),
 ("no_reply_exactly_30_days_is_not_yet", [ev(1,1,None,A,"2026-10-17")], {1: exp(A, 0)}),
 ("no_reply_31_days", [ev(1,1,None,A,"2026-10-16")], {1: exp(A, 1)}),
 ("late_reply_clears_no_reply", [ev(1,1,None,A,"2026-09-01"), ev(2,1,A,R,"2026-10-20")], {1: exp(R, 0)}),
 ("not_started_is_never_no_reply", [ev(1,1,None,NS,"2026-08-01")], {1: exp(NS, 0)}),
 ("interview_date_is_first_interviewed_event",
  [ev(1,1,None,A,"11-01"), ev(2,1,A,IS,"11-05"), ev(3,1,IS,I,"11-08"), ev(4,1,I,IS,"11-10"), ev(5,1,IS,I,"11-12")], {1: exp(I, 0, [], "2026-11-08")}),
 ("superseded_interview_is_not_counted",
  [ev(1,1,None,A,"11-01"), ev(2,1,A,IS,"11-05"), ev(3,1,IS,I,"11-08"), corr(4,1,IS,R,"11-08",3,"11-09")], {1: exp(R, 0, [], None)}),
 ("no_reply_uses_the_active_applied_event_time",  # the correction moves Applied back to 2026-10-01, so it is 46 days old
  [ev(1,1,None,A,"11-10"), corr(2,1,None,A,"2026-10-01",1,"11-11")], {1: exp(A, 1)}),
]

# ---- Transition matrix: every (from, to) pair, using an independent copy of the contract's transition table ----
ORDER = [NS, A, IS, I, R, W, X]
ALLOWED = {  # docs/snapshot-contract.md, "Transition table"
    NS: {A, W}, A: {IS, R, W}, IS: {I, R, W}, I: {IS, R, X, W}, R: {IS}, W: set(), X: set(),
}
PATH = {NS: [NS], A: [A], IS: [A, IS], I: [A, IS, I], R: [A, R], W: [A, W], X: [A, IS, I, X]}

def matrix_case():
    events, expected = [], {}
    seq = app = 0
    for frm in ORDER:
        for to in ORDER:
            app += 1
            prev, day, interview = None, 1, None
            for status in PATH[frm]:
                seq += 1
                events.append(ev(seq, app, prev, status, f"11-{day:02d}"))
                if status == I and interview is None:
                    interview = f"2026-11-{day:02d}"
                prev, day = status, day + 1
            seq += 1
            events.append(ev(seq, app, prev, to, f"11-{day:02d}"))
            if to in ALLOWED[frm]:
                if to == I and interview is None:
                    interview = f"2026-11-{day:02d}"
                expected[app] = exp(to, 0, [], interview)
            else:
                expected[app] = exp(frm, 0, ["ILLEGAL_TRANSITION"], interview)
    return events, expected

_events, _expected = matrix_case()
cases.append(("transition_matrix_every_from_to_pair", _events, _expected))

doc = {"contract_version": "1", "as_of": "2026-11-16T00:00:00.000000Z",
       "cases": [{"name": n, "events": e, "expected": {str(k): v for k, v in x.items()}} for n, e, x in cases]}
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False); f.write("\n")
print(len(cases), "cases")
