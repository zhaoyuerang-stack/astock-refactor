"""Centralised configuration for the A-stock factor research project.

All hard-coded parameters across the codebase should eventually be read from
here.  Environment variable ``ASTOCK_CONFIG`` can point to a YAML override
file for local customisation.
"""
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CostModelConfig:
    buy_cost: float = 0.00225      # 0.225%
    sell_cost: float = 0.00275     # 0.275%
    financing_rate: float = 0.065  # 6.5% annual
    # 场内 ETF(债券轮动腿):免印花税/过户费,佣金+点差保守上限 0.05%
    etf_buy_cost: float = 0.0005
    etf_sell_cost: float = 0.0005


# ---------------------------------------------------------------------------
# Strategy defaults (illiquidity v3.1, current LIVE production config)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyConfig:
    family: str = "illiquidity"
    version: str = "v3.1"
    start: str = "2018-01-01"
    size_window: int = 60
    timing_ma: int = 16
    top_n: int = 25
    rebalance_days: int = 20
    leverage: float = 1.25
    cost: CostModelConfig = field(default_factory=CostModelConfig)


# ---------------------------------------------------------------------------
# Data loading defaults
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DataConfig:
    warmup_start: str = "2010-01-01"
    default_start: str = "2018-01-01"
    price_fields: tuple = ("close", "volume")


# ---------------------------------------------------------------------------
# Factory search defaults
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactoryConfig:
    top_n_choices: tuple = (15, 20, 25, 40, 60, 80, 120)
    leverage_choices: tuple = (1.0, 1.25)
    review_corr_threshold: float = 0.50
    auto_promote_after_approve: bool = False


# ---------------------------------------------------------------------------
# Notification / alerting (运维告警)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NotifyConfig:
    desktop: bool = True                        # macOS 桌面通知(零依赖)
    obsidian: bool = True                       # 写入 Obsidian vault(30.output/2.[A]inbox/ai_data)
    alert_on: tuple = ("failed", "partial_ok")  # 哪些日更 status 触发告警
    recovery: bool = True                       # 失败后转 ok 时发"已恢复"


# ---------------------------------------------------------------------------
# Global settings container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    cost: CostModelConfig = field(default_factory=CostModelConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    data: DataConfig = field(default_factory=DataConfig)
    factory: FactoryConfig = field(default_factory=FactoryConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    @classmethod
    def from_yaml(cls, path: str | None = None):
        """Load settings from YAML, with optional env override."""
        if yaml is None:
            return cls()
        path = path or _env_config_path()
        if path and Path(path).exists():
            with open(path) as f:
                raw = yaml.safe_load(f)
            return cls._from_dict(raw or {})
        return cls()

    @classmethod
    def _from_dict(cls, d: dict):
        return cls(
            cost=CostModelConfig(**d.get("cost", {})),
            strategy=StrategyConfig(**d.get("strategy", {})),
            data=DataConfig(**d.get("data", {})),
            factory=FactoryConfig(**d.get("factory", {})),
            notify=NotifyConfig(**d.get("notify", {})),
        )


def _env_config_path() -> str | None:
    import os
    env_path = os.environ.get("ASTOCK_CONFIG")
    if env_path:
        return env_path
    default_path = Path(__file__).parent / "settings.yaml"
    if default_path.exists():
        return str(default_path)
    return None


# ---------------------------------------------------------------------------
# Singleton instance (lazy loaded)
# ---------------------------------------------------------------------------

_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings.from_yaml()
    return _SETTINGS
