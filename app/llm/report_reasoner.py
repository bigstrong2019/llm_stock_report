from __future__ import annotations

import logging
from typing import Any

from app.common.decision_policy import baseline_decision, baseline_trend, calibrate_decision
from app.common.schemas import MarketNarrative, NewsItem, PredictionRecord, StockNarrative
from app.llm.base import LLMClient, LLMError
from app.llm.prompts import (
    build_market_reasoning_prompt,
    build_stock_reasoning_prompt,
    build_summary_prompt,
    get_system_prompt,
)

logger = logging.getLogger(__name__)


# ── 标准化辅助函数（保持不变）────────────────────────────────────

def _normalize_bias(raw_bias: str, prediction: PredictionRecord) -> str:
    canonical = {
        "偏多": "偏多", "bullish": "偏多", "long": "偏多",
        "中性": "中性", "neutral": "中性",
        "偏空": "偏空", "bearish": "偏空", "short": "偏空",
    }
    bias = canonical.get((raw_bias or "").strip().lower()) or canonical.get((raw_bias or "").strip())
    if bias:
        return bias
    if prediction.side == "top":
        return "偏多"
    if prediction.side == "bottom":
        return "偏空"
    return "中性"


def _normalize_market_bias(raw_bias: str) -> str:
    canonical = {
        "偏多": "偏多", "bullish": "偏多", "long": "偏多",
        "中性": "中性", "neutral": "中性",
        "偏空": "偏空", "bearish": "偏空", "short": "偏空",
    }
    return canonical.get((raw_bias or "").strip().lower()) or canonical.get((raw_bias or "").strip(), "中性")


def _clamp_confidence(value: Any) -> int:
    try:
        conf = int(value)
    except Exception:
        return 50
    return max(0, min(100, conf))


def _normalize_decision(raw: str, prediction: PredictionRecord) -> str:
    canonical = {
        "买入": "买入", "buy": "买入",
        "观望": "观望", "hold": "观望",
        "减仓": "减仓", "trim": "减仓",
        "卖出": "卖出", "sell": "卖出",
        "卖出/观望": "卖出/观望", "sell/hold": "卖出/观望",
    }
    value = canonical.get((raw or "").strip().lower()) or canonical.get((raw or "").strip())
    if value:
        return value
    return baseline_decision(prediction)


def _normalize_trend(raw: str, prediction: PredictionRecord) -> str:
    canonical = {
        "看多": "看多", "bullish": "看多",
        "震荡": "震荡", "sideways": "震荡",
        "看空": "看空", "bearish": "看空",
        "强烈看空": "强烈看空", "strong bearish": "强烈看空",
    }
    value = canonical.get((raw or "").strip().lower()) or canonical.get((raw or "").strip())
    if value:
        return value
    return baseline_trend(prediction)


def _normalize_urgency(raw: str, confidence: int) -> str:
    canonical = {
        "高": "高", "high": "高",
        "中": "中", "medium": "中",
        "低": "低", "low": "低",
    }
    value = canonical.get((raw or "").strip().lower()) or canonical.get((raw or "").strip())
    if value:
        return value
    if confidence >= 75:
        return "高"
    if confidence <= 45:
        return "低"
    return "中"


def _market_label(market: str) -> str:
    return {"cn": "A股", "us": "美股", "hk": "港股"}.get((market or "").strip().lower(), market.upper())


def _market_mood_text(market_snapshot: dict[str, Any]) -> str:
    anchor_ret = float(market_snapshot.get("avg_ret_1d", 0.0) or 0.0)
    benches = market_snapshot.get("benchmarks") or []
    if isinstance(benches, list) and benches:
        try:
            anchor_ret = float((benches[0] or {}).get("ret_1d", anchor_ret) or anchor_ret)
        except Exception:
            pass
    if anchor_ret >= 0.01:
        return "强势上涨"
    if anchor_ret >= 0.002:
        return "小幅上涨"
    if anchor_ret <= -0.01:
        return "明显下跌"
    if anchor_ret <= -0.002:
        return "小幅下跌"
    return "震荡整理"


