#!/usr/bin/env python3
"""
AI 美股盤前分析 完整版
模組：期權掃描 + 財經新聞 + 事件日曆 + FDA行事曆 + 政治風向雷達
依賴: pip install google-generativeai yfinance requests python-dotenv
"""

import os, json, datetime, time, random, requests, re
from google import genai
from google.genai import types
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
from dotenv import load_dotenv
from xml.etree import ElementTree as ET

load_dotenv()

# ── 設定 ──────────────────────────────────────────────────
WATCHLIST = [
    "SPY", "QQQ", "NVDA", "AAPL", "MSFT",
    "TSLA", "AMZN", "META", "GOOGL", "AMD"
]
SP500_URL            = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SCAN_MIN_PRE_MOVE    = 3.0
SCAN_MIN_VOL_RATIO   = 2.5
SCAN_MIN_IV_SPIKE    = 0.25
SCAN_TOP_N           = 10
POLITICAL_NEWS_LIMIT = 8

GEMINI_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM        = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]
EMAIL_TO          = os.environ["EMAIL_TO"]
QUIVER_API_KEY    = os.environ.get("QUIVER_API_KEY", "")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ══════════════════════════════════════════════════════════
# 1. 標普500成分股
# ══════════════════════════════════════════════════════════
def get_sp500_tickers() -> list:
    try:
        import pandas as pd
        tickers = pd.read_html(SP500_URL)[0]["Symbol"].tolist()
        return [t.replace(".", "-") for t in tickers]
    except Exception as e:
        print(f"[WARN] SP500清單備用: {e}")
        return [
            "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","BRK-B","UNH","JPM",
            "LLY","V","XOM","MA","PG","JNJ","AVGO","HD","CVX","MRK","ABBV","COST",
            "PEP","ADBE","WMT","BAC","KO","CRM","MCD","TMO","CSCO","ACN","ABT",
            "NFLX","AMD","LIN","NEE","TXN","PM","CMCSA","WFC","ORCL","INTC","RTX",
            "AMGN","BMY","QCOM","HON","IBM","UPS","GE","SPGI","CAT","PLTR","ARM",
        ]


# ══════════════════════════════════════════════════════════
# 2. 股票數據 + 期權
# ══════════════════════════════════════════════════════════
def fetch_stock_data(ticker: str) -> dict | None:
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        info = t.fast_info
        hist = t.history(period="5d")
        if len(hist) < 2:
            return None

        prev_close = float(hist["Close"].iloc[-2])
        last_close = float(hist["Close"].iloc[-1])
        avg_vol    = float(hist["Volume"].mean())
        pre_price  = getattr(info, "pre_market_price", None)
        pre_vol    = getattr(info, "pre_market_volume", None) or 0
        pre_change = (float(pre_price) - last_close) / last_close * 100 if pre_price and last_close > 0 else None

        iv_cur = iv_prev = put_call = max_oi_strike = max_oi_val = None
        exp_dates = []
        try:
            exp_dates = list(t.options[:4])
            if exp_dates:
                chain = t.option_chain(exp_dates[0])
                calls, puts = chain.calls, chain.puts
                if not calls.empty:
                    iv_cur = float(calls["impliedVolatility"].median())
                    if len(exp_dates) > 1:
                        c2 = t.option_chain(exp_dates[1]).calls
                        if not c2.empty:
                            iv_prev = float(c2["impliedVolatility"].median()) * 0.85
                    total_c = calls["openInterest"].sum()
                    total_p = puts["openInterest"].sum() if not puts.empty else 0
                    if total_c > 0:
                        put_call = round(total_p / total_c, 2)
                    best = calls.loc[calls["openInterest"].idxmax()]
                    max_oi_strike = float(best["strike"])
                    max_oi_val    = int(best["openInterest"])
        except Exception:
            pass

        return {
            "ticker":        ticker,
            "last_close":    round(last_close, 2),
            "prev_close":    round(prev_close, 2),
            "change_pct":    round((last_close - prev_close) / prev_close * 100, 2),
            "avg_volume":    int(avg_vol),
            "last_volume":   int(hist["Volume"].iloc[-1]),
            "pre_price":     round(float(pre_price), 2) if pre_price else None,
            "pre_change":    round(pre_change, 2) if pre_change is not None else None,
            "pre_volume":    int(pre_vol),
            "iv_current":    round(iv_cur, 3) if iv_cur else None,
            "iv_prev":       round(iv_prev, 3) if iv_prev else None,
            "put_call":      put_call,
            "max_oi_strike": max_oi_strike,
            "max_oi_val":    max_oi_val,
            "exp_dates":     exp_dates[:3],
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════
# 3. 期權評分
# ══════════════════════════════════════════════════════════
def fetch_fear_greed() -> dict:
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            data = r.json()
            score = data.get("fear_and_greed", {}).get("score", 50)
            rating = data.get("fear_and_greed", {}).get("rating", "Neutral")
            label_map = {
                "Extreme Fear": "極度恐慌",
                "Fear": "恐慌",
                "Neutral": "中性",
                "Greed": "貪婪",
                "Extreme Greed": "極度貪婪",
            }
            return {
                "score": round(float(score), 1),
                "label": label_map.get(rating, rating),
                "raw":   rating,
            }
    except Exception as e:
        print(f"[WARN] Fear & Greed: {e}")
    return {"score": 50, "label": "中性", "raw": "Neutral"}


def fetch_vix() -> dict:
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if len(hist) >= 2:
            current = round(float(hist["Close"].iloc[-1]), 2)
            prev    = round(float(hist["Close"].iloc[-2]), 2)
            change  = round(current - prev, 2)
            if current < 15:
                level = "極低（期權便宜）"
            elif current < 20:
                level = "正常"
            elif current < 30:
                level = "偏高（市場緊張）"
            else:
                level = "極高（恐慌，期權昂貴）"
            return {"current": current, "change": change, "level": level}
    except Exception as e:
        print(f"[WARN] VIX: {e}")
    return {"current": 20, "change": 0, "level": "正常"}


def score_option(s: dict) -> tuple:
    score, flags = 0, []
    call_signals, put_signals = 0, 0

    pre = s.get("pre_change") or 0
    if abs(pre) >= 5:
        score += 30
        flags.append(f"盤前異動 {pre:+.1f}%")
        if pre > 0: call_signals += 3
        else: put_signals += 3
    elif abs(pre) >= SCAN_MIN_PRE_MOVE:
        score += 15
        flags.append(f"盤前異動 {pre:+.1f}%")
        if pre > 0: call_signals += 2
        else: put_signals += 2

    avg_vol = s.get("avg_volume") or 1
    pre_vol = s.get("pre_volume") or 0
    vol_ratio = (pre_vol / avg_vol) * (390 / 90) if avg_vol > 0 else 0
    if vol_ratio >= SCAN_MIN_VOL_RATIO:
        score += 20
        flags.append(f"成交量 {vol_ratio:.1f}x 均量")

    iv_cur, iv_prev = s.get("iv_current"), s.get("iv_prev")
    iv_spike = 0
    if iv_cur and iv_prev and iv_prev > 0:
        iv_spike = (iv_cur - iv_prev) / iv_prev
        if iv_spike >= SCAN_MIN_IV_SPIKE:
            score += 25
            flags.append(f"IV 飆升 {iv_spike:.0%}")

    pc = s.get("put_call")
    if pc is not None:
        if pc < 0.35:
            score += 20
            flags.append(f"P/C={pc:.2f} 大量買Call")
            call_signals += 2
        elif pc < 0.6:
            call_signals += 1
        elif pc > 1.5:
            score += 20
            flags.append(f"P/C={pc:.2f} 大量買Put")
            put_signals += 2
        elif pc > 1.0:
            put_signals += 1

    if pc is not None and pc > 1.2:
        if pre <= 0:
            direction = "PUT"
        elif pre > 3:
            direction = "CALL" if call_signals > put_signals else "PUT"
        else:
            direction = "PUT"
    elif pc is not None and pc < 0.6:
        if pre >= 0:
            direction = "CALL"
        elif pre < -3:
            direction = "PUT" if put_signals > call_signals else "CALL"
        else:
            direction = "CALL"
    elif pre > 0:
        direction = "CALL"
    elif pre < 0:
        direction = "PUT"
    else:
        direction = "PUT" if put_signals > call_signals else "CALL"

    if call_signals >= 3 or put_signals >= 3:
        score += 15
        flags.append(f"{'多頭' if call_signals >= put_signals else '空頭'}信號三重確認")

    if direction == "CALL" and put_signals > call_signals + 1:
        score = max(score - 15, 0)
    elif direction == "PUT" and call_signals > put_signals + 1:
        score = max(score - 15, 0)

    return score, flags, direction


# ══════════════════════════════════════════════════════════
# 4. 全市場掃描
# ══════════════════════════════════════════════════════════
def scan_options(tickers: list) -> list:
    print(f"[掃描] {len(tickers)} 支股票...")
    results = []
    today_str = datetime.date.today().isoformat()

    for i, ticker in enumerate(tickers):
        if i % 50 == 0:
            print(f"  進度: {i}/{len(tickers)}")
        data = fetch_stock_data(ticker)
        if not data:
            continue

        exp_dates = data.get("exp_dates", [])
        valid_dates = [d for d in exp_dates if d > today_str]
        if not valid_dates:
            continue
        data["exp_dates"] = valid_dates

        sc, flags, direction = score_option(data)

        if sc >= 30 and flags:
            pc = data.get("put_call")
            pre = data.get("pre_change") or 0
            consistency = "高" if (
                (direction == "CALL" and (pre > 0 or (pc and pc < 0.6))) or
                (direction == "PUT" and (pre < 0 or (pc and pc > 1.0)))
            ) else "中"
            data["direction_consistency"] = consistency
            results.append({**data, "score": sc, "flags": flags, "direction": direction})

        time.sleep(0.15 + random.uniform(0, 0.1))

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:SCAN_TOP_N]


