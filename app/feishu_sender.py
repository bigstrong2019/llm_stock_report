"""
飞书机器人推送模块
替换原项目的 Telegram 推送，适配飞书群机器人 Webhook。
使用方式：在原项目推送入口处 import 并调用 send_report_to_feishu()
"""

import os
import json
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

# 飞书单条消息最大字符数（保守值）
MAX_CHARS = 4000


def _send_single(text: str, webhook: str) -> bool:
    """发送单条飞书文本消息"""
    payload = {
        "msg_type": "text",
        "content": {"text": text}
    }
    try:
        resp = requests.post(
            webhook,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=15
        )
        result = resp.json()
        if result.get("code", -1) != 0:
            logger.error(f"飞书推送失败: {result}")
            return False
        return True
    except Exception as e:
        logger.error(f"飞书推送异常: {e}")
        return False


def _send_markdown(title: str, content: str, webhook: str) -> bool:
    """
    发送飞书富文本卡片（card 格式，支持 Markdown 加粗/链接/换行）
    飞书卡片 Markdown 支持：**加粗**、[链接](url)、> 引用
    """
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content[:MAX_CHARS]  # 飞书卡片内容上限
                }
            ]
        }
    }
    try:
        resp = requests.post(
            webhook,
            headers={"Content-Type": "application/json"},
            data=json.dumps(card),
            timeout=15
        )
        result = resp.json()
        if result.get("code", -1) != 0:
            logger.error(f"飞书卡片推送失败: {result}")
            return False
        return True
    except Exception as e:
        logger.error(f"飞书卡片推送异常: {e}")
        return False


def _chunk_text(text: str, max_len: int = MAX_CHARS) -> list[str]:
    """将长文本按段落切分，避免超出飞书单条消息限制"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


def send_report_to_feishu(
    summary: str,
    details: str,
    market: str,
    date: str,
    webhook: Optional[str] = None
) -> None:
    """
    主推送入口，对应原项目 Telegram 推送逻辑：
    1. 发送摘要卡片
    2. 按段落分块发送详细内容
    3. 发送大盘复盘

    参数：
        summary  - summary.md 文件内容
        details  - details.md 文件内容
        market   - 市场标识，如 cn / us / hk
        date     - 日期字符串，如 2026-04-09
        webhook  - 可选，若不传则读取环境变量 FEISHU_WEBHOOK
    """
    hook = webhook or FEISHU_WEBHOOK
    if not hook:
        raise ValueError("未配置 FEISHU_WEBHOOK，请在环境变量或 GitHub Secrets 中设置")

    market_label = {"cn": "🇨🇳 A股", "us": "🇺🇸 美股", "hk": "🇭🇰 港股"}.get(market, market.upper())

    # ── 第1条：摘要卡片 ──────────────────────────────────────────
    title = f"📊 {market_label} 股票晨报 · {date}"
    _send_markdown(title, summary, hook)
    logger.info("飞书摘要卡片已发送")

    # ── 第2条起：详细内容分块 ────────────────────────────────────
    chunks = _chunk_text(details)
    for i, chunk in enumerate(chunks, 1):
        part_title = f"📋 {market_label} 详细报告 ({i}/{len(chunks)}) · {date}"
        _send_markdown(part_title, chunk, hook)
        logger.info(f"飞书详细内容第 {i}/{len(chunks)} 块已发送")

    logger.info(f"✅ {market_label} {date} 飞书推送完成，共 {1 + len(chunks)} 条消息")


# ── 集成示例（替换原项目 run_report.py 中的 Telegram 调用）──────
#
# 原项目大概是这样调用 Telegram 的：
#   send_telegram(summary_text, details_text)
#
# 替换为飞书只需：
#   from feishu_sender import send_report_to_feishu
#   send_report_to_feishu(summary_text, details_text, market="cn", date="2026-04-09")
#
# ────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    # 本地测试：python feishu_sender.py
    import sys

    test_webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if not test_webhook:
        print("❌ 请先设置环境变量 FEISHU_WEBHOOK")
        sys.exit(1)

    test_summary = """**今日摘要**
- 贵州茅台(600519)：量化预测 **上涨** 概率 62%，近期资金净流入
- 腾讯控股(00700)：情绪中性，建议观望
- META：技术指标偏强，关注财报后走势
> 以上为 AI 辅助分析，不构成投资建议"""

    test_details = """## 贵州茅台 600519
**预测方向**：上涨（置信度 62%）
**关键信号**：
- MACD 金叉，RSI=54 处于健康区间
- 近5日主力净流入 3.2 亿元
- 无重大负面公告

## 腾讯控股 00700
**预测方向**：震荡（置信度 51%）
**关键信号**：
- 港股通近日净流出
- 游戏版号政策不确定性仍存

---
## 🌐 大盘复盘
**上证指数**：+0.32%，成交额 8200 亿，北向资金净流入 12 亿
**恒生指数**：-0.8%，科技股承压
**纳斯达克**：+1.2%，AI板块领涨"""

    send_report_to_feishu(test_summary, test_details, market="cn", date="2026-04-09", webhook=test_webhook)
    print("✅ 测试推送完成，请检查飞书群")
