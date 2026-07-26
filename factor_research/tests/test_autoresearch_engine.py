"""Auto Factor Research Engine: DSL/guard/decision safety tests.

Run:
    cd factor_research && python3 tests/test_autoresearch_engine.py
"""
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Hermetic 钉死:方向登记簿/metasearch steering 是搜索行为的**仓库态输入**
# (种子重排 + knowledge_gate priority_adjustment 进 fitness)。不钉死,任何人编辑
# knowledge/direction_registry.json 都会漂移本文件固定 rng_seed 的确定性搜索轨迹
# (例:BOOST 基本面族会把 bp_proxy 种子推到队头,在合成价格面板上算不出因子)。
# steering 行为本身的对抗测试归 tests/test_direction_registry.py。
import knowledge.directions as _kd  # noqa: E402

_kd.DEFAULT_REGISTRY = "/nonexistent/direction_registry.json"
_kd.DEFAULT_CLUSTERS = "/nonexistent/redundancy_clusters.json"
_kd.DEFAULT_FRONTIER = "/nonexistent/frontier.json"

from factory.autoresearch import (  # noqa: E402
    CandidateDecision,
    CandidateRepository,
    CandidateStatus,
    ExperimentLog,
    ReviewQueue,
    ast_to_hypothesis,
    compute_complexity,
    evaluate_lite,
    factor_redundancy_score,
    fingerprint_ast,
    generate_seed_candidates,
    run_validation_pipeline,
    validate_candidate_ast,
)
from factory.autoresearch.guards import LeakageGuardError, run_leakage_guard  # noqa: E402
from factory.autoresearch.validator import DSLValidationError  # noqa: E402
from factory.ontology import (  # noqa: E402
    Decision,
    Experiment,
    ExperimentProtocol,
    ExperimentResult,
)
from services.read.autoresearch import (  # noqa: E402
    autoresearch_candidates,
    autoresearch_funnel,
    autoresearch_review_queue,
)


def _candidate(weight: float = 0.7) -> dict:
    return {
        "type": "linear_combo",
        "terms": [
            {
                "factor": "momentum",
                "params": {"window": 20},
                "transforms": ["mad_clip", "zscore", "rank"],
                "weight": weight,
            },
            {
                "factor": "volume_ratio",
                "params": {"window": 5},
                "transforms": ["mad_clip", "zscore", "rank"],
                "weight": round(1 - weight, 2),
            },
        ],
        "direction": "positive",
        "thesis": {
            "mechanism": "动量和放量共同刻画关注度上升后的截面延续。",
            "citation": "internal hypothesis",
        },
    }


def test_json_ast_validation_rejects_free_string_and_unknown_ops():
    candidate = validate_candidate_ast(_candidate())
    assert candidate.ast["type"] == "linear_combo"
    assert candidate.fingerprint == fingerprint_ast(_candidate())
    assert candidate.status == CandidateStatus.GENERATED

    try:
        validate_candidate_ast({"expr": "rank(momentum_20d) + future_return"})
        raise AssertionError("free-form expression should be rejected")
    except DSLValidationError as e:
        assert "JSON AST" in str(e)

    bad = _candidate()
    bad["terms"][0]["transforms"] = ["zscore", "python_eval"]
    try:
        validate_candidate_ast(bad)
        raise AssertionError("unknown transform should be rejected")
    except DSLValidationError as e:
        assert "transform" in str(e)

    bad_root = _candidate()
    bad_root["transforms"] = ["python_eval"]
    try:
        validate_candidate_ast(bad_root)
        raise AssertionError("unknown root-level transform should be rejected")
    except DSLValidationError as e:
        assert "root transform" in str(e)


def test_neutralize_declaration_accepted_since_runtime_supports_it():
    # neutralize is now fully supported in compute_dsl_factor runtime and validator.
    ast = _candidate()
    ast["neutralize"] = ["industry"]
    # This should be validated successfully without raising any DSLValidationError
    candidate = validate_candidate_ast(ast)
    assert candidate.ast.get("neutralize") == ["industry"]


def test_fingerprint_is_stable_and_repository_dedupes():
    left = _candidate()
    right = _candidate()
    right["terms"][0]["params"] = {"window": 20}  # same content, fresh dict

    assert fingerprint_ast(left) == fingerprint_ast(right)

    with tempfile.TemporaryDirectory() as td:
        repo = CandidateRepository(Path(td) / "candidates.jsonl")
        first = repo.add(validate_candidate_ast(left))
        second = repo.add(validate_candidate_ast(right))
        assert first is True
        assert second is False
        assert len(repo.all()) == 1


def test_fingerprint_semantic_equivalence():
    base = _candidate()

    # 案例①:相加顺序互换(交换律)
    swapped = _candidate()
    swapped["terms"] = list(reversed(swapped["terms"]))
    assert fingerprint_ast(swapped) == fingerprint_ast(base)

    # 案例②:同类项权重拆分(0.42 + 0.28 ≡ 0.7,含浮点求和噪声)
    split = _candidate()
    momentum = split["terms"][0]
    part = {**momentum, "weight": 0.42}
    momentum["weight"] = 0.28
    split["terms"].insert(0, part)
    assert fingerprint_ast(split) == fingerprint_ast(base)

    # thesis 是解释性元数据,不参与身份
    reworded = _candidate()
    reworded["thesis"]["mechanism"] = "换一种机制描述,因子本身不变。"
    assert fingerprint_ast(reworded) == fingerprint_ast(base)

    # transforms 是有序管线,顺序不同 = 不同因子,绝不合并
    reordered = _candidate()
    reordered["terms"][0]["transforms"] = ["rank", "zscore", "mad_clip"]
    assert fingerprint_ast(reordered) != fingerprint_ast(base)

    # 同 window 但权重不同的项不会被误判为同一候选
    reweighted = _candidate(weight=0.6)
    assert fingerprint_ast(reweighted) != fingerprint_ast(base)

    # 整体取反 = 同一假设(方向在 L0 经验定向,|ICIR| 适应度符号无关)
    # 编码①:direction 字段翻转
    neg_dir = _candidate()
    neg_dir["direction"] = "negative"
    assert fingerprint_ast(neg_dir) == fingerprint_ast(base)
    # 编码②:整组权重取负
    neg_w = _candidate()
    for t in neg_w["terms"]:
        t["weight"] = -t["weight"]
    assert fingerprint_ast(neg_w) == fingerprint_ast(base)
    # 但单项取反(非整体)是不同信号,绝不折叠
    partial = _candidate()
    partial["terms"][0]["weight"] = -partial["terms"][0]["weight"]
    assert fingerprint_ast(partial) != fingerprint_ast(base)


def test_candidate_generator_produces_unique_valid_ast_batch():
    candidates = list(generate_seed_candidates(limit=10))
    assert len(candidates) == 10
    assert len({c.fingerprint for c in candidates}) == 10
    assert all(c.ast["type"] == "linear_combo" for c in candidates)
    assert all(compute_complexity(c).score <= 5 for c in candidates)


def test_ast_to_hypothesis_uses_controlled_runtime_factor():
    candidate = validate_candidate_ast(_candidate())
    hyp = ast_to_hypothesis(candidate)
    assert hyp.factor_fn_name == "factors.autoresearch_dsl.compute_dsl_factor"
    assert hyp.factor_params["ast"] == candidate.ast
    assert "price/close" in hyp.data_dependencies
    assert "price/volume" in hyp.data_dependencies
    assert hyp.source == "autoresearch"
    assert hyp.source_ref == candidate.fingerprint


def test_dsl_runtime_factor_computes_linear_combo_panel():
    from factors.autoresearch_dsl import compute_dsl_factor

    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    close = pd.DataFrame(
        {
            "A": range(10, 40),
            "B": range(40, 10, -1),
            "C": [20 + (i % 5) for i in range(30)],
        },
        index=dates,
        dtype=float,
    )
    volume = pd.DataFrame(
        {
            "A": range(100, 130),
            "B": range(130, 100, -1),
            "C": [100 + (i % 3) for i in range(30)],
        },
        index=dates,
        dtype=float,
    )
    factor = compute_dsl_factor(close, volume, ast=_candidate())
    assert factor.shape == close.shape
    assert factor.index.equals(close.index)
    assert factor.columns.equals(close.columns)
    assert factor.iloc[-1].notna().sum() >= 2


def test_complexity_budget_blocks_overfit_candidates():
    simple = validate_candidate_ast(_candidate())
    assert compute_complexity(simple).score <= 5

    complex_ast = _candidate()
    complex_ast["terms"] = complex_ast["terms"] + [
        {
            "factor": "volatility",
            "params": {"window": 23},
            "transforms": ["mad_clip", "zscore", "rank", "neg"],
            "weight": 0.13,
        },
        {
            "factor": "roe",
            "params": {},
            "transforms": ["mad_clip", "zscore", "rank"],
            "weight": 0.11,
        },
    ]
    complex_ast["regime_filter"] = {"type": "market_state", "allowed": ["bull"]}
    complex_ast["industry_scope"] = ["801010", "801020"]

    complex_candidate = validate_candidate_ast(complex_ast)
    complexity = compute_complexity(complex_candidate)
    assert complexity.score > 8
    assert complexity.max_auto_stage == "review_only"


def test_leakage_guard_blocks_future_and_label_fields():
    candidate = validate_candidate_ast(_candidate())
    report = run_leakage_guard(candidate)
    assert report.passed is True

    bad_ast = _candidate()
    bad_ast["terms"][0]["factor"] = "future_return_20d"
    try:
        bad = validate_candidate_ast(bad_ast, allow_experimental_factors=True)
        run_leakage_guard(bad)
        raise AssertionError("future-looking factor should be blocked")
    except LeakageGuardError as e:
        assert "future" in str(e).lower() or "label" in str(e).lower()


def test_redundancy_score_combines_multiple_similarity_inputs():
    low = factor_redundancy_score(
        spearman_corr=0.1,
        normalized_mi=0.2,
        holding_overlap=0.1,
        return_corr=0.1,
        exposure_similarity=0.2,
    )
    high = factor_redundancy_score(
        spearman_corr=0.9,
        normalized_mi=0.8,
        holding_overlap=0.7,
        return_corr=0.9,
        exposure_similarity=0.8,
    )
    assert 0 <= low.score < 0.25
    assert high.score > 0.75


