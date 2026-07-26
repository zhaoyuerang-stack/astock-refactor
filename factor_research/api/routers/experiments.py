"""研究实验端点:假设池漏斗 / 假设列表 / 已登记实验。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from contracts.views import (
    ActionJobView,
    AutoResearchCandidateView,
    AutoResearchFunnelView,
    AutoResearchReviewItemView,
    AutoResearchReviewRequest,
    FunnelView,
    HypothesisView,
    PromotionReadinessView,
    RegisteredExperimentView,
    ResearchDraftCreateRequest,
    ResearchDraftUpdateRequest,
    ResearchDraftView,
    ResearchReviewRequest,
    ResearchReviewView,
    ResearchRunIndexView,
    ResearchWorkItemActionRequest,
    ResearchWorkItemDetailView,
    ResearchWorkItemListView,
)
from services.actions.action_guard import ACTION_HEADER, audit_action, verify_action_token
from services.actions.autoresearch import (
    promote_approved_candidate,
    run_autoresearch_seeds,
)
from services.actions.autoresearch_llm import run_autoresearch_llm
from services.actions.autoresearch_search import run_autoresearch_island_search
from services.actions.jobs import get_action_job, list_action_jobs, submit_action_job
from services.actions.research_workspace import (
    InvalidTransition,
    WorkItemConflict,
    create_draft,
    review_legacy_autoresearch_candidate,
    review_work_item,
    submit_work_item_action,
    update_draft,
)
from services.read.autoresearch import (
    autoresearch_candidates,
    autoresearch_funnel,
    autoresearch_review_queue,
)
from services.read.experiments import funnel, hypotheses, registered_experiments, research_run_index
from services.read.promotion_readiness import get_promotion_readiness
from services.read.research_work_items import get_work_item, list_work_items

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.get("/shadow-incubation")
def get_shadow_incubation() -> dict:
    import json
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    
    shadow_log = ROOT / "data_lake" / "agent" / "shadow_incubation_log.json"
    shadow_data = {}
    if shadow_log.exists():
        try:
            shadow_data = json.loads(shadow_log.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    predictions_file = ROOT / "data_lake" / "research_signals" / "ontology_predictions.json"
    predictions_data = {}
    if predictions_file.exists():
        try:
            predictions_data = json.loads(predictions_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    performance_file = ROOT / "reports" / "islands" / "shadow_ontology_performance.json"
    performance_data = {}
    if performance_file.exists():
        try:
            performance_data = json.loads(performance_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    return {
        "incubation": shadow_data,
        "predictions": predictions_data,
        "performance": performance_data
    }


@router.get("/amount-timing-validation")
def get_amount_timing_validation() -> dict:
    import json
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    
    validation_file = ROOT / "reports" / "ops" / "amount_timing_validation.json"
    if validation_file.exists():
        try:
            return json.loads(validation_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    return {
        "latest_signal": None,
        "all_market": [],
        "ex688": [],
        "cost_sensitivity": [],
        "walk_forward": []
    }


@router.get("/logical-chains")
def get_logical_chains() -> list[dict]:
    import json
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    logic_dir = ROOT / "data_lake" / "research_signals" / "logic_chains"
    chains = []
    if logic_dir.exists():
        for f in logic_dir.glob("*.json"):
            try:
                chains.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return chains


@router.get("/industry-knowledge-graph")
def get_industry_knowledge_graph() -> dict:
    import json
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    graph_file = ROOT / "data_lake" / "research_signals" / "industry_knowledge_graph.json"
    
    if graph_file.exists():
        try:
            return json.loads(graph_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    return {"nodes": [], "links": [], "meta": {"total_nodes": 0, "total_links": 0}}


def require_action_token(x_action_token: str | None = Header(default=None, alias=ACTION_HEADER)) -> None:
    verify_action_token(x_action_token)


@router.get("/promotion-readiness", response_model=PromotionReadinessView)
def get_promotion_readiness_endpoint() -> PromotionReadinessView:
    """Alpha 工厂「晋级就绪」驾驶舱:候选按「距入册」排序 + 唯一卡点 + 边际动作 + 拥挤度。"""
    return get_promotion_readiness()


@router.get("/funnel", response_model=FunnelView)
def get_funnel() -> FunnelView:
    return funnel()


@router.get("/hypotheses", response_model=list[HypothesisView])
def get_hypotheses(status: str | None = None, limit: int = 60) -> list[HypothesisView]:
    return hypotheses(status=status, limit=limit)


@router.get("/registered", response_model=list[RegisteredExperimentView])
def get_registered() -> list[RegisteredExperimentView]:
    return registered_experiments()


@router.get("/research-runs", response_model=ResearchRunIndexView)
def get_research_runs() -> ResearchRunIndexView:
    return research_run_index()


@router.get("/work-items", response_model=ResearchWorkItemListView)
def get_research_work_items(
    status: str = "",
    kind: str = "",
    action: str = "",
    limit: int = 200,
) -> ResearchWorkItemListView:
    return list_work_items(
        status=status,
        kind=kind,
        action=action,
        limit=max(0, min(limit, 2000)),
        job_views=list_action_jobs(),
    )


@router.get("/work-items/{kind}/{item_id}", response_model=ResearchWorkItemDetailView)
def get_research_work_item(kind: str, item_id: str) -> ResearchWorkItemDetailView:
    try:
        return get_work_item(kind, item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"work item not found: {kind}:{item_id}") from exc


@router.post("/drafts", response_model=ResearchDraftView)
def post_research_draft(
    body: ResearchDraftCreateRequest,
    _confirmed: None = Depends(require_action_token),
) -> ResearchDraftView:
    return create_draft(**body.model_dump())


@router.patch("/drafts/{draft_id}", response_model=ResearchDraftView)
def patch_research_draft(
    draft_id: str,
    body: ResearchDraftUpdateRequest,
    _confirmed: None = Depends(require_action_token),
) -> ResearchDraftView:
    try:
        return update_draft(draft_id, **body.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"draft not found: {draft_id}") from exc


@router.post("/work-items/{kind}/{item_id}/reviews", response_model=ResearchReviewView)
def post_research_review(
    kind: str,
    item_id: str,
    body: ResearchReviewRequest,
    _confirmed: None = Depends(require_action_token),
) -> ResearchReviewView:
    try:
        return review_work_item(
            kind, item_id, action=body.action, notes=body.notes, reviewer=body.reviewer,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"work item not found: {kind}:{item_id}") from exc
    except WorkItemConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/work-items/{kind}/{item_id}/actions/{action}", response_model=ActionJobView)
def post_research_action(
    kind: str,
    item_id: str,
    action: str,
    body: ResearchWorkItemActionRequest,
    _confirmed: None = Depends(require_action_token),
) -> ActionJobView:
    try:
        return submit_work_item_action(
            kind, item_id, action,
            start=body.start, sample_dates=body.sample_dates,
            version=body.version, target_status=body.target_status,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"work item not found: {kind}:{item_id}") from exc
    except WorkItemConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/autoresearch/funnel", response_model=AutoResearchFunnelView)
def get_autoresearch_funnel() -> AutoResearchFunnelView:
    return autoresearch_funnel()


@router.get("/autoresearch/candidates", response_model=list[AutoResearchCandidateView])
def get_autoresearch_candidates(limit: int = 60) -> list[AutoResearchCandidateView]:
    return autoresearch_candidates(limit=limit)


@router.get("/autoresearch/review-queue", response_model=list[AutoResearchReviewItemView])
def get_autoresearch_review_queue(limit: int = 60) -> list[AutoResearchReviewItemView]:
    return autoresearch_review_queue(limit=limit)


@router.post("/autoresearch/review/{fingerprint}", response_model=AutoResearchReviewItemView)
def post_autoresearch_review(
    fingerprint: str,
    body: AutoResearchReviewRequest,
    _confirmed: None = Depends(require_action_token),
) -> AutoResearchReviewItemView:
    """人工复核 approve/reject。approve 不写 LIVE 台账,入册仍走 workflow/promote。"""
    try:
        return review_legacy_autoresearch_candidate(
            fingerprint=fingerprint,
            action=body.action,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/jobs/{job_id}", response_model=ActionJobView)
def get_experiment_job(job_id: str) -> ActionJobView:
    try:
        return get_action_job(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}") from e


@router.get("/jobs", response_model=list[ActionJobView])
def get_experiment_jobs() -> list[ActionJobView]:
    return list_action_jobs()


@router.post("/autoresearch/run-seeds", response_model=ActionJobView)
def post_autoresearch_run_seeds(
    limit: int = 5,
    max_stage: str = "l0",
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    _confirmed: None = Depends(require_action_token),
) -> ActionJobView:
    job = submit_action_job(
        "autoresearch.run_seeds",
        run_autoresearch_seeds,
        limit=limit,
        max_stage=max_stage,
        start=start,
        sample_dates=sample_dates,
    )
    audit_action("submit AutoResearch seeds", f"job_id={job.job_id} max_stage={max_stage}", status=job.status)
    return job


@router.post("/autoresearch/promote/{fingerprint}", response_model=ActionJobView)
def post_autoresearch_promote(
    fingerprint: str,
    version: str = "v1.0",
    _confirmed: None = Depends(require_action_token),
) -> ActionJobView:
    """APPROVED 候选 → workflow phase1~4 正式入册(分钟级,phase4 是唯一台账入口)。"""
    job = submit_action_job(
        "autoresearch.promote",
        promote_approved_candidate,
        fingerprint=fingerprint,
        version=version,
    )
    audit_action("submit AutoResearch promote", f"job_id={job.job_id} fingerprint={fingerprint[:10]}", status=job.status)
    return job


@router.post("/autoresearch/run-llm", response_model=ActionJobView)
def post_autoresearch_run_llm(
    n: int = 5,
    theme: str = "",
    max_stage: str = "l1",
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    computation_time_budget: float = 10.0,
    _confirmed: None = Depends(require_action_token),
) -> ActionJobView:
    """LLM 生成候选并走真实验证线。LLM 未配置 → 400。"""
    job = submit_action_job(
        "autoresearch.run_llm",
        run_autoresearch_llm,
        n=n,
        theme=theme,
        max_stage=max_stage,
        start=start,
        sample_dates=sample_dates,
        computation_time_budget=computation_time_budget,
    )
    audit_action("submit AutoResearch LLM", f"job_id={job.job_id} n={n} max_stage={max_stage}", status=job.status)
    return job


@router.post("/autoresearch/island-search", response_model=ActionJobView)
def post_autoresearch_island_search(
    islands: int = 4,
    generations: int = 3,
    population: int = 8,
    top_k: int = 5,
    final_stage: str = "l0",
    use_llm: bool = True,
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    complexity_weight: float = 0.0,
    computation_time_budget: float = 10.0,
    _confirmed: None = Depends(require_action_token),
) -> ActionJobView:
    """多岛屿进化搜索(分钟级;LLM 可用时按主题播种,否则确定性种子)。"""
    job = submit_action_job(
        "autoresearch.island_search",
        run_autoresearch_island_search,
        islands=islands,
        generations=generations,
        population=population,
        top_k=top_k,
        final_stage=final_stage,
        use_llm=use_llm,
        start=start,
        sample_dates=sample_dates,
        complexity_weight=complexity_weight,
        computation_time_budget=computation_time_budget,
    )
    audit_action("submit AutoResearch island search", f"job_id={job.job_id} islands={islands}", status=job.status)
    return job