def fetch_technical_data(ticker: str) -> dict:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo")
        if len(hist) < 20:
            return {}

        close = hist["Close"]

        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)

        ma20 = round(float(close.rolling(20).mean().iloc[-1]), 2)
        ma50 = round(float(close.rolling(50).mean().iloc[-1]), 2)
        current = round(float(close.iloc[-1]), 2)

        resistance = round(float(close.rolling(20).max().iloc[-1]), 2)
        support    = round(float(close.rolling(20).min().iloc[-1]), 2)

        high = hist["High"]
        low  = hist["Low"]
        tr   = (high - low).rolling(14).mean()
        atr  = round(float(tr.iloc[-1]), 2)

        try:
            cal = t.calendar
            earn_date = str(cal.get("Earnings Date", ["—"])[0])[:10] if cal else "—"
        except Exception:
            earn_date = "—"

        short_pct = None
        short_ratio = None
        try:
            info = t.info
            short_pct   = round(float(info.get("shortPercentOfFloat", 0) or 0) * 100, 2)
            short_ratio = round(float(info.get("shortRatio", 0) or 0), 1)
        except Exception:
            pass

        if current > ma20 > ma50:
            ma_signal = "多頭排列"
        elif current < ma20 < ma50:
            ma_signal = "空頭排列"
        else:
            ma_signal = "整理中"

        if rsi > 70:
            rsi_signal = "超買"
        elif rsi < 30:
            rsi_signal = "超賣"
        else:
            rsi_signal = "正常"

        squeeze_risk = "高" if (short_pct or 0) > 15 else "中" if (short_pct or 0) > 8 else "低"

        call_entry_low  = round(support + atr * 0.3, 2)
        call_entry_high = round(support + atr * 0.8, 2)
        put_entry_low   = round(resistance - atr * 0.8, 2)
        put_entry_high  = round(resistance - atr * 0.3, 2)
        atm_call_est = round(atr * 1.2, 2)
        atm_put_est  = round(atr * 1.2, 2)

        return {
            "ticker":         ticker,
            "current":        current,
            "rsi":            rsi,
            "rsi_signal":     rsi_signal,
            "ma20":           ma20,
            "ma50":           ma50,
            "ma_signal":      ma_signal,
            "resistance":     resistance,
            "support":        support,
            "atr":            atr,
            "earn_date":      earn_date,
            "short_pct":      short_pct,
            "short_ratio":    short_ratio,
            "squeeze_risk":   squeeze_risk,
            "call_entry_low":  call_entry_low,
            "call_entry_high": call_entry_high,
            "put_entry_low":   put_entry_low,
            "put_entry_high":  put_entry_high,
            "atm_call_est":   atm_call_est,
            "atm_put_est":    atm_put_est,
        }
    except Exception as e:
        print(f"[WARN] 技術指標 {ticker}: {e}")
        return {}


def fetch_watchlist_data() -> list:
    results = []
    for ticker in WATCHLIST:
        data = fetch_stock_data(ticker)
        if data:
            tech = fetch_technical_data(ticker)
            data.update({"tech": tech})
            results.append(data)
        time.sleep(0.2)
    return results


