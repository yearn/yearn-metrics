"""Explicit repair of historical canonical fee projections."""

import json

from .fee_acceptance import POLICY_VERSION, assess_candidate, normalize_candidate
from .fee_zero_rules import ZERO_GAIN_RELEASES
from .storage import to_json


def repair_legacy_fees(conn):
    """Run inside the caller's transaction, excluding concurrent projection changes."""
    conn.execute(
        "LOCK TABLE strategy_reports, canonical_fee_reports IN SHARE ROW EXCLUSIVE MODE"
    )
    invalidated = 0
    reassessed = 0
    # Old direct-zero projections must not survive a newly corrected release rule.
    for row in conn.execute("""SELECT f.*,r.gain_raw FROM canonical_fee_reports f JOIN strategy_reports r
        ON r.chain_id=f.chain_id AND r.tx_hash=f.tx_hash AND r.log_index=f.report_log_index
        WHERE f.contract_family='yearn-v2-vault' AND f.candidate_json IS NULL
        AND f.accounting_json IS NOT NULL AND r.gain_raw='0'""").fetchall():
        evidence = json.loads(row["evidence_json"])
        amounts = json.loads(row["accounting_json"])
        if (
            row["api_version"] not in ZERO_GAIN_RELEASES
            and evidence.get("fee_log_index") is None
            and row["event_log_index"] in (None, row["report_log_index"])
            and amounts.get("total_fees_paid_raw") == "0"
        ):
            decision = {
                "policy_version": POLICY_VERSION,
                "status": "unavailable",
                "reason": "zero_gain_requires_fee_evidence",
                "state_scope": None,
                "state_suitability": "unverified",
            }
            invalidated += 1
            conn.execute(
                """UPDATE canonical_fee_reports SET accounting_json=NULL,status='unresolved',reason=?,decision_json=?
                WHERE chain_id=? AND tx_hash=? AND report_log_index=?""",
                (
                    decision["reason"],
                    to_json(decision),
                    row["chain_id"],
                    row["tx_hash"],
                    row["report_log_index"],
                ),
            )
    # Conservatively reassess legacy derived values without refetching evidence.
    for row in conn.execute("""SELECT * FROM canonical_fee_reports WHERE candidate_json IS NULL
        AND accounting_json::json->>'amount_source'='derived-contract'"""):
        reassessed += 1
        amounts = json.loads(row["accounting_json"])
        evidence = json.loads(row["evidence_json"])
        candidate = normalize_candidate(
            evidence.pop(
                "reconstruction",
                {
                    "method": "legacy-unverified",
                    "total_fees_paid_raw": amounts["total_fees_paid_raw"],
                    "interval": None,
                },
            )
        )
        decision = assess_candidate(candidate, evidence)
        accepted = decision["status"] == "accepted"
        conn.execute(
            """UPDATE canonical_fee_reports SET candidate_json=?,decision_json=?,evidence_json=?,
            accounting_json=?,status=?,reason=? WHERE chain_id=? AND tx_hash=? AND report_log_index=?""",
            (
                to_json(candidate),
                to_json(decision),
                to_json(evidence),
                row["accounting_json"] if accepted else None,
                "ok" if accepted else "unresolved",
                decision["reason"],
                row["chain_id"],
                row["tx_hash"],
                row["report_log_index"],
            ),
        )
    return {"invalidatedZeros": invalidated, "reassessedDerived": reassessed}
