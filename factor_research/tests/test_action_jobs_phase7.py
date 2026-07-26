"""Phase 7 验证:动作接口确认令牌 + 分钟级任务异步 job 化。

Run: cd factor_research && python3 tests/test_action_jobs_phase7.py
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["ASTCOK_ACTION_TOKEN"] = "phase7-unit-token"

import api.routers.experiments as experiments_router
from api.main import app
from contracts.views import AutoResearchRunResponse


def _run(coro):
    return asyncio.run(coro)


def _async_client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def test_action_endpoints_require_confirmation_token():
    async def scenario():
        async with _async_client() as client:
            url = "/experiments/autoresearch/review/not-a-real-fingerprint"
            body = {"action": "approve", "notes": "unit test"}

            missing = await client.post(url, json=body)
            assert missing.status_code == 403, "动作接口缺少确认令牌必须拒绝"

            bad = await client.post(url, json=body, headers={"X-Action-Token": "wrong"})
            assert bad.status_code == 403, "动作接口确认令牌错误必须拒绝"

            guarded = await client.post(url, json=body, headers={"X-Action-Token": "phase7-unit-token"})
            assert guarded.status_code == 400, "令牌正确后才进入业务校验"

    _run(scenario())
    print("✅ 动作接口缺失/错误确认令牌会被 403 拒绝")


def test_long_task_endpoint_returns_pollable_job():
    def fake_run_autoresearch_seeds(**kwargs):
        return AutoResearchRunResponse(vintage_id="unit", max_stage=kwargs["max_stage"], results=[])

    async def scenario():
        with patch.object(experiments_router, "run_autoresearch_seeds", side_effect=fake_run_autoresearch_seeds):
            async with _async_client() as client:
                accepted = await client.post(
                    "/experiments/autoresearch/run-seeds?limit=1&max_stage=l0&sample_dates=5",
                    json={},
                    headers={"X-Action-Token": "phase7-unit-token"},
                )

                assert accepted.status_code == 200
                payload = accepted.json()
                assert payload["job_id"], "分钟级动作应先返回 job_id"
                assert payload["kind"] == "autoresearch.run_seeds"
                assert payload["status"] in {"queued", "running", "succeeded"}

                polled = await client.get(f"/experiments/jobs/{payload['job_id']}")
                assert polled.status_code == 200
                assert polled.json()["job_id"] == payload["job_id"]

    _run(scenario())
    print("✅ 分钟级动作返回可轮询 job")


if __name__ == "__main__":
    print("Running Phase 7 action/job tests...\n")
    test_action_endpoints_require_confirmation_token()
    test_long_task_endpoint_returns_pollable_job()
    print("\n🎉 Phase 7 action/job tests passed!")
