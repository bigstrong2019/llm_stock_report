from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    snippet: str
    published_at: str | None = None


@dataclass
class PredictionRecord:
    market: str
    symbol: str
    asof_date: str
    score: float
    rank: int
    side: str
    pred_return: float
    model_version: str
    data_window_start: str
    data_window_end: str

    def to_csv_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StockNarrative:
    symbol: str
    summary: str
    details: str
    used_provider: str
    news_items: list[NewsItem] = field(default_factory=list)
    decision: str = "观望"
    trend: str = "震荡"
    urgency: str = "中"
    confidence: int = 50
    risk_points: list[str] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)
    evidence_used: list[str] = field(default_factory=list)
    reliability_notes: list[str] = field(default_factory=list)
    latest_close: float | None = None
    feature_snapshot: dict[str, float] = field(default_factory=dict)
    # ── 新增字段 ──────────────────────────────────────────────────
    one_liner: str = ""                  # 一句话总结
    valuation_analysis: str = ""         # 估值对比分析
    fund_flow_analysis: str = ""         # 近一周资金面概况
    risks: list[str] = field(default_factory=list)   # 利空消息
    core_conclusion: str = ""            # 核心结论
    fund_analysis: str = ""              # 详细资金面分析
    news_analysis: str = ""              # 消息面分析
    policy_analysis: str = ""            # 行业政策面分析


@dataclass
class MarketNarrative:
    market: str
    summary: str
    details: str
    used_provider: str
    news_items: list[NewsItem] = field(default_factory=list)
    # ── 新增字段 ──────────────────────────────────────────────────
    index_summary: str = ""                          # 各指数情况
    top_gainers_sectors: list[str] = field(default_factory=list)   # 前10上涨板块
    top_losers_sectors: list[str] = field(default_factory=list)    # 前10下跌板块
    fund_flow: str = ""                              # 南向/北向资金
    valuation: str = ""                              # 市场历史估值


@dataclass
class RunMeta:
    run_id: str
    market: str
    status: str
    total_symbols: int
    success_symbols: int
    failed_symbols: int
    failed_list: list[str]
    model_version: str
    llm_model: str
    search_provider_primary: str
    search_provider_fallback: str
    start_time: str
    end_time: str
    model_engine: str = ""
    model_fallback_used: bool = False
    model_warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
