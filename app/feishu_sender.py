"""
飞书机器人推送模块 v3
推送结构：
  1. 汇总卡片（置顶，所有股票一览）
  2. 每只股票一张卡片（去掉数据透视/技术快照/证据引用等冗余段落）
  3. 大盘复盘卡片
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

MAX_CARD_CHARS = 3000

MARKET_LABEL = {"cn": "🇨🇳 A股", "us": "🇺🇸 美股", "hk": "🇭🇰 港股"}

# 需要过滤掉的段落（按段落标题匹配）
_SKIP_SECTIONS = [
    r"^#{1,3}\s*📊\s*数据透视",
    r"^#{1,3}\s*技术面快照",
    r"^#{1,3}\s*证据引用",
    r"^#{1,3}\s*数据可靠性说明",
    r"^#{1,3}\s*📢\s*最新动态",  # 原始新闻标题列表，可读性差
]


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


# ── Markdown 格式转换 ─────────────────────────────────────────────

def _to_feishu_md(text: str) -> str:
    """
    标准 Markdown → 飞书卡片兼容格式
    # 标题  →  **标题**
    ---     →  ────────────────────
    * / -   →  •
    """
    lines = text.splitlines()
    out = []
    for line in lines:
        # 标题 → 加粗
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            out.append(f"\n**{m.group(1).strip()}**")
            continue
        # 分隔线
        if re.match(r"^-{3,}$", line.strip()):
            out.append("────────────────────")
            continue
        # 无序列表
        m = re.match(r"^(\s*)[\*\-]\s+(.*)", line)
        if m:
            indent = "　" * (len(m.group(1)) // 2)
            out.append(f"{indent}• {m.group(2)}")
            continue
        out.append(line)
    return "\n".join(out).strip()


# ── 内容过滤 ──────────────────────────────────────────────────────

def _should_skip(header_line: str) -> bool:
    for pat in _SKIP_SECTIONS:
        if re.match(pat, header_line.strip()):
            return True
    return False


def _filter_sections(text: str) -> str:
    """按段落标题过滤掉冗余内容"""
    # 在每个段落标题前插入分隔符方便切割
    sections = re.split(r"(?=\n#{1,3} )", "\n" + text)
    kept = []
    for sec in sections:
        lines = sec.strip().splitlines()
        if not lines:
            continue
        if _should_skip(lines[0]):
            continue
        kept.append(sec.strip())
    return "\n\n".join(kept)


# ── 内容解析 ──────────────────────────────────────────────────────

def _split_stocks_and_market(details: str) -> tuple[list[tuple[str, str]], Optional[tuple[str, str]]]:
    """
    将 details.md 拆分为：
    - stocks: [(股票标题, 内容), ...]
    - market_block: (标题, 内容) 或 None
    """
    parts = re.split(r"(?=\n# |\n## )", "\n" + details)
    stocks = []
    market_block = None

    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.splitlines()
        title = lines[0].lstrip("#").strip()
        content = "\n".join(lines[1:]).strip()
        if not title:
            continue

        if "MARKET" in title or "大盘" in title or title.lower() == "market":
            market_block = (title, content)
        else:
            stocks.append((title, content))

    return stocks, market_block


def _card_color(content: str) -> str:
    """根据内容判断卡片颜色"""
    if any(k in content for k in ["买入", "看多", "BUY", "bullish"]):
        return "green"
    if any(k in content for k in ["卖出", "看空", "SELL", "bearish"]):
        return "red"
    return "blue"


# ── 主推送入口 ────────────────────────────────────────────────────

def send_report_to_feishu(
    summary: str,
    details: str,
    market: str,
    date: str,
    webhook: Optional[str] = None,
) -> None:
    """
    推送结构：
      第1条 - 汇总卡片（今日所有股票一览，置顶）
      第2~N条 - 每只股票一张卡片（过滤冗余段落）
      最后1条 - 大盘复盘卡片
    """
    hook = webhook or FEISHU_WEBHOOK
    if not hook:
        raise ValueError("未配置 FEISHU_WEBHOOK，请在环境变量或 GitHub Secrets 中设置")

    label = MARKET_LABEL.get(market, market.upper())
    stocks, market_block = _split_stocks_and_market(details)
    total = len(stocks)

    # ── 第1条：汇总卡片（置顶）────────────────────────────────────
    summary_filtered = _filter_sections(summary)
    _send_card(
        title=f"📊 {label} 今日晨报 · {date}  共{total}只",
        content_md=summary_filtered,
        webhook=hook,
        color="blue",
    )
    logger.info("飞书汇总卡片已发送")
    time.sleep(0.5)

    # ── 第2~N条：每只股票一张卡片 ─────────────────────────────────
    for i, (title, content) in enumerate(stocks, 1):
        filtered = _filter_sections(content)
        color = _card_color(content)
        _send_card(
            title=f"{label} [{i}/{total}] {title} · {date}",
            content_md=filtered,
            webhook=hook,
            color=color,
        )
        logger.info("飞书股票卡片已发送 [%d/%d]: %s", i, total, title)
        time.sleep(0.5)

    # ── 最后1条：大盘复盘 ─────────────────────────────────────────
    if market_block:
        _, content = market_block
        filtered = _filter_sections(content)
        _send_card(
            title=f"🌍 {label} 大盘复盘 · {date}",
            content_md=filtered,
            webhook=hook,
            color="purple",
        )
        logger.info("飞书大盘复盘已发送")

    logger.info("✅ %s %s 飞书推送完成，共 %d 条消息", label, date, 1 + total + (1 if market_block else 0))


# ── 本地测试 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    test_webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if not test_webhook:
        print("❌ 请先设置环境变量 FEISHU_WEBHOOK")
        sys.exit(1)

    test_summary = """# 📊 今日摘要
- **宁德时代 SZ300750**：🟢 买入 | 看多 | 评分50
- **贵州茅台 SH600519**：🔵 持有 | 中性 | 评分48
> 以上为 AI 辅助分析，不构成投资建议"""

    test_details = """# SZ300750 宁德时代
## 📰 重要信息速览
- 💭 一句话判断: 技术面中性偏强，业绩超预期支撑看多
### ✨ 利好催化
- 2025年归母净利润同比+42%超预期
- 全球动力电池市占率稳居第一
### 🚨 风险警报
- 2026年增速放缓风险
- 当前估值PE约28x高于近3年中位数
## 📊 数据透视
- 量化分数: 0.0000（此段应被过滤）
## 技术面快照
- RSI14: 50（此段应被过滤）
## 💡 详细推理
技术面：MACD为正（4.84）显示短期动能偏多，RSI14=50处于中性区间。
消息面：2025年财报净利润同比+42%，显著超预期。
风险控制：若收盘连续两日跌破340元，需重新评估多头逻辑。
## 🎯 作战计划
- 空仓者: 关注放量与趋势确认后再行动
- 持仓者: 走势偏离看多预期时优先执行风险控制
---
# MARKET 大盘复盘
## 🇨🇳 A股
- 上证指数：+0.32%，成交额8200亿，北向资金净流入12亿
## 🌍 外围市场
- 纳斯达克：+1.2%，AI板块领涨"""

    send_report_to_feishu(test_summary, test_details, market="cn", date="2026-04-10", webhook=test_webhook)
    print("✅ 测试推送完成，请检查飞书群")