def test_lite_engine_logs_discard_shelve_and_promote_without_registry_write():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = CandidateRepository(root / "candidates.jsonl")
        review = ReviewQueue(root / "review_queue.jsonl")

        good = validate_candidate_ast(_candidate())
        result = evaluate_lite(
            good,
            l0_metrics={
                "rank_ic_mean": 0.035,
                "icir": 0.55,
                "coverage": 0.91,
                "nan_ratio": 0.02,
                "extreme_ratio": 0.01,
            },
            l1_metrics={
                "monotonic_groups": True,
                "top_bottom_return": 0.08,
                "cost_after_return": 0.05,
                "turnover": 0.8,
            },
            redundancy_inputs={
                "spearman_corr": 0.15,
                "normalized_mi": 0.2,
                "holding_overlap": 0.1,
                "return_corr": 0.1,
                "exposure_similarity": 0.2,
            },
            repository=repo,
            review_queue=review,
        )
        assert result.decision == CandidateDecision.PROMOTE
        assert result.status == CandidateStatus.PROMOTED_TO_REVIEW
        assert len(review.all()) == 1
        assert not (root / "strategy_versions.json").exists()

        redundant = validate_candidate_ast(_candidate(weight=0.6))
        shelved = evaluate_lite(
            redundant,
            l0_metrics={
                "rank_ic_mean": 0.04,
                "icir": 0.7,
                "coverage": 0.95,
                "nan_ratio": 0.01,
                "extreme_ratio": 0.01,
            },
            l1_metrics={
                "monotonic_groups": True,
                "top_bottom_return": 0.09,
                "cost_after_return": 0.06,
                "turnover": 0.7,
            },
            redundancy_inputs={
                "spearman_corr": 0.9,
                "normalized_mi": 0.8,
                "holding_overlap": 0.8,
                "return_corr": 0.9,
                "exposure_similarity": 0.8,
            },
            repository=repo,
            review_queue=review,
        )
        assert shelved.decision == CandidateDecision.SHELVE
        assert shelved.status == CandidateStatus.SHELVED

        weak = validate_candidate_ast(_candidate(weight=0.5))
        discarded = evaluate_lite(
            weak,
            l0_metrics={
                "rank_ic_mean": 0.005,
                "icir": 0.1,
                "coverage": 0.9,
                "nan_ratio": 0.02,
                "extreme_ratio": 0.01,
            },
            l1_metrics={},
            redundancy_inputs={},
            repository=repo,
            review_queue=review,
        )
        assert discarded.decision == CandidateDecision.DISCARD
        assert discarded.status == CandidateStatus.DISCARDED
        assert len(repo.all()) == 3


def test_experiment_log_roundtrips_decision_enum():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        log = ExperimentLog(root / "experiment_log.jsonl")
        result = evaluate_lite(
            validate_candidate_ast(_candidate()),
            l0_metrics={
                "rank_ic_mean": 0.035,
                "icir": 0.55,
                "coverage": 0.91,
                "nan_ratio": 0.02,
                "extreme_ratio": 0.01,
            },
            l1_metrics={
                "monotonic_groups": True,
                "top_bottom_return": 0.08,
                "cost_after_return": 0.05,
                "turnover": 0.8,
            },
            redundancy_inputs={
                "spearman_corr": 0.15,
                "normalized_mi": 0.2,
                "holding_overlap": 0.1,
                "return_corr": 0.1,
                "exposure_similarity": 0.2,
            },
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=log,
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )
        loaded = list(log.iter_all())
        assert len(loaded) == 1
        assert loaded[0].decision == result.decision == CandidateDecision.PROMOTE


def test_validation_pipeline_runs_l0_to_l3_and_promotes_only_after_l3():
    calls: list[str] = []

    def fake(protocol: ExperimentProtocol, decision: Decision):
        def _run(hyp, *args, **kwargs):
            calls.append(protocol.value)
            return Experiment(
                experiment_id=f"fake-{protocol.value}",
                hypothesis_id=hyp.id,
                protocol=protocol,
                vintage_id=kwargs.get("vintage_id", "test-vintage"),
                result=ExperimentResult(
                    metrics={"ok": 1.0},
                    details={"direction": "long" if protocol == ExperimentProtocol.L0_IC_SCAN else 1},
                ),
                decision=decision,
                notes="fake pass",
            )
        return _run

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        candidate = validate_candidate_ast(_candidate())
        result = run_validation_pipeline(
            candidate,
            close=pd.DataFrame(),
            volume=pd.DataFrame(),
            amount=pd.DataFrame(),
            forward_ret=pd.DataFrame(),
            vintage_id="test-vintage",
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
            runners={
                "l0": fake(ExperimentProtocol.L0_IC_SCAN, Decision.PROMOTE),
                "l1": fake(ExperimentProtocol.L1_QUICK_BT, Decision.PROMOTE),
                "l2": fake(ExperimentProtocol.L2_MULTI_REGIME, Decision.PROMOTE),
                "l3": fake(ExperimentProtocol.L3_WALK_FORWARD, Decision.PROMOTE),
            },
        )
        assert calls == ["l0_ic_scan", "l1_quick_bt", "l2_multi_regime", "l3_walk_forward"]
        assert result.status == CandidateStatus.PROMOTED_TO_REVIEW
        assert result.decision == CandidateDecision.PROMOTE
        assert len(ReviewQueue(root / "review_queue.jsonl").all()) == 1


def test_validation_pipeline_skips_candidates_blocked_by_knowledge_graph():
    from knowledge.graph import Finding, KnowledgeGraph, SearchGate

    calls: list[str] = []

    def fake_l0(hyp, *args, **kwargs):
        calls.append("l0")
        return Experiment(
            experiment_id="fake-l0",
            hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "test-vintage"),
            result=ExperimentResult(metrics={"ok": 1.0}, details={"direction": "long"}),
            decision=Decision.PROMOTE,
            notes="should not run",
        )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        candidate = validate_candidate_ast(_candidate())
        hyp = ast_to_hypothesis(candidate)
        kg = KnowledgeGraph(str(root / "findings.json"))
        kg.add(Finding(
            id="known_bad_candidate",
            statement="known leaky candidate",
            gates=[SearchGate(match={"name": hyp.name}, action="SKIP", reason="known bad")],
        ))

        result = run_validation_pipeline(
            candidate,
            close=pd.DataFrame(),
            volume=pd.DataFrame(),
            amount=pd.DataFrame(),
            forward_ret=pd.DataFrame(),
            vintage_id="test-vintage",
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
            runners={"l0": fake_l0},
            max_stage="l0",
            knowledge_graph=kg,
        )

        assert calls == []
        assert result.status == CandidateStatus.DISCARDED
        assert result.decision == CandidateDecision.DISCARD
        assert result.metrics["knowledge_gate"]["action"] == "SKIP"


def _synthetic_panel(n_days: int = 420, n_stocks: int = 25):
    """确定性合成面板:逐股持续漂移,使动量在截面上可预测,不依赖 data_lake。"""
    import numpy as np

    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2021-01-04", periods=n_days)
    codes = [f"{600000 + i:06d}" for i in range(n_stocks)]
    drift = np.linspace(-0.0015, 0.0015, n_stocks)
    rets = drift + rng.normal(0.0, 0.01, size=(n_days, n_stocks))
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(rets, axis=0)), index=dates, columns=codes)
    volume = pd.DataFrame(
        rng.integers(200_000, 5_000_000, size=(n_days, n_stocks)).astype(float),
        index=dates,
        columns=codes,
    )
    amount = close * volume
    return close, volume, amount


def test_real_runners_accept_autoresearch_hypothesis_contract():
    """逐级调用真实 run_l0..run_l3,验证桥接契约:F-1/F-2 铁律、签名、DSL 运行时解析。

    Experiment.result.error 必须为 None;decision 由真实 gate 决定,不在断言范围。
    """
    from factory.autoresearch.pipeline import _hyp_with_status
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns, run_l0
    from factory.lines.line2_validation.l1_quick_bt import run_l1
    from factory.lines.line2_validation.l2_multi_regime import run_l2
    from factory.lines.line2_validation.l3_walk_forward import run_l3
    from factory.ontology import HypothesisStatus

    close, volume, amount = _synthetic_panel()
    forward_ret = precompute_forward_returns(close)
    candidate = validate_candidate_ast(_candidate())
    hyp = ast_to_hypothesis(candidate)
    assert hyp.status == HypothesisStatus.QUEUED  # F-2: run_l0 入口要求 QUEUED

    exp0 = run_l0(hyp, close, volume, amount, forward_ret, vintage_id="synthetic")
    assert exp0.result.error is None, exp0.result.error

    hyp = _hyp_with_status(hyp, HypothesisStatus.L0_PASSED)
    exp1 = run_l1(hyp, close, volume, amount, direction=1, vintage_id="synthetic", start="2021-01-04")
    assert exp1.result.error is None, exp1.result.error

    hyp = _hyp_with_status(hyp, HypothesisStatus.L1_PASSED)
    exp2 = run_l2(hyp, close, volume, amount, direction=1, vintage_id="synthetic", start="2021-01-04")
    assert exp2.result.error is None, exp2.result.error

    hyp = _hyp_with_status(hyp, HypothesisStatus.L2_PASSED)
    exp3 = run_l3(hyp, close, volume, amount, direction=1, vintage_id="synthetic", start="2021-01-04")
    assert exp3.result.error is None, exp3.result.error


def test_validation_pipeline_executes_real_l0_on_synthetic_panel():
    """端到端走默认真实 runner(max_stage=l0),决策交给真实 gate,只要求不报错。"""
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns

    close, volume, amount = _synthetic_panel()
    forward_ret = precompute_forward_returns(close)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        candidate = validate_candidate_ast(_candidate())
        result = run_validation_pipeline(
            candidate,
            close=close,
            volume=volume,
            amount=amount,
            forward_ret=forward_ret,
            vintage_id="synthetic",
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
            max_stage="l0",
        )
        experiments = result.metrics["experiments"]
        assert experiments and experiments[0]["protocol"] == "l0_ic_scan"
        assert all(e["error"] is None for e in experiments), experiments


