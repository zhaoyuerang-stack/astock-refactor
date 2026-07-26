#!/usr/bin/env python3
"""Bulk promote review/L3 candidates —— 硬闸版(LOOP_ENGINEERING / ADR-017 / 根因分析 #1)。

历史教训:本脚本曾用 force=True + run_nine_gate 未开 + run_marginal=False + 无条件批准所有
pending,把 9-Gate 明确 REJECTED 的 4 个 autoresearch 候选强制入册(DSR p=0.81 等)。
修复:**force 恒为 False**(phase1/2 防未来 + 图谱门必须过)、**run_nine_gate=True**(必跑 9-Gate
留证)、**run_marginal=True**(边际残差去冗余)。blanket auto-approve 默认关,须显式
BULK_AUTO_APPROVE=1 才批准 pending(否则只晋级人已批准项)——绝不再悄悄盖章。
"""
import os
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from factory.autoresearch import CandidateRepository, CandidateStatus, ReviewQueue
from services.actions.autoresearch import promote_approved_candidate, review_autoresearch_candidate
from workflow.promote import promote_hypothesis, promote_pool_l3


def run_bulk_promotion():
    print("=== Step 1: Loading Review Queue ===")
    review_queue = ReviewQueue()
    repository = CandidateRepository()
    
    # 1. Approve pending items —— 默认**不**盲批(blanket auto-approve 是人审旁路)。
    #    须显式 BULK_AUTO_APPROVE=1 才批准 pending;否则只晋级人已批准项。
    auto_approve = os.environ.get("BULK_AUTO_APPROVE") == "1"
    pending_items = [
        item for item in review_queue.all()
        if item.get("status") == CandidateStatus.PROMOTED_TO_REVIEW.value
    ]
    if not auto_approve:
        print(f"Found {len(pending_items)} pending candidates —— 跳过盲批(设 BULK_AUTO_APPROVE=1 才批)。")
    else:
        print(f"⚠️ BULK_AUTO_APPROVE=1:批准 {len(pending_items)} 个 pending(仍受下方 9-Gate/边际硬闸)。")
        for item in pending_items:
            fp = item["fingerprint"]
            print(f"Approving pending candidate: {fp}...")
            try:
                review_autoresearch_candidate(
                    fingerprint=fp,
                    action="approve",
                    notes="Approved via bulk promotion script (BULK_AUTO_APPROVE)",
                    repository=repository,
                    review_queue=review_queue
                )
                print("  → Approved successfully.")
            except Exception as e:
                print(f"  → Error approving {fp}: {e}")

    # Reload queue after approvals
    review_queue = ReviewQueue()
    approved_items = [
        item for item in review_queue.all()
        if item.get("status") == CandidateStatus.APPROVED.value or item.get("review_action") == "approve"
    ]
    print(f"\nFound {len(approved_items)} approved candidates in Review Queue to promote.")
    
    # 硬闸 promote:force=False(phase1/2 防未来+图谱门必过)+ run_nine_gate=True(必跑9-Gate留证)
    # + run_marginal=True(边际残差去冗余)。绝不再 force/skip。
    def gated_promote_fn(hyp, version="v1.0", run_marginal=True):
        return promote_hypothesis(hyp, version=version, run_marginal=True,
                                  force=False, run_nine_gate=True)

    # 2. Promote all approved items
    for item in approved_items:
        fp = item["fingerprint"]
        print(f"\nPromoting candidate: {fp}...")
        try:
            res = promote_approved_candidate(
                fingerprint=fp,
                version="v1.0",
                run_marginal=True,
                repository=repository,
                review_queue=review_queue,
                promote_fn=gated_promote_fn
            )
            print(f"  → Promotion result: registered={res.registered}, hypothesis={res.hypothesis_name}, detail={res.detail}")
        except Exception as e:
            print(f"  → Error promoting candidate {fp}: {e}")

    # 3. Promote L3_PASSED pool candidates
    print("\n=== Step 2: Promoting L3 Passed Pool Candidates ===")
    try:
        reports = promote_pool_l3(version="v1.0", force=False, run_marginal=True, run_nine_gate=True)
        print(f"Promoted {len(reports)} candidates from Hypothesis Pool.")
        for r in reports:
            if r:
                print(f"  → Family={r.family}, registered={r.registered}, detail={r.detail}")
    except Exception as e:
        print(f"Error promoting pool candidates: {e}")

    print("\n=== Step 3: Done ===")


if __name__ == "__main__":
    run_bulk_promotion()
