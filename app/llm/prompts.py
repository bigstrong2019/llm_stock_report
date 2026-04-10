from __future__ import annotations

from app.common.decision_policy import baseline_decision, baseline_trend
from app.common.schemas import NewsItem, PredictionRecord


# ── 系统提示词 ────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "你是一名严谨的中文股票策略研究员。"
    "你只能基于用户给出的结构化输入作答，禁止编造数据。"
    "不允许输出买卖保证、收益承诺、内幕信息或确定性措辞。"
    "若信息不足必须明确写出不确定性来源。"
    "输出必须是严格 JSON，不包含 markdown 代码块。"
)

SYSTEM_PROMPT_EN = (
    "You are a rigorous equity research analyst."
    "Use only the provided structured inputs and never fabricate facts."
    "Do not provide guaranteed returns, certainty claims, or insider-information style advice."
    "If evidence is insufficient, explicitly state uncertainty sources."
    "Output must be strict JSON without markdown code fences."
)


def get_system_prompt(language: str = "zh") -> str:
    return SYSTEM_PROMPT_EN if (language or "").strip().lower() == "en" else SYSTEM_PROMPT


# ── 股票分析提示词 ────────────────────────────────────────────────

def build_stock_reasoning_prompt(
    market: str,
    symbol: str,
    prediction: PredictionRecord,
    latest_close: float,
    feature_snapshot: dict[str, float],
    news_items: list[NewsItem],
    language: str = "zh",
) -> str:
    language = (language or "zh").strip().lower()
    quant_decision = baseline_decision(prediction)
    quant_trend = baseline_trend(prediction)

    anchor_line = (
        f"量化基线判断: {quant_decision} / {quant_trend}。"
        "请把它当作模型排序给出的起点判断；只有当技术面或新闻证据明确冲突时，才下调到观望或反向结论。"
    )

    ordered_features = sorted(feature_snapshot.items(), key=lambda x: x[0])
    feature_lines = [f"- {k}: {v:.6f}" for k, v in ordered_features]
    feature_block = "\n".join(feature_lines) if feature_lines else "- 无可用技术特征"

    news_lines = []
    for idx, item in enumerate(news_items[:4], start=1):
        news_lines.append(
            f"[N{idx}] 标题: {item.title}\n"
            f"      摘要: {item.snippet[:120]}\n"
            f"      链接: {item.url}"
        )
    news_block = "\n".join(news_lines) if news_lines else "无相关新闻"

    return f"""
请基于以下输入生成中文股票研究简报，遵守"可解释、可追溯、不过度结论"的原则。

市场: {market}
股票: {symbol}
收盘价: {latest_close:.4f}
预测分数(score): {prediction.score:.6f}
预测收益(pred_return): {prediction.pred_return:.6f}
排名: {prediction.rank}
多空标签(side): {prediction.side}
{anchor_line}

技术面快照:
{feature_block}

新闻证据:
{news_block}

输出要求（必须同时满足）：
1) one_liner：一句话总结，不超过30字，包含方向判断与不确定性提示
2) valuation_analysis：与过去3年估值对比分析，2-3句话
   必须在开头注明：【⚠️ 仅AI搜索分析，未拿到接口数据】
   基于新闻和已知行业信息推断估值水位，若无依据需明确说明
3) fund_flow_analysis：最近一周资金面概况，2-3句话
   必须在开头注明：【⚠️ 仅AI搜索分析，未拿到接口数据】
   可基于成交量特征（vol_ratio_lb）和新闻推断资金动向
4) catalysts：利好消息，1-3条，必须来自新闻证据，不可凭空捏造；无证据则空数组
5) risks：利空消息，1-3条，具体可追溯；不要写空泛句
6) core_conclusion：核心结论，2-3句话，不超过100字
7) fund_analysis：详细资金面分析，2-3句
   必须在开头注明：【⚠️ 仅AI搜索分析，未拿到接口数据】
8) news_analysis：消息面分析，2-3句，必须引用 N1/N2 等新闻编号
   若无新闻需明确写"新闻证据不足，结论仅基于技术面"
9) policy_analysis：所属行业政策面分析，2-3句
   必须在开头注明：【⚠️ 仅AI搜索分析，未拿到接口数据】
10) trend：必须是 看多|震荡|看空|强烈看空 之一
11) decision：必须是 买入|观望|减仓|卖出|卖出/观望 之一
    不要因为"存在不确定性"就默认写成观望，decision 仍应反映当前排序方向，除非证据明确冲突
12) confidence：0-100的整数，体现当前结论可靠性

仅输出 JSON，格式如下：
{{
  "one_liner": "一句话总结不超过30字",
  "valuation_analysis": "【⚠️ 仅AI搜索分析，未拿到接口数据】...",
  "fund_flow_analysis": "【⚠️ 仅AI搜索分析，未拿到接口数据】...",
  "catalysts": ["利好1", "利好2"],
  "risks": ["利空1", "利空2"],
  "core_conclusion": "核心结论2-3句",
  "fund_analysis": "【⚠️ 仅AI搜索分析，未拿到接口数据】...",
  "news_analysis": "消息面分析引用N1N2...",
  "policy_analysis": "【⚠️ 仅AI搜索分析，未拿到接口数据】...",
  "trend": "看多",
  "decision": "买入",
  "confidence": 72
}}
""".strip()