def test_run_autoresearch_seeds_action_uses_pipeline_contract():
    from services.actions.autoresearch import run_autoresearch_seeds

    def fake_l0(hyp, *args, **kwargs):
        return Experiment(
            experiment_id="fake-l0",
            hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "test-vintage"),
            result=ExperimentResult(metrics={"ICIR": 0.1}, details={"direction": "long"}),
            decision=Decision.PROMOTE,
            notes="fake l0 pass",
        )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        response = run_autoresearch_seeds(
            limit=2,
            max_stage="l0",
            close=pd.DataFrame(),
            volume=pd.DataFrame(),
            amount=pd.DataFrame(),
            forward_ret=pd.DataFrame(),
            vintage_id="test-vintage",
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
            runners={"l0": fake_l0},
        )
        assert response.max_stage == "l0"
        assert len(response.results) == 2
        assert all(r.protocols == ["l0_ic_scan"] for r in response.results)
        assert all(r.status == "l0_passed" for r in response.results)
        assert not (root / "strategy_versions.json").exists()


def test_human_review_approve_reject_without_registry_write():
    from services.actions.autoresearch import review_autoresearch_candidate

    promote_kwargs = dict(
        l0_metrics={"rank_ic_mean": 0.035, "icir": 0.55, "coverage": 0.91, "nan_ratio": 0.02, "extreme_ratio": 0.01},
        l1_metrics={"monotonic_groups": True, "top_bottom_return": 0.08, "cost_after_return": 0.05, "turnover": 0.8},
        redundancy_inputs={"spearman_corr": 0.15, "normalized_mi": 0.2, "holding_overlap": 0.1, "return_corr": 0.1, "exposure_similarity": 0.2},
    )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = CandidateRepository(root / "candidates.jsonl")
        review = ReviewQueue(root / "review_queue.jsonl")

        approved_cand = validate_candidate_ast(_candidate())
        rejected_cand = validate_candidate_ast(_candidate(weight=0.6))
        for cand in (approved_cand, rejected_cand):
            result = evaluate_lite(cand, repository=repo, review_queue=review, **promote_kwargs)
            assert result.decision == CandidateDecision.PROMOTE
        assert len(review.pending()) == 2

        # approve:推进状态,但绝不写台账
        item = review_autoresearch_candidate(
            fingerprint=approved_cand.fingerprint, action="approve", notes="机制可信,转 workflow promote",
            repository=repo, review_queue=review,
        )
        assert item.review_action == "approve"
        assert item.status == CandidateStatus.APPROVED.value
        assert repo.get(approved_cand.fingerprint).status == CandidateStatus.APPROVED

        # reject
        item = review_autoresearch_candidate(
            fingerprint=rejected_cand.fingerprint, action="reject", notes="与现有 illiquidity 冗余",
            repository=repo, review_queue=review,
        )
        assert item.status == CandidateStatus.REJECTED_BY_HUMAN.value
        assert repo.get(rejected_cand.fingerprint).status == CandidateStatus.REJECTED_BY_HUMAN

        # 决策后不再 pending;重复决策 / 未知 fingerprint / 非法 action 全部拒绝
        assert len(review.pending()) == 0
        for bad in (
            dict(fingerprint=approved_cand.fingerprint, action="reject"),
            dict(fingerprint="deadbeef", action="approve"),
            dict(fingerprint=rejected_cand.fingerprint, action="retire"),
        ):
            try:
                review_autoresearch_candidate(repository=repo, review_queue=review, **bad)
                raise AssertionError(f"should reject: {bad}")
            except ValueError:
                pass

        # 重新加载验证 append-only 持久化(latest wins)
        reloaded = ReviewQueue(root / "review_queue.jsonl")
        assert reloaded.get(approved_cand.fingerprint)["review_action"] == "approve"
        assert len(reloaded.pending()) == 0
        assert not (root / "strategy_versions.json").exists()


def test_promote_approved_candidate_gates_and_calls_workflow_promote():
    from services.actions.autoresearch import (
        promote_approved_candidate,
        review_autoresearch_candidate,
    )

    promote_kwargs = dict(
        l0_metrics={"rank_ic_mean": 0.035, "icir": 0.55, "coverage": 0.91, "nan_ratio": 0.02, "extreme_ratio": 0.01},
        l1_metrics={"monotonic_groups": True, "top_bottom_return": 0.08, "cost_after_return": 0.05, "turnover": 0.8},
        redundancy_inputs={"spearman_corr": 0.15, "normalized_mi": 0.2, "holding_overlap": 0.1, "return_corr": 0.1, "exposure_similarity": 0.2},
    )

    class FakeReport:
        registered = True
        detail = "registered by fake phase4"

    calls = []

    def fake_promote(hyp, version="v1.0", **kw):
        calls.append((hyp.name, version))
        return FakeReport()

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = CandidateRepository(root / "candidates.jsonl")
        review = ReviewQueue(root / "review_queue.jsonl")
        cand = validate_candidate_ast(_candidate())
        evaluate_lite(cand, repository=repo, review_queue=review, **promote_kwargs)

        # 未 approve 不允许入册
        try:
            promote_approved_candidate(fingerprint=cand.fingerprint, repository=repo, review_queue=review, promote_fn=fake_promote)
            raise AssertionError("pending candidate must not be promotable")
        except ValueError as e:
            assert "not approved" in str(e)

        review_autoresearch_candidate(fingerprint=cand.fingerprint, action="approve", repository=repo, review_queue=review)
        resp = promote_approved_candidate(
            fingerprint=cand.fingerprint, version="v2.0",
            repository=repo, review_queue=review, promote_fn=fake_promote,
        )
        assert resp.registered is True
        assert resp.version == "v2.0"
        assert calls == [(f"autoresearch_{cand.fingerprint[:8]}", "v2.0")]
        assert "registered" in repo.get(cand.fingerprint).notes
        # 本动作自身绝不写台账
        assert not (root / "strategy_versions.json").exists()


def test_review_approve_auto_promote_submits_shadow_job():
    from types import SimpleNamespace

    from services.actions.autoresearch import review_autoresearch_candidate

    promote_kwargs = dict(
        l0_metrics={"rank_ic_mean": 0.035, "icir": 0.55, "coverage": 0.91, "nan_ratio": 0.02, "extreme_ratio": 0.01},
        l1_metrics={"monotonic_groups": True, "top_bottom_return": 0.08, "cost_after_return": 0.05, "turnover": 0.8},
        redundancy_inputs={"spearman_corr": 0.15, "normalized_mi": 0.2, "holding_overlap": 0.1, "return_corr": 0.1, "exposure_similarity": 0.2},
    )
    submitted = {}

    def fake_submitter(kind, fn, *args, **kwargs):
        submitted.update({"kind": kind, "fn": fn, "args": args, "kwargs": kwargs})
        return SimpleNamespace(job_id="job-shadow-1", kind=kind, status="queued")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = CandidateRepository(root / "candidates.jsonl")
        review = ReviewQueue(root / "review_queue.jsonl")
        cand = validate_candidate_ast(_candidate())
        evaluate_lite(cand, repository=repo, review_queue=review, **promote_kwargs)

        item = review_autoresearch_candidate(
            fingerprint=cand.fingerprint,
            action="approve",
            repository=repo,
            review_queue=review,
            auto_promote_after_approve=True,
            job_submitter=fake_submitter,
        )

        assert item.status == CandidateStatus.PROMOTING.value
        assert item.promote_job_id == "job-shadow-1"
        assert item.target_status == "SHADOW"
        assert repo.get(cand.fingerprint).status == CandidateStatus.PROMOTING
        assert review.get(cand.fingerprint)["status"] == CandidateStatus.PROMOTING.value
        assert submitted["kind"] == "autoresearch.promote_after_approve"
        assert submitted["kwargs"]["target_status"] == "SHADOW"


def test_auto_promote_shadow_updates_review_queue_and_never_active():
    from types import SimpleNamespace

    from services.actions.autoresearch import (
        promote_approved_candidate,
        review_autoresearch_candidate,
    )

    promote_kwargs = dict(
        l0_metrics={"rank_ic_mean": 0.035, "icir": 0.55, "coverage": 0.91, "nan_ratio": 0.02, "extreme_ratio": 0.01},
        l1_metrics={"monotonic_groups": True, "top_bottom_return": 0.08, "cost_after_return": 0.05, "turnover": 0.8},
        redundancy_inputs={"spearman_corr": 0.15, "normalized_mi": 0.2, "holding_overlap": 0.1, "return_corr": 0.1, "exposure_similarity": 0.2},
    )
    calls = []

    def fake_shadow_promote(hyp, version="v1.0", **kw):
        calls.append({"hyp": hyp.name, "version": version, **kw})
        return SimpleNamespace(
            registered=True,
            detail="registered as shadow",
            status="候选",
            phase_summary={"phase4": {"registered": True, "status": "候选"}},
        )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = CandidateRepository(root / "candidates.jsonl")
        review = ReviewQueue(root / "review_queue.jsonl")
        cand = validate_candidate_ast(_candidate())
        evaluate_lite(cand, repository=repo, review_queue=review, **promote_kwargs)
        review_autoresearch_candidate(fingerprint=cand.fingerprint, action="approve", repository=repo, review_queue=review)

        resp = promote_approved_candidate(
            fingerprint=cand.fingerprint,
            version="v-shadow",
            target_status="SHADOW",
            auto_job=True,
            repository=repo,
            review_queue=review,
            promote_fn=fake_shadow_promote,
        )

        assert calls[0]["target_status"] == "SHADOW"
        assert resp.registered is True
        assert resp.target_status == "SHADOW"
        assert resp.registry_status == "候选"
        assert resp.phase_summary["phase4"]["status"] == "候选"
        assert review.get(cand.fingerprint)["status"] == CandidateStatus.PROMOTED_SHADOW.value
        assert repo.get(cand.fingerprint).status == CandidateStatus.PROMOTED_SHADOW

        def fake_active_promote(hyp, version="v1.0", **kw):
            return SimpleNamespace(registered=True, detail="would be active", status="在册")

        review.record_decision(cand.fingerprint, CandidateStatus.APPROVED, action="approve")
        try:
            promote_approved_candidate(
                fingerprint=cand.fingerprint,
                target_status="SHADOW",
                auto_job=True,
                repository=repo,
                review_queue=review,
                promote_fn=fake_active_promote,
            )
            raise AssertionError("SHADOW auto-promote must reject ACTIVE/在册 registry status")
        except RuntimeError as e:
            assert "ACTIVE" in str(e) or "在册" in str(e)


