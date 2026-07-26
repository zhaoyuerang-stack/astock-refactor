"""多账户 paper 实测管理器(WS-D 执行侧,PLAN_paper_multiaccount_loop.md T2)。

「排名靠前策略自动模拟盘」(R-PROD-001「不下单 ≠ 不实测」)的执行侧:把单账户
portfolio/paper_engine.py 的成交/估值原语(T1 已参数化)按账户路径复用,给
reports/research/portfolio_recompose.json::paper_candidates 里的每个候选版本
开一个独立账本,互不干扰、可并排展示。

铁边界:
  · 禁第二套信号/回测引擎(R-BT-001):每账户目标持仓只经
    strategies/executable.py::build_executable_strategy(spec, prices) 产出,
    与 run_daily.py 同款 canonical 路径。台账 config 转不出 ExecutableStrategySpec
    的版本 = 账户显式 blocked(no_executable_spec),绝不手写信号逻辑替代。
  · 禁前端/引擎重算排名(R-PROD-001):账户名单唯一来源 =
    reports/research/portfolio_recompose.json::paper_candidates(后端周度持久化)。
    stale(>14 天,与决策收件箱 recompose 源同口径)或缺失 → fail-closed 拒绝
    provision,不产生任何账户、不假装空转成功。
  · 与部署解耦:不读 deployments/production.json,不碰生产信号;账户状态与
    生产 live 与否无关——即使某版本未被部署,只要台账里能转出可执行 spec 就能开户。

账户状态机(§7.4 退役纪律:下榜冻结不删,历史账本永久保留不可变):
  active   —— 在候选名单里,能正常出目标持仓、正常成交/估值
  frozen   —— 曾在候选名单,本轮 recompose 已下榜:账本/NAV 永久保留,
              update_all 跳过(不再产生新成交/估值),状态可读不可变
  blocked  —— 在名单里但台账 config 转不出 ExecutableStrategySpec
              (no_executable_spec):不产生任何交易或估值,不产假 NAV
  degraded —— 有可执行 spec,但当日价格面板缺该账户需要的数据
              (no_price_data):跳过当日更新,不产假 NAV,状态可读

账户目录:paper/accounts/<family>__<version>/{account.json,trades.csv,nav.csv,state.json}
(复用单账户文件格式;state.json 记录本账户自己的调仓游标,互相独立)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.engine import PricePanel
from core.strategy_spec import ExecutableStrategySpec
from portfolio import paper_engine as pe
from strategies.executable import build_executable_strategy

CHINA_TZ = ZoneInfo("Asia/Shanghai")

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS_ROOT = ROOT / "paper" / "accounts"
RECOMPOSE_FP = ROOT / "reports" / "research" / "portfolio_recompose.json"
SUMMARY_FP = ACCOUNTS_ROOT / "summary.json"

# 与决策收件箱 _items_recompose 同口径(services/read/decision_inbox.py):
# 周度 job 两个周期 = 14 天,过期即视为「名单不可信」,不得据此开户/更新。
STALE_DAYS = 14

INIT_CAPITAL = pe.INIT_CAPITAL


def _account_dir(family: str, version: str, accounts_root: Path | None = None) -> Path:
    root = accounts_root if accounts_root is not None else ACCOUNTS_ROOT
    return root / f"{family}__{version}"


@dataclass(frozen=True)
class AccountPaths:
    """一个账户的四份持久化文件路径(复用 paper_engine 单账户文件格式)。"""
    account_fp: Path
    trades_fp: Path
    nav_fp: Path
    state_fp: Path

    @classmethod
    def for_version(cls, family: str, version: str, accounts_root: Path | None = None) -> AccountPaths:
        """accounts_root=None(默认)→ 模块常量 ACCOUNTS_ROOT(生产路径);
        显式传入时按该根目录解析——调用方(provision_from_recompose/update_account/
        update_all)必须把自己收到的 accounts_root 原样转发到这里,否则「传了
        accounts_root 参数却仍写到默认路径」这类隐蔽 bug 不会被静态检查发现,
        只能靠测试(test_paper_accounts.py 的隔离用例用不同 accounts_root 对照
        "组内跑" vs "单独跑" 才会暴露)。"""
        d = _account_dir(family, version, accounts_root)
        return cls(account_fp=d / "account.json", trades_fp=d / "trades.csv",
                   nav_fp=d / "nav.csv", state_fp=d / "state.json")


@dataclass
class AccountRecord:
    """账户元信息(状态机 + 名单归属),与 account.json 里的持仓账本分开存放。"""
    family: str
    version: str
    status: str  # active | frozen | blocked | degraded
    reason: str = ""
    opened_at: str = ""
    frozen_at: str = ""
    last_update_date: str = ""

    @property
    def name(self) -> str:
        return f"{self.family}.{self.version}"

    def to_dict(self) -> dict:
        return {
            "family": self.family, "version": self.version, "status": self.status,
            "reason": self.reason, "opened_at": self.opened_at,
            "frozen_at": self.frozen_at, "last_update_date": self.last_update_date,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AccountRecord:
        return cls(family=d["family"], version=d["version"], status=d["status"],
                    reason=d.get("reason", ""), opened_at=d.get("opened_at", ""),
                    frozen_at=d.get("frozen_at", ""), last_update_date=d.get("last_update_date", ""))


def _now_str() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def _load_registry_index() -> dict[str, dict]:
    """strategy_registry 全量索引:{"family.version": version_record_dict}。

    只读 strategy_registry._load()(services.read.* 同款受控接缝,唯一写入口仍是
    strategy_registry.register/register_family),不做任何写入。
    """
    import strategy_registry

    data = strategy_registry._load()
    idx: dict[str, dict] = {}
    for fam in data.get("families", []):
        for v in fam.get("versions", []):
            name = f"{fam['id']}.{v['version']}"
            record = dict(v)
            record["_family_id"] = fam["id"]
            record["_family_status"] = fam.get("status", "")
            idx[name] = record
    return idx


def resolve_executable_spec(record: dict) -> tuple[ExecutableStrategySpec | None, str]:
    """从台账版本记录里取 executable_spec,校验通过则返回 (spec, "")；
    不可执行则返回 (None, 原因)——绝不为不可执行的版本手写信号旁路(R-BT-001)。

    与 runtime/deployment.py::load_deployed_strategy_spec 同一套解析规则
    (record.executable_spec.spec → ExecutableStrategySpec.from_dict → validate()),
    但不要求 spec_hash 与某条部署腿匹配——paper 账户与部署解耦,只要 spec 自身
    结构合法即可开户。
    """
    executable = record.get("executable_spec") or {}
    spec_data = executable.get("spec")
    if not spec_data:
        return None, "no_executable_spec: registry 版本无 executable_spec.spec 字段"
    try:
        spec = ExecutableStrategySpec.from_dict(spec_data)
        spec.validate()
    except (ValueError, TypeError, KeyError) as exc:
        return None, f"no_executable_spec: spec 校验失败({exc})"
    return spec, ""


def read_recompose_candidates(recompose_fp: Path | None = None, *, now: datetime | None = None
                               ) -> tuple[list[str], str]:
    """读 recompose 持久化名单,返回 (candidates, block_reason)。

    block_reason 非空即 fail-closed:调用方不得据此 provision 任何账户。
    三态诚实(区分「文件不存在」/「名单健康但空」/「过期」),不得把「文件不存在」
    误报成「名单健康但空」。
    """
    fp = recompose_fp or RECOMPOSE_FP
    if not fp.exists():
        return [], f"recompose 产物不存在({fp}):尚未跑过周度组合再构成,拒绝 provision"
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [], f"recompose 产物无法解析({exc}):拒绝 provision"

    generated_at = data.get("generated_at", "")
    try:
        gen = datetime.fromisoformat(str(generated_at))
    except (ValueError, TypeError):
        return [], f"recompose 产物 generated_at 无法解析({generated_at!r}):拒绝 provision"

    now = now or datetime.now(CHINA_TZ)
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=CHINA_TZ)
    age_days = (now - gen).days
    if age_days > STALE_DAYS:
        return [], f"recompose 产物已过期(生成于 {generated_at},{age_days} 天前 > {STALE_DAYS} 天):拒绝 provision"

    raw = list(data.get("paper_candidates") or [])
    # 兼容守卫审计 #5:paper_candidates 条目可为 str 或 {name, identity_tier}
    candidates: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("leg") or "").strip()
            if name:
                candidates.append(name)
        else:
            candidates.append(str(item))
    return candidates, ""  # 空列表是健康态(全灭空提案),非 block


def provision_from_recompose(recompose_fp: Path | None = None, *, now: datetime | None = None,
                              accounts_root: Path | None = None) -> dict:
    """按 recompose 名单开户/冻结。返回 provisioning 摘要(供 T3 落 summary.json)。

    · stale/缺失 → fail-closed:不开任何户,返回 status="rejected" + reason。
    · 名单健康但空 → 不算错误,只是没有候选(status="ok", provisioned=[])。
    · 新上榜(名单里但账户目录不存在)→ 开户,status=active(若能解出 spec)
      或 blocked(no_executable_spec)(解不出)。
    · 下榜(账户目录存在但不在本轮名单)→ frozen,账本/NAV 永久保留不改。
    · 名单里的名字在台账找不到对应 family/version → 该项目标记 unknown,不开户。
    """
    root = accounts_root or ACCOUNTS_ROOT
    candidates, block_reason = read_recompose_candidates(recompose_fp, now=now)
    if block_reason:
        return {"status": "rejected", "reason": block_reason, "candidates": [], "accounts": []}

    registry_idx = _load_registry_index()
    root.mkdir(parents=True, exist_ok=True)
    existing_dirs = {p.name for p in root.iterdir() if p.is_dir()} if root.exists() else set()
    candidate_dirnames = set()
    accounts: list[dict] = []

    for name in candidates:
        record = registry_idx.get(name)
        if record is None:
            accounts.append({"name": name, "status": "unknown",
                             "reason": f"unknown_candidate: 台账无 {name} 对应记录"})
            continue
        family, version = record["_family_id"], record["version"]
        dirname = f"{family}__{version}"
        candidate_dirnames.add(dirname)
        paths = AccountPaths.for_version(family, version, root)
        rec_fp = paths.account_fp.parent / "meta.json"
        is_new = dirname not in existing_dirs

        spec, reason = resolve_executable_spec(record)
        if spec is None:
            status = "blocked"
        else:
            status = "active"

        if is_new:
            meta = AccountRecord(family=family, version=version, status=status,
                                  reason=reason, opened_at=_now_str())
            paths.account_fp.parent.mkdir(parents=True, exist_ok=True)
            rec_fp.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            meta = _read_meta(rec_fp)
            if meta is None:
                meta = AccountRecord(family=family, version=version, status=status,
                                      reason=reason, opened_at=_now_str())
            else:
                # 重新上榜的 frozen 账户 或 spec 状态变化:刷新 status/reason,
                # 但 opened_at/历史账本不动(§7.4:不得覆盖历史)。
                meta.status = status
                meta.reason = reason
                if meta.frozen_at:
                    meta.frozen_at = ""  # 解冻:重新上榜
            rec_fp.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        accounts.append(meta.to_dict())

    # 下榜:existing_dirs 里不在本轮 candidate_dirnames 的 → frozen(账本永久保留不删)。
    # sorted() 而非裸 set 差集迭代:set 顺序依赖哈希种子,跨进程不稳定;这里排序
    # 保证 provision_result["accounts"] 尾部的下榜段落顺序确定性可复现。
    for dirname in sorted(existing_dirs - candidate_dirnames):
        rec_fp = root / dirname / "meta.json"
        meta = _read_meta(rec_fp)
        if meta is None:
            continue  # 目录存在但无 meta.json(异常态)不是本函数职责,交给读层显式报告
        if meta.status != "frozen":
            meta.status = "frozen"
            meta.frozen_at = _now_str()
            rec_fp.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        accounts.append(meta.to_dict())

    return {"status": "ok", "reason": "", "candidates": candidates, "accounts": accounts}


def _read_meta(rec_fp: Path) -> AccountRecord | None:
    if not rec_fp.exists():
        return None
    try:
        return AccountRecord.from_dict(json.loads(rec_fp.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def list_account_metas(accounts_root: Path | None = None) -> list[AccountRecord]:
    root = accounts_root or ACCOUNTS_ROOT
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        meta = _read_meta(d / "meta.json")
        if meta is not None:
            out.append(meta)
    return out


def _resolve_target_and_exposure(executable, as_of: pd.Timestamp) -> tuple[list[str], float]:
    """从 canonical ExecutableStrategy 里取 as_of(含)之前最近一个调仓日的目标持仓
    + 当日 exposure(band/binary timing,由 spec.timing.type 决定,executable.timing
    已经是 build_executable_strategy 产出的最终 exposure 序列,不重新推导)。

    target/exposure 全部来自 executable.scheduled_weights / executable.timing——
    与 run_daily.py 同一个 canonical 产物,不新写任何选股/择时逻辑(R-BT-001)。
    """
    scheduled = executable.scheduled_weights or {}
    valid_dates = sorted(d for d in scheduled if d <= as_of)
    if not valid_dates:
        return [], 0.0
    latest_rd = valid_dates[-1]
    weights = scheduled[latest_rd]
    target = sorted(weights.index.tolist())  # 排序 = 确定性(跨进程账本字节稳定)
    exposure = float(executable.timing.loc[as_of]) if as_of in executable.timing.index else 0.0
    return target, exposure


def update_account(family: str, version: str, spec: ExecutableStrategySpec, prices: PricePanel,
                    as_of: str, *, accounts_root: Path | None = None) -> dict:
    """单账户日更:target 持仓 → T+1 成交原语(复用 T1 参数化 paper_engine)→ 估值 → 落盘。

    prices 由调用方注入(不在本函数内 load_price_panels)——保证可 hermetic 测试,
    且与「谁负责联网取数」解耦(T3 的编排脚本负责取数,这里只负责决策与记账)。
    返回本次更新摘要(status/date/nav/trades 数/blocked 数),供 T3 汇总 summary.json。
    """
    root = accounts_root or ACCOUNTS_ROOT
    paths = AccountPaths.for_version(family, version, root)
    rec_fp = paths.account_fp.parent / "meta.json"
    meta = _read_meta(rec_fp)
    if meta is None:
        return {"family": family, "version": version, "status": "unknown",
                "reason": "unknown_account: 未 provision,无 meta.json"}
    if meta.status == "frozen":
        return {"family": family, "version": version, "status": "frozen",
                "reason": "下榜冻结,不再更新(历史账本永久保留)"}
    if meta.status == "blocked":
        return {"family": family, "version": version, "status": "blocked",
                "reason": meta.reason}

    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts not in prices.close.index:
        return {"family": family, "version": version, "status": "degraded",
                "reason": f"no_price_data: {as_of} 不在注入价格面板的交易日索引里,跳过本次更新"}

    executable = build_executable_strategy(spec, prices)
    target, exposure = _resolve_target_and_exposure(executable, as_of_ts)
    names = pe.load_names()

    acc = pe.load_account(account_fp=paths.account_fp)
    if acc["inception"] is None:
        acc["inception"] = as_of

    trades: list = []
    blocked: list = []
    in_market = exposure > 0
    pe.execute_to_target(acc, as_of, target if in_market else [], max(len(target), 1),
                         names, trades, blocked, leverage=exposure if in_market else 0.0, bond=None)
    if trades:
        pe.append_trades(trades, trades_fp=paths.trades_fp)

    nav, pos_value, _detail = pe.valuation(acc, as_of)
    ret = nav / acc["init_capital"] - 1
    pe.upsert_nav(as_of, nav, acc["cash"], pos_value, ret, nav_fp=paths.nav_fp)
    acc["last_date"] = as_of
    pe.save_account(acc, account_fp=paths.account_fp)

    meta.last_update_date = as_of
    rec_fp.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    return {"family": family, "version": version, "status": "active", "date": as_of,
            "nav": round(nav, 2), "trades": len(trades), "blocked": len(blocked)}


def update_all(prices: PricePanel, as_of: str, *, accounts_root: Path | None = None) -> list[dict]:
    """遍历所有非 frozen 账户逐一 update_account;frozen/blocked 账户原样跳过并如实上报状态。

    确定性:同输入(同 prices + 同 as_of + 同台账快照)两次调用产出逐字节相同的
    account.json/trades.csv/nav.csv(target 已在 _resolve_target_and_exposure 里
    排序,不依赖 Python set 的哈希种子)。
    账本隔离:每账户独立 AccountPaths,互不共享可变状态(acc dict 各自 load 一份)。
    """
    root = accounts_root or ACCOUNTS_ROOT
    results = []
    for meta in list_account_metas(root):
        if meta.status == "frozen":
            results.append({"family": meta.family, "version": meta.version,
                            "status": "frozen", "reason": "下榜冻结,不再更新"})
            continue
        if meta.status == "blocked":
            results.append({"family": meta.family, "version": meta.version,
                            "status": "blocked", "reason": meta.reason})
            continue
        registry_idx = _load_registry_index()
        record = registry_idx.get(meta.name)
        if record is None:
            results.append({"family": meta.family, "version": meta.version, "status": "unknown",
                            "reason": "registry 里找不到该 family/version(可能已被删除条目)"})
            continue
        spec, reason = resolve_executable_spec(record)
        if spec is None:
            results.append({"family": meta.family, "version": meta.version,
                            "status": "blocked", "reason": reason})
            continue
        results.append(update_account(meta.family, meta.version, spec, prices, as_of,
                                      accounts_root=root))
    return results