# ── 兜底函数 ──────────────────────────────────────────────────────

def _default_fallback(
    symbol: str,
    prediction: PredictionRecord,
    provider: str,
    *,
    reason: str | None = None,
) -> StockNarrative:
    reason_text = (reason or "").strip()
    summary = (
        f"{symbol} 预测分数 {prediction.score:.2f}，"
        f"模型信号为{prediction.side}，请结合风险控制审慎评估（已启用模板兜底）。"
    )
    details = (
        "## 信息概览\n"
        "- 一句话总结：模型输出来自量化因子排序，LLM解析失败，已启用兜底模板\n"
        "- 估值分析：【⚠️ 仅AI搜索分析，未拿到接口数据】暂无\n"
        "- 资金面：【⚠️ 仅AI搜索分析，未拿到接口数据】暂无\n\n"
        "## 核心结论\n"
        "新闻证据不足或解析失败时不建议单独依赖该信号。\n"
        "关注量能变化、MA20趋势完整性与事件催化带来的波动。\n\n"
        "## 详细推理\n"
        "### 资金面分析\n"
        "【⚠️ 仅AI搜索分析，未拿到接口数据】暂无资金面数据。\n"
        "### 消息面分析\n"
        "新闻证据不足，结论仅基于技术面。\n"
        "### 行业政策面\n"
        "【⚠️ 仅AI搜索分析，未拿到接口数据】暂无政策面数据。\n"
    )
    if reason_text:
        details += f"\n兜底原因: {reason_text[:180]}"

    return StockNarrative(
        symbol=symbol,
        summary=summary,
        details=details,
        used_provider=f"{(provider or 'none').strip()}+template",
        news_items=[],
        decision=_normalize_decision("", prediction),
        trend=_normalize_trend("", prediction),
        urgency="中",
        confidence=50,
        risk_points=[],
        catalysts=[],
        evidence_used=[],
        reliability_notes=["新闻证据不足或LLM解析失败"],
        # 新字段兜底值
        one_liner=f"{symbol} 模型信号{prediction.side}，LLM解析失败，请审慎评估",
        valuation_analysis="【⚠️ 仅AI搜索分析，未拿到接口数据】暂无估值数据",
        fund_flow_analysis="【⚠️ 仅AI搜索分析，未拿到接口数据】暂无资金面数据",
        risks=[],
        core_conclusion="LLM解析失败，已启用兜底模板，请结合风险控制审慎评估",
        fund_analysis="【⚠️ 仅AI搜索分析，未拿到接口数据】暂无资金面数据",
        news_analysis="新闻证据不足，结论仅基于技术面",
        policy_analysis="【⚠️ 仅AI搜索分析，未拿到接口数据】暂无政策面数据",
    )