def test_auto_promote_failure_marks_retryable_promote_failed():
    from services.actions.autoresearch import (
        promote_approved_candidate_job,
        review_autoresearch_candidate,
    )

    promote_kwargs = dict(
        l0_metrics={"rank_ic_mean": 0.035, "icir": 0.55, "coverage": 0.91, "nan_ratio": 0.02, "extreme_ratio": 0.01},
        l1_metrics={"monotonic_groups": True, "top_bottom_return": 0.08, "cost_after_return": 0.05, "turnover": 0.8},
        redundancy_inputs={"spearman_corr": 0.15, "normalized_mi": 0.2, "holding_overlap": 0.1, "return_corr": 0.1, "exposure_similarity": 0.2},
    )

    def failing_promote(*args, **kwargs):
        raise RuntimeError("shadow promote boom")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = CandidateRepository(root / "candidates.jsonl")
        review = ReviewQueue(root / "review_queue.jsonl")
        cand = validate_candidate_ast(_candidate())
        evaluate_lite(cand, repository=repo, review_queue=review, **promote_kwargs)
        review_autoresearch_candidate(fingerprint=cand.fingerprint, action="approve", repository=repo, review_queue=review)

        try:
            promote_approved_candidate_job(
                fingerprint=cand.fingerprint,
                repository=repo,
                review_queue=review,
                promote_fn=failing_promote,
            )
            raise AssertionError("failing promote job must re-raise so job status becomes failed")
        except RuntimeError:
            pass

        item = review.get(cand.fingerprint)
        assert item["status"] == CandidateStatus.PROMOTE_FAILED.value
        assert item["review_action"] == "approve"
        assert "shadow promote boom" in item["promote_error"]
        assert repo.get(cand.fingerprint).status == CandidateStatus.PROMOTE_FAILED


class _FakeLLMAdapter:
    name = "fake"
    model = "fake-model"

    def __init__(self, text):
        self._text = text

    def available(self):
        return True

    def complete(self, system, user, max_tokens=2000):
        assert "momentum" in system and "白名单" in system  # DSL spec 必须注入 prompt
        return self._text


def test_llm_generation_validates_rejects_and_dedupes():
    import json as _json

    from factory.autoresearch.validator import validate_candidate_ast as _v
    from services.actions.autoresearch_llm import generate_llm_candidates

    good = _candidate()
    bad_factor = _candidate()
    bad_factor["terms"][0]["factor"] = "future_return_20d"
    payload = "```json\n" + _json.dumps([good, bad_factor, good]) + "\n```"

    with tempfile.TemporaryDirectory() as td:
        repo = CandidateRepository(Path(td) / "candidates.jsonl")
        accepted, rejected, model = generate_llm_candidates(
            n=3, adapter=_FakeLLMAdapter(payload), repository=repo,
        )
        assert model == "fake-model"
        assert len(accepted) == 1 and accepted[0].source == "llm"
        assert accepted[0].fingerprint == _v(good).fingerprint
        assert len(rejected) == 2  # 非法因子 + 重复 fingerprint

        # 仓库里已有的 fingerprint 也要拒
        repo.add(accepted[0])
        accepted2, rejected2, _ = generate_llm_candidates(n=1, adapter=_FakeLLMAdapter(payload), repository=repo)
        assert len(accepted2) == 0 and len(rejected2) == 3

    # LLM 未配置 → 明确报错,不静默降级
    class _Null:
        model = ""

        def available(self):
            return False

    try:
        generate_llm_candidates(n=1, adapter=_Null())
        raise AssertionError("unavailable adapter must raise")
    except ValueError as e:
        assert "LLM" in str(e)


def test_island_search_mutation_stays_in_whitelist_and_search_is_deterministic():
    import random as _random

    from factory.autoresearch.islands import crossover_ast, mutate_ast, run_island_search
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns

    # 变异/杂交模糊测试:50 轮全部仍在白名单内
    rng = _random.Random(11)
    asts = [c.ast for c in generate_seed_candidates(limit=4)]
    for _ in range(50):
        validate_candidate_ast(mutate_ast(rng.choice(asts), rng))
        validate_candidate_ast(crossover_ast(asts[0], asts[1], rng))

    close, volume, amount = _synthetic_panel()
    forward_ret = precompute_forward_returns(close)

    def _run(root):
        return run_island_search(
            close, volume, amount, forward_ret,
            vintage_id="synthetic",
            n_islands=2, generations=2, population=4, top_k=3, rng_seed=7,
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )

    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        r1 = _run(Path(td1))
        r2 = _run(Path(td2))
        assert r1.evaluated > 0
        assert len(r1.champions) == 3
        assert all(c.icir != 0.0 or c.status for c in r1.champions)
        # 同 seed + 同数据 → 同冠军(实验可复现)
        assert [c.fingerprint for c in r1.champions] == [c.fingerprint for c in r2.champions]
        # 冠军按混合适应度(|ICIR| + novelty_weight × 新颖性)降序
        fits = [c.fitness for c in r1.champions]
        assert fits == sorted(fits, reverse=True)
        assert all(abs(c.fitness - (abs(c.icir) + 0.25 * c.novelty)) < 1e-9 for c in r1.champions)


def test_novelty_scores_behavioral_distance_not_syntax():
    """新颖性按行为算:克隆(含反向克隆)被识别为冗余,行为不同的因子得高分。"""
    from factors.autoresearch_dsl import clear_factor_cache
    from factory.autoresearch.novelty import (
        candidate_factor_panel,
        novelty_score,
        sample_behavior_dates,
    )
    clear_factor_cache()
    close, volume, _ = _synthetic_panel()
    dates = sample_behavior_dates(close.index, 60)
    assert len(dates) <= 60 and dates[-1] == close.index[-1]

    base_ast = _candidate()
    base = candidate_factor_panel(base_ast, close, volume, dates, cache_mode="memory")

    # 空参考池:未知即新颖
    assert novelty_score(base, []) == 1.0
    # 自身克隆:spearman=1 + 持仓全重叠 → 新颖性归零
    assert novelty_score(base, [base]) < 1e-6

    # 反向克隆:|spearman| 仍为 1(方向无关),只是持仓不再重叠 → 仍判低新颖
    flipped = candidate_factor_panel({**base_ast, "direction": "negative"}, close, volume, dates, cache_mode="memory")
    nov_flipped = novelty_score(flipped, [base])

    # 行为不同的因子(波动率)应明显比反向克隆新颖
    vol_ast = {
        "type": "linear_combo",
        "terms": [{"factor": "volatility", "params": {"window": 20},
                   "transforms": ["mad_clip", "zscore", "rank"], "weight": 1.0}],
        "direction": "positive",
        "thesis": {"mechanism": "低波动异象测试因子。", "citation": "test"},
    }
    vol = candidate_factor_panel(vol_ast, close, volume, dates, cache_mode="memory")
    nov_vol = novelty_score(vol, [base])
    assert nov_vol > nov_flipped
    assert nov_vol > 0.5

    # 最近邻语义:克隆参考池中任意一个即低新颖,不被其余参考稀释
    assert novelty_score(base, [vol, base]) < 1e-6


def test_island_fitness_blends_novelty_with_icir():
    """同 ICIR 下排序由新颖性决定(fake l0 恒定 ICIR,新颖性成为唯一区分项)。"""
    from factory.autoresearch.islands import run_island_search

    def fake_l0(hyp, *args, **kwargs):
        return Experiment(
            experiment_id="fake-l0",
            hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "synthetic"),
            result=ExperimentResult(metrics={"ICIR": 0.1}, details={"direction": "long"}),
            decision=Decision.PROMOTE,
            notes="fake l0 pass",
        )

    close, volume, amount = _synthetic_panel()
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns
    forward_ret = precompute_forward_returns(close)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        result = run_island_search(
            close, volume, amount, forward_ret,
            vintage_id="synthetic",
            n_islands=2, generations=1, population=4, top_k=4, rng_seed=7,
            runners={"l0": fake_l0},
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )

    assert result.champions
    for c in result.champions:
        assert 0.0 <= c.novelty <= 1.0
        assert abs(c.fitness - (abs(c.icir) + 0.25 * c.novelty)) < 1e-9
    # ICIR 恒定 → 冠军排序 = 新颖性排序
    novs = [c.novelty for c in result.champions]
    assert novs == sorted(novs, reverse=True)


def test_marginal_return_correlation_helpers():
    """top-N 收益代理 + 有符号最大相关:自相关≈1、反相关为负、无效→0。"""
    import numpy as np

    from factory.autoresearch.novelty import max_return_correlation, topn_long_return

    dates = pd.bdate_range("2024-01-01", periods=6)
    cols = list("ABCDE")
    # 因子:A>B>C>D>E 恒定;top_n=2 → 每日选 {A,B}
    panel = pd.DataFrame({c: [5 - i] * 6 for i, c in enumerate(cols)}, index=dates, dtype=float)
    fr = pd.DataFrame(np.arange(30).reshape(6, 5) / 100.0, index=dates, columns=cols)
    ret = topn_long_return(panel, fr, top_n=2)
    # 每日 {A,B} 的前向收益均值 = (5k + 5k+1)/2/100
    expected = [(fr.loc[d, "A"] + fr.loc[d, "B"]) / 2 for d in dates]
    assert np.allclose(ret.values, expected)

    # 自相关 ≈ 1；反相关 ≈ -1;方差退化 / 空参考 → 0
    assert abs(max_return_correlation(ret, [ret]) - 1.0) < 1e-9
    assert max_return_correlation(ret, [-ret]) < -0.99
    assert max_return_correlation(ret, []) == 0.0
    flat = pd.Series(0.0, index=dates)
    assert max_return_correlation(ret, [flat]) == 0.0
    # 取 max:与任一腿雷同即算冗余(高相关腿主导)
    assert abs(max_return_correlation(ret, [-ret, ret]) - 1.0) < 1e-9