# ── 大盘复盘提示词 ────────────────────────────────────────────────

def build_market_reasoning_prompt(
    market: str,
    asof_date: str,
    market_snapshot: dict,
    news_items: list[NewsItem],
    language: str = "zh",
) -> str:
    language = (language or "zh").strip().lower()

    benchmark_lines: list[str] = []
    for item in market_snapshot.get("benchmarks", []):
        benchmark_lines.append(
            f"- {item.get('name')}({item.get('ticker')}): "
            f"收盘={float(item.get('latest_close', 0.0)):.2f}, "
            f"1日涨跌={float(item.get('ret_1d', 0.0)) * 100:+.2f}%, "
            f"5日涨跌={float(item.get('ret_5d', 0.0)) * 100:+.2f}%, "
            f"MA20乖离={float(item.get('ma20_ratio', 0.0)):.4f}"
        )
    benchmark_block = "\n".join(benchmark_lines) if benchmark_lines else "- 无可用基准指数数据"

    breadth_block = (
        f"样本数={int(market_snapshot.get('sample_size', 0))}, "
        f"上涨={int(market_snapshot.get('up_count', 0))}, "
        f"下跌={int(market_snapshot.get('down_count', 0))}, "
        f"平盘={int(market_snapshot.get('flat_count', 0))}, "
        f"均值涨跌={float(market_snapshot.get('avg_ret_1d', 0.0)) * 100:+.2f}%, "
        f"中位涨跌={float(market_snapshot.get('median_ret_1d', 0.0)) * 100:+.2f}%"
    )

    gainers = market_snapshot.get("gainers", []) or []
    losers = market_snapshot.get("losers", []) or []
    gainers_block = "\n".join(
        [f"- {x.get('symbol')}: {float(x.get('ret_1d', 0.0)) * 100:+.2f}%" for x in gainers]
    ) or "- 无"
    losers_block = "\n".join(
        [f"- {x.get('symbol')}: {float(x.get('ret_1d', 0.0)) * 100:+.2f}%" for x in losers]
    ) or "- 无"

    news_lines = []
    for idx, item in enumerate(news_items[:6], start=1):
        news_lines.append(
            f"[N{idx}] 标题: {item.title}\n"
            f"      摘要: {item.snippet[:120]}\n"
            f"      链接: {item.url}"
        )
    news_block = "\n".join(news_lines) if news_lines else "无相关新闻"

    return f"""
请基于以下输入生成{market.upper()}市场大盘复盘，要求"客观、可追溯、不过度结论"。

日期: {asof_date}
市场: {market}

基准指数:
{benchmark_block}

样本宽度:
{breadth_block}

样本领涨个股:
{gainers_block}

样本领跌个股:
{losers_block}

新闻证据:
{news_block}

输出要求（必须同时满足）：
1) index_summary：各指数情况汇总，每个指数一行
   格式示例：上证指数 +0.32% 收盘3280点
   有几个指数写几行，数据来自基准指数输入
2) top_gainers_sectors：前10上涨板块，基于样本领涨个股和新闻推断所属板块
   数组第一条必须是：【⚠️ 仅AI推断，未拿到板块接口数据】
   若无法推断则只保留该注明条目
3) top_losers_sectors：前10下跌板块，基于样本领跌个股和新闻推断所属板块
   数组第一条必须是：【⚠️ 仅AI推断，未拿到板块接口数据】
   若无法推断则只保留该注明条目
4) fund_flow：南向/北向资金动向分析，2-3句
   必须在开头注明：【⚠️ 仅AI搜索分析，未拿到接口数据】
   可基于新闻推断，若无依据需明确说明
5) valuation：市场整体历史估值情况，2-3句
   必须在开头注明：【⚠️ 仅AI搜索分析，未拿到接口数据】
   可引用沪深300/标普500等常见估值区间做参考，若无依据需明确说明
6) summary：整体一句话总结，不超过50字，包含市场风险偏好判断与不确定性提示

仅输出 JSON，格式如下：
{{
  "index_summary": "上证指数 +0.32% 收盘3280点\\n深证成指 -0.10% 收盘10200点",
  "top_gainers_sectors": ["【⚠️ 仅AI推断，未拿到板块接口数据】", "新能源", "半导体"],
  "top_losers_sectors": ["【⚠️ 仅AI推断，未拿到板块接口数据】", "房地产", "消费"],
  "fund_flow": "【⚠️ 仅AI搜索分析，未拿到接口数据】...",
  "valuation": "【⚠️ 仅AI搜索分析，未拿到接口数据】...",
  "summary": "整体一句话总结不超过50字"
}}
""".strip()