def _default_market_fallback(
    market: str,
    provider: str,
    *,
    asof_date: str | None = None,
    market_snapshot: dict[str, Any] | None = None,
    news_items: list[NewsItem] | None = None,
    reason: str | None = None,
) -> MarketNarrative:
    snapshot = market_snapshot or {}
    news = list(news_items or [])[:3]
    up = int(snapshot.get("up_count", 0) or 0)
    down = int(snapshot.get("down_count", 0) or 0)
    flat = int(snapshot.get("flat_count", 0) or 0)
    avg_ret_1d = float(snapshot.get("avg_ret_1d", 0.0) or 0.0)
    median_ret_1d = float(snapshot.get("median_ret_1d", 0.0) or 0.0)
    mood = _market_mood_text(snapshot)
    label = _market_label(market)
    reason_text = (reason or "").strip()
    date_text = asof_date or ""

    summary = (
        f"{label}市场呈现{mood}，市场宽度上涨{up}/下跌{down}/平盘{flat}，"
        f"样本1日均值{avg_ret_1d * 100:+.2f}%；已启用模板复盘兜底。"
    )

    # 指数行情
    benches = snapshot.get("benchmarks") or []
    index_lines = []
    if isinstance(benches, list) and benches:
        for item in benches[:4]:
            ret = float(item.get("ret_1d", 0.0)) * 100
            index_lines.append(
                f"{item.get('name')} {ret:+.2f}% 收盘{float(item.get('latest_close', 0.0)):.2f}点"
            )
    index_summary = "\n".join(index_lines) if index_lines else "暂无指数行情数据"

    details = (
        f"## 指数情况\n{index_summary}\n\n"
        f"## 上涨板块\n【⚠️ 仅AI推断，未拿到板块接口数据】暂无板块数据\n\n"
        f"## 下跌板块\n【⚠️ 仅AI推断，未拿到板块接口数据】暂无板块数据\n\n"
        f"## 市场资金面\n【⚠️ 仅AI搜索分析，未拿到接口数据】暂无资金面数据\n\n"
        f"## 市场历史估值\n【⚠️ 仅AI搜索分析，未拿到接口数据】暂无估值数据\n"
    )
    if reason_text:
        details += f"\n兜底触发原因: {reason_text}"

    return MarketNarrative(
        market=market,
        summary=summary,
        details=details,
        used_provider=f"{(provider or 'none').strip()}+template",
        news_items=news,
        # 新字段兜底值
        index_summary=index_summary,
        top_gainers_sectors=["【⚠️ 仅AI推断，未拿到板块接口数据】"],
        top_losers_sectors=["【⚠️ 仅AI推断，未拿到板块接口数据】"],
        fund_flow="【⚠️ 仅AI搜索分析，未拿到接口数据】暂无资金面数据",
        valuation="【⚠️ 仅AI搜索分析，未拿到接口数据】暂无估值数据",
    )


# ── 股票分析生成 ──────────────────────────────────────────────────

def generate_stock_narrative(
    llm_client: LLMClient,
    market: str,
    prediction: PredictionRecord,
    latest_close: float,
    feature_snapshot: dict[str, float],
    news_items: list[NewsItem],
    provider_used: str,
    language: str = "zh",
) -> StockNarrative:
    language = (language or "zh").strip().lower()
    prompt = build_stock_reasoning_prompt(
        market=market,
        symbol=prediction.symbol,
        prediction=prediction,
        latest_close=latest_close,
        feature_snapshot=feature_snapshot,
        news_items=news_items,
        language=language,
    )

    try:
        parsed: dict[str, Any] = llm_client.chat_json(get_system_prompt(language), prompt)
    except LLMError as exc:
        logger.warning("LLM failed for %s, use template fallback: %s", prediction.symbol, exc)
        return _default_fallback(prediction.symbol, prediction, provider_used, reason=str(exc))

    # ── 解析新字段 ────────────────────────────────────────────────
    one_liner = str(parsed.get("one_liner") or "").strip()
    valuation_analysis = str(parsed.get("valuation_analysis") or "").strip()
    fund_flow_analysis = str(parsed.get("fund_flow_analysis") or "").strip()
    catalysts = [str(x).strip() for x in (parsed.get("catalysts") or []) if str(x).strip()][:3]
    risks = [str(x).strip() for x in (parsed.get("risks") or []) if str(x).strip()][:3]
    core_conclusion = str(parsed.get("core_conclusion") or "").strip()
    fund_analysis = str(parsed.get("fund_analysis") or "").strip()
    news_analysis = str(parsed.get("news_analysis") or "").strip()
    policy_analysis = str(parsed.get("policy_analysis") or "").strip()
    trend = _normalize_trend(str(parsed.get("trend") or ""), prediction)
    decision = _normalize_decision(str(parsed.get("decision") or ""), prediction)
    confidence = _clamp_confidence(parsed.get("confidence"))

    # ── 兜底检查 ──────────────────────────────────────────────────
    if not one_liner or not core_conclusion:
        logger.warning("LLM output missing key fields for %s, use fallback", prediction.symbol)
        return _default_fallback(prediction.symbol, prediction, provider_used)

    # ── 推导兼容旧字段（summary / details）供 renderer 使用 ───────
    summary = one_liner

    details = (
        f"## 信息概览\n"
        f"- **一句话总结**：{one_liner}\n"
        f"- **估值分析**：{valuation_analysis}\n"
        f"- **资金面**：{fund_flow_analysis}\n\n"
        f"## 利好催化\n"
        + "\n".join([f"- {c}" for c in catalysts] or ["- 暂无明确利好"]) + "\n\n"
        f"## 利空风险\n"
        + "\n".join([f"- {r}" for r in risks] or ["- 暂无明确利空"]) + "\n\n"
        f"## 核心结论\n{core_conclusion}\n\n"
        f"## 详细推理\n"
        f"### 资金面分析\n{fund_analysis}\n\n"
        f"### 消息面分析\n{news_analysis}\n\n"
        f"### 行业政策面\n{policy_analysis}\n\n"
        f"## 结论\n"
        f"- **方向**：{trend}\n"
        f"- **决策**：{decision}\n"
        f"- **置信度**：{confidence}/100\n"
    )

    # calibrate decision
    action_bias = _normalize_bias("", prediction)
    decision = calibrate_decision(
        prediction=prediction,
        decision=decision,
        action_bias=action_bias,
        confidence=confidence,
    )
    urgency = _normalize_urgency("", confidence)

    return StockNarrative(
        symbol=prediction.symbol,
        summary=summary,
        details=details,
        used_provider=provider_used,
        news_items=news_items,
        decision=decision,
        trend=trend,
        urgency=urgency,
        confidence=confidence,
        risk_points=risks,
        catalysts=catalysts,
        evidence_used=[],
        reliability_notes=[],
        latest_close=latest_close,
        feature_snapshot=feature_snapshot,
        # 新字段
        one_liner=one_liner,
        valuation_analysis=valuation_analysis,
        fund_flow_analysis=fund_flow_analysis,
        risks=risks,
        core_conclusion=core_conclusion,
        fund_analysis=fund_analysis,
        news_analysis=news_analysis,
        policy_analysis=policy_analysis,
    )