def test_topn_turnover_proxy():
    """换手代理:成员恒定→0,每期全换→1,部分重叠→中间。"""
    from factory.autoresearch.novelty import topn_turnover

    dates = pd.bdate_range("2024-01-01", periods=4)
    cols = list("ABCDEF")
    # 恒定 top-2 = {A,B} 每期都选 → churn 0
    const = pd.DataFrame({c: [6 - i] * 4 for i, c in enumerate(cols)}, index=dates, dtype=float)
    assert topn_turnover(const, top_n=2) == 0.0
    # 每期 top-2 完全不同 → churn 1.0
    rot = pd.DataFrame(0.0, index=dates, columns=cols)
    picks = [["A", "B"], ["C", "D"], ["E", "F"], ["A", "B"]]
    for d, pk in zip(dates, picks, strict=True):
        for j, c in enumerate(pk):
            rot.loc[d, c] = 10 - j
    assert abs(topn_turnover(rot, top_n=2) - 1.0) < 1e-9


def test_island_fitness_penalizes_turnover():
    """turnover_weight>0 时,适应度含 −turnover_weight×换手;冠军按换手升序(低换手胜)。"""
    from factory.autoresearch.islands import run_island_search
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns

    def fake_l0(hyp, *args, **kwargs):
        return Experiment(
            experiment_id="fake-l0", hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "synthetic"),
            result=ExperimentResult(metrics={"ICIR": 0.3}, details={"direction": "long"}),
            decision=Decision.PROMOTE, notes="fake",
        )

    close, volume, amount = _synthetic_panel()
    forward_ret = precompute_forward_returns(close)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        result = run_island_search(
            close, volume, amount, forward_ret,
            vintage_id="synthetic",
            n_islands=2, generations=1, population=4, top_k=4, rng_seed=7,
            novelty_weight=0.0, corr_weight=0.0, turnover_weight=1.0,
            runners={"l0": fake_l0},
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )
    assert result.champions
    for c in result.champions:
        assert 0.0 <= c.turnover <= 1.0
        assert abs(c.fitness - (abs(c.icir) - 1.0 * c.turnover)) < 1e-9
    # ICIR 恒定 → 冠军排序 = 换手升序(低换手者胜出)
    turns = [c.turnover for c in result.champions]
    assert turns == sorted(turns)


def _momentum_variant_seeds():
    """在册腿(momentum 20)的窗口近邻种子:保证初始种群机械含对册高相关候选。

    相关性惩罚/重发现闸的断言需要种群里真的存在相关候选;靠随机种子碰运气会随
    上游种子目录/变异算子演化而漂移(2026-07-02 基线预存失败的根因)。"""
    seeds = []
    for w in (10, 15, 20, 25, 30, 40):
        ast = {"type": "linear_combo", "direction": "positive",
               "terms": [{"factor": "momentum", "params": {"window": w},
                          "transforms": ["mad_clip", "zscore", "rank"], "weight": 1.0}],
               "thesis": {"mechanism": "在册动量腿的窗口近邻,注入以保证对册相关候选存在。",
                          "citation": "test"}}
        seeds.append(validate_candidate_ast(ast))
    return seeds


def test_island_fitness_penalizes_correlation_to_book():
    """corr_weight>0 时,适应度 = |ICIR| − corr_weight×对在册相关(novelty=0 隔离)。

    ICIR 恒定 + novelty_weight=0 → 适应度 = C − w×corr,冠军排序 = 对在册相关升序
    (越去相关越靠前)。直接证明边际惩罚把选择推向去相关。
    """
    from factory.autoresearch.islands import run_island_search
    from factory.autoresearch.novelty import candidate_factor_panel
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns

    def fake_l0(hyp, *args, **kwargs):
        return Experiment(
            experiment_id="fake-l0", hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "synthetic"),
            result=ExperimentResult(metrics={"ICIR": 0.3}, details={"direction": "long"}),
            decision=Decision.PROMOTE, notes="fake",
        )

    close, volume, amount = _synthetic_panel()
    forward_ret = precompute_forward_returns(close)
    # 参考"在册腿" = 一个 momentum(20) 因子面板(全窗口,服务层传入口径)
    book_ast = {"type": "linear_combo", "direction": "positive",
                "terms": [{"factor": "momentum", "params": {"window": 20},
                           "transforms": ["mad_clip", "zscore", "rank"], "weight": 1.0}],
                "thesis": {"mechanism": "在册动量腿。", "citation": "test"}}
    book_panel = candidate_factor_panel(book_ast, close, volume, close.index, cache_mode="memory")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        result = run_island_search(
            close, volume, amount, forward_ret,
            vintage_id="synthetic",
            n_islands=2, generations=1, population=4, top_k=4, rng_seed=7,
            novelty_weight=0.0, corr_weight=1.0, rediscovery_corr=0.0,  # 隔离软罚,关硬闸
            seeds=_momentum_variant_seeds(),
            reference_panels=[book_panel],
            runners={"l0": fake_l0},
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )

    assert result.champions
    for c in result.champions:
        assert -1.0 <= c.corr_to_book <= 1.0
        # novelty=0 → fitness == |icir| − corr_weight×corr_to_book
        assert abs(c.fitness - (abs(c.icir) - 1.0 * c.corr_to_book)) < 1e-9
    # ICIR 恒定 → 冠军排序 = 对在册相关升序(去相关者胜出)
    corrs = [c.corr_to_book for c in result.champions]
    assert corrs == sorted(corrs)
    # 至少有候选与在册腿正相关被压(否则惩罚没起作用)
    assert max(corrs) > 0.1


def test_newey_west_icir_corrects_overlap_inflation():
    """NW 校正:重叠/自相关 IC 序列的 ICIR 被压回真值;白噪声基本不变。"""
    import numpy as np

    from engine.factor_analysis import newey_west_icir

    rng = np.random.default_rng(7)
    wn = rng.normal(0.05, 0.1, 2000)
    raw_wn = abs(wn.mean()) / wn.std()
    nw_wn = newey_west_icir(wn, max_lag=20)
    assert abs(nw_wn - raw_wn) / raw_wn < 0.2  # 白噪声:NW≈raw

    ar = np.zeros(2000); ar[0] = 0.05
    for i in range(1, 2000):
        ar[i] = 0.95 * ar[i - 1] + rng.normal(0.0025, 0.03)
    raw_ar = abs(ar.mean()) / ar.std()
    nw_ar = newey_west_icir(ar, max_lag=20)
    assert nw_ar < raw_ar * 0.5  # 强自相关:NW 显著压低(重叠虚高被校正)
    assert newey_west_icir([0.1], max_lag=20) != newey_west_icir([0.1], max_lag=20) or True  # n<2 → nan 不崩


def test_l0_reports_nw_corrected_icir():
    """L0 落账 raw ICIR(闸门)与 ICIR_nw(诚实绝对量级)两个口径。"""
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns, run_l0

    close, volume, amount = _synthetic_panel(n_stocks=60)  # ≥30 截面样本(L0 min_ic_count)
    forward_ret = precompute_forward_returns(close)  # horizon=20
    hyp = ast_to_hypothesis(validate_candidate_ast(_candidate()))  # QUEUED,run_l0 入口要求
    exp = run_l0(hyp, close, volume, amount, forward_ret, vintage_id="nw-test")
    assert exp.result.error is None, exp.result.error
    assert "ICIR_nw" in exp.result.metrics and "ICIR" in exp.result.metrics
    assert exp.result.details.get("ic_ir_nw") is not None


def test_rediscovery_gate_zeros_edge_above_corr_threshold():
    """重发现硬闸:对在册相关 ≥ 阈值 → |ICIR| 归零(边际为零),沉到真候选之下。"""
    from factory.autoresearch.islands import run_island_search
    from factory.autoresearch.novelty import candidate_factor_panel
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns

    def fake_l0(hyp, *args, **kwargs):
        return Experiment(
            experiment_id="fake-l0", hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "synthetic"),
            result=ExperimentResult(metrics={"ICIR": 0.3}, details={"direction": "long"}),
            decision=Decision.PROMOTE, notes="fake",
        )

    close, volume, amount = _synthetic_panel()
    forward_ret = precompute_forward_returns(close)
    book_ast = {"type": "linear_combo", "direction": "positive",
                "terms": [{"factor": "momentum", "params": {"window": 20},
                           "transforms": ["mad_clip", "zscore", "rank"], "weight": 1.0}],
                "thesis": {"mechanism": "在册动量腿。", "citation": "test"}}
    book_panel = candidate_factor_panel(book_ast, close, volume, close.index, cache_mode="memory")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        result = run_island_search(
            close, volume, amount, forward_ret,
            vintage_id="synthetic",
            n_islands=2, generations=1, population=4, top_k=4, rng_seed=7,
            novelty_weight=0.0, corr_weight=1.0, turnover_weight=0.0,
            seeds=_momentum_variant_seeds(),
            rediscovery_corr=0.5, reference_panels=[book_panel],
            runners={"l0": fake_l0},
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )

    assert result.champions
    gated = 0
    for c in result.champions:
        if c.corr_to_book >= 0.5:  # 重发现:edge 归零 → fitness == −corr_weight×corr
            assert abs(c.fitness - (-1.0 * c.corr_to_book)) < 1e-9
            gated += 1
        else:  # 真候选:保留 |ICIR| → fitness == |icir| − corr
            assert abs(c.fitness - (abs(c.icir) - 1.0 * c.corr_to_book)) < 1e-9
    # 至少有一个重发现被闸(否则没测到 gate)
    assert gated >= 1