# ══════════════════════════════════════════════════════════
# 5. 新增：財經新聞模組
# ══════════════════════════════════════════════════════════
def fetch_market_news() -> list:
    """抓取今日重要財經新聞"""
    print("[財經新聞] 抓取中...")
    news_items = []
    queries = [
        ("stock market news today 2026", "市場動態"),
        ("earnings report beat miss today 2026", "財報"),
        ("federal reserve interest rate inflation 2026", "Fed/通脹"),
        ("semiconductor AI tech stock news 2026", "科技/AI"),
        ("S&P500 nasdaq market moving news today", "大市"),
    ]
    for query, label in queries:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")[:16]
                link    = item.findtext("link", "")
                if title:
                    news_items.append({
                        "category": label,
                        "title":    title[:120],
                        "date":     pubdate,
                        "link":     link,
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 財經新聞RSS({label}): {e}")

    # 去重
    seen = set()
    unique = []
    for n in news_items:
        key = n["title"][:40]
        if key not in seen:
            seen.add(key)
            unique.append(n)
    return unique[:10]


# ══════════════════════════════════════════════════════════
# 6. 新增：事件日曆模組
# ══════════════════════════════════════════════════════════
def fetch_event_calendar() -> list:
    """抓取本週重要事件"""
    print("[事件日曆] 抓取中...")
    events = []
    queries = [
        ("earnings calendar this week S&P500 2026", "📊 財報"),
        ("CPI PPI economic data release this week 2026", "📈 經濟數據"),
        ("Federal Reserve FOMC meeting speech 2026", "🏦 Fed"),
        ("options expiration date this week 2026", "📅 期權到期"),
        ("FDA PDUFA drug approval this week 2026", "💊 FDA"),
    ]
    for query, label in queries:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")[:16]
                if title:
                    events.append({
                        "category": label,
                        "title":    title[:100],
                        "date":     pubdate,
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 事件日曆RSS({label}): {e}")

    # 去重
    seen = set()
    unique = []
    for e in events:
        key = e["title"][:40]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[:10]


# ══════════════════════════════════════════════════════════
# 7. FDA 行事曆
# ══════════════════════════════════════════════════════════
def fetch_friday_weekly_analysis() -> dict:
    today = datetime.date.today()
    is_friday = today.weekday() == 4
    if not is_friday:
        return {"is_friday": False}

    print("[星期五分析] 抓取下週關鍵事件...")
    next_week_events = []

    queries = [
        ("next week earnings reports options S&P500", "財報"),
        ("next week CPI inflation report Federal Reserve 2026", "CPI/Fed"),
        ("next week FDA PDUFA drug approval 2026", "FDA"),
        ("next week options expiration market events 2026", "期權"),
    ]
    for query, label in queries:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title = item.findtext("title", "").strip()
                if title:
                    next_week_events.append({
                        "category": label,
                        "title": title[:100],
                        "date": item.findtext("pubDate", "")[:16],
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 星期五分析RSS({label}): {e}")

    return {
        "is_friday": True,
        "next_week_events": next_week_events[:8],
        "next_monday": str(today + datetime.timedelta(days=3)),
        "next_friday": str(today + datetime.timedelta(days=8)),
    }


def fetch_fda_calendar() -> list:
    events = []
    try:
        query = "FDA drug approval PDUFA 2026"
        encoded = requests.utils.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:5]:
                events.append({
                    "title": item.findtext("title", "")[:80],
                    "date":  item.findtext("pubDate", "")[:16],
                    "link":  item.findtext("link", ""),
                })
    except Exception as e:
        print(f"[WARN] FDA: {e}")
    if not events:
        events = [{"title": "FDA行事曆暫時無法取得", "date": "", "link": "https://www.fda.gov"}]
    return events


# ══════════════════════════════════════════════════════════
# 8. 政治風向雷達
# ══════════════════════════════════════════════════════════
def fetch_political_intelligence() -> dict:
    print("[政治雷達] 抓取中...")
    news_items  = []
    congress_trades = []
    trump_signals   = []

    rss_queries = [
        ("trump stock trade buy sell", "特朗普/股票"),
        ("congress stock trade STOCK ACT disclosure", "國會申報"),
        ("trump tariff policy semiconductor energy stock", "政策板塊"),
        ("pelosi congress stock purchase", "Pelosi持倉"),
    ]
    for query, label in rss_queries:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:3]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")[:16]
                link    = item.findtext("link", "")
                if title:
                    news_items.append({
                        "category": label,
                        "title":    title[:100],
                        "date":     pubdate,
                        "link":     link,
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 政治新聞RSS({label}): {e}")

    seen = set()
    unique_news = []
    for n in news_items:
        key = n["title"][:40]
        if key not in seen:
            seen.add(key)
            unique_news.append(n)
    news_items = unique_news[:POLITICAL_NEWS_LIMIT]

    if QUIVER_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {QUIVER_API_KEY}"}
            r = requests.get(
                "https://api.quiverquant.com/beta/live/congresstrading",
                headers=headers, timeout=10
            )
            if r.status_code == 200:
                trades = r.json()[:10]
                for t in trades:
                    congress_trades.append({
                        "politician": t.get("Representative", ""),
                        "ticker":     t.get("Ticker", ""),
                        "transaction": t.get("Transaction", ""),
                        "amount":     t.get("Amount", ""),
                        "filed":      t.get("Filed", ""),
                        "traded":     t.get("Traded", ""),
                    })
            r2 = requests.get(
                "https://api.quiverquant.com/beta/live/trump",
                headers=headers, timeout=10
            )
            if r2.status_code == 200:
                for t in r2.json()[:5]:
                    trump_signals.append({
                        "ticker":      t.get("Ticker", ""),
                        "transaction": t.get("Transaction", ""),
                        "amount":      t.get("Amount", ""),
                        "date":        t.get("Date", ""),
                        "source":      t.get("Source", ""),
                    })
        except Exception as e:
            print(f"[WARN] Quiver API: {e}")

    return {
        "news":           news_items,
        "congress_trades": congress_trades,
        "trump_signals":   trump_signals,
        "data_source":    "Quiver API + Google News" if QUIVER_API_KEY else "Google News RSS（免費版）",
    }


# ══════════════════════════════════════════════════════════
# 9. AI 分析（整合所有模組）
# ══════════════════════════════════════════════════════════
def ai_analyze(watchlist_data, scan_results, fda_events, political_data, fear_greed, vix_data, market_news, event_calendar, friday_data=None) -> dict:
    today = datetime.date.today().strftime("%Y年%m月%d日")

    tech_summary = []
    for s in watchlist_data:
        tech = s.get("tech", {})
        if tech:
            tech_summary.append({
                "ticker":          tech.get("ticker"),
                "current":         tech.get("current"),
                "rsi":             tech.get("rsi"),
                "rsi_signal":      tech.get("rsi_signal"),
                "ma_signal":       tech.get("ma_signal"),
                "support":         tech.get("support"),
                "resistance":      tech.get("resistance"),
                "atr":             tech.get("atr"),
                "earn_date":       tech.get("earn_date"),
                "short_pct":       tech.get("short_pct"),
                "short_ratio":     tech.get("short_ratio"),
                "squeeze_risk":    tech.get("squeeze_risk"),
                "call_entry_low":  tech.get("call_entry_low"),
                "call_entry_high": tech.get("call_entry_high"),
                "put_entry_low":   tech.get("put_entry_low"),
                "put_entry_high":  tech.get("put_entry_high"),
                "atm_call_est":    tech.get("atm_call_est"),
            })

    is_friday = friday_data and friday_data.get("is_friday", False)

    payload = {
        "date":            today,
        "is_friday":       is_friday,
        "fear_greed":      fear_greed,
        "vix":             vix_data,
        "market_news":     market_news[:8],
        "event_calendar":  event_calendar[:8],
        "watchlist":       watchlist_data[:10],
        "technical":       tech_summary,
        "top_options":     scan_results[:5],
        "fda_events":      fda_events[:3],
        "political_news":  political_data.get("news", [])[:6],
        "congress_trades": political_data.get("congress_trades", [])[:5],
        "trump_signals":   political_data.get("trump_signals", [])[:3],
    }

    if is_friday:
        payload["next_week_events"] = friday_data.get("next_week_events", [])
        payload["next_monday"] = friday_data.get("next_monday", "")
        payload["next_friday"] = friday_data.get("next_friday", "")

    friday_prompt_section = ""
    if is_friday:
        friday_prompt_section = """

【星期五特別分析】今天是星期五，請額外提供以下分析（加入到 JSON 中）：
"friday_analysis": {
  "today_action": "今天應放出期權還是繼續持有？給出明確建議（放出/持有/部分放出）",
  "today_reason": "今天行動的原因，包括週末時間值損耗、市場情緒等50字",
  "weekend_risk": "持倉過週末的主要風險30字",
  "next_week_outlook": "下週市場整體展望50字",
  "next_week_key_events": ["下週最重要事件1", "下週最重要事件2", "下週最重要事件3"],
  "should_buy_today": "今天是否適合買入下週期權？是/否/謹慎",
  "buy_reason": "買入或不買的原因40字",
  "next_week_picks": [
    {
      "ticker": "股票代碼",
      "direction": "CALL或PUT",
      "strategy": "策略",
      "strike": "建議Strike",
      "expiry": "建議到期日",
      "catalyst": "下週催化劑20字",
      "entry_note": "入場建議20字",
      "signal_strength": 1到5整數
    }
  ],
  "avoid_reason": "本週五不宜持倉過週末的股票及原因30字（若有）"
}"""

    prompt = f"""你是專業美股期權交易分析師。以下是 {today} 的完整數據，包含財經新聞、事件日曆、技術指標、沽空數據、期權異動、政治風向、市場情緒：

{json.dumps(payload, ensure_ascii=False, indent=2)}
{friday_prompt_section}

請用繁體中文回傳純 JSON（不要 markdown 或任何其他文字）：
{{
  "date": "{today}",
  "market_mood": "多頭/空頭/震盪",
  "mood_score": 0到100的整數,
  "headline": "今日最重要一句話20字以內",
  "news_summary": "根據今日財經新聞的市場重點摘要，50字以內",
  "key_events_today": ["今日最重要事件1", "今日最重要事件2", "今日最重要事件3"],
  "trade_plans": [
    {{
      "rank": 1,
      "ticker": "股票代碼",
      "direction": "CALL或PUT",
      "strategy": "直接買Call/直接買Put/Bull Call Spread/Bear Put Spread/Straddle",
      "strike": "建議行使價（具體數字，如210.0）",
      "expiry": "建議到期日（YYYY-MM-DD）",
      "expiry_reason": "選這個到期日的原因15字",
      "entry_zone": "建議入場價格區間（如$205-210）",
      "entry_timing": "開市後15分確認/盤中回調支撐/明日開市確認",
      "delta_range": "建議Delta範圍（如0.3-0.45）",
      "est_premium": "預估期權費範圍（如$3-6）",
      "signal_strength": 1到5的整數,
      "signals": ["信號1", "信號2", "信號3"],
      "max_loss": "最大虧損（就是期權費，如$300-600）",
      "target_gain": "目標獲利（如+50-100%即$150-300）",
      "stop_loss_price": "止損股價（如跌破$200立即出場）",
      "stop_loss_option": "期權止損（如虧損超過50%即出場）",
      "squeeze_risk": "軋空風險高/中/低",
      "risk": "主要風險15字",
      "political_factor": "政治因素10字，無則填無",
      "best_day_to_enter": "最佳入場時段描述"
    }}
  ],
  "portfolio_suggestion": {{
    "theme": "今日組合主題20字",
    "combination": "建議組合描述40字",
    "risk_level": "保守/平衡/積極",
    "total_budget": "建議總預算佔比（如不超過總資金10%）",
    "notes": "組合操作注意事項40字"
  }},
  "top_option_pick": {{
    "ticker": "今日最強單一期權機會代碼",
    "direction": "CALL或PUT",
    "reason": "原因30字以內",
    "key_strike": "建議Strike具體數字",
    "entry_zone": "入場區間",
    "risk": "主要風險20字以內"
  }},
  "squeeze_watchlist": ["高軋空風險的股票代碼列表，最多3個"],
  "political_summary": "政治風向對今日股市的關鍵影響50字以內",
  "political_hot_tickers": ["受政治消息影響最大的3支股票代碼"],
  "political_sentiment": "利多/利空/中性",
  "congress_highlight": "最值得關注的國會議員持倉動作30字",
  "fda_watch": "本週FDA事件影響30字，若無則填無重大事件",
  "sector_rotation": "板塊輪動觀察40字",
  "key_movers": [
    {{"ticker": "代碼", "signal": "強勢或弱勢或觀察", "reason": "原因20字"}}
  ],
  "risk_warning": "今日最大風險30字",
  "summary": "整體摘要100字"
}}

重要分析原則：
- trade_plans 提供3-5個機會，按信號強度排序
- strike 必須根據當前股價給出具體合理數字
- entry_zone 根據技術支撐阻力位計算
- delta_range 建議0.3-0.5（平衡成本與勝率）
- expiry 考慮財報日/FDA日，財報前選財報後3-7天到期
- 財報前一律建議Spread策略控制IV Crush風險
- short_pct>15%且上漲時考慮軋空機會（加分給CALL）
- Fear&Greed<25時市場極度恐慌，CALL機會更好
- VIX>25時期權偏貴，建議Spread策略
- signal_strength 需3個以上信號同向才給4-5分
- best_day_to_enter 說明具體最佳入場時間段
- 結合 market_news 和 event_calendar 分析催化劑"""

    models_to_try = ["gemini-2.5-flash", "gemini-3.6-flash"]
    response = None
    for model_name in models_to_try:
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                print(f"  [OK] 使用模型：{model_name}")
                break
            except Exception as e:
                if attempt < 2:
                    wait = 20 * (attempt + 1)
                    print(f"[WARN] {model_name} 重試 {attempt+1}/3，等待{wait}秒: {e}")
                    time.sleep(wait)
                else:
                    print(f"[WARN] {model_name} 失敗，嘗試下一個模型")
        if response:
            break
    if not response:
        raise Exception("所有模型都無法連線，請稍後重試")
    raw = response.text.strip()
    raw = re.sub(r'```json\s*', '', raw)
    raw = re.sub(r'```\s*', '', raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise


# ══════════════════════════════════════════════════════════
# 10. 生成 HTML 報告
# ══════════════════════════════════════════════════════════
def build_html(watchlist_data, scan_results, fda_events, political_data, analysis, fear_greed, vix_data, market_news, event_calendar, friday_data=None) -> str:
    mood_color = {"多頭": "#22c55e", "空頭": "#ef4444", "震盪": "#f59e0b"}.get(
        analysis.get("market_mood", "震盪"), "#6b7280")
    score = analysis.get("mood_score", 50)
    pol_sentiment = analysis.get("political_sentiment", "中性")
    pol_color = {"利多": "#22c55e", "利空": "#ef4444", "中性": "#f59e0b"}.get(pol_sentiment, "#6b7280")

    # ── 財經新聞 HTML ──
    market_news_html = ""
    cat_colors = {
        "市場動態": "#3b82f6",
        "財報":     "#22c55e",
        "Fed/通脹": "#f59e0b",
        "科技/AI":  "#a78bfa",
        "大市":     "#64748b",
    }
    for n in market_news[:8]:
        cc = cat_colors.get(n.get("category", ""), "#64748b")
        market_news_html += f"""
        <div style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #1e293b">
          <span style="background:{cc}22;color:{cc};padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;margin-top:1px">{n.get('category','')}</span>
          <div>
            <div style="font-size:13px;color:#e2e8f0;line-height:1.4">{n.get('title','')}</div>
            <div style="font-size:11px;color:#475569;margin-top:2px">{n.get('date','')}</div>
          </div>
        </div>"""

    # ── 事件日曆 HTML ──
    event_calendar_html = ""
    event_colors = {
        "📊 財報":    "#22c55e",
        "📈 經濟數據": "#f59e0b",
        "🏦 Fed":     "#ef4444",
        "📅 期權到期": "#a78bfa",
        "💊 FDA":     "#3b82f6",
    }
    key_events = analysis.get("key_events_today", [])
    for ev in event_calendar[:8]:
        ec = event_colors.get(ev.get("category", ""), "#64748b")
        event_calendar_html += f"""
        <div style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #1e293b;align-items:flex-start">
          <span style="background:{ec}22;color:{ec};padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap">{ev.get('category','')}</span>
          <div>
            <div style="font-size:13px;color:#e2e8f0;line-height:1.4">{ev.get('title','')}</div>
            <div style="font-size:11px;color:#475569;margin-top:2px">{ev.get('date','')}</div>
          </div>
        </div>"""

    # AI關鍵事件標籤
    key_events_html = ""
    for ev in key_events:
        key_events_html += f'<div style="font-size:12px;color:#94a3b8;padding:3px 0">• {ev}</div>'

    # ── AI 精選期權 ──
    top = analysis.get("top_option_pick", {})
    top_html = ""
    if top.get("ticker"):
        tc = "#22c55e" if top.get("direction") == "CALL" else "#ef4444"
        top_html = f"""
        <div style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:18px;margin-bottom:20px">
          <div style="font-size:10px;color:#64748b;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">⭐ AI 精選期權機會</div>
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
            <div style="font-size:28px;font-weight:800;color:#f1f5f9">{top['ticker']}</div>
            <div style="background:{tc}22;color:{tc};padding:4px 14px;border-radius:20px;font-size:14px;font-weight:700">{top.get('direction','')}</div>
          </div>
          <div style="color:#cbd5e1;font-size:14px;margin-bottom:10px;line-height:1.5">{top.get('reason','')}</div>
          <div style="display:flex;gap:20px;font-size:13px;flex-wrap:wrap">
            <span style="color:#64748b">Strike: <span style="color:#e2e8f0;font-weight:600">{top.get('key_strike','—')}</span></span>
            <span style="color:#64748b">入場: <span style="color:#e2e8f0">{top.get('entry_zone','—')}</span></span>
            <span style="color:#64748b">風險: <span style="color:#ef4444">{top.get('risk','—')}</span></span>
          </div>
        </div>"""

    # ── 今日操作清單 ──
    trade_plans = analysis.get("trade_plans", [])
    trade_html = ""
    for plan in trade_plans:
        dc = "#22c55e" if plan.get("direction") == "CALL" else "#ef4444"
        sig_strength = int(plan.get("signal_strength", 3))
        stars = "★" * sig_strength + "☆" * (5 - sig_strength)
        signals_html = "".join(
            f'<span style="background:#f59e0b22;color:#f59e0b;padding:2px 7px;border-radius:4px;font-size:11px;margin-right:4px">{sig}</span>'
            for sig in plan.get("signals", [])
        )
        squeeze = plan.get("squeeze_risk", "低")
        sq_color = "#ef4444" if squeeze == "高" else "#f59e0b" if squeeze == "中" else "#64748b"
        trade_html += f"""
        <div style="background:#0f172a;border-radius:10px;padding:16px;margin-bottom:10px;border:1px solid #1e293b">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
            <div>
              <div style="font-size:10px;color:#475569;margin-bottom:3px">#{plan.get('rank','')} · {plan.get('entry_timing','')}</div>
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <div style="font-size:20px;font-weight:800;color:#f1f5f9">{plan.get('ticker','')}</div>
                <span style="background:{dc}22;color:{dc};padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700">{plan.get('direction','')}</span>
                <span style="font-size:13px;color:#f59e0b">{stars}</span>
                <span style="background:{sq_color}22;color:{sq_color};font-size:10px;padding:2px 7px;border-radius:4px">軋空{squeeze}</span>
              </div>
            </div>
            <div style="text-align:right">
              <div style="color:#e2e8f0;font-weight:600;font-size:12px">{plan.get('strategy','')}</div>
              <div style="font-size:11px;color:#64748b;margin-top:2px">預估費用 {plan.get('est_premium','—')}</div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:10px">
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">Strike</div>
              <div style="font-size:15px;font-weight:700;color:{dc}">{plan.get('strike','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">到期日</div>
              <div style="font-size:12px;font-weight:600;color:#e2e8f0">{plan.get('expiry','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">Delta</div>
              <div style="font-size:13px;font-weight:600;color:#a78bfa">{plan.get('delta_range','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">選期理由</div>
              <div style="font-size:10px;color:#94a3b8">{plan.get('expiry_reason','—')}</div>
            </div>
          </div>
          <div style="background:#0a0f1e;border-radius:6px;padding:10px;margin-bottom:10px;border-left:2px solid {dc}">
            <div style="font-size:10px;color:#475569;margin-bottom:4px">入場資訊</div>
            <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px">
              <span style="color:#64748b">入場區間：<span style="color:#e2e8f0;font-weight:600">{plan.get('entry_zone','—')}</span></span>
              <span style="color:#64748b">最佳時段：<span style="color:#e2e8f0">{plan.get('best_day_to_enter','—')}</span></span>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">目標獲利</div>
              <div style="font-size:12px;color:#22c55e;font-weight:600">{plan.get('target_gain','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">止損（股價）</div>
              <div style="font-size:12px;color:#ef4444;font-weight:600">{plan.get('stop_loss_price','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">最大虧損</div>
              <div style="font-size:12px;color:#94a3b8">{plan.get('max_loss','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:8px">
              <div style="font-size:10px;color:#475569;margin-bottom:2px">期權止損</div>
              <div style="font-size:12px;color:#ef4444">{plan.get('stop_loss_option','—')}</div>
            </div>
          </div>
          <div style="margin-bottom:8px">{signals_html}</div>
          <div style="display:flex;justify-content:space-between;font-size:11px;flex-wrap:wrap;gap:4px">
            <span style="color:#64748b">風險：<span style="color:#f59e0b">{plan.get('risk','—')}</span></span>
            <span style="color:#64748b">政治：<span style="color:#3b82f6">{plan.get('political_factor','無')}</span></span>
          </div>
        </div>"""

    # ── 投資組合建議 ──
    portfolio = analysis.get("portfolio_suggestion", {})
    portfolio_html = ""
    if portfolio:
        risk_color = {"保守": "#22c55e", "平衡": "#f59e0b", "積極": "#ef4444"}.get(
            portfolio.get("risk_level", "平衡"), "#f59e0b")
        portfolio_html = f"""
        <div style="background:#0f172a;border-radius:10px;padding:16px;border:1px solid #334155">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
            <span style="background:{risk_color}22;color:{risk_color};padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700">{portfolio.get('risk_level','平衡')}</span>
            <span style="font-size:14px;font-weight:600;color:#f1f5f9">{portfolio.get('theme','—')}</span>
          </div>
          <div style="font-size:13px;color:#94a3b8;margin-bottom:8px;line-height:1.6">{portfolio.get('combination','—')}</div>
          <div style="font-size:12px;color:#64748b">建議預算：<span style="color:#f59e0b;font-weight:600">{portfolio.get('total_budget','—')}</span></div>
          <div style="font-size:12px;color:#64748b;border-top:1px solid #1e293b;padding-top:8px;margin-top:8px">⚠️ {portfolio.get('notes','—')}</div>
        </div>"""

    # ── 軋空觀察名單 ──
    squeeze_list = analysis.get("squeeze_watchlist", [])
    squeeze_html = ""
    if squeeze_list:
        squeeze_tags = " ".join(
            f'<span style="background:#ef444422;color:#ef4444;padding:3px 10px;border-radius:4px;font-size:13px;font-weight:700">{t}</span>'
            for t in squeeze_list
        )
        squeeze_html = f"""
        <div style="background:#0f172a;border-radius:10px;padding:14px 16px;border:1px solid #ef444433;margin-bottom:4px">
          <div style="font-size:10px;color:#ef4444;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px">⚡ 高沽空比例 · 軋空風險股票</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">{squeeze_tags}</div>
          <div style="font-size:11px;color:#64748b">Short % of Float 高，若盤前急漲可考慮 CALL 追入，軋空行情爆發力強</div>
        </div>"""

    # ── 自選股動向 ──
    movers_html = ""
    for m in analysis.get("key_movers", []):
        sc_color = {"強勢":"#22c55e","弱勢":"#ef4444","觀察":"#f59e0b"}.get(m.get("signal",""),"#94a3b8")
        movers_html += f"""
        <div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #1e293b">
          <span style="font-weight:700;color:#f1f5f9;width:60px">{m['ticker']}</span>
          <span style="background:{sc_color}22;color:{sc_color};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{m['signal']}</span>
          <span style="color:#94a3b8;font-size:13px">{m['reason']}</span>
        </div>"""

    # ── 期權掃描卡片 ──
    scan_cards = ""
    for rank, s in enumerate(scan_results[:5], 1):
        sc = s.get("score", 0)
        direction = s.get("direction", "CALL")
        dc = "#22c55e" if direction == "CALL" else "#ef4444"
        consistency = s.get("direction_consistency", "中")
        cons_color = "#22c55e" if consistency == "高" else "#f59e0b"
        pre = s.get("pre_change")
        iv_str = f"{s['iv_current']:.0%}" if s.get("iv_current") else "—"
        pc = s.get("put_call")
        pc_str = f"{pc:.2f}" if pc is not None else "—"
        exps = " · ".join(s.get("exp_dates", [])[:2])
        flags_html = "".join(
            f'<span style="background:#f59e0b22;color:#f59e0b;padding:2px 7px;border-radius:4px;font-size:11px;margin-right:4px;margin-bottom:4px;display:inline-block">{f}</span>'
            for f in s.get("flags", [])
        )
        if pc is not None:
            if pc < 0.4:
                pc_explain = "市場大量買Call，偏多"
                pc_explain_color = "#22c55e"
            elif pc < 0.8:
                pc_explain = "Call略多於Put，偏多"
                pc_explain_color = "#22c55e"
            elif pc < 1.2:
                pc_explain = "Put/Call均衡，方向不明"
                pc_explain_color = "#64748b"
            elif pc < 1.8:
                pc_explain = "市場大量買Put，偏空"
                pc_explain_color = "#ef4444"
            else:
                pc_explain = "Put遠多於Call，強烈偏空"
                pc_explain_color = "#ef4444"
        else:
            pc_explain = "—"
            pc_explain_color = "#64748b"

        scan_cards += f"""
        <div style="background:#0f172a;border-radius:10px;padding:14px;margin-bottom:10px;border:1px solid {'#334155' if sc >= 60 else '#1e293b'}">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
            <div>
              <div style="font-size:10px;color:#475569;margin-bottom:2px">#{rank}</div>
              <div style="font-size:20px;font-weight:800;color:#f1f5f9">{s['ticker']}</div>
            </div>
            <div style="text-align:right">
              <span style="background:{dc}22;color:{dc};padding:4px 14px;border-radius:20px;font-size:14px;font-weight:700">{direction}</span>
              <div style="font-size:11px;color:{cons_color};margin-top:4px">方向一致性：{consistency}</div>
            </div>
          </div>
          <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">
            <div style="background:#0a0f1e;border-radius:6px;padding:6px 10px;flex:1;min-width:70px">
              <div style="font-size:10px;color:#475569;margin-bottom:1px">盤前</div>
              <div style="color:{'#22c55e' if (pre or 0)>=0 else '#ef4444'};font-size:14px;font-weight:700">{f"{pre:+.1f}%" if pre is not None else "—"}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:6px 10px;flex:1;min-width:70px">
              <div style="font-size:10px;color:#475569;margin-bottom:1px">IV</div>
              <div style="color:#f59e0b;font-size:14px;font-weight:700">{iv_str}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:6px 10px;flex:1;min-width:70px">
              <div style="font-size:10px;color:#475569;margin-bottom:1px">評分</div>
              <div style="color:{dc};font-size:14px;font-weight:700">{sc}</div>
            </div>
          </div>
          <div style="background:#0a0f1e;border-radius:6px;padding:8px 10px;margin-bottom:8px">
            <div style="font-size:10px;color:#475569;margin-bottom:3px">期權市場情緒（P/C={pc_str}）</div>
            <div style="font-size:13px;color:{pc_explain_color};font-weight:600">{pc_explain}</div>
          </div>
          <div style="margin-bottom:6px">{flags_html}</div>
          <div style="font-size:11px;color:#475569">📅 到期日：{exps or '—'}</div>
        </div>"""

    # ── 政治新聞 ──
    news_html = ""
    for n in political_data.get("news", [])[:5]:
        cat_color = {
            "特朗普/股票": "#ef4444",
            "國會申報": "#22c55e",
            "政策板塊": "#f59e0b",
            "Pelosi持倉": "#a78bfa",
        }.get(n.get("category", ""), "#64748b")
        news_html += f"""
        <div style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #1e293b">
          <span style="background:{cat_color}22;color:{cat_color};padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;margin-top:1px">{n.get('category','')}</span>
          <div>
            <div style="font-size:13px;color:#e2e8f0;line-height:1.4">{n.get('title','')}</div>
            <div style="font-size:11px;color:#475569;margin-top:2px">{n.get('date','')}</div>
          </div>
        </div>"""

    # ── 國會申報 ──
    congress_html = ""
    trades = political_data.get("congress_trades", [])
    if trades:
        for t in trades[:4]:
            tx = t.get("transaction", "")
            tc = "#22c55e" if "Purchase" in tx or "Buy" in tx else "#ef4444"
            congress_html += f"""
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #1e293b">
              <span style="background:{tc}22;color:{tc};font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px">{tx}</span>
              <span style="font-weight:700;color:#f1f5f9;min-width:52px">{t.get('ticker','')}</span>
              <span style="color:#94a3b8;font-size:12px;flex:1">{t.get('politician','')}</span>
            </div>"""
    else:
        congress_html = f'<div style="color:#64748b;font-size:13px;padding:8px 0">{analysis.get("congress_highlight","—")}</div>'

    # ── FDA ──
    fda_html = ""
    for ev in fda_events:
        fda_html += f"""
        <div style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #1e293b">
          <div style="width:6px;height:6px;border-radius:50%;background:#a78bfa;margin-top:5px;flex-shrink:0"></div>
          <div>
            <div style="font-size:13px;color:#e2e8f0">{ev.get('title','')}</div>
            <div style="font-size:11px;color:#475569;margin-top:2px">{ev.get('date','')}</div>
          </div>
        </div>"""

    data_src = political_data.get("data_source", "Google News RSS")
    hot_tickers = analysis.get("political_hot_tickers", [])
    hot_html = " ".join(
        f'<span style="background:#3b82f622;color:#3b82f6;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700">{t}</span>'
        for t in hot_tickers
    )

    # ── 星期五特別分析 ──
    friday_html = ""
    fa = analysis.get("friday_analysis", {})
    if fa and friday_data and friday_data.get("is_friday"):
        action = fa.get("today_action", "—")
        action_color = "#22c55e" if "放出" in action else "#f59e0b" if "部分" in action else "#3b82f6"
        next_picks_html = ""
        for p in fa.get("next_week_picks", []):
            dc = "#22c55e" if p.get("direction") == "CALL" else "#ef4444"
            sig = int(p.get("signal_strength", 3))
            stars = "★" * sig + "☆" * (5 - sig)
            next_picks_html += f"""
            <div style="background:#0a0f1e;border-radius:8px;padding:12px;margin-bottom:8px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <span style="font-size:16px;font-weight:700;color:#f1f5f9">{p.get('ticker','')}</span>
                <span style="background:{dc}22;color:{dc};padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700">{p.get('direction','')}</span>
                <span style="color:#f59e0b;font-size:12px">{stars}</span>
              </div>
              <div style="display:flex;gap:12px;font-size:12px;margin-bottom:4px;flex-wrap:wrap">
                <span style="color:#64748b">Strike：<span style="color:{dc};font-weight:600">{p.get('strike','—')}</span></span>
                <span style="color:#64748b">到期：<span style="color:#e2e8f0">{p.get('expiry','—')}</span></span>
              </div>
              <div style="font-size:12px;color:#94a3b8">📅 {p.get('catalyst','—')}</div>
              <div style="font-size:11px;color:#64748b">⏰ {p.get('entry_note','—')}</div>
            </div>"""
        events_html = "".join(
            f'<div style="font-size:12px;color:#94a3b8;padding:3px 0">• {ev}</div>'
            for ev in fa.get("next_week_key_events", [])
        )
        buy_today = fa.get("should_buy_today", "謹慎")
        buy_color = "#22c55e" if buy_today == "是" else "#ef4444" if buy_today == "否" else "#f59e0b"
        friday_html = f"""
        <div style="background:#0f172a;border:1px solid #f59e0b55;border-radius:12px;padding:18px;margin-bottom:16px">
          <div style="font-size:10px;color:#f59e0b;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">📅 星期五特別分析</div>
          <div style="background:#0a0f1e;border-radius:8px;padding:14px;margin-bottom:12px;border-left:3px solid {action_color}">
            <div style="font-size:10px;color:#475569;text-transform:uppercase;margin-bottom:6px">今天應該怎樣做？</div>
            <div style="font-size:18px;font-weight:700;color:{action_color};margin-bottom:6px">{action}</div>
            <div style="font-size:13px;color:#94a3b8;line-height:1.5;margin-bottom:8px">{fa.get('today_reason','—')}</div>
            <div style="font-size:12px;color:#64748b">⚠️ 週末風險：{fa.get('weekend_risk','—')}</div>
          </div>
          <div style="background:#0a0f1e;border-radius:8px;padding:12px;margin-bottom:12px">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
              <span style="font-size:13px;color:#64748b">今天適合買入下週期權？</span>
              <span style="background:{buy_color}22;color:{buy_color};padding:3px 12px;border-radius:20px;font-size:13px;font-weight:700">{buy_today}</span>
            </div>
            <div style="font-size:12px;color:#94a3b8">{fa.get('buy_reason','—')}</div>
          </div>
          <div style="margin-bottom:12px">
            <div style="font-size:13px;color:#94a3b8;line-height:1.5;margin-bottom:8px">{fa.get('next_week_outlook','—')}</div>
            {events_html}
          </div>
          <div style="margin-bottom:10px">
            <div style="font-size:10px;color:#475569;text-transform:uppercase;margin-bottom:8px">下週期權部署</div>
            {next_picks_html or '<div style="color:#475569;font-size:12px">暫無明確推薦</div>'}
          </div>
          <div style="border-top:1px solid #1e293b;padding-top:10px">
            <div style="font-size:11px;color:#ef4444">🚫 不宜過週末：{fa.get('avoid_reason','—')}</div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 美股日報 {analysis['date']}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0f1e;color:#e2e8f0;font-family:'Helvetica Neue',Arial,sans-serif;padding:16px}}
.wrap{{max-width:680px;margin:0 auto}}
.sec{{font-size:10px;letter-spacing:2px;color:#475569;text-transform:uppercase;margin:22px 0 12px}}
@media(max-width:480px){{.grid2{{grid-template-columns:1fr!important}}}}
</style>
</head>
<body>
<div class="wrap">

  <div style="text-align:center;padding:24px 0 18px;border-bottom:1px solid #1e293b;margin-bottom:18px">
    <div style="font-size:10px;letter-spacing:3px;color:#475569;text-transform:uppercase;margin-bottom:6px">AI 美股日報</div>
    <div style="font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px">盤前分析 · 期權掃描 · 政治雷達</div>
    <div style="font-size:13px;color:#64748b">{analysis['date']}</div>
  </div>

  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
    <span style="background:{mood_color}22;color:{mood_color};padding:3px 12px;border-radius:20px;font-size:13px;font-weight:700">{analysis.get('market_mood','—')}</span>
    <span style="color:#64748b;font-size:13px">市場情緒 {score}/100</span>
  </div>
  <div style="background:#1e293b;border-radius:4px;height:5px;margin-bottom:16px;overflow:hidden">
    <div style="background:{mood_color};height:5px;border-radius:4px;width:{score}%"></div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:16px">
    <div style="background:#0f172a;border-radius:8px;padding:12px;text-align:center;border:1px solid #1e293b">
      <div style="font-size:10px;color:#475569;margin-bottom:4px">FEAR & GREED</div>
      <div style="font-size:20px;font-weight:700;color:{'#22c55e' if fear_greed['score']>50 else '#ef4444'}">{fear_greed['score']}</div>
      <div style="font-size:11px;color:#64748b">{fear_greed['label']}</div>
    </div>
    <div style="background:#0f172a;border-radius:8px;padding:12px;text-align:center;border:1px solid #1e293b">
      <div style="font-size:10px;color:#475569;margin-bottom:4px">VIX</div>
      <div style="font-size:20px;font-weight:700;color:{'#ef4444' if vix_data['current']>25 else '#f59e0b' if vix_data['current']>15 else '#22c55e'}">{vix_data['current']}</div>
      <div style="font-size:11px;color:#64748b">{vix_data['level']}</div>
    </div>
    <div style="background:#0f172a;border-radius:8px;padding:12px;text-align:center;border:1px solid #1e293b">
      <div style="font-size:10px;color:#475569;margin-bottom:4px">信號門檻</div>
      <div style="font-size:20px;font-weight:700;color:#f59e0b">3+</div>
      <div style="font-size:11px;color:#64748b">多信號確認</div>
    </div>
  </div>

  <div style="background:#0f172a;border-left:3px solid {mood_color};padding:12px 16px;margin-bottom:18px;font-size:15px;font-weight:600;color:#f1f5f9;line-height:1.5;border-radius:0 8px 8px 0">
    💡 {analysis.get('headline','—')}
  </div>

  {friday_html}

  <!-- 今日財經新聞 -->
  <div class="sec">📰 今日財經新聞</div>
  <div style="background:#0f172a;border-radius:12px;padding:14px 16px;border:1px solid #1e293b;margin-bottom:4px">
    <div style="font-size:13px;color:#94a3b8;line-height:1.6;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid #1e293b">{analysis.get('news_summary','—')}</div>
    {market_news_html or '<div style="color:#475569;font-size:13px;padding:8px 0">暫無財經新聞</div>'}
  </div>

  <!-- 本週事件日曆 -->
  <div class="sec">📅 本週事件日曆</div>
  <div style="background:#0f172a;border-radius:12px;padding:14px 16px;border:1px solid #1e293b;margin-bottom:4px">
    {f'<div style="margin-bottom:10px">{key_events_html}</div>' if key_events_html else ''}
    {event_calendar_html or '<div style="color:#475569;font-size:13px;padding:8px 0">暫無本週事件</div>'}
  </div>

  {top_html}

  <div class="sec">📋 今日操作清單</div>
  {trade_html or '<div style="color:#475569;padding:16px 0;text-align:center">今日無明確操作建議</div>'}

  <div class="sec">💼 今日組合建議</div>
  {portfolio_html or '<div style="color:#475569;padding:16px 0;text-align:center">—</div>'}

  {squeeze_html}

  <div class="sec">🏛 政治風向雷達</div>
  <div style="background:#0f172a;border-radius:12px;padding:16px;border:1px solid #1e293b;margin-bottom:4px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
      <span style="background:{pol_color}22;color:{pol_color};padding:3px 12px;border-radius:20px;font-size:12px;font-weight:700">{pol_sentiment}</span>
      <span style="color:#94a3b8;font-size:13px;flex:1">{analysis.get('political_summary','—')}</span>
    </div>
    <div style="margin-bottom:12px">
      <span style="font-size:10px;color:#475569;margin-right:8px">受影響股票</span>
      {hot_html}
    </div>
    <div style="font-size:10px;color:#475569;margin-bottom:8px">最新消息</div>
    {news_html or '<div style="color:#475569;font-size:12px;padding:8px 0">暫無相關新聞</div>'}
  </div>

  <div class="sec">📋 國會議員持倉</div>
  <div style="background:#0f172a;border-radius:12px;padding:16px;border:1px solid #1e293b;margin-bottom:4px">
    {congress_html}
    <div style="font-size:11px;color:#334155;margin-top:8px">來源：{data_src} · STOCK Act 申報延遲最長45天</div>
  </div>

  <div class="sec">🔍 期權異動掃描 Top 5</div>
  {scan_cards or '<div style="color:#475569;padding:16px 0;text-align:center">今日無顯著期權異動</div>'}

  <div class="sec">💊 FDA / 生技事件</div>
  <div style="background:#0f172a;border-radius:12px;padding:14px 16px;border:1px solid #1e293b">
    {fda_html}
  </div>

  <div class="sec">📊 自選股動向</div>
  <div style="background:#0f172a;border-radius:12px;padding:4px 16px;border:1px solid #1e293b">
    {movers_html}
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px" class="grid2">
    <div style="background:#0f172a;border-radius:10px;padding:14px;border:1px solid #1e293b">
      <div style="font-size:10px;color:#475569;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">板塊輪動</div>
      <div style="font-size:13px;color:#cbd5e1;line-height:1.5">{analysis.get('sector_rotation','—')}</div>
    </div>
    <div style="background:#0f172a;border-radius:10px;padding:14px;border:1px solid #1e293b">
      <div style="font-size:10px;color:#475569;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">風險警示</div>
      <div style="font-size:13px;color:#cbd5e1;line-height:1.5">{analysis.get('risk_warning','—')}</div>
    </div>
  </div>

  <div style="background:#0f172a;border-radius:10px;padding:16px;border:1px solid #1e293b;margin-top:10px">
    <div style="font-size:10px;color:#475569;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px">整體摘要</div>
    <div style="font-size:14px;color:#94a3b8;line-height:1.7">{analysis.get('summary','—')}</div>
  </div>

  <div style="text-align:center;padding:22px 0;color:#334155;font-size:11px;border-top:1px solid #1e293b;margin-top:24px;line-height:1.8">
    由 Gemini AI 自動生成 · 掃描標普500全市場<br>
    僅供參考，不構成投資建議<br>
    數據：Yahoo Finance · FDA · Google News · {data_src}
  </div>

</div>
</body>
</html>"""


# ══════════════════════════════════════════════════════════
# 11. 儲存報告
# ══════════════════════════════════════════════════════════
def save_report(html: str, analysis: dict):
    os.makedirs("web/reports", exist_ok=True)
    date_str = datetime.date.today().isoformat()
    for path in [f"web/reports/{date_str}.html", "web/latest.html"]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    index_path = "web/index.json"
    index = []
    if os.path.exists(index_path):
        with open(index_path) as f:
            index = json.load(f)
    entry = {"date": date_str, "headline": analysis.get("headline",""), "mood": analysis.get("market_mood","")}
    index = [e for e in index if e["date"] != date_str]
    index.insert(0, entry)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index[:30], f, ensure_ascii=False, indent=2)
    print("[OK] 報告儲存完成")


# ══════════════════════════════════════════════════════════
# 12. 發送 Email
# ══════════════════════════════════════════════════════════
def send_email(html: str, analysis: dict):
    msg = MIMEMultipart("alternative")
    top = analysis.get("top_option_pick", {})
    pol = analysis.get("political_sentiment", "")
    msg["Subject"] = f"📈 AI美股日報 {analysis['date']} · {analysis.get('headline','')} · {top.get('ticker','')} {top.get('direction','')} · 政治:{pol}"
    msg["From"] = EMAIL_FROM
    msg["To"]   = EMAIL_TO
    msg.attach(MIMEText(analysis.get("summary",""), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print("[OK] Email 發送完成")


def send_push_notification(analysis: dict):
    try:
        top = analysis.get("top_option_pick", {})
        mood = analysis.get("market_mood", "-")
        score = analysis.get("mood_score", 0)
        headline = analysis.get("headline", "-")
        pol = analysis.get("political_sentiment", "-")
        ticker = top.get("ticker", "-")
        direction = top.get("direction", "-")
        message = f"{mood} {score}/100 | {ticker} {direction} | {headline}"
        requests.post(
            "https://ntfy.sh/kidandkitty-stock-daily",
            data=message.encode("utf-8"),
            headers={
                "Title": "AI Stock Daily",
                "Priority": "high",
                "Tags": "stock_chart",
            },
            timeout=10,
        )
        print("[OK] 推送通知已發送")
    except Exception as e:
        print(f"[WARN] 推送通知失敗: {e}")


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════
def main():
    print("=== AI 美股日報 完整版 開始執行 ===")

    print("\n[1/10] 取得標普500成分股清單...")
    sp500 = get_sp500_tickers()

    print("\n[2/10] 抓取自選股數據...")
    watchlist_data = fetch_watchlist_data()

    print("\n[3/10] 全市場期權異動掃描...")
    scan_results = scan_options(sp500)
    print(f"  發現 {len(scan_results)} 支高評分異動股")

    print("\n[4/10] 抓取 FDA 行事曆...")
    fda_events = fetch_fda_calendar()

    print("\n[5/10] 政治風向雷達...")
    political_data = fetch_political_intelligence()
    print(f"  新聞 {len(political_data['news'])} 條 · 國會申報 {len(political_data['congress_trades'])} 筆")

    print("\n[6/10] 市場情緒指標...")
    fear_greed = fetch_fear_greed()
    vix_data   = fetch_vix()
    print(f"  Fear & Greed: {fear_greed['score']} ({fear_greed['label']}) · VIX: {vix_data['current']} {vix_data['level']}")

    print("\n[7/10] 今日財經新聞...")
    market_news = fetch_market_news()
    print(f"  抓取 {len(market_news)} 條財經新聞")

    print("\n[8/10] 本週事件日曆...")
    event_calendar = fetch_event_calendar()
    print(f"  抓取 {len(event_calendar)} 個事件")

    print("\n[9/10] 星期五特別分析...")
    friday_data = fetch_friday_weekly_analysis()
    if friday_data.get("is_friday"):
        print(f"  今天是星期五！下週事件 {len(friday_data.get('next_week_events', []))} 條")
    else:
        print("  今天不是星期五，跳過")

    print("\n[10/10] Gemini AI 整合分析...")
    analysis = ai_analyze(
        watchlist_data, scan_results, fda_events, political_data,
        fear_greed, vix_data, market_news, event_calendar, friday_data
    )
    print(f"  標題：{analysis.get('headline')}")
    print(f"  政治情緒：{analysis.get('political_sentiment')} · {analysis.get('political_summary','')[:40]}")

    print("\n[生成報告與發送]")
    html = build_html(
        watchlist_data, scan_results, fda_events, political_data,
        analysis, fear_greed, vix_data, market_news, event_calendar, friday_data
    )
    save_report(html, analysis)
    send_email(html, analysis)
    send_push_notification(analysis)

    print("\n=== 完成 ✓ ===")


if __name__ == "__main__":
    main()