# ── 大盘复盘生成 ──────────────────────────────────────────────────

def generate_market_narrative(
    llm_client: LLMClient,
    market: str,
    asof_date: str,
    market_snapshot: dict[str, Any],
    news_items: list[NewsItem],
    provider_used: str,
    language: str = "zh",
) -> MarketNarrative:
    language = (language or "zh").strip().lower()
    prompt = build_market_reasoning_prompt(
        market=market,
        asof_date=asof_date,
        market_snapshot=market_snapshot,
        news_items=news_items,
        language=language,
    )

    try:
        parsed: dict[str, Any] = llm_client.chat_json(get_system_prompt(language), prompt)
    except LLMError as exc:
        logger.warning("Market LLM failed for %s, use template fallback: %s", market, exc)
        return _default_market_fallback(
            market, provider_used,
            asof_date=asof_date,
            market_snapshot=market_snapshot,
            news_items=news_items,
            reason=str(exc),
        )

    # ── 解析新字段 ────────────────────────────────────────────────
    index_summary = str(parsed.get("index_summary") or "").strip()
    top_gainers_sectors = [str(x).strip() for x in (parsed.get("top_gainers_sectors") or []) if str(x).strip()]
    top_losers_sectors = [str(x).strip() for x in (parsed.get("top_losers_sectors") or []) if str(x).strip()]
    fund_flow = str(parsed.get("fund_flow") or "").strip()
    valuation = str(parsed.get("valuation") or "").strip()
    summary = str(parsed.get("summary") or "").strip()

    if not summary or not index_summary:
        logger.warning("Market LLM output missing key fields for %s, use fallback", market)
        return _default_market_fallback(
            market, provider_used,
            asof_date=asof_date,
            market_snapshot=market_snapshot,
            news_items=news_items,
            reason="LLM output missing required fields",
        )

    # ── 推导兼容旧字段 details ────────────────────────────────────
    gainers_text = "\n".join([f"- {s}" for s in top_gainers_sectors]) or "- 暂无数据"
    losers_text = "\n".join([f"- {s}" for s in top_losers_sectors]) or "- 暂无数据"

    details = (
        f"## 指数情况\n{index_summary}\n\n"
        f"## 上涨板块\n{gainers_text}\n\n"
        f"## 下跌板块\n{losers_text}\n\n"
        f"## 市场资金面\n{fund_flow}\n\n"
        f"## 市场历史估值\n{valuation}\n"
    )

    return MarketNarrative(
        market=market,
        summary=summary,
        details=details,
        used_provider=provider_used,
        news_items=news_items,
        # 新字段
        index_summary=index_summary,
        top_gainers_sectors=top_gainers_sectors,
        top_losers_sectors=top_losers_sectors,
        fund_flow=fund_flow,
        valuation=valuation,
    )