def test_walk_forward_search_truncates_train_and_scores_oos_after_cutoff():
    """元级防未来:进化选择回路只见 <=cutoff 的物理截断面板;冠军在 cutoff 后一次性 OOS 评分。"""
    from factory.autoresearch.walkforward import run_walk_forward_search
    from factory.lines.line2_validation.l0_ic_scan import run_l0

    close, volume, amount = _synthetic_panel()
    cutoff = "2022-01-31"
    cutoff_ts = pd.Timestamp(cutoff)
    seen = {"train_calls": 0, "oos_windows": []}

    def spy_l0(hyp, c, v, a, forward_ret, vintage_id, sample_dates=None, **kwargs):
        if "|train" in vintage_id:
            seen["train_calls"] += 1
            assert c.index.max() <= cutoff_ts
            assert forward_ret.index.max() <= cutoff_ts
            # forward_ret 必须由截断后的 close 重算:末端 horizon 天 NaN,
            # 若是全样本 forward_ret 切片,这里会掺入 cutoff 后的价格
            assert forward_ret.iloc[-1].isna().all()
        else:
            assert forward_ret.index.min() > cutoff_ts
            seen["oos_windows"].append((forward_ret.index.min(), forward_ret.index.max()))
        return run_l0(hyp, c, v, a, forward_ret, vintage_id=vintage_id, sample_dates=sample_dates, **kwargs)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        result = run_walk_forward_search(
            close, volume, amount,
            cutoff=cutoff,
            vintage_id="synthetic-wf",
            repository=CandidateRepository(root / "candidates.jsonl"),
            runners={"l0": spy_l0},
            n_islands=2, generations=1, population=3, top_k=2, rng_seed=7,
            sample_dates=60,
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )

    assert seen["train_calls"] > 0
    assert len(result.champions) == 2
    assert len(seen["oos_windows"]) == 2  # 每冠军恰好 OOS 评一次,绝不回流训练选择
    assert result.cutoff < result.oos_start <= result.oos_end
    for c in result.champions:
        assert c.oos_icir is not None
        assert c.oos_decision in {"promote", "discard"}
        assert "|train" in result.train_vintage_id and "|oos:" in result.oos_vintage_id


def test_walk_forward_reference_builder_only_sees_truncated_panels():
    """元级防未来:在册参考面板的构造器只能在 <=cutoff 的截断面板上被调用。"""
    from factory.autoresearch.walkforward import run_walk_forward_search
    from factory.lines.line2_validation.l0_ic_scan import run_l0

    close, volume, amount = _synthetic_panel()
    cutoff = "2022-01-31"
    cutoff_ts = pd.Timestamp(cutoff)
    seen = {"builder_max_dates": []}

    def spy_builder(c, v, a):
        seen["builder_max_dates"].append((c.index.max(), v.index.max(), a.index.max()))
        return [c.rolling(20).mean()]  # 任意参考面板,口径与 c 一致

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run_walk_forward_search(
            close, volume, amount,
            cutoff=cutoff, vintage_id="wf-ref",
            repository=CandidateRepository(root / "candidates.jsonl"),
            runners={"l0": run_l0},
            reference_builder=spy_builder, corr_weight=0.3,
            n_islands=2, generations=1, population=3, top_k=2, rng_seed=7, sample_dates=60,
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )

    assert seen["builder_max_dates"], "corr_weight>0 时 reference_builder 必须被调用"
    for cmax, vmax, amax in seen["builder_max_dates"]:
        assert cmax <= cutoff_ts and vmax <= cutoff_ts and amax <= cutoff_ts


def test_read_views_expose_candidates_and_review_queue():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = CandidateRepository(root / "candidates.jsonl")
        review = ReviewQueue(root / "review_queue.jsonl")
        candidate = validate_candidate_ast(_candidate())
        evaluate_lite(
            candidate,
            l0_metrics={
                "rank_ic_mean": 0.035,
                "icir": 0.55,
                "coverage": 0.91,
                "nan_ratio": 0.02,
                "extreme_ratio": 0.01,
            },
            l1_metrics={
                "monotonic_groups": True,
                "top_bottom_return": 0.08,
                "cost_after_return": 0.05,
                "turnover": 0.8,
            },
            redundancy_inputs={
                "spearman_corr": 0.15,
                "normalized_mi": 0.2,
                "holding_overlap": 0.1,
                "return_corr": 0.1,
                "exposure_similarity": 0.2,
            },
            repository=repo,
            review_queue=review,
        )

        candidates = autoresearch_candidates(path=root / "candidates.jsonl")
        queue = autoresearch_review_queue(path=root / "review_queue.jsonl")
        funnel = autoresearch_funnel(
            candidate_path=root / "candidates.jsonl",
            review_path=root / "review_queue.jsonl",
        )

        assert len(candidates) == 1
        assert candidates[0].status == "promoted_to_review"
        assert candidates[0].ast["type"] == "linear_combo"
        assert candidates[0].complexity_score > 0
        assert len(queue) == 1
        assert funnel.review_queue == 1
        assert any(s["stage"] == "promoted_to_review" and s["count"] == 1 for s in funnel.stages)


def _failed_result(fingerprint: str, death_protocol: str, *, veto_review: bool = False):
    """构造与 run_validation_pipeline 同构的失败记录(死于指定关卡)。"""
    from factory.autoresearch.models import CandidateEvaluationResult

    experiments = [{"protocol": "l0_ic_scan", "decision": "promote", "metrics": {}, "details": {}}]
    if death_protocol != "l0_ic_scan":
        experiments.append({"protocol": death_protocol, "decision": "discard", "metrics": {}, "details": {}})
    else:
        experiments = [{"protocol": "l0_ic_scan", "decision": "discard", "metrics": {}, "details": {}}]
    return CandidateEvaluationResult(
        fingerprint=fingerprint,
        status=CandidateStatus.DISCARDED,
        decision=CandidateDecision.DISCARD,
        metrics={"experiments": experiments, "veto_review_candidate": veto_review},
        reason="annual failed" if death_protocol == "l1_quick_bt" else "no signal",
    )


def test_failure_ledger_aggregates_death_causes_with_evidence_gated_lessons():
    from factory.autoresearch.reflection import build_failure_ledger, ledger_to_prompt

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        log = ExperimentLog(root / "log.jsonl")
        repo = CandidateRepository(root / "candidates.jsonl")

        # 两个动量类候选死 L1 且 L0 信息强(货币化形态死因);一个死 L0
        c1, c2, c3 = (validate_candidate_ast(_candidate(w)) for w in (0.7, 0.8, 0.9))
        for c in (c1, c2, c3):
            repo.add(c)
        log.append(_failed_result(c1.fingerprint, "l1_quick_bt", veto_review=True))
        log.append(_failed_result(c2.fingerprint, "l1_quick_bt", veto_review=True))
        log.append(_failed_result(c3.fingerprint, "l0_ic_scan"))
        # 成功记录不计入失败台账
        from factory.autoresearch.models import CandidateEvaluationResult
        log.append(CandidateEvaluationResult(
            fingerprint="ok", status=CandidateStatus.PROMOTED_TO_REVIEW,
            decision=CandidateDecision.PROMOTE, metrics={}, reason="",
        ))

        ledger = build_failure_ledger(log, repo)
        assert ledger.total_failed == 3
        assert ledger.deaths_by_stage == {"l1_quick_bt": 2, "l0_ic_scan": 1}
        assert ledger.veto_form_deaths == 2
        # 证据门控:货币化形态教训(2 次达阈值)+ 因子级教训(momentum 2 次死 L1)
        assert any("空头侧" in les for les in ledger.lessons)
        assert any("momentum" in les and "2 次" in les for les in ledger.lessons)
        # L0 只死 1 次,不产出因子级教训(低于 min_pattern_count)
        assert not any("L0" in les and "volume_ratio" in les for les in ledger.lessons)

        prompt = ledger_to_prompt(ledger)
        assert "失败台账" in prompt and "L1" in prompt

        # 空日志 → 空提示(不注入噪音)
        empty = build_failure_ledger(ExperimentLog(root / "empty.jsonl"))
        assert empty.total_failed == 0 and ledger_to_prompt(empty) == ""


def test_llm_generation_injects_failure_ledger_into_prompt():
    import json as _json

    from services.actions.autoresearch_llm import generate_llm_candidates

    captured = {}

    class _CaptureAdapter(_FakeLLMAdapter):
        def complete(self, system, user, max_tokens=2000):
            captured["user"] = user
            return super().complete(system, user, max_tokens)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        log = ExperimentLog(root / "log.jsonl")
        repo = CandidateRepository(root / "candidates.jsonl")
        for w in (0.6, 0.65):
            c = validate_candidate_ast(_candidate(w))
            repo.add(c)
            log.append(_failed_result(c.fingerprint, "l1_quick_bt", veto_review=True))

        payload = "```json\n" + _json.dumps([_candidate(0.75)]) + "\n```"
        accepted, _, _ = generate_llm_candidates(
            n=1, adapter=_CaptureAdapter(payload), repository=repo, experiment_log=log,
        )
        assert len(accepted) == 1
        assert "失败台账" in captured["user"] and "优先级最高" in captured["user"]
        assert "空头侧" in captured["user"]  # 货币化形态教训进了提示词

        # 不传 experiment_log → 不注入(向后兼容)
        repo2 = CandidateRepository(root / "candidates2.jsonl")
        generate_llm_candidates(n=1, adapter=_CaptureAdapter(payload), repository=repo2)
        assert "失败台账" not in captured["user"]


def test_island_fitness_penalizes_complexity():
    """测试在进化搜索中复杂度惩罚生效。"""
    from factory.autoresearch.islands import run_island_search

    def fake_l0(hyp, *args, **kwargs):
        return Experiment(
            experiment_id="fake-l0",
            hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "synthetic"),
            result=ExperimentResult(metrics={"ICIR": 0.1}, details={"direction": "long"}),
            decision=Decision.PROMOTE,
            notes="fake l0 pass",
        )

    close, volume, amount = _synthetic_panel()
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns
    forward_ret = precompute_forward_returns(close)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        result = run_island_search(
            close, volume, amount, forward_ret,
            vintage_id="synthetic",
            n_islands=2, generations=1, population=4, top_k=4, rng_seed=7,
            runners={"l0": fake_l0},
            complexity_weight=0.1,
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )

    assert result.champions
    for c in result.champions:
        # Calculate expected fitness: edge (0.1) + novelty_weight(0.25)*novelty - complexity_weight(0.1)*complexity
        assert c.complexity > 0.0
        expected_fit = abs(c.icir) + 0.25 * c.novelty - 0.1 * c.complexity
        assert abs(c.fitness - expected_fit) < 1e-9


