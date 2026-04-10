"""
AKShare 股票数据补充模块
封装三类免费接口，补充 Tavily 抓不到的数据：
  1. 个股新闻（stock_news_em）
  2. 公司公告（stock_zh_a_disclosure_em）
  3. 资金流向（stock_dzjy_mrmx 大宗交易 + stock_individual_fund_flow）
  4. 估值比较（stock_zh_valuation_comparison_em）

所有接口均来自 AKShare，免费无需 key。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ── 工具函数 ──────────────────────────────────────────────────────

def _safe_import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError:
        logger.error("akshare 未安装，请运行 pip install akshare")
        return None


def _normalize_code(symbol: str) -> str:
    """
    将 SZ300750 / SH600519 格式转换为纯6位代码 300750 / 600519
    港股/美股直接返回原始代码
    """
    s = symbol.upper().strip()
    if s.startswith("SZ") or s.startswith("SH"):
        return s[2:]
    return s


def _days_ago(n: int) -> str:
    """返回 n 天前的日期字符串 YYYY-MM-DD"""
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ── 1. 个股新闻 ───────────────────────────────────────────────────

def fetch_stock_news(symbol: str, max_count: int = 10) -> list[dict[str, str]]:
    """
    拉取东方财富个股新闻
    接口：ak.stock_news_em(symbol)
    返回：[{"title": ..., "content": ..., "publish_time": ..., "url": ...}, ...]
    """
    ak = _safe_import_akshare()
    if not ak:
        return []

    code = _normalize_code(symbol)
    try:
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            logger.warning("stock_news_em 返回空数据 symbol=%s", symbol)
            return []

        results = []
        for _, row in df.head(max_count).iterrows():
            results.append({
                "title": str(row.get("新闻标题", "") or row.get("title", "")),
                "content": str(row.get("新闻内容", "") or row.get("content", ""))[:300],
                "publish_time": str(row.get("发布时间", "") or row.get("publish_time", "")),
                "url": str(row.get("新闻链接", "") or row.get("url", "")),
                "source": "东方财富",
            })
        logger.info("fetch_stock_news symbol=%s count=%d", symbol, len(results))
        return results

    except Exception as exc:
        logger.warning("fetch_stock_news 失败 symbol=%s: %s", symbol, exc)
        return []


# ── 2. 公司公告 ───────────────────────────────────────────────────

def fetch_stock_announcements(symbol: str, days: int = 7) -> list[dict[str, str]]:
    """
    拉取东方财富公司公告（近 N 天）
    接口：ak.stock_zh_a_disclosure_em(symbol, start_date, end_date)
    返回：[{"title": ..., "date": ..., "type": ..., "url": ...}, ...]
    """
    ak = _safe_import_akshare()
    if not ak:
        return []

    code = _normalize_code(symbol)
    start_date = _days_ago(days)
    end_date = _today()

    try:
        df = ak.stock_zh_a_disclosure_em(
            symbol=code,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        )
        if df is None or df.empty:
            logger.info("stock_zh_a_disclosure_em 无公告 symbol=%s", symbol)
            return []

        results = []
        for _, row in df.head(10).iterrows():
            results.append({
                "title": str(row.get("公告标题", "") or row.get("title", "")),
                "date": str(row.get("公告日期", "") or row.get("date", "")),
                "type": str(row.get("公告类型", "") or row.get("type", "")),
                "url": str(row.get("公告链接", "") or row.get("url", "")),
                "source": "巨潮/东方财富",
            })
        logger.info("fetch_stock_announcements symbol=%s count=%d", symbol, len(results))
        return results

    except Exception as exc:
        logger.warning("fetch_stock_announcements 失败 symbol=%s: %s", symbol, exc)
        return []


# ── 3. 资金流向 ───────────────────────────────────────────────────

def fetch_fund_flow(symbol: str) -> dict[str, Any]:
    """
    拉取个股资金流向（近5日）
    接口：ak.stock_individual_fund_flow(stock, market)
    返回：{
        "latest": {"date": ..., "main_net": ..., "super_large_net": ..., "large_net": ...},
        "5day_total_main_net": ...,
        "trend": "流入|流出|震荡",
        "raw": [...]
    }
    """
    ak = _safe_import_akshare()
    if not ak:
        return {}

    code = _normalize_code(symbol)
    # 判断市场
    market = "sh" if code.startswith("6") else "sz"

    try:
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            logger.warning("stock_individual_fund_flow 返回空 symbol=%s", symbol)
            return {}

        # 取最近5日
        recent = df.tail(5).copy()
        rows = []
        for _, row in recent.iterrows():
            rows.append({
                "date": str(row.get("日期", "")),
                "main_net": float(row.get("主力净流入-净额", 0) or 0),
                "super_large_net": float(row.get("超大单净流入-净额", 0) or 0),
                "large_net": float(row.get("大单净流入-净额", 0) or 0),
                "medium_net": float(row.get("中单净流入-净额", 0) or 0),
                "small_net": float(row.get("小单净流入-净额", 0) or 0),
            })

        total_main_net = sum(r["main_net"] for r in rows)
        latest = rows[-1] if rows else {}

        # 判断趋势
        if total_main_net > 5000_0000:  # 5000万
            trend = "主力净流入"
        elif total_main_net < -5000_0000:
            trend = "主力净流出"
        else:
            trend = "资金震荡"

        result = {
            "latest": latest,
            "5day_total_main_net": total_main_net,
            "5day_total_main_net_yi": round(total_main_net / 1e8, 2),  # 转换为亿元
            "trend": trend,
            "raw": rows,
        }
        logger.info("fetch_fund_flow symbol=%s trend=%s total_net=%.2f亿", symbol, trend, total_main_net / 1e8)
        return result

    except Exception as exc:
        logger.warning("fetch_fund_flow 失败 symbol=%s: %s", symbol, exc)
        return {}


def fetch_block_trade(symbol: str, days: int = 7) -> list[dict[str, Any]]:
    """
    拉取大宗交易数据（近 N 天）
    接口：ak.stock_dzjy_mrmx(symbol)
    返回：[{"date": ..., "price": ..., "volume": ..., "amount": ..., "buyer": ..., "seller": ...}, ...]
    """
    ak = _safe_import_akshare()
    if not ak:
        return []

    code = _normalize_code(symbol)
    try:
        df = ak.stock_dzjy_mrmx(symbol=code)
        if df is None or df.empty:
            return []

        # 过滤近 N 天
        cutoff = _days_ago(days)
        results = []
        for _, row in df.iterrows():
            date_str = str(row.get("交易日期", "") or "")
            if date_str < cutoff:
                continue
            results.append({
                "date": date_str,
                "price": float(row.get("成交价", 0) or 0),
                "volume": float(row.get("成交量", 0) or 0),
                "amount": float(row.get("成交额", 0) or 0),
                "discount": float(row.get("折溢价率", 0) or 0),
                "buyer": str(row.get("买方营业部", "") or ""),
                "seller": str(row.get("卖方营业部", "") or ""),
            })

        logger.info("fetch_block_trade symbol=%s count=%d", symbol, len(results))
        return results[:10]

    except Exception as exc:
        logger.warning("fetch_block_trade 失败 symbol=%s: %s", symbol, exc)
        return []


# ── 4. 估值比较 ───────────────────────────────────────────────────

def fetch_valuation(symbol: str) -> dict[str, Any]:
    """
    拉取个股与行业估值比较
    接口：ak.stock_zh_valuation_comparison_em(symbol)
    返回：{
        "pe_ttm": ...,       # 当前PE(TTM)
        "pb": ...,           # 当前PB
        "industry_pe_avg": ...,   # 行业平均PE
        "industry_pe_median": ..., # 行业中位PE
        "vs_industry": "低估|合理|高估",
    }
    """
    ak = _safe_import_akshare()
    if not ak:
        return {}

    # 需要加交易所前缀
    code = _normalize_code(symbol)
    if code.startswith("6"):
        full_code = f"SH{code}"
    elif code.startswith(("0", "3")):
        full_code = f"SZ{code}"
    else:
        full_code = symbol  # 港股/美股直接用

    try:
        df = ak.stock_zh_valuation_comparison_em(symbol=full_code)
        if df is None or df.empty:
            return {}

        # 第一行是目标股票，后面是行业对比
        target_row = df.iloc[0]
        industry_rows = df[df["代码"].isna() | (df["简称"].str.contains("平均|中值", na=False))]

        pe_ttm = None
        pb = None
        industry_pe_avg = None
        industry_pe_median = None

        # 找 PE TTM 列
        for col in df.columns:
            if "市盈率" in str(col) and "TTM" in str(col):
                try:
                    pe_ttm = float(target_row.get(col, 0) or 0)
                except Exception:
                    pass
            if "市净率" in str(col):
                try:
                    pb = float(target_row.get(col, 0) or 0)
                except Exception:
                    pass

        for _, row in industry_rows.iterrows():
            name = str(row.get("简称", ""))
            for col in df.columns:
                if "市盈率" in str(col) and "TTM" in str(col):
                    try:
                        val = float(row.get(col, 0) or 0)
                        if "平均" in name:
                            industry_pe_avg = val
                        elif "中值" in name:
                            industry_pe_median = val
                    except Exception:
                        pass

        # 简单判断估值水位
        vs_industry = "数据不足"
        if pe_ttm and industry_pe_median:
            if pe_ttm < industry_pe_median * 0.8:
                vs_industry = "低于行业中位，相对低估"
            elif pe_ttm > industry_pe_median * 1.2:
                vs_industry = "高于行业中位，相对高估"
            else:
                vs_industry = "接近行业中位，估值合理"

        result = {
            "pe_ttm": pe_ttm,
            "pb": pb,
            "industry_pe_avg": industry_pe_avg,
            "industry_pe_median": industry_pe_median,
            "vs_industry": vs_industry,
        }
        logger.info("fetch_valuation symbol=%s pe_ttm=%s vs=%s", symbol, pe_ttm, vs_industry)
        return result

    except Exception as exc:
        logger.warning("fetch_valuation 失败 symbol=%s: %s", symbol, exc)
        return {}


# ── 汇总入口：一次拉取所有补充数据 ───────────────────────────────

def fetch_all_supplementary_data(symbol: str) -> dict[str, Any]:
    """
    对单只股票一次性拉取所有补充数据，返回结构化字典。
    在 run_report.py 里调用，把结果拼进 feature_snapshot 或单独传给提示词。

    返回：
    {
        "news": [...],           # 个股新闻列表
        "announcements": [...],  # 公司公告列表
        "fund_flow": {...},      # 资金流向
        "block_trade": [...],    # 大宗交易
        "valuation": {...},      # 估值比较
    }
    """
    logger.info("fetch_all_supplementary_data start symbol=%s", symbol)

    news = fetch_stock_news(symbol, max_count=8)
    announcements = fetch_stock_announcements(symbol, days=7)
    fund_flow = fetch_fund_flow(symbol)
    block_trade = fetch_block_trade(symbol, days=7)
    valuation = fetch_valuation(symbol)

    result = {
        "news": news,
        "announcements": announcements,
        "fund_flow": fund_flow,
        "block_trade": block_trade,
        "valuation": valuation,
    }

    logger.info(
        "fetch_all_supplementary_data done symbol=%s news=%d ann=%d fund_flow=%s val=%s",
        symbol,
        len(news),
        len(announcements),
        fund_flow.get("trend", "N/A"),
        valuation.get("vs_industry", "N/A"),
    )
    return result


def format_supplementary_for_prompt(data: dict[str, Any], symbol: str) -> str:
    """
    把 fetch_all_supplementary_data 的结果格式化为提示词文本块，
    直接拼入 build_stock_reasoning_prompt 的 news_block 里。
    """
    lines = []

    # 公告
    announcements = data.get("announcements", [])
    if announcements:
        lines.append("## 近期公司公告")
        for ann in announcements[:5]:
            lines.append(f"- [{ann.get('date', '')}] {ann.get('title', '')} （类型：{ann.get('type', '')}）")
        lines.append("")

    # 个股新闻
    news = data.get("news", [])
    if news:
        lines.append("## 个股新闻（东方财富）")
        for i, item in enumerate(news[:6], 1):
            lines.append(f"[E{i}] {item.get('publish_time', '')} {item.get('title', '')}")
            if item.get("content"):
                lines.append(f"     摘要: {item['content'][:100]}")
        lines.append("")

    # 资金流向
    fund_flow = data.get("fund_flow", {})
    if fund_flow:
        net_yi = fund_flow.get("5day_total_main_net_yi", 0)
        trend = fund_flow.get("trend", "")
        latest = fund_flow.get("latest", {})
        lines.append("## 近5日资金流向（AKShare接口数据）")
        lines.append(f"- 5日主力净流入合计：{net_yi:+.2f}亿元，{trend}")
        if latest:
            lines.append(
                f"- 最新一日：主力净{latest.get('main_net', 0)/1e8:+.2f}亿，"
                f"超大单净{latest.get('super_large_net', 0)/1e8:+.2f}亿，"
                f"大单净{latest.get('large_net', 0)/1e8:+.2f}亿"
            )
        lines.append("")

    # 大宗交易
    block_trade = data.get("block_trade", [])
    if block_trade:
        lines.append("## 近期大宗交易")
        for bt in block_trade[:3]:
            lines.append(
                f"- {bt.get('date', '')} 成交价{bt.get('price', 0):.2f}元 "
                f"成交额{bt.get('amount', 0)/1e4:.0f}万元 "
                f"折溢价{bt.get('discount', 0):.2f}%"
            )
        lines.append("")

    # 估值
    valuation = data.get("valuation", {})
    if valuation:
        lines.append("## 估值情况（AKShare接口数据）")
        lines.append(f"- 当前PE(TTM)：{valuation.get('pe_ttm', 'N/A')}")
        lines.append(f"- 当前PB：{valuation.get('pb', 'N/A')}")
        lines.append(f"- 行业平均PE：{valuation.get('industry_pe_avg', 'N/A')}")
        lines.append(f"- 行业中位PE：{valuation.get('industry_pe_median', 'N/A')}")
        lines.append(f"- 估值判断：{valuation.get('vs_industry', 'N/A')}")
        lines.append("")

    if not lines:
        return "无AKShare补充数据"

    return "\n".join(lines)