# ── 汇总生成 ──────────────────────────────────────────────────────

def generate_summary_narrative(
    llm_client: LLMClient,
    market: str,
    asof_date: str,
    stock_results: list[dict],
) -> dict[str, Any]:
    """
    汇总所有股票分析结果，生成汇总卡片内容。
    返回包含 overview / avg_change / median_change / stock_list / top_pick 的字典。
    """
    if not stock_results:
        return {
            "overview": "今日无股票数据",
            "avg_change": "N/A",
            "median_change": "N/A",
            "stock_list": [],
            "top_pick": "无",
        }

    prompt = build_summary_prompt(
        market=market,
        asof_date=asof_date,
        stock_results=stock_results,
    )

    try:
        parsed: dict[str, Any] = llm_client.chat_json(get_system_prompt("zh"), prompt)
    except LLMError as exc:
        logger.warning("Summary LLM failed for %s: %s, use fallback", market, exc)
        return _default_summary_fallback(market, stock_results)

    overview = str(parsed.get("overview") or "").strip()
    avg_change = str(parsed.get("avg_change") or "").strip()
    median_change = str(parsed.get("median_change") or "").strip()
    stock_list = [str(x).strip() for x in (parsed.get("stock_list") or []) if str(x).strip()]
    top_pick = str(parsed.get("top_pick") or "").strip()

    if not overview:
        return _default_summary_fallback(market, stock_results)

    return {
        "overview": overview,
        "avg_change": avg_change,
        "median_change": median_change,
        "stock_list": stock_list,
        "top_pick": top_pick,
    }


def _default_summary_fallback(market: str, stock_results: list[dict]) -> dict[str, Any]:
    """汇总LLM失败时的兜底"""
    total = len(stock_results)
    buy_count = sum(1 for s in stock_results if s.get("decision") in ["买入"])
    sell_count = sum(1 for s in stock_results if s.get("decision") in ["卖出", "减仓"])
    watch_count = total - buy_count - sell_count

    # 计算平均/中位预测收益
    returns = [float(s.get("pred_return", 0.0)) for s in stock_results]
    avg_ret = sum(returns) / len(returns) if returns else 0.0
    sorted_returns = sorted(returns)
    mid = len(sorted_returns) // 2
    median_ret = sorted_returns[mid] if sorted_returns else 0.0

    is_cn = market == "cn"
    up_emoji = "🔴" if is_cn else "🟢"
    down_emoji = "🟢" if is_cn else "🔴"

    avg_emoji = up_emoji if avg_ret >= 0 else down_emoji
    med_emoji = up_emoji if median_ret >= 0 else down_emoji

    stock_list = []
    for s in stock_results:
        decision = s.get("decision", "观望")
        if is_cn:
            d_emoji = "🔴" if decision == "买入" else ("🟢" if decision in ["卖出", "减仓"] else "⚪")
        else:
            d_emoji = "🟢" if decision == "买入" else ("🔴" if decision in ["卖出", "减仓"] else "⚪")
        stock_list.append(
            f"{s.get('symbol')} {s.get('name', '')} {d_emoji}{decision} {s.get('one_liner', '')}"
        )

    return {
        "overview": f"共{total}只，{buy_count}涨{up_emoji} {sell_count}跌{down_emoji} {watch_count}观望⚪",
        "avg_change": f"{avg_emoji} {avg_ret * 100:+.2f}%",
        "median_change": f"{med_emoji} {median_ret * 100:+.2f}%",
        "stock_list": stock_list,
        "top_pick": "LLM生成失败，请查看各股票详细卡片",
    }