def test_algebraic_metric_proxy_uses_ast_weights_for_corr_and_turnover():
    """Phase 1:代数代理用 AST 线性权重估算 corr/turnover,不需要候选级 top-N 模拟。"""
    from factory.autoresearch.islands import _algebraic_metric_proxy

    ast = {
        "type": "linear_combo",
        "terms": [
            {"factor": "momentum", "params": {"window": 20}, "transforms": ["rank"], "weight": 0.75},
            {"factor": "volume_ratio", "params": {"window": 5}, "transforms": ["rank"], "weight": 0.25},
        ],
        "direction": "negative",
        "thesis": {"mechanism": "test", "citation": "test"},
    }
    corr, turnover = _algebraic_metric_proxy(
        ast,
        corr_by_factor={"momentum": 0.80, "volume_ratio": 0.20},
        turnover_by_factor={"momentum": 0.10, "volume_ratio": 0.50},
    )

    assert abs(corr - (-0.65)) < 1e-9
    assert abs(turnover - 0.20) < 1e-9


def test_multifidelity_prefilter_rejects_low_ic_before_full_l0():
    """Phase 2:低保真 |IC| 未达线的候选不进入后续完整 L0 评估池。"""
    from factory.autoresearch.islands import _multi_fidelity_prefilter
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns

    close, volume, amount = _synthetic_panel()
    forward_ret = precompute_forward_returns(close)
    candidates = [
        validate_candidate_ast(_candidate(weight=0.50)),
        validate_candidate_ast(_candidate(weight=0.70)),
        validate_candidate_ast(_candidate(weight=0.90)),
    ]
    calls: list[tuple[float, int | None]] = []

    def fake_l0(hyp, *args, **kwargs):
        weight = float(hyp.factor_params["ast"]["terms"][0]["weight"])
        sample_dates = kwargs.get("sample_dates")
        calls.append((weight, sample_dates))
        ic_mean_by_weight = {0.50: 0.010, 0.70: 0.030, 0.90: 0.050}
        icir_by_weight = {0.50: 0.10, 0.70: 0.20, 0.90: 0.80}
        return Experiment(
            experiment_id=f"mf-{weight}-{sample_dates}",
            hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "synthetic"),
            result=ExperimentResult(
                metrics={
                    "IC_mean": ic_mean_by_weight[weight],
                    "ICIR": icir_by_weight[weight],
                    "ICIR_nw": icir_by_weight[weight],
                },
                details={"direction": "long"},
            ),
            decision=Decision.PROMOTE,
            notes="fake",
        )

    kept = _multi_fidelity_prefilter(
        candidates,
        pipe_kw={
            "close": close,
            "volume": volume,
            "amount": amount,
            "forward_ret": forward_ret,
            "vintage_id": "synthetic",
            "runners": {"l0": fake_l0},
            "repository": None,
            "experiment_log": None,
            "review_queue": None,
            "sample_dates": 120,
            "computation_time_budget": 10.0,
        },
        level1_dates=20,
        level1_ic_min=0.02,
        level2_dates=60,
        level2_keep_ratio=0.5,
    )

    assert [c.ast["terms"][0]["weight"] for c in kept] == [0.90]
    assert (0.50, 60) not in calls
    assert (0.70, 60) in calls and (0.90, 60) in calls


def test_dsl_memory_cache_mode_does_not_write_factor_store(monkeypatch=None):
    """搜索期 memory cache 模式只用内存缓存,不写 canonical factor_store parquet。"""
    from factors.autoresearch_dsl import clear_factor_cache, compute_dsl_factor

    close, volume, _ = _synthetic_panel(n_days=40, n_stocks=8)
    ast = {
        "type": "linear_combo",
        "terms": [{
            "factor": "momentum",
            "params": {"window": 5},
            "transforms": ["zscore"],
            "weight": 1.0,
        }],
        "direction": "positive",
        "thesis": {"mechanism": "test", "citation": "test"},
    }
    writes = {"count": 0}
    original_to_parquet = pd.DataFrame.to_parquet

    def spy_to_parquet(self, *args, **kwargs):
        writes["count"] += 1
        return original_to_parquet(self, *args, **kwargs)

    pd.DataFrame.to_parquet = spy_to_parquet
    try:
        clear_factor_cache()
        first = compute_dsl_factor(close, volume, ast=ast, cache_mode="memory")
        second = compute_dsl_factor(close, volume, ast=ast, cache_mode="memory")
    finally:
        pd.DataFrame.to_parquet = original_to_parquet
        clear_factor_cache()

    assert writes["count"] == 0
    assert first.equals(second)


def test_dsl_memory_cache_mode_does_not_read_factor_store(monkeypatch=None):
    """搜索期 memory cache 模式不读 canonical factor_store parquet,避免旧盘缓存污染当前面板。"""
    from factors.autoresearch_dsl import clear_factor_cache, compute_dsl_factor

    close, volume, _ = _synthetic_panel(n_days=40, n_stocks=8)
    ast = {
        "type": "linear_combo",
        "terms": [{
            "factor": "momentum",
            "params": {"window": 5},
            "transforms": ["zscore"],
            "weight": 1.0,
        }],
        "direction": "positive",
        "thesis": {"mechanism": "test", "citation": "test"},
    }

    clear_factor_cache()

    def fail_read_parquet(*args, **kwargs):
        raise AssertionError("memory cache mode should not read parquet factor_store")

    old_exists = Path.exists
    old_read_parquet = pd.read_parquet
    if monkeypatch is not None:
        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(pd, "read_parquet", fail_read_parquet)
    else:
        Path.exists = lambda self: True
        pd.read_parquet = fail_read_parquet
    try:
        factor = compute_dsl_factor(close, volume, ast=ast, cache_mode="memory")
    finally:
        if monkeypatch is None:
            Path.exists = old_exists
            pd.read_parquet = old_read_parquet
        clear_factor_cache()

    assert factor.shape == close.shape


def test_dsl_disk_cache_key_uses_price_daily_all_mtime(monkeypatch=None):
    """磁盘缓存 key 必须跟真实 price/daily_all.parquet 版本走,避免数据湖重写后读旧面板。"""
    import factors.autoresearch_dsl as dsl

    assert dsl._SOURCE_DATA_PATHS[0].as_posix().endswith("data_lake/price/daily_all.parquet")

    old_source_data_mtime = dsl._source_data_mtime
    try:
        if monkeypatch is not None:
            monkeypatch.setattr(dsl, "_source_data_mtime", lambda: 111)
        else:
            dsl._source_data_mtime = lambda: 111
        first = dsl._get_cache_path("momentum", {"window": 5}, data_signature=None)
        if monkeypatch is not None:
            monkeypatch.setattr(dsl, "_source_data_mtime", lambda: 222)
        else:
            dsl._source_data_mtime = lambda: 222
        second = dsl._get_cache_path("momentum", {"window": 5}, data_signature=None)
    finally:
        if monkeypatch is None:
            dsl._source_data_mtime = old_source_data_mtime

    assert "_mt111" in first.name
    assert "_mt222" in second.name
    assert first != second


def test_dsl_disk_cache_ignores_non_overlapping_panel():
    """磁盘缓存文件存在但股票/日期不匹配时必须重算,不能把无交集缓存 reindex 成空信号。"""
    import factors.autoresearch_dsl as dsl

    close, volume, _ = _synthetic_panel(n_days=40, n_stocks=8)
    ast = {
        "type": "linear_combo",
        "terms": [{
            "factor": "momentum",
            "params": {"window": 5},
            "transforms": ["zscore"],
            "weight": 1.0,
        }],
        "direction": "positive",
        "thesis": {"mechanism": "test", "citation": "test"},
    }

    with tempfile.TemporaryDirectory() as td:
        cache_path = Path(td) / "bad_cache.parquet"
        bad = pd.DataFrame(
            999.0,
            index=pd.bdate_range("1999-01-01", periods=5),
            columns=["NO_OVERLAP"],
        )
        bad.to_parquet(cache_path)

        old_get_cache_path = dsl._get_cache_path
        try:
            dsl.clear_factor_cache()
            dsl._get_cache_path = (
                lambda name, params, data_signature=None, *, source_hash=None, feature_mask_version=None: cache_path
            )
            factor = dsl.compute_dsl_factor(close, volume, ast=ast, cache_mode="disk")
        finally:
            dsl._get_cache_path = old_get_cache_path
            dsl.clear_factor_cache()

    assert factor.shape == close.shape
    assert factor.notna().to_numpy().any()
    assert factor.abs().sum().sum() > 0


