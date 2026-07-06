"""结构化配置 —— pydantic-settings 读 .env（宪章技术约束）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class ProviderParams(dict):
    """占位：per-provider 采集参数在 scheduler 阶段细化（contracts/ingestion.md）。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_prefix="QUANTCHIVE_",
        extra="ignore",
    )

    # 数据库
    db_path: Path = Field(default=_REPO_ROOT / "data" / "quantchive.db")

    # 保留窗口 —— spec002 分级保留（research.md D4 + 用户降采决策）
    retention_trade_days: int = Field(default=30, ge=1)      # 总保留窗口(交易日)
    downsample_after_days: int = Field(default=7, ge=1)      # 超此天数降采为小时级

    # 分层采集间隔（秒）—— contracts/ingestion.md，按 collect_tier 分层
    minute_interval_sec: int = Field(default=60)             # 板块/热点股 1min
    coarse_interval_sec: int = Field(default=300)            # 个股/ETF 5min
    interval_cap_sec: int = Field(default=600)               # 硬上限 10min
    hot_top_n: int = Field(default=100)                      # 热点股提频数量

    # 调度器：默认关（测试/开发不起后台采集线程）；生产置 True 或 env QUANTCHIVE_SCHEDULER_ENABLED=1
    scheduler_enabled: bool = Field(default=False)

    # 大盘求和覆盖率门禁（D2）。默认 0.95；停牌股多的交易日（无流可求和）可下调。
    # 注意：停牌股贡献 0 流入，求和本身完整；门禁主要防"抓取失败"漏计，非停牌。
    market_coverage_threshold: float = Field(default=0.95, gt=0, le=1)
    # 盘中实时大盘门禁：盘中总有停牌/无成交股（东财实时不返），92%+ 已很高，
    # 用较松阈值让盘中大盘能正常显示；历史日线仍走上面的 0.95 严格门禁。
    market_coverage_threshold_intraday: float = Field(default=0.90, gt=0, le=1)

    # ---- spec003 数据层韧性（限流治理 / 缓存 / 回填）----
    # 翻页页间随机节流区间（秒）——防高频翻页触发东财 IP 限流（research D1）
    page_sleep_min: float = Field(default=0.5, ge=0)
    page_sleep_max: float = Field(default=1.5, ge=0)
    # 退避随机抖动上限（秒）——打散重试指纹（research D1）
    backoff_jitter: float = Field(default=1.5, ge=0)
    # HTTP 缓存 TTL（秒）：历史/日终收盘不变→长；实时快照→短或不缓存（research D2）
    cache_ttl_historical: int = Field(default=86400)   # 1 天
    cache_ttl_realtime: int = Field(default=0)         # 0=不缓存
    cache_enabled: bool = Field(default=True)
    # 历史回填默认深度（交易日），对齐保留窗
    backfill_default_days: int = Field(default=30, ge=1)

    # ---- spec005 资金档位博弈（gross / 校验 / 盘中）----
    # 跨源校验：量级差超此倍数判分歧（方向相反必判）；口径差异不报
    flow_validate_magnitude_ratio: float = Field(default=3.0, gt=1)
    # 百度校验探针：每次抽样股数 + 是否启用（无头浏览器慢，只做校验抽样）
    baidu_probe_sample_size: int = Field(default=30, ge=1)
    baidu_probe_enabled: bool = Field(default=False)   # 验证 hexin/反爬后启用
    # 盘中分钟归档：三层保留（分钟7天已由 downsample_after_days，此为总窗）
    intraday_archive_enabled: bool = Field(default=False)  # 调度器盘中归档开关

    # ---- QuantChive agent LLM（provider 无关：openai 兼容 / anthropic）----
    llm_provider: str = Field(default="openai")          # openai | anthropic
    llm_api_key: str = Field(default="")                 # 空=未配置,agent 端点报错引导
    llm_base_url: str = Field(default="")                # 空=用 provider 默认；可指向兼容网关
    llm_model: str = Field(default="gpt-4o")             # 具体模型名(deepseek/claude/... 由用户配)
    llm_max_steps: int = Field(default=6, ge=1, le=20)   # ReAct 最大轮数(防失控)



    # 采集间隔（秒）—— spec001 兼容保留
    em_interval_min_sec: int = Field(default=60)
    em_interval_max_sec: int = Field(default=600)
    ths_interval_min_sec: int = Field(default=120)
    ths_interval_max_sec: int = Field(default=600)

    # 代码版本（审计用，宪章 I/V），可由 CI 注入 git sha
    code_version: str | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    return Settings()