# ── 汇总提示词 ────────────────────────────────────────────────────

def build_summary_prompt(
    market: str,
    asof_date: str,
    stock_results: list[dict],
) -> str:
    """
    stock_results 格式：
    [
      {
        "symbol": "SZ300750",
        "name": "宁德时代",
        "decision": "买入",
        "trend": "看多",
        "confidence": 72,
        "one_liner": "业绩超预期，技术面偏强",
        "pred_return": 0.002266,
      },
      ...
    ]
    """
    lines = []
    for s in stock_results:
        pred_return_pct = float(s.get("pred_return", 0.0)) * 100
        lines.append(
            f"{s.get('symbol')} | {s.get('name', '')} | "
            f"{s.get('decision')} | {s.get('trend')} | "
            f"置信度{s.get('confidence')}% | "
            f"预测收益{pred_return_pct:+.2f}% | "
            f"{s.get('one_liner', '')}"
        )
    stock_block = "\n".join(lines) if lines else "无股票数据"
    total = len(stock_results)

    return f"""
你是一名严谨的中文股票策略研究员。
基于以下今日所有股票的分析结果，生成投资组合汇总报告。
禁止编造数据，所有结论必须来自以下输入。
输出必须是严格 JSON，不包含 markdown 代码块。

日期: {asof_date}
市场: {market}
股票总数: {total}

今日股票分析结果:
{stock_block}

输出要求（必须同时满足）：
1) overview：股票概览一句话
   格式：共N只，X涨 Y跌 Z观望
   A股习惯：涨用🔴，跌用🟢
   美股/港股：涨用🟢，跌用🔴
2) avg_change：所有股票预测收益的平均值，保留2位小数，带+/-符号
   A股涨为🔴，跌为🟢；美股/港股涨为🟢，跌为🔴
3) median_change：所有股票预测收益的中位数，保留2位小数，带+/-符号
   emoji规则同上
4) stock_list：每只股票一行简要总结，数组格式
   每行格式：代码 股票名 决策emoji 一句话总结
   A股决策emoji：买入=🔴 观望=⚪ 减仓/卖出=🟢
   美股/港股决策emoji：买入=🟢 观望=⚪ 减仓/卖出=🔴
5) top_pick：今日最值得关注的1只股票代码+名称及理由，1-2句话
   必须从stock_list中选择，不可凭空捏造

仅输出 JSON，格式如下：
{{
  "overview": "共3只，2涨🔴 1跌🟢",
  "avg_change": "🔴 +1.23%",
  "median_change": "🔴 +0.98%",
  "stock_list": [
    "300750 宁德时代 🔴买入 业绩超预期，技术面偏强",
    "600519 贵州茅台 ⚪观望 估值偏高，等待回调"
  ],
  "top_pick": "今日最关注宁德时代(300750)，业绩超预期叠加资金流入信号明确。"
}}
""".strip()
	
