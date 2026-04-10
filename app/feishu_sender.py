"""
飞书机器人推送模块 v4
推送结构：
  1. 汇总卡片（今日所有股票概览）
  2. 每只股票一张卡片（新字段格式）
  3. 大盘复盘卡片
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
import requests
from typing import Any, Optional

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
MAX_CARD_CHARS = 3000
MARKET_LABEL = {"cn": "🇨🇳 A股", "us": "🇺🇸 美股", "hk": "🇭🇰 港股"}


# ── 底层发送 ──────────────────────────────────────────────────────

def _post(webhook: str, payload: dict) -> bool:
    try:
        resp = requests.post(
            webhook,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False),
            timeout=15,
        )
        result = resp.json()
        if result.get("code", -1) != 0:
            logger.error("飞书推送失败 code=%s msg=%s", result.get("code"), result.get("msg"))
            return False
        return True
    except Exception as exc:
        logger.error("飞书推送异常: %s", exc)
        return False


def _send_card(title: str, content_md: str, webhook: str, color: str = "blue") -> bool:
    """发送飞书消息卡片"""
    content_md = _to_feishu_md(content_md)
    if len(content_md) > MAX_CARD_CHARS:
        content_md = content_md[:MAX_CARD_CHARS] + "\n\n…（内容过长已截断）"
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [
                {"tag": "markdown", "content": content_md}
            ],
        },
    }
    return _post(webhook, payload)


# ── Markdown 转换 ─────────────────────────────────────────────────

def _to_feishu_md(text: str) -> str:
    """标准 Markdown → 飞书卡片兼容格式"""
    lines = text.splitlines()
    out = []
    for line in lines:
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            out.append(f"\n**{m.group(1).strip()}**")
            continue
        if re.match(r"^-{3,}$", line.strip()):
            out.append("────────────────────")
            continue
        m = re.match(r"^(\s*)[\*\-]\s+(.*)", line)
        if m:
            indent = "　" * (len(m.group(1)) // 2)
            out.append(f"{indent}• {m.group(2)}")
            continue
        out.append(line)
    return "\n".join(out).strip()


# ── 卡片内容构建 ──────────────────────────────────────────────────

def _build_summary_card(
    summary_data: dict[str, Any],
    market: str,
    date: str,
) -> str:
    """构建汇总卡片内容"""
    overview = summary_data.get("overview", "暂无数据")
    avg_change = summary_data.get("avg_change", "N/A")
    median_change = summary_data.get("median_change", "N/A")
    stock_list = summary_data.get("stock_list", [])
    top_pick = summary_data.get("top_pick", "")

    lines = [
        f"**{overview}**",
        f"平均涨跌幅：{avg_change}　中位涨跌幅：{median_change}",
        "",
        "**个股一览**",
    ]
    for item in stock_list:
        lines.append(f"• {item}")

    if top_pick:
        lines.extend(["", "**今日重点关注**", f"• {top_pick}"])

    lines.extend(["", f"*{date} · 仅供参考，不构成投资建议*"])
    return "\n".join(lines)


def _build_stock_card(
    narrative: Any,
    prediction: Any,
    market: str,
    date: str,
) -> str:
    """构建单只股票卡片内容"""
    is_cn = market == "cn"

    decision = narrative.decision or "观望"
    if decision == "买入":
        d_emoji = "🔴" if is_cn else "🟢"
    elif decision in ["卖出", "减仓"]:
        d_emoji = "🟢" if is_cn else "🔴"
    else:
        d_emoji = "⚪"

    trend = narrative.trend or "震荡"
    confidence = narrative.confidence or 50
    latest_close = narrative.latest_close or 0.0

    lines = [
        "**信息概览**",
        f"• 一句话总结：{narrative.one_liner or narrative.summary}",
        f"• 最新收盘：{latest_close:.2f}",
        f"• 估值分析：{narrative.valuation_analysis or '暂无'}",
        f"• 资金面：{narrative.fund_flow_analysis or '暂无'}",
        "",
    ]

    catalysts = narrative.catalysts or []
    if catalysts:
        lines.append("**利好催化** 🔴" if is_cn else "**利好催化** 🟢")
        for c in catalysts:
            lines.append(f"• {c}")
        lines.append("")

    risks = narrative.risks or narrative.risk_points or []
    if risks:
        lines.append("**利空风险** 🟢" if is_cn else "**利空风险** 🔴")
        for r in risks:
            lines.append(f"• {r}")
        lines.append("")

    lines.extend([
        "**核心结论**",
        narrative.core_conclusion or "暂无",
        "",
        "**详细推理**",
    ])

    if narrative.fund_analysis:
        lines.extend([f"*资金面*：{narrative.fund_analysis}", ""])
    if narrative.news_analysis:
        lines.extend([f"*消息面*：{narrative.news_analysis}", ""])
    if narrative.policy_analysis:
        lines.extend([f"*政策面*：{narrative.policy_analysis}", ""])

    lines.extend([
        "**结论**",
        f"• 方向：{trend}　决策：{d_emoji}{decision}　置信度：{confidence}/100",
        "",
        f"*{date} · 仅供参考，不构成投资建议*",
    ])

    return "\n".join(lines)


def _build_market_card(
    market_narrative: Any,
    market: str,
    date: str,
) -> str:
    """构建大盘复盘卡片内容"""
    lines = []

    index_summary = getattr(market_narrative, "index_summary", "") or ""
    if index_summary:
        lines.append("**指数情况**")
        for idx_line in index_summary.splitlines():
            if idx_line.strip():
                lines.append(f"• {idx_line.strip()}")
        lines.append("")

    gainers = getattr(market_narrative, "top_gainers_sectors", []) or []
    if gainers:
        lines.append("**上涨板块**")
        for s in gainers:
            lines.append(f"• {s}")
        lines.append("")

    losers = getattr(market_narrative, "top_losers_sectors", []) or []
    if losers:
        lines.append("**下跌板块**")
        for s in losers:
            lines.append(f"• {s}")
        lines.append("")

    fund_flow = getattr(market_narrative, "fund_flow", "") or ""
    if fund_flow:
        lines.extend(["**市场资金面**", fund_flow, ""])

    valuation = getattr(market_narrative, "valuation", "") or ""
    if valuation:
        lines.extend(["**市场历史估值**", valuation, ""])

    summary = getattr(market_narrative, "summary", "") or ""
    if summary:
        lines.extend(["**今日总结**", summary, ""])

    lines.append(f"*{date} · 仅供参考，不构成投资建议*")
    return "\n".join(lines)


def _stock_card_color(narrative: Any, market: str) -> str:
    decision = (narrative.decision or "").strip()
    is_cn = market == "cn"
    if decision == "买入":
        return "red" if is_cn else "green"
    if decision in ["卖出", "减仓"]:
        return "green" if is_cn else "red"
    return "blue"


# ── 主推送入口 ────────────────────────────────────────────────────

def send_report_to_feishu(
    summary_data: dict[str, Any],
    narratives: dict[str, Any],
    predictions: list[Any],
    market_narrative: Any | None,
    market: str,
    date: str,
    webhook: Optional[str] = None,
) -> None:
    """
    推送结构：
      第1条 - 汇总卡片
      第2~N条 - 每只股票一张卡片
      最后1条 - 大盘复盘卡片
    """
    hook = webhook or FEISHU_WEBHOOK
    if not hook:
        raise ValueError("未配置 FEISHU_WEBHOOK，请在环境变量或 GitHub Secrets 中设置")

    label = MARKET_LABEL.get(market, market.upper())
    total = len(predictions)

    # 第1条：汇总
    _send_card(
        title=f"📊 {label} 今日晨报 · {date}  共{total}只",
        content_md=_build_summary_card(summary_data, market, date),
        webhook=hook,
        color="blue",
    )
    logger.info("飞书汇总卡片已发送")
    time.sleep(0.5)

    # 第2~N条：每只股票
    for i, pred in enumerate(predictions, 1):
        symbol = pred.symbol
        narrative = narratives.get(symbol)
        if not narrative:
            continue
        _send_card(
            title=f"{label} [{i}/{total}] {symbol} · {date}",
            content_md=_build_stock_card(narrative, pred, market, date),
            webhook=hook,
            color=_stock_card_color(narrative, market),
        )
        logger.info("飞书股票卡片已发送 [%d/%d]: %s", i, total, symbol)
        time.sleep(0.5)

    # 最后1条：大盘复盘
    if market_narrative:
        _send_card(
            title=f"🌍 {label} 大盘复盘 · {date}",
            content_md=_build_market_card(market_narrative, market, date),
            webhook=hook,
            color="purple",
        )
        logger.info("飞书大盘复盘已发送")

    total_msgs = 1 + total + (1 if market_narrative else 0)
    logger.info("✅ %s %s 飞书推送完成，共 %d 条消息", label, date, total_msgs)