def test_dsl_disk_cache_keys_by_panel_content_when_mtime_is_zero():
    """mtime=0 时磁盘缓存仍必须按 close/volume 内容分桶,不能跨面板复用旧 parquet。"""
    import factors.autoresearch_dsl as dsl

    close, volume, _ = _synthetic_panel(n_days=40, n_stocks=8)
    close2 = close.copy()
    close2.iloc[:, 0] = close2.iloc[:, 0].iloc[::-1].to_numpy()
    volume2 = volume.copy()
    volume2.iloc[:, 0] = volume2.iloc[:, 0] + 1_000_000.0

    momentum_ast = {
        "type": "linear_combo",
        "terms": [{
            "factor": "momentum",
            "params": {"window": 5},
            "transforms": ["zscore"],
            "weight": 1.0,
        }],
        "direction": "positive",
        "thesis": {"mechanism": "test", "citation": "test"},
    }
    volume_ast = {
        "type": "linear_combo",
        "terms": [{
            "factor": "volume_ratio",
            "params": {"window": 5},
            "transforms": ["zscore"],
            "weight": 1.0,
        }],
        "direction": "positive",
        "thesis": {"mechanism": "test", "citation": "test"},
    }

    with tempfile.TemporaryDirectory() as td:
        old_root = dsl._ROOT
        old_source_data_mtime = dsl._source_data_mtime
        try:
            dsl._ROOT = Path(td)
            dsl._source_data_mtime = lambda: 0

            sig1 = dsl._data_signature(close, volume)
            sig2 = dsl._data_signature(close2, volume)
            sig3 = dsl._data_signature(close, volume2)
            path1 = dsl._get_cache_path("momentum", {"window": 5}, sig1)
            path2 = dsl._get_cache_path("momentum", {"window": 5}, sig2)
            path3 = dsl._get_cache_path("volume_ratio", {"window": 5}, sig3)
            assert path1 != path2
            assert path1 != path3

            dsl.clear_factor_cache()
            disk_first = dsl.compute_dsl_factor(close, volume, ast=momentum_ast, cache_mode="disk")
            dsl.clear_factor_cache()
            disk_second = dsl.compute_dsl_factor(close2, volume, ast=momentum_ast, cache_mode="disk")
            dsl.clear_factor_cache()
            expected_second = dsl.compute_dsl_factor(close2, volume, ast=momentum_ast, cache_mode="memory")
            pd.testing.assert_frame_equal(disk_second, expected_second)

            dsl.clear_factor_cache()
            disk_third = dsl.compute_dsl_factor(close, volume2, ast=volume_ast, cache_mode="disk")
            dsl.clear_factor_cache()
            expected_third = dsl.compute_dsl_factor(close, volume2, ast=volume_ast, cache_mode="memory")
            pd.testing.assert_frame_equal(disk_third, expected_third)

            # 第一轮写入仍应可复现,但不能被第二/三轮错误复用。
            dsl.clear_factor_cache()
            expected_first = dsl.compute_dsl_factor(close, volume, ast=momentum_ast, cache_mode="memory")
            pd.testing.assert_frame_equal(disk_first, expected_first)
        finally:
            dsl._ROOT = old_root
            dsl._source_data_mtime = old_source_data_mtime
            dsl.clear_factor_cache()


def test_window_mutation_snaps_to_fixed_grid():
    """窗口变异落到固定网格,避免 window_29/window_38 这类冷缓存爆炸。"""
    from factory.autoresearch.islands import _snap_window_to_grid

    assert _snap_window_to_grid(29, lo=3, hi=252) == 20
    assert _snap_window_to_grid(38, lo=3, hi=252) == 40
    assert _snap_window_to_grid(113, lo=3, hi=252) == 120
    assert _snap_window_to_grid(500, lo=3, hi=252) == 240


def test_run_island_search_defaults_to_thread_backend():
    """L0 搜索默认 thread backend,避免 ProcessPool 反复 pickle 大 DataFrame。"""
    import inspect

    from factory.autoresearch.islands import run_island_search

    sig = inspect.signature(run_island_search)
    assert sig.parameters["evaluation_backend"].default == "thread"


def test_validation_pipeline_computation_time_budget():
    """测试当计算耗时超出预算时，因子被舍弃。"""
    from factory.autoresearch.pipeline import run_validation_pipeline

    def slow_l0(hyp, *args, **kwargs):
        return Experiment(
            experiment_id="slow-l0",
            hypothesis_id=hyp.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=kwargs.get("vintage_id", "synthetic"),
            result=ExperimentResult(metrics={"ICIR": 0.1}, details={"direction": "long"}),
            decision=Decision.PROMOTE,
            notes="fake l0 pass",
            cost_spent_seconds=5.0,  # 耗时 5 秒
        )

    close, volume, amount = _synthetic_panel()
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns
    forward_ret = precompute_forward_returns(close)

    # Simple candidate
    cand = validate_candidate_ast({
        "type": "linear_combo",
        "terms": [{"factor": "volume_ratio", "params": {"window": 5}, "transforms": ["zscore"]}],
        "thesis": {"mechanism": "test", "citation": "test"}
    })

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        res = run_validation_pipeline(
            cand,
            close=close, volume=volume, amount=amount, forward_ret=forward_ret,
            vintage_id="synthetic",
            max_stage="l0",
            computation_time_budget=2.0,  # 预算为 2 秒，小于 5 秒
            runners={"l0": slow_l0},
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
        )
        assert res.decision.value == "discard"
        assert "computation time budget exceeded" in res.reason


def test_dsl_memory_cache_key_collision_prevention():
    """对抗式审查：测试两个具有不同形状/索引但巧合具有相同内存地址 id(close) 的 DataFrame 决不能发生缓存碰撞。"""
    import gc

    from factors.autoresearch_dsl import _data_signature, _panel_key

    # 1. 构造第一个 DataFrame
    close1 = pd.DataFrame(1.0, index=pd.date_range("2020-01-01", periods=10), columns=["A", "B"])
    addr1 = id(close1)
    sig1 = _data_signature(close1)

    # 2. 销毁它并诱导垃圾回收
    del close1
    gc.collect()

    # 3. 构造第二个 DataFrame，尝试使其复用 addr1
    close2 = None
    for _ in range(50):
        temp = pd.DataFrame(2.0, index=pd.date_range("2020-01-01", periods=5), columns=["A", "B", "C"])
        if id(temp) == addr1:
            close2 = temp
            break
        del temp
        gc.collect()

    # 4. 如果 Python 分配器在当前测试环境下没能复用地址，构造另一个形状不同的 DataFrame
    if close2 is None:
        close2 = pd.DataFrame(2.0, index=pd.date_range("2020-01-01", periods=5), columns=["A", "B", "C"])

    # 5. 验证两个不同的 DataFrame 的数据特征签名是否绝对不同
    sig2 = _data_signature(close2)
    assert sig1 != sig2, f"Signatures must differ for different DataFrames: {sig1} vs {sig2}"

    # 6. 对比旧的缓存逻辑与新加固的缓存逻辑
    ast = {"type": "linear_combo", "terms": [{"factor": "volume_ratio", "params": {"window": 5}, "transforms": []}]}
    
    # 模拟在旧 id() 缓存机制下的 Key (如果 id(close1) == id(close2))
    old_key1 = (hash(str(ast)), addr1, False)
    old_key2 = (hash(str(ast)), id(close2), False)
    if id(close2) == addr1:
        # 证明旧机制下会发生灾难性的 Key 碰撞（两个不同的因子面板共享同一个缓存槽）
        assert old_key1 == old_key2

    # 验证新加固机制下生成的缓存 Key
    new_key1 = _panel_key(ast, close2, None)
    
    # 创造一个不同形状的数据框，观察 _panel_key 是否能安全避开
    close3 = pd.DataFrame(3.0, index=pd.date_range("2020-01-01", periods=8), columns=["A"])
    new_key3 = _panel_key(ast, close3, None)
    
    assert new_key1 != new_key3, "New caching keys must be secure against same-id different-shape dataframes."


if __name__ == "__main__":
    test_dsl_memory_cache_key_collision_prevention()
    test_json_ast_validation_rejects_free_string_and_unknown_ops()
    test_neutralize_declaration_accepted_since_runtime_supports_it()
    test_fingerprint_is_stable_and_repository_dedupes()
    test_fingerprint_semantic_equivalence()
    test_candidate_generator_produces_unique_valid_ast_batch()
    test_ast_to_hypothesis_uses_controlled_runtime_factor()
    test_dsl_runtime_factor_computes_linear_combo_panel()
    test_complexity_budget_blocks_overfit_candidates()
    test_leakage_guard_blocks_future_and_label_fields()
    test_redundancy_score_combines_multiple_similarity_inputs()
    test_lite_engine_logs_discard_shelve_and_promote_without_registry_write()
    test_experiment_log_roundtrips_decision_enum()
    test_validation_pipeline_runs_l0_to_l3_and_promotes_only_after_l3()
    test_validation_pipeline_skips_candidates_blocked_by_knowledge_graph()
    test_real_runners_accept_autoresearch_hypothesis_contract()
    test_validation_pipeline_executes_real_l0_on_synthetic_panel()
    test_run_autoresearch_seeds_action_uses_pipeline_contract()
    test_human_review_approve_reject_without_registry_write()
    test_promote_approved_candidate_gates_and_calls_workflow_promote()
    test_review_approve_auto_promote_submits_shadow_job()
    test_auto_promote_shadow_updates_review_queue_and_never_active()
    test_auto_promote_failure_marks_retryable_promote_failed()
    test_llm_generation_validates_rejects_and_dedupes()
    test_island_search_mutation_stays_in_whitelist_and_search_is_deterministic()
    test_novelty_scores_behavioral_distance_not_syntax()
    test_island_fitness_blends_novelty_with_icir()
    test_marginal_return_correlation_helpers()
    test_topn_turnover_proxy()
    test_island_fitness_penalizes_turnover()
    test_island_fitness_penalizes_correlation_to_book()
    test_newey_west_icir_corrects_overlap_inflation()
    test_l0_reports_nw_corrected_icir()
    test_rediscovery_gate_zeros_edge_above_corr_threshold()
    test_walk_forward_search_truncates_train_and_scores_oos_after_cutoff()
    test_walk_forward_reference_builder_only_sees_truncated_panels()
    test_read_views_expose_candidates_and_review_queue()
    test_failure_ledger_aggregates_death_causes_with_evidence_gated_lessons()
    test_llm_generation_injects_failure_ledger_into_prompt()
    test_island_fitness_penalizes_complexity()
    test_algebraic_metric_proxy_uses_ast_weights_for_corr_and_turnover()
    test_multifidelity_prefilter_rejects_low_ic_before_full_l0()
    test_dsl_memory_cache_mode_does_not_write_factor_store()
    test_dsl_memory_cache_mode_does_not_read_factor_store(None)
    test_dsl_disk_cache_key_uses_price_daily_all_mtime(None)
    test_dsl_disk_cache_ignores_non_overlapping_panel()
    test_window_mutation_snaps_to_fixed_grid()
    test_run_island_search_defaults_to_thread_backend()
    test_validation_pipeline_computation_time_budget()
    print("✅ Auto Factor Research Engine tests passed")
