"""
飞书机器人推送模块 v2
- 使用飞书卡片格式，Markdown 正确渲染加粗/分段
- 按股票逐条发卡片，每只股票一张，避免内容堆叠
- 自动过滤冗余的原始技术指标数据段落
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

# 飞书单张卡片内容最大字符数
MAX_BLOCK_CHARS = 3000

MARKET_LABEL = {"cn": "🇨🇳 A股", "us": "🇺🇸 美股", "hk": "🇭🇰 港股"}

# 过滤掉的冗余段落（原始技术指标数据）
_SKIP_SECTION_PATTERNS = [
    r"^#{1,3}\s*技术面快照",
    r"^#{1,3}\s*证据引用",
    r"^#{1,3}\s*数据可靠性说明",
    r"^#{1,3}\s*📊\s*数据透视",
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
    """发送飞书消息卡片，内容支持飞书 Markdown"""
    content_md = _md_to_feishu(content_md)
    if len(content_md) > MAX_BLOCK_CHARS:
        content_md = content_md[:MAX_BLOCK_CHARS] + "\n\n…（内容过长已截断）"
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

def _md_to_feishu(text: str) -> str:
    """
    标准 Markdown → 飞书卡片兼容格式：
    # 标题  →  **标题**
    ---     →  ────────────
    * / -   →  •
    """
    lines = text.splitlines()
    result = []
    for line in lines:
        # 标题转加粗
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            result.append(f"**{m.group(1).strip()}**")
            continue
        # 分隔线
        if re.match(r"^-{3,}$", line.strip()):
            result.append("────────────────────")
            continue
        # 无序列表
        m = re.match(r"^(\s*)[\*\-]\s+(.*)", line)
        if m:
            indent = "　" * (len(m.group(1)) // 2)
            result.append(f"{indent}• {m.group(2)}")
            continue
        result.append(line)
    return "\n".join(result)


# ── 内容过滤与解析 ────────────────────────────────────────────────

def _should_skip_section(header_line: str) -> bool:
    for pat in _SKIP_SECTION_PATTERNS:
        if re.match(pat, header_line.strip()):
            return True
    return False


def _filter_content(text: str) -> str:
    """过滤掉冗余的技术指标数据段落"""
    sections = re.split(r"(?=\n#{1,3} )", "\n" + text)
    kept = []
    for sec in sections:
        lines = sec.strip().splitlines()
        if not lines:
            continue
        if _should_skip_section(lines[0]):
            continue
        kept.append(sec.strip())
    return "\n\n".join(kept)


def _split_by_stock(details: str) -> list[tuple[str, str]]:
    """将 details.md 按股票切分，返回 [(标题, 内容), ...]"""
    parts = re.split(r"(?=\n# |\n## )", "\n" + details)
    blocks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.splitlines()
        title_line = lines[0].lstrip("#").strip()
        content = "\n".join(lines[1:]).strip()
        if title_line:
            blocks.append((title_line, content))
    return blocks


# ── 主推送入口 ────────────────────────────────────────────────────

def send_report_to_feishu(
    summary: str,
    details: str,
    market: str,
    date: str,
    webhook: Optional[str] = None,
) -> None:
    """
    主推送入口：
    1. 摘要卡片（蓝色）
    2. 每只股票单独一张卡片（看多=绿/看空=红/中性=蓝）
    3. 大盘复盘卡片（紫色）
    """
    hook = webhook or FEISHU_WEBHOOK
    if not hook:
        raise ValueError("未配置 FEISHU_WEBHOOK，请在环境变量或 GitHub Secrets 中设置")

    label = MARKET_LABEL.get(market, market.upper())

    # 第1条：摘要
    _send_card(
        title=f"📊 {label} 晨报摘要 · {date}",
        content_md=_filter_content(summary),
        webhook=hook,
        color="blue",
    )
    logger.info("飞书摘要已发送")
    time.sleep(0.5)

    # 第2条起：按股票逐条发送
    stock_blocks = _split_by_stock(details)
    market_block = None

    for title, content in stock_blocks:
        # 大盘复盘最后发
        if "MARKET" in title or "大盘" in title or "market" in title.lower():
            market_block = (title, content)
            continue

        color = "blue"
        if any(k in content for k in ["买入", "看多", "BUY", "bullish"]):
            color = "green"
        elif any(k in content for k in ["卖出", "看空", "SELL", "bearish"]):
            color = "red"

        _send_card(
            title=f"{label} · {title} · {date}",
            content_md=_filter_content(content),
            webhook=hook,
            color=color,
        )
        logger.info("飞书股票卡片已发送: %s", title)
        time.sleep(0.5)

    # 最后：大盘复盘
    if market_block:
        title, content = market_block
        _send_card(
            title=f"🌍 {label} 大盘复盘 · {date}",
            content_md=_filter_content(content),
            webhook=hook,
            color="purple",
        )
        logger.info("飞书大盘复盘已发送")

    logger.info("✅ %s %s 飞书推送完成", label, date)


# ── 本地测试 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    test_webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if not test_webhook:
        print("❌ 请先设置环境变量 FEISHU_WEBHOOK")
        sys.exit(1)

    test_summary = """# 📊 今日摘要
## 关注股票
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
## 💡 详细推理
技术面：MACD为正（4.84）显示短期动能偏多，RSI14=50处于中性区间。
消息面：2025年财报净利润同比+42%，显著超预期。
## 🎯 作战计划
- 空仓者: 关注放量与趋势确认后再行动
- 持仓者: 走势偏离看多预期时优先执行风险控制
---
# MARKET 大盘复盘
## 🇨🇳 A股
- 上证指数：+0.32%，成交额8200亿，北向资金净流入12亿"""

    send_report_to_feishu(test_summary, test_details, market="cn", date="2026-04-10", webhook=test_webhook)
    print("✅ 测试推送完成，请检查飞书群")
