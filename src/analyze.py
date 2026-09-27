#!/usr/bin/env python3
"""
AI 美股盤前分析 完整版
模組：期權掃描 + 財經新聞 + 事件日曆 + FDA行事曆 + 政治風向雷達
依賴: pip install google-generativeai yfinance requests python-dotenv pandas
"""

import os, json, datetime, time, random, requests, re
from google import genai
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
NEWS_MAX_AGE_DAYS    = 7   # 只保留7天內新聞

GEMINI_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM     = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
EMAIL_TO       = os.environ["EMAIL_TO"]
QUIVER_API_KEY = os.environ.get("QUIVER_API_KEY", "")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ══════════════════════════════════════════════════════════
# 工具：判斷今天星期幾 + 新聞日期過濾
# ══════════════════════════════════════════════════════════
def get_day_mode() -> str:
    """
    回傳今天的日報模式：
    weekday  = 週一至週四（正常日報）
    friday   = 週五（日報 + 星期五特別分析）
    saturday = 週六（本週回顧 + 下週部署）
    sunday   = 週日（下週展望 + 下週部署）
    """
    weekday = datetime.date.today().weekday()
    if weekday == 4:
        return "friday"
    elif weekday == 5:
        return "saturday"
    elif weekday == 6:
        return "sunday"
    else:
        return "weekday"


def is_recent_news(date_str: str, max_days: int = NEWS_MAX_AGE_DAYS) -> bool:
    """過濾掉超過 max_days 天的舊新聞"""
    if not date_str:
        return True
    try:
        from email.utils import parsedate_to_datetime
        pub_date = parsedate_to_datetime(date_str)
        now = datetime.datetime.now(pub_date.tzinfo)
        return (now - pub_date).days <= max_days
    except Exception:
        return True


def filter_recent(items: list, date_key: str = "date", max_days: int = NEWS_MAX_AGE_DAYS) -> list:
    """從列表中過濾掉舊新聞，保留近 max_days 天內的"""
    recent = [item for item in items if is_recent_news(item.get(date_key, ""), max_days)]
    # 若過濾後太少，放寬到 30 天
    if len(recent) < 3:
        recent = [item for item in items if is_recent_news(item.get(date_key, ""), 30)]
    return recent


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
# 3. Fear & Greed + VIX
# ══════════════════════════════════════════════════════════
def fetch_fear_greed() -> dict:
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            timeout=8, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            data   = r.json()
            score  = data.get("fear_and_greed", {}).get("score", 50)
            rating = data.get("fear_and_greed", {}).get("rating", "Neutral")
            label_map = {
                "Extreme Fear": "極度恐慌", "Fear": "恐慌",
                "Neutral": "中性", "Greed": "貪婪", "Extreme Greed": "極度貪婪",
            }
            return {"score": round(float(score), 1), "label": label_map.get(rating, rating), "raw": rating}
    except Exception as e:
        print(f"[WARN] Fear & Greed: {e}")
    return {"score": 50, "label": "中性", "raw": "Neutral"}


def fetch_vix() -> dict:
    try:
        import yfinance as yf
        vix  = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if len(hist) >= 2:
            current = round(float(hist["Close"].iloc[-1]), 2)
            prev    = round(float(hist["Close"].iloc[-2]), 2)
            change  = round(current - prev, 2)
            if current < 15:   level = "極低（期權便宜）"
            elif current < 20: level = "正常"
            elif current < 30: level = "偏高（市場緊張）"
            else:              level = "極高（恐慌，期權昂貴）"
            return {"current": current, "change": change, "level": level}
    except Exception as e:
        print(f"[WARN] VIX: {e}")
    return {"current": 20, "change": 0, "level": "正常"}


# ══════════════════════════════════════════════════════════
# 4. 期權評分
# ══════════════════════════════════════════════════════════
def score_option(s: dict) -> tuple:
    score, flags = 0, []
    call_signals, put_signals = 0, 0

    pre = s.get("pre_change") or 0
    if abs(pre) >= 5:
        score += 30; flags.append(f"盤前異動 {pre:+.1f}%")
        if pre > 0: call_signals += 3
        else: put_signals += 3
    elif abs(pre) >= SCAN_MIN_PRE_MOVE:
        score += 15; flags.append(f"盤前異動 {pre:+.1f}%")
        if pre > 0: call_signals += 2
        else: put_signals += 2

    avg_vol   = s.get("avg_volume") or 1
    pre_vol   = s.get("pre_volume") or 0
    vol_ratio = (pre_vol / avg_vol) * (390 / 90) if avg_vol > 0 else 0
    if vol_ratio >= SCAN_MIN_VOL_RATIO:
        score += 20; flags.append(f"成交量 {vol_ratio:.1f}x 均量")

    iv_cur, iv_prev = s.get("iv_current"), s.get("iv_prev")
    if iv_cur and iv_prev and iv_prev > 0:
        iv_spike = (iv_cur - iv_prev) / iv_prev
        if iv_spike >= SCAN_MIN_IV_SPIKE:
            score += 25; flags.append(f"IV 飆升 {iv_spike:.0%}")

    pc = s.get("put_call")
    if pc is not None:
        if pc < 0.35:
            score += 20; flags.append(f"P/C={pc:.2f} 大量買Call"); call_signals += 2
        elif pc < 0.6:
            call_signals += 1
        elif pc > 1.5:
            score += 20; flags.append(f"P/C={pc:.2f} 大量買Put"); put_signals += 2
        elif pc > 1.0:
            put_signals += 1

    if pc is not None and pc > 1.2:
        direction = "PUT" if pre <= 0 else ("CALL" if call_signals > put_signals and pre > 3 else "PUT")
    elif pc is not None and pc < 0.6:
        direction = "CALL" if pre >= 0 else ("PUT" if put_signals > call_signals and pre < -3 else "CALL")
    elif pre > 0: direction = "CALL"
    elif pre < 0: direction = "PUT"
    else: direction = "PUT" if put_signals > call_signals else "CALL"

    if call_signals >= 3 or put_signals >= 3:
        score += 15; flags.append(f"{'多頭' if call_signals >= put_signals else '空頭'}信號三重確認")

    if direction == "CALL" and put_signals > call_signals + 1:
        score = max(score - 15, 0)
    elif direction == "PUT" and call_signals > put_signals + 1:
        score = max(score - 15, 0)

    return score, flags, direction


# ══════════════════════════════════════════════════════════
# 5. 全市場掃描
# ══════════════════════════════════════════════════════════
def scan_options(tickers: list) -> list:
    print(f"[掃描] {len(tickers)} 支股票...")
    results  = []
    today_str = datetime.date.today().isoformat()

    for i, ticker in enumerate(tickers):
        if i % 50 == 0: print(f"  進度: {i}/{len(tickers)}")
        data = fetch_stock_data(ticker)
        if not data: continue

        valid_dates = [d for d in data.get("exp_dates", []) if d > today_str]
        if not valid_dates: continue
        data["exp_dates"] = valid_dates

        sc, flags, direction = score_option(data)
        if sc >= 30 and flags:
            pc  = data.get("put_call")
            pre = data.get("pre_change") or 0
            consistency = "高" if (
                (direction == "CALL" and (pre > 0 or (pc and pc < 0.6))) or
                (direction == "PUT"  and (pre < 0 or (pc and pc > 1.0)))
            ) else "中"
            data["direction_consistency"] = consistency
            results.append({**data, "score": sc, "flags": flags, "direction": direction})

        time.sleep(0.15 + random.uniform(0, 0.1))

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:SCAN_TOP_N]


def fetch_technical_data(ticker: str) -> dict:
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        hist = t.history(period="3mo")
        if len(hist) < 20: return {}

        close = hist["Close"]
        delta = close.diff()
        gain  = delta.where(delta > 0, 0).rolling(14).mean()
        loss  = -delta.where(delta < 0, 0).rolling(14).mean()
        rs    = gain / loss
        rsi   = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)

        ma20       = round(float(close.rolling(20).mean().iloc[-1]), 2)
        ma50       = round(float(close.rolling(50).mean().iloc[-1]), 2)
        current    = round(float(close.iloc[-1]), 2)
        resistance = round(float(close.rolling(20).max().iloc[-1]), 2)
        support    = round(float(close.rolling(20).min().iloc[-1]), 2)
        atr        = round(float((hist["High"] - hist["Low"]).rolling(14).mean().iloc[-1]), 2)

        try:
            cal       = t.calendar
            earn_date = str(cal.get("Earnings Date", ["—"])[0])[:10] if cal else "—"
        except Exception:
            earn_date = "—"

        short_pct = short_ratio = None
        try:
            info        = t.info
            short_pct   = round(float(info.get("shortPercentOfFloat", 0) or 0) * 100, 2)
            short_ratio = round(float(info.get("shortRatio", 0) or 0), 1)
        except Exception:
            pass

        ma_signal    = "多頭排列" if current > ma20 > ma50 else "空頭排列" if current < ma20 < ma50 else "整理中"
        rsi_signal   = "超買" if rsi > 70 else "超賣" if rsi < 30 else "正常"
        squeeze_risk = "高" if (short_pct or 0) > 15 else "中" if (short_pct or 0) > 8 else "低"

        return {
            "ticker": ticker, "current": current,
            "rsi": rsi, "rsi_signal": rsi_signal,
            "ma20": ma20, "ma50": ma50, "ma_signal": ma_signal,
            "resistance": resistance, "support": support, "atr": atr,
            "earn_date": earn_date,
            "short_pct": short_pct, "short_ratio": short_ratio, "squeeze_risk": squeeze_risk,
            "call_entry_low":  round(support + atr * 0.3, 2),
            "call_entry_high": round(support + atr * 0.8, 2),
            "put_entry_low":   round(resistance - atr * 0.8, 2),
            "put_entry_high":  round(resistance - atr * 0.3, 2),
            "atm_call_est":    round(atr * 1.2, 2),
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
# 6. 財經新聞模組（只抓7天內）
# ══════════════════════════════════════════════════════════
def fetch_market_news() -> list:
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
            r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:3]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                link    = item.findtext("link", "")
                if title and is_recent_news(pubdate):
                    news_items.append({
                        "category": label,
                        "title":    title[:120],
                        "date":     pubdate[:16],
                        "link":     link,
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 財經新聞RSS({label}): {e}")

    # 去重
    seen, unique = set(), []
    for n in news_items:
        key = n["title"][:40]
        if key not in seen:
            seen.add(key); unique.append(n)
    return unique[:10]


# ══════════════════════════════════════════════════════════
# 7. 事件日曆模組（只抓本週 + 下週事件）
# ══════════════════════════════════════════════════════════
def fetch_event_calendar() -> list:
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
            r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:3]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                # 事件日曆放寬到 30 天（讓下週事件也能進來）
                if title and is_recent_news(pubdate, max_days=30):
                    events.append({
                        "category": label,
                        "title":    title[:100],
                        "date":     pubdate[:16],
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 事件日曆RSS({label}): {e}")

    seen, unique = set(), []
    for e in events:
        key = e["title"][:40]
        if key not in seen:
            seen.add(key); unique.append(e)
    return unique[:10]


def fetch_momentum_stocks() -> list:
    """
    掃描自選股過去兩天累計升跌幅，找出爆升/爆跌股
    累計升跌超過5%自動列入重點追蹤
    """
    print("[爆升股追蹤] 掃描中...")
    momentum_stocks = []
    try:
        import yfinance as yf
        for ticker in WATCHLIST + ["DELL", "PLTR", "ARM", "SMCI", "MU", "SNOW", "CRWD", "PANW"]:
            try:
                t    = yf.Ticker(ticker)
                hist = t.history(period="5d")
                if len(hist) < 3:
                    continue

                # 過去兩天累計漲跌幅
                close_today    = float(hist["Close"].iloc[-1])
                close_2day_ago = float(hist["Close"].iloc[-3])
                two_day_chg    = round((close_today - close_2day_ago) / close_2day_ago * 100, 2)

                # 今天單日漲跌幅
                close_yesterday = float(hist["Close"].iloc[-2])
                today_chg       = round((close_today - close_yesterday) / close_yesterday * 100, 2)

                # 成交量異動
                avg_vol  = float(hist["Volume"].iloc[:-1].mean())
                last_vol = float(hist["Volume"].iloc[-1])
                vol_ratio= round(last_vol / avg_vol, 1) if avg_vol > 0 else 1.0

                # 只記錄有明顯動能的股票
                if abs(two_day_chg) >= 4 or abs(today_chg) >= 3:
                    momentum_stocks.append({
                        "ticker":       ticker,
                        "today_chg":    today_chg,
                        "two_day_chg":  two_day_chg,
                        "close":        round(close_today, 2),
                        "vol_ratio":    vol_ratio,
                        "momentum":     "爆升" if two_day_chg >= 4 else "爆跌" if two_day_chg <= -4 else "強勢" if today_chg >= 3 else "弱勢",
                    })
                time.sleep(0.2)
            except Exception:
                continue

        # 按兩日漲幅絕對值排序
        momentum_stocks.sort(key=lambda x: abs(x["two_day_chg"]), reverse=True)
        print(f"  發現 {len(momentum_stocks)} 支動能股")

    except Exception as e:
        print(f"[WARN] 爆升股追蹤: {e}")

    return momentum_stocks[:8]


# ══════════════════════════════════════════════════════════
# 新增：個股新聞掃描（追蹤爆升/爆跌原因）
# ══════════════════════════════════════════════════════════
def fetch_stock_news(tickers: list) -> dict:
    """
    針對自選股抓取最新新聞，判斷新聞方向（利多/利空/中性）
    用於修正技術指標與新聞面矛盾的問題
    """
    print("[個股新聞] 抓取中...")
    stock_news = {}

    # 正面關鍵詞
    bullish_keywords = [
        "beat", "surge", "soar", "rally", "jump", "rise", "gain",
        "record", "upgrade", "buy", "outperform", "strong", "growth",
        "profit", "revenue beat", "raised guidance", "partnership",
        "deal", "contract", "breakthrough", "爆升", "大漲", "創新高"
    ]
    # 負面關鍵詞
    bearish_keywords = [
        "miss", "fall", "drop", "decline", "cut", "downgrade", "sell",
        "underperform", "weak", "loss", "revenue miss", "lowered guidance",
        "lawsuit", "investigation", "recall", "layoff", "warning",
        "爆跌", "大跌", "下調", "虧損"
    ]

    for ticker in tickers:
        try:
            query = f"{ticker} stock news today 2026"
            encoded = requests.utils.quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root  = ET.fromstring(r.content)
            items = list(root.iter("item"))[:3]

            news_list  = []
            bull_count = 0
            bear_count = 0

            for item in items:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                if not title or not is_recent_news(pubdate, max_days=3):
                    continue

                title_lower = title.lower()
                bull = sum(1 for k in bullish_keywords if k in title_lower)
                bear = sum(1 for k in bearish_keywords if k in title_lower)
                bull_count += bull
                bear_count += bear

                news_list.append({
                    "title": title[:100],
                    "date":  pubdate[:16],
                    "bull":  bull,
                    "bear":  bear,
                })

            # 判斷新聞方向
            if bull_count > bear_count + 1:
                news_direction = "bullish"
            elif bear_count > bull_count + 1:
                news_direction = "bearish"
            else:
                news_direction = "neutral"

            stock_news[ticker] = {
                "news":           news_list,
                "news_direction": news_direction,
                "bull_count":     bull_count,
                "bear_count":     bear_count,
            }
            time.sleep(0.3)

        except Exception as e:
            print(f"[WARN] 個股新聞 {ticker}: {e}")

    print(f"  掃描 {len(stock_news)} 支個股新聞完成")
    return stock_news


# ══════════════════════════════════════════════════════════
# 8. 週末專用：下週部署分析
# ══════════════════════════════════════════════════════════
def fetch_weekend_analysis(mode: str) -> dict:
    """週六、週日專用：抓取下週關鍵事件"""
    today = datetime.date.today()
    print(f"[週末分析-{mode}] 抓取下週事件...")
    next_week_events = []

    queries = [
        ("next week earnings reports S&P500 2026", "財報"),
        ("next week CPI inflation Federal Reserve 2026", "CPI/Fed"),
        ("next week FDA PDUFA drug approval 2026", "FDA"),
        ("next week options expiration market events 2026", "期權到期"),
        ("next week stock market outlook preview 2026", "市場展望"),
    ]
    for query, label in queries:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title = item.findtext("title", "").strip()
                if title and is_recent_news(item.findtext("pubDate",""), max_days=14):
                    next_week_events.append({
                        "category": label,
                        "title": title[:100],
                        "date": item.findtext("pubDate", "")[:16],
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 週末分析RSS({label}): {e}")

    # 計算下週一到下週五
    days_to_monday = (7 - today.weekday()) % 7 or 7
    next_monday = today + datetime.timedelta(days=days_to_monday)
    next_friday = next_monday + datetime.timedelta(days=4)

    return {
        "mode":              mode,       # "saturday" or "sunday"
        "next_week_events":  next_week_events[:8],
        "next_monday":       str(next_monday),
        "next_friday":       str(next_friday),
    }


# ══════════════════════════════════════════════════════════
# 9. 星期五特別分析
# ══════════════════════════════════════════════════════════
def fetch_friday_weekly_analysis() -> dict:
    today = datetime.date.today()
    if today.weekday() != 4:
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
            r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title = item.findtext("title", "").strip()
                if title and is_recent_news(item.findtext("pubDate",""), max_days=14):
                    next_week_events.append({
                        "category": label,
                        "title": title[:100],
                        "date": item.findtext("pubDate", "")[:16],
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 星期五RSS({label}): {e}")

    next_monday = today + datetime.timedelta(days=3)
    next_friday = today + datetime.timedelta(days=8)
    return {
        "is_friday":        True,
        "next_week_events": next_week_events[:8],
        "next_monday":      str(next_monday),
        "next_friday":      str(next_friday),
    }


# ══════════════════════════════════════════════════════════
# 10. FDA 行事曆
# ══════════════════════════════════════════════════════════
def fetch_fda_calendar() -> list:
    """
    抓取 FDA 審批事件。
    - 使用多個搜尋關鍵詞增加命中率
    - 放寬至90天（PDUFA日期提前很久就有報道）
    - 嘗試從標題提取股票代碼
    """
    # 常見生技/製藥股代碼對照表
    BIOTECH_TICKERS = {
        "biogen": "BIIB", "biib": "BIIB",
        "moderna": "MRNA", "mrna": "MRNA",
        "pfizer": "PFE", "pfe": "PFE",
        "bristol": "BMY", "bmy": "BMY",
        "merck": "MRK", "mrk": "MRK",
        "abbvie": "ABBV", "abbv": "ABBV",
        "amgen": "AMGN", "amgn": "AMGN",
        "gilead": "GILD", "gild": "GILD",
        "regeneron": "REGN", "regn": "REGN",
        "vertex": "VRTX", "vrtx": "VRTX",
        "alnylam": "ALNY", "alny": "ALNY",
        "blueprint": "BPMC", "bpmc": "BPMC",
        "sarepta": "SRPT", "srpt": "SRPT",
        "ionis": "IONS", "ions": "IONS",
        "jazz": "JAZZ", "jazz pharma": "JAZZ",
        "neurocrine": "NBIX", "nbix": "NBIX",
        "biomarin": "BMRN", "bmrn": "BMRN",
        "ultragenyx": "RARE", "rare": "RARE",
        "praxis": "PRAX", "prax": "PRAX",
        "akeso": "AKSO",
    }

    def extract_ticker(title: str) -> str:
        """從新聞標題嘗試提取股票代碼"""
        title_lower = title.lower()
        for keyword, ticker in BIOTECH_TICKERS.items():
            if keyword in title_lower:
                return ticker
        # 嘗試抓取括號內的代碼，如 "Biogen (BIIB)"
        import re
        match = re.search(r'\(([A-Z]{2,5})\)', title)
        if match:
            return match.group(1)
        return ""

    events = []
    queries = [
        "FDA drug approval PDUFA 2026",
        "FDA approval decision biotech 2026",
        "FDA PDUFA date clinical trial 2026",
        "FDA NDA BLA approval upcoming 2026",
    ]
    for query in queries:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r   = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:3]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                link    = item.findtext("link", "")
                if title and is_recent_news(pubdate, max_days=90):
                    ticker = extract_ticker(title)
                    events.append({
                        "title":  title[:80],
                        "date":   pubdate[:16],
                        "link":   link,
                        "ticker": ticker,
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] FDA({query[:20]}): {e}")

    # 去重
    seen, unique = set(), []
    for e in events:
        key = e["title"][:40]
        if key not in seen:
            seen.add(key)
            unique.append(e)

    # 備用：完全不過濾，直接抓最新5條
    if not unique:
        print("[FDA] 過濾後無結果，嘗試不過濾日期...")
        try:
            encoded = requests.utils.quote("FDA drug approval 2026")
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r   = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                for item in list(root.iter("item"))[:5]:
                    title = item.findtext("title", "").strip()
                    if title:
                        unique.append({
                            "title":  title[:80],
                            "date":   item.findtext("pubDate", "")[:16],
                            "link":   item.findtext("link", ""),
                            "ticker": extract_ticker(title),
                        })
        except Exception as e:
            print(f"[WARN] FDA備用: {e}")

    if not unique:
        unique = [{
            "title":  "FDA行事曆暫時無法取得，請查閱 fda.gov",
            "date":   "",
            "link":   "https://www.fda.gov/patients/drug-approvals-and-databases/drug-approvals-and-databases",
            "ticker": "",
        }]

    return unique[:5]


# ══════════════════════════════════════════════════════════
# 11. 政治風向雷達
# ══════════════════════════════════════════════════════════
def fetch_political_intelligence() -> dict:
    print("[政治雷達] 抓取中...")
    news_items = []
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
            r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:3]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                if title and is_recent_news(pubdate):
                    news_items.append({
                        "category": label,
                        "title":    title[:100],
                        "date":     pubdate[:16],
                        "link":     item.findtext("link", ""),
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 政治新聞RSS({label}): {e}")

    seen, unique_news = set(), []
    for n in news_items:
        key = n["title"][:40]
        if key not in seen:
            seen.add(key); unique_news.append(n)
    news_items = unique_news[:POLITICAL_NEWS_LIMIT]

    if QUIVER_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {QUIVER_API_KEY}"}
            r = requests.get("https://api.quiverquant.com/beta/live/congresstrading", headers=headers, timeout=10)
            if r.status_code == 200:
                for t in r.json()[:10]:
                    congress_trades.append({
                        "politician":  t.get("Representative", ""),
                        "ticker":      t.get("Ticker", ""),
                        "transaction": t.get("Transaction", ""),
                        "amount":      t.get("Amount", ""),
                        "filed":       t.get("Filed", ""),
                        "traded":      t.get("Traded", ""),
                    })
            r2 = requests.get("https://api.quiverquant.com/beta/live/trump", headers=headers, timeout=10)
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
        "news":            news_items,
        "congress_trades": congress_trades,
        "trump_signals":   trump_signals,
        "data_source":     "Quiver API + Google News" if QUIVER_API_KEY else "Google News RSS（免費版）",
    }


# ══════════════════════════════════════════════════════════
# 新增：政治實時信號追蹤（取代延遲45天的議員申報）
# ══════════════════════════════════════════════════════════
def fetch_political_realtime() -> dict:
    """
    追蹤更實時的政治信號：
    1. 特朗普 Truth Social / 最新動態
    2. 政府合約公告（誰拿到大合約）
    3. 白宮政策動向影響板塊
    4. 議員委員會職位 + 相關板塊
    """
    print("[政治實時信號] 抓取中...")

    trump_signals   = []
    gov_contracts   = []
    policy_signals  = []

    # ── 1. 特朗普最新動態 ──
    trump_queries = [
        ("trump truth social post stock market today 2026", "特朗普動態"),
        ("trump executive order policy stock impact 2026",  "行政命令"),
        ("trump tariff trade policy announcement 2026",     "關稅政策"),
        ("trump energy oil semiconductor policy 2026",      "能源/科技政策"),
    ]
    for query, label in trump_queries:
        try:
            encoded = requests.utils.quote(query)
            url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r       = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                if title and is_recent_news(pubdate, max_days=3):
                    trump_signals.append({
                        "category": label,
                        "title":    title[:100],
                        "date":     pubdate[:16],
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 特朗普動態({label}): {e}")

    # ── 2. 政府合約公告 ──
    contract_queries = [
        ("government defense contract awarded billion 2026", "國防合約"),
        ("pentagon contract NVDA Microsoft Amazon 2026",     "科技合約"),
        ("government contract LMT RTX NOC awarded 2026",    "軍工合約"),
    ]
    for query, label in contract_queries:
        try:
            encoded = requests.utils.quote(query)
            url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r       = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                if title and is_recent_news(pubdate, max_days=7):
                    # 提取相關股票代碼
                    import re as _re
                    tickers = _re.findall(r'\b([A-Z]{2,5})\b', title)
                    exclude = {"THE","GOP","SEC","IRS","FBI","CIA","FDA","ETF","IPO","CEO","CFO","AI","US","UK","EU"}
                    tickers = [t for t in tickers if t not in exclude]
                    gov_contracts.append({
                        "category": label,
                        "title":    title[:100],
                        "date":     pubdate[:16],
                        "tickers":  tickers[:3],
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 政府合約({label}): {e}")

    # ── 3. 白宮政策動向 ──
    policy_queries = [
        ("white house policy semiconductor chip AI 2026",   "半導體/AI政策"),
        ("white house energy oil gas policy stock 2026",    "能源政策"),
        ("federal reserve white house interest rate 2026",  "貨幣政策"),
        ("white house china trade tariff stock 2026",       "貿易政策"),
    ]
    for query, label in policy_queries:
        try:
            encoded = requests.utils.quote(query)
            url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r       = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:2]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                if title and is_recent_news(pubdate, max_days=5):
                    policy_signals.append({
                        "category": label,
                        "title":    title[:100],
                        "date":     pubdate[:16],
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 白宮政策({label}): {e}")

    # 去重
    def dedup(items):
        seen, unique = set(), []
        for item in items:
            key = item["title"][:40]
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    result = {
        "trump_signals":  dedup(trump_signals)[:6],
        "gov_contracts":  dedup(gov_contracts)[:4],
        "policy_signals": dedup(policy_signals)[:6],
        # 議員委員會職位對應板塊（靜態數據，不需要抓取）
        "committee_sectors": [
            {"committee": "軍事委員會", "sectors": ["LMT","RTX","NOC","GD","BA"], "bias": "利多國防股"},
            {"committee": "科技委員會", "sectors": ["NVDA","MSFT","GOOGL","META","AMZN"], "bias": "利多科技股"},
            {"committee": "能源委員會", "sectors": ["XOM","CVX","COP","OXY"], "bias": "利多能源股"},
            {"committee": "金融委員會", "sectors": ["JPM","BAC","GS","MS"], "bias": "利多金融股"},
        ],
    }

    print(f"  特朗普動態 {len(result['trump_signals'])} 條 · 政府合約 {len(result['gov_contracts'])} 條 · 政策信號 {len(result['policy_signals'])} 條")
    return result


# ══════════════════════════════════════════════════════════
# 12a. 昨日推介追蹤（成效追蹤）
# ══════════════════════════════════════════════════════════
def fetch_previous_recommendations() -> list:
    """
    讀取 index.json 最新一條記錄的 trade_plans，
    抓取各股票當前價格，計算昨日推介的表現。
    返回：[{ticker, direction, strike, entry_zone, current_price, chg_pct, result}]
    """
    index_path = "web/index.json"
    if not os.path.exists(index_path):
        print("  [跳過] 未找到 index.json，跳過昨日追蹤")
        return []

    try:
        with open(index_path) as f:
            index = json.load(f)
    except Exception as e:
        print(f"  [WARN] 讀取 index.json 失敗: {e}")
        return []

    # 找最近一條（排除今日）
    today_str = datetime.date.today().isoformat()
    prev_entry = None
    for entry in index:
        if entry.get("date", "") != today_str:
            prev_entry = entry
            break

    if not prev_entry:
        print("  [跳過] 無昨日記錄")
        return []

    prev_date   = prev_entry.get("date", "")
    trade_plans = prev_entry.get("trade_plans", [])

    # 若 index.json 舊格式不含 trade_plans，嘗試從對應 HTML 記錄略過
    if not trade_plans:
        # 嘗試從 top_ticker 建一個最簡單記錄
        top = {
            "ticker":     prev_entry.get("top_ticker", ""),
            "direction":  prev_entry.get("top_dir", ""),
            "strike":     prev_entry.get("top_strike", ""),
            "entry_zone": prev_entry.get("top_entry", ""),
        }
        if top["ticker"]:
            trade_plans = [top]
        else:
            print("  [跳過] 昨日記錄無推介資料")
            return []

    import yfinance as yf
    results = []
    for plan in trade_plans[:5]:   # 最多追蹤5筆
        ticker = plan.get("ticker", "").strip()
        if not ticker or ticker == "—":
            continue
        direction   = plan.get("direction", "")
        strike_raw  = plan.get("strike", "—")
        entry_zone  = plan.get("entry_zone", "—")

        try:
            info = yf.Ticker(ticker).fast_info
            current = round(float(info.last_price), 2)
        except Exception:
            current = None

        # 解析 entry_zone 中間值作為入場基準
        entry_mid = None
        if entry_zone and entry_zone != "—":
            nums = re.findall(r"[\d.]+", str(entry_zone))
            if nums:
                entry_mid = sum(float(x) for x in nums) / len(nums)

        chg_pct = None
        result  = "未知"
        if current and entry_mid:
            chg_pct = round((current - entry_mid) / entry_mid * 100, 1)
            if direction == "CALL":
                result = "✅ 獲利" if chg_pct > 1.5 else ("🔴 虧損" if chg_pct < -1.5 else "⚪ 持平")
            elif direction == "PUT":
                result = "✅ 獲利" if chg_pct < -1.5 else ("🔴 虧損" if chg_pct > 1.5 else "⚪ 持平")

        results.append({
            "date":        prev_date,
            "ticker":      ticker,
            "direction":   direction,
            "strike":      strike_raw,
            "entry_zone":  entry_zone,
            "current":     current,
            "chg_pct":     chg_pct,
            "result":      result,
        })
        print(f"    {ticker} {direction} 入場{entry_zone} → 現價{current} ({chg_pct:+.1f}%) {result}" if chg_pct is not None else f"    {ticker} {direction} 無法取得價格")

    return results


# ══════════════════════════════════════════════════════════
# 12b. 分析師評級變動掃描
# ══════════════════════════════════════════════════════════
def fetch_analyst_ratings() -> list:
    """
    掃描 Google News RSS 找升降評事件（3天內），
    返回：[{ticker, firm, action, old_rating, new_rating, price_target, title, date}]
    """
    queries = []
    for t in WATCHLIST:
        queries.append((f"{t} analyst upgrade downgrade price target 2026", t))
    # 加入 S&P 整體分析師評級
    queries.append(("stock analyst upgrade downgrade rating change today 2026", "MARKET"))

    results  = []
    seen     = set()

    upgrade_kw   = ["upgrade", "upgraded", "raises", "buy", "outperform", "overweight",
                    "strong buy", "positive", "initiates coverage", "reiterates buy"]
    downgrade_kw = ["downgrade", "downgraded", "lowers", "sell", "underperform",
                    "underweight", "negative", "cuts", "reduces", "bear"]

    for query, hint_ticker in queries:
        try:
            encoded = requests.utils.quote(query)
            url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            r       = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in list(root.iter("item"))[:3]:
                title   = item.findtext("title", "").strip()
                pubdate = item.findtext("pubDate", "")
                link    = item.findtext("link", "")

                if not title or not is_recent_news(pubdate, max_days=3):
                    continue

                title_lower = title.lower()
                # 必須含評級相關字眼
                is_upgrade   = any(k in title_lower for k in upgrade_kw)
                is_downgrade = any(k in title_lower for k in downgrade_kw)
                if not is_upgrade and not is_downgrade:
                    continue

                # 去重
                key = title[:50]
                if key in seen:
                    continue
                seen.add(key)

                # 嘗試從標題解析 ticker
                ticker_found = hint_ticker if hint_ticker != "MARKET" else ""
                for t in WATCHLIST:
                    if t in title.upper():
                        ticker_found = t
                        break

                # 解析目標價
                price_target = "—"
                pt_match = re.search(r'\$\s*([\d,.]+)', title)
                if pt_match:
                    price_target = "$" + pt_match.group(1)

                # 解析券商名稱（第一個詞若非股票代碼視為券商）
                words = title.split()
                firm  = words[0] if words and not words[0].isupper() else "—"

                action = "升評" if is_upgrade else "降評"

                results.append({
                    "ticker":       ticker_found,
                    "firm":         firm[:20],
                    "action":       action,
                    "price_target": price_target,
                    "title":        title[:100],
                    "date":         pubdate[:16],
                    "direction":    "CALL" if is_upgrade else "PUT",
                })
            time.sleep(0.3)
        except Exception as e:
            print(f"[WARN] 分析師評級({hint_ticker}): {e}")

    results.sort(key=lambda x: x["date"], reverse=True)
    results = results[:10]
    print(f"  發現 {len(results)} 條分析師評級變動")
    return results


def ai_analyze(
    watchlist_data, scan_results, fda_events, political_data,
    fear_greed, vix_data, market_news, event_calendar, stock_news,
    momentum_stocks, political_realtime, day_mode: str, friday_data=None, weekend_data=None,
    prev_recs=None, analyst_ratings=None
) -> dict:

    today = datetime.date.today().strftime("%Y年%m月%d日")
    is_friday  = (day_mode == "friday")
    is_weekend = day_mode in ("saturday", "sunday")

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
                "squeeze_risk":    tech.get("squeeze_risk"),
                "call_entry_low":  tech.get("call_entry_low"),
                "call_entry_high": tech.get("call_entry_high"),
                "put_entry_low":   tech.get("put_entry_low"),
                "put_entry_high":  tech.get("put_entry_high"),
                "atm_call_est":    tech.get("atm_call_est"),
            })

    payload = {
        "date":               today,
        "day_mode":           day_mode,
        "fear_greed":         fear_greed,
        "vix":                vix_data,
        "market_news":        market_news[:8],
        "event_calendar":     event_calendar[:8],
        "stock_news":         stock_news,
        "momentum_stocks":    momentum_stocks,
        "political_realtime": political_realtime,
        "technical":          tech_summary,
        "top_options":        scan_results[:5],
        "fda_events":         fda_events[:3],
        "political_news":     political_data.get("news", [])[:6],
        "congress_trades":    political_data.get("congress_trades", [])[:5],
        "prev_recommendations": prev_recs or [],
        "analyst_ratings":      analyst_ratings or [],
    }

    # 週末加入下週數據
    if is_weekend and weekend_data:
        payload["next_week_events"] = weekend_data.get("next_week_events", [])
        payload["next_monday"]      = weekend_data.get("next_monday", "")
        payload["next_friday"]      = weekend_data.get("next_friday", "")

    # 週五加入下週數據
    if is_friday and friday_data:
        payload["next_week_events"] = friday_data.get("next_week_events", [])
        payload["next_monday"]      = friday_data.get("next_monday", "")
        payload["next_friday"]      = friday_data.get("next_friday", "")

    # ── 週末專用 prompt ──
    if is_weekend:
        mode_label = "週六（本週回顧 + 下週部署）" if day_mode == "saturday" else "週日（下週展望 + 下週完整部署）"
        weekend_prompt = f"""

【{mode_label}】今天美股不開市，請提供週末版分析，加入以下 JSON：
"weekend_analysis": {{
  "this_week_recap": "本週市場回顧，重點大事50字",
  "this_week_winners": ["本週強勢股1", "本週強勢股2", "本週強勢股3"],
  "this_week_losers": ["本週弱勢股1", "本週弱勢股2"],
  "next_week_outlook": "下週市場整體展望60字",
  "next_week_theme": "下週主題10字",
  "next_week_key_events": [
    {{"day": "週一", "event": "事件描述", "impact": "高/中/低"}},
    {{"day": "週二", "event": "事件描述", "impact": "高/中/低"}},
    {{"day": "週三", "event": "事件描述", "impact": "高/中/低"}},
    {{"day": "週四", "event": "事件描述", "impact": "高/中/低"}},
    {{"day": "週五", "event": "事件描述", "impact": "高/中/低"}}
  ],
  "next_week_picks": [
    {{
      "ticker": "股票代碼",
      "direction": "CALL或PUT",
      "strategy": "策略",
      "strike": "建議Strike",
      "expiry": "建議到期日YYYY-MM-DD",
      "entry_day": "建議週幾入場",
      "catalyst": "催化劑20字",
      "signal_strength": 1到5整數
    }}
  ],
  "watchlist_for_monday": ["週一開盤重點關注股票，最多5個"],
  "risk_factors": "下週主要風險30字",
  "strategy_tip": "下週操作策略建議40字"
}}"""
    else:
        weekend_prompt = ""

    # ── 週五專用 prompt ──
    friday_prompt = ""
    if is_friday:
        friday_prompt = """

【星期五特別分析】請額外加入以下 JSON：
"friday_analysis": {
  "today_action": "今天應放出期權還是繼續持有？（放出/持有/部分放出）",
  "today_reason": "原因50字",
  "weekend_risk": "持倉過週末主要風險30字",
  "next_week_outlook": "下週展望50字",
  "next_week_key_events": ["重要事件1", "重要事件2", "重要事件3"],
  "should_buy_today": "今天是否適合買入下週期權？是/否/謹慎",
  "buy_reason": "原因40字",
  "next_week_picks": [
    {
      "ticker": "代碼", "direction": "CALL或PUT", "strategy": "策略",
      "strike": "Strike", "expiry": "到期日YYYY-MM-DD",
      "catalyst": "催化劑20字", "entry_note": "入場建議20字",
      "signal_strength": 1到5整數
    }
  ],
  "avoid_reason": "不宜過週末持倉的股票及原因30字"
}"""

    prompt = f"""你是專業美股期權交易分析師。以下是 {today}（{day_mode}）的完整數據：

{json.dumps(payload, ensure_ascii=False, indent=2)}
{weekend_prompt}
{friday_prompt}

請用繁體中文回傳純 JSON（不要 markdown）：
{{
  "date": "{today}",
  "day_mode": "{day_mode}",
  "market_mood": "多頭/空頭/震盪",
  "mood_score": 0到100整數,
  "headline": "今日最重要一句話20字以內",
  "news_summary": "根據財經新聞的市場重點摘要50字",
  "market_news_analysis": [
    {{
      "title": "新聞英文標題（原文）",
      "zh_summary": "中文摘要20字",
      "impact": "對市場的具體影響20字",
      "direction": "利多/利空/中性",
      "affected_tickers": ["受影響股票代碼最多3個"],
      "action": "買Call/買Put/觀望/持有"
    }}
  ],
  "key_events_today": ["重要事件1", "重要事件2", "重要事件3"],
  "trade_plans": [
    {{
      "rank": 1,
      "ticker": "代碼",
      "direction": "CALL或PUT",
      "strategy": "直接買Call/直接買Put/Bull Call Spread/Bear Put Spread/Straddle",
      "strike": "具體行使價數字",
      "expiry": "到期日YYYY-MM-DD",
      "expiry_reason": "選期理由15字",
      "entry_zone": "入場價格區間",
      "entry_timing": "入場時機描述",
      "delta_range": "建議Delta如0.3-0.45",
      "est_premium": "預估費用範圍",
      "signal_strength": 1到5整數,
      "signals": ["信號1", "信號2", "信號3"],
      "max_loss": "最大虧損",
      "target_gain": "目標獲利",
      "stop_loss_price": "止損股價",
      "stop_loss_option": "期權止損條件",
      "squeeze_risk": "高/中/低",
      "risk": "主要風險15字",
      "political_factor": "政治因素，無則填無",
      "best_day_to_enter": "最佳入場時段"
    }}
  ],
  "portfolio_suggestion": {{
    "theme": "今日組合主題20字",
    "combination": "組合描述40字",
    "risk_level": "保守/平衡/積極",
    "total_budget": "建議預算佔比",
    "notes": "注意事項40字"
  }},
  "top_option_pick": {{
    "ticker": "今日最強期權機會",
    "direction": "CALL或PUT",
    "reason": "原因30字",
    "key_strike": "Strike數字",
    "entry_zone": "入場區間",
    "risk": "風險20字"
  }},
  "squeeze_watchlist": ["高軋空風險股票，最多3個"],
  "political_summary": "政治風向影響50字",
  "political_hot_tickers": ["受影響股票3個"],
  "political_sentiment": "利多/利空/中性",
  "congress_highlight": "最值得關注的國會動態30字",
  "political_realtime_analysis": {{
    "trump_market_impact": "特朗普最新動態對市場的影響50字",
    "trump_affected_tickers": ["受特朗普動態影響的股票代碼，最多3個"],
    "trump_direction": "利多/利空/中性",
    "gov_contract_picks": [
      {{
        "ticker": "拿到合約的公司代碼",
        "contract": "合約描述15字",
        "impact": "預計股價影響20字",
        "suggestion": "CALL/PUT/觀望"
      }}
    ],
    "policy_sector_impact": [
      {{
        "policy": "政策名稱10字",
        "sectors": ["受影響板塊股票代碼"],
        "direction": "利多/利空",
        "urgency": "高/中/低"
      }}
    ],
    "best_political_trade": "今日最佳政治驅動交易機會30字"
  }},
  "fda_analysis": [
    {{
      "ticker": "相關股票代碼，如BIIB，若無則填—",
      "company": "公司名稱",
      "drug": "藥物或療法名稱15字以內",
      "event_type": "PDUFA審批/臨床數據/FDA聆訊",
      "expected_date": "預計日期YYYY-MM-DD，不確定填—",
      "approval_prob": "通過機率如70%，不確定填—",
      "if_approved": "通過的話股價反應和操作建議20字",
      "if_rejected": "拒絕的話股價反應和操作建議20字",
      "call_strike": "若看漲建議Strike，不適用填—",
      "put_strike": "若看跌建議Strike，不適用填—",
      "expiry_suggest": "建議到期日YYYY-MM-DD",
      "entry_timing": "建議何時入場15字",
      "risk_note": "主要風險15字"
    }}
  ],
  "sector_rotation": "板塊輪動觀察40字",
  "key_movers": [
    {{"ticker": "代碼", "signal": "強勢或弱勢或觀察", "reason": "原因20字"}}
  ],
  "prev_rec_review": [
    {{
      "ticker": "昨日推介代碼",
      "direction": "CALL或PUT",
      "result": "✅ 獲利/🔴 虧損/⚪ 持平/⏳ 未到期",
      "chg_pct": "實際股價變動如+2.3%",
      "lesson": "經驗教訓或後市展望20字"
    }}
  ],
  "analyst_highlights": [
    {{
      "ticker": "股票代碼",
      "firm": "券商名稱",
      "action": "升評/降評",
      "price_target": "目標價如$200",
      "impact": "對股價影響20字",
      "trade_suggestion": "CALL/PUT/觀望"
    }}
  ],
  "risk_warning": "今日最大風險30字",
  "summary": "整體摘要100字"
}}

【政治實時信號原則（取代延遲45天的議員申報）】
- political_realtime 包含特朗普動態、政府合約、白宮政策，延遲只有1-3天
- 特朗普 Truth Social 貼文或行政命令 → 立即分析受益/受損板塊
- 政府合約公告 → 中標公司股票通常當天或隔天反應，是最實時的政治信號
- 白宮政策方向（半導體/能源/貿易）→ 判斷中長線板塊方向
- 議員申報延遲最長45天，參考價值低，不作主要分析依據
- 但若有最新議員新聞（1週內），仍可作輔助參考

【數據誠信原則（最重要）】
- 每個 trade_plans 必須標明數據來源和日期
- Strike 和入場區間必須基於真實的支撐阻力位計算，不能憑空估算
- 不能虛構新聞標題、股價或回測結果
- 沒有即時數據的推斷必須在 signals 中標明「估算」
- 事實和推論必須分開：有數據支持的才能寫，沒有就直接填「數據不足」
- 冇即時數據就停止估算，不要填假數字

【市場機會分析框架】
- 先列出能核實的最新數據和日期
- 找出值得研究的交易設定，按信號強度排序
- 講明可能入場區、目標、失效條件和主要風險
- 沒有即時數據就停止估算

【關鍵價位框架】
- 每個支撐阻力位必須解釋依據（均線/歷史高低點/成交密集區）
- 標示數據日期
- 不能假裝知道即時價格，strike 必須基於技術位計算

【最重要：新聞優先原則】
- 每個 trade_plans 必須有對應的新聞催化劑或爆升/爆跌原因
- 若 stock_news 顯示個股新聞方向與技術指標方向矛盾：
  → 新聞方向優先，技術指標降權
  → 例如：技術指標顯示超買建議PUT，但新聞bullish → 改為CALL或觀望
  → 例如：技術指標建議CALL，但新聞bearish → 改為PUT或觀望
- 沒有新聞催化劑支撐的推介，signal_strength 最高只能給 3

【排除條件（以下情況不推介）】
- 過去兩天升跌幅度不足1%的股票 → 不推介，期權時間值損耗太大
- 新聞方向與技術方向完全相反且無法判斷 → 填觀望，不給操作建議
- IV極低（低於20%）且無催化劑 → 不推介

【爆升股追蹤原則】
- momentum_stocks 列出了過去兩天爆升/爆跌的股票，必須優先分析
- 若某股票兩日累計升幅超過5%，必須列入 trade_plans 說明：
  → 爆升原因（結合 stock_news 和 market_news）
  → 是否仍有追入空間（看 RSI、均線位置）
  → 過熱則建議觀望或反向 PUT
- 若某股票兩日累計跌幅超過5%，分析是否有反彈機會
- Dell、AMD、PLTR 等非自選股若出現在 momentum_stocks，也要分析

【其他原則】
- 週末模式（saturday/sunday）：trade_plans 是下週預備清單，非今日操作
- strike 必須根據當前股價和技術支撐阻力位給出具體數字
- 財報前一律建議Spread策略
- signal_strength 需3個以上信號同向才給4-5分
- fda_analysis 每個事件必須給出明確的操作方向（Call/Put/觀望）和Strike
- FDA 審批前一天入場，到期日選審批日後一週
- 若無法識別具體股票代碼，ticker填—並說明原因
- 若新聞標題含approved/granted/cleared/already approved等字眼，代表事件已發生
  → if_approved填「已批准，事件已計價，不建議入場」
  → if_rejected填「—」
  → call_strike和put_strike全部填「—」
  → entry_timing填「事件已發生，觀望」
- 只有upcoming/expected/PDUFA date/anticipated等未來式字眼才生成操作建議

【昨日推介成效追蹤（prev_recommendations）】
- prev_recommendations 含昨日推介的股票、入場區間、現時股價、漲跌幅
- 必須逐一填入 prev_rec_review，包括：
  → result：根據 chg_pct 和 direction 判斷（CALL漲>1.5%=✅，跌<-1.5%=🔴，否則⚪）
  → lesson：若虧損，說明錯誤判斷或市場變化；若獲利，確認信號是否有效
- 若無昨日記錄，prev_rec_review 填空陣列 []

【分析師評級原則（analyst_ratings）】
- analyst_ratings 含最新券商升降評事件（3天內）
- 升評（upgrade/buy）+ 目標價 → 通常短期利多，CALL方向
- 降評（downgrade/sell）→ 通常短期利空，PUT方向
- 必須填入 analyst_highlights，優先選影響自選股（WATCHLIST）的評級
- 若同一股票同日有升評也有降評（分歧），說明分歧并建議觀望"""

    # ── 動態取得可用模型，再補充固定備用清單 ──
    # 先嘗試從 API 列出含 "flash" 的模型，找到就插到優先位置
    dynamic_models = []
    try:
        for m in gemini_client.models.list():
            name = getattr(m, "name", "") or ""
            # name 格式為 "models/gemini-xxx"，取後半部
            short = name.replace("models/", "")
            # 只要 flash 或 pro，且支援 generateContent
            methods = getattr(m, "supported_generation_methods", []) or []
            if ("generateContent" in methods
                    and ("flash" in short or "pro" in short)
                    and "gemini" in short):
                dynamic_models.append(short)
        # 按名稱排序：數字越大越新，lite 放前面（速度快）
        dynamic_models.sort(key=lambda x: (
            -sum(int(c) for c in re.findall(r'\d', x)),
            "lite" not in x
        ))
        print(f"  [模型列表] 可用: {dynamic_models[:5]}")
    except Exception as e:
        print(f"  [WARN] 無法列出模型: {e}")

    # 固定備用清單（已知曾可用）
    fallback_models = [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.0-flash",
        "gemini-2.0-flash-lite",
    ]
    # 合並：動態優先，去重
    seen_m = set()
    models_to_try = []
    for m in dynamic_models + fallback_models:
        if m not in seen_m:
            seen_m.add(m)
            models_to_try.append(m)

    response = None
    for model_name in models_to_try:
        for attempt in range(4):
            try:
                # 使用 models.generate_content（穩定，SDK 所有版本通用）
                response = gemini_client.models.generate_content(
                    model=model_name, contents=prompt
                )
                print(f"  [OK] 使用模型：{model_name}")
                break
            except Exception as e:
                err_str = str(e)
                # 404 / NOT_FOUND = 模型不存在，直接跳下一個（不重試）
                if ("404" in err_str or "NOT_FOUND" in err_str
                        or "no longer available" in err_str
                        or "not found" in err_str.lower()
                        or "is not supported" in err_str):
                    print(f"[WARN] {model_name} 不可用，跳過")
                    break
                # 429 = 配額超限
                elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait = 45 * (2 ** attempt)   # 45 / 90 / 180 / 360 秒
                    print(f"[WARN] {model_name} 配額超限，等待{wait}秒({attempt+1}/4)")
                    time.sleep(wait)
                # 503 = 高需求，指數退避
                elif "503" in err_str or "UNAVAILABLE" in err_str:
                    wait = 30 * (2 ** attempt)   # 30 / 60 / 120 / 240 秒
                    print(f"[WARN] {model_name} 高需量，等待{wait}秒({attempt+1}/4)")
                    time.sleep(wait)
                else:
                    wait = 20 * (attempt + 1)
                    if attempt < 3:
                        print(f"[WARN] {model_name} 重試{attempt+1}/4，等待{wait}秒: {e}")
                        time.sleep(wait)
                    else:
                        print(f"[WARN] {model_name} 失敗: {e}")
                        break
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
        if m: return json.loads(m.group())
        raise


# ══════════════════════════════════════════════════════════
# 13. 生成 HTML 報告
# ══════════════════════════════════════════════════════════
def build_html(
    watchlist_data, scan_results, fda_events, political_data,
    analysis, fear_greed, vix_data, market_news, event_calendar,
    day_mode: str, momentum_stocks=None, political_realtime=None,
    friday_data=None, weekend_data=None
) -> str:

    mood_color  = {"多頭": "#22c55e", "空頭": "#ef4444", "震盪": "#f59e0b"}.get(analysis.get("market_mood","震盪"), "#6b7280")
    score       = analysis.get("mood_score", 50)
    pol_sent    = analysis.get("political_sentiment", "中性")
    pol_color   = {"利多": "#22c55e", "利空": "#ef4444", "中性": "#f59e0b"}.get(pol_sent, "#6b7280")
    is_weekend  = day_mode in ("saturday", "sunday")
    is_friday   = day_mode == "friday"

    day_labels  = {
        "weekday":  "📈 每日日報",
        "friday":   "📅 星期五特別版",
        "saturday": "🗓 週六回顧 · 下週部署",
        "sunday":   "🗓 週日展望 · 下週部署",
    }
    day_label = day_labels.get(day_mode, "📈 AI 美股日報")

    # ── 1. 週末橫幅提示 ──
    weekend_banner = ""
    if is_weekend:
        weekend_banner = f"""
        <div style="background:#1e293b;border:1px solid #f59e0b55;border-radius:10px;padding:12px 16px;margin-bottom:16px;text-align:center">
          <span style="color:#f59e0b;font-size:13px;font-weight:600">🏖 今天美股休市 · 以下為下週部署分析</span>
        </div>"""

    # ── 2. 昨日推介追蹤（僅週一至週五顯示） ──
    prev_rec_html = ""
    weekday_num = datetime.date.today().weekday()
    if weekday_num in range(0, 5):
        prev_recs = analysis.get("prev_rec_review", [])
        if prev_recs:
            rows = ""
            for item in prev_recs:
                res = item.get("result", "⚪ 持平")
                res_col = "#22c55e" if "獲利" in res or "✅" in res else ("#ef4444" if "虧損" in res or "🔴" in res else "#94a3b8")
                dc = "#22c55e" if item.get("direction") == "CALL" else "#ef4444"
                rows += f"""
                <tr style="border-bottom:1px solid #1e293b;font-size:13px">
                  <td style="padding:10px;font-weight:700;color:#f1f5f9">{item.get('ticker','—')}</td>
                  <td style="padding:10px"><span style="background:{dc}22;color:{dc};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:700">{item.get('direction','—')}</span></td>
                  <td style="padding:10px;color:{res_col};font-weight:700">{res}</td>
                  <td style="padding:10px;color:#e2e8f0;font-weight:600">{item.get('chg_pct','—')}</td>
                  <td style="padding:10px;color:#94a3b8;font-size:12px">{item.get('lesson','—')}</td>
                </tr>"""
            prev_rec_html = f"""
            <div style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:18px;margin-bottom:20px">
              <div style="font-size:10px;color:#3b82f6;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px;font-weight:700">🎯 昨日推介追蹤</div>
              <div style="overflow-x:auto">
                <table style="width:100%;border-collapse:collapse;text-align:left">
                  <thead>
                    <tr style="border-bottom:1px solid #334155;color:#64748b;font-size:11px;text-transform:uppercase">
                      <th style="padding:8px">股票</th>
                      <th style="padding:8px">方向</th>
                      <th style="padding:8px">結果</th>
                      <th style="padding:8px">變動</th>
                      <th style="padding:8px">復盤備註</th>
                    </tr>
                  </thead>
                  <tbody>{rows}</tbody>
                </table>
              </div>
            </div>"""

    # ── 3. 週末分析 HTML ──
    weekend_html = ""
    wa = analysis.get("weekend_analysis", {})
    if wa and is_weekend:
        winners_html = " ".join(f'<span style="background:#22c55e22;color:#22c55e;padding:3px 10px;border-radius:4px;font-size:13px;font-weight:700">{t}</span>' for t in wa.get("this_week_winners",[]))
        losers_html  = " ".join(f'<span style="background:#ef444422;color:#ef4444;padding:3px 10px;border-radius:4px;font-size:13px;font-weight:700">{t}</span>' for t in wa.get("this_week_losers",[]))

        impact_color = {"高":"#ef4444","中":"#f59e0b","低":"#22c55e"}
        week_cal_html = ""
        for ev in wa.get("next_week_key_events",[]):
            ic = impact_color.get(ev.get("impact","中"),"#f59e0b")
            week_cal_html += f"""
            <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #1e293b;align-items:flex-start">
              <span style="background:#1e293b;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;min-width:32px;text-align:center">{ev.get('day','')}</span>
              <span style="flex:1;font-size:13px;color:#e2e8f0">{ev.get('event','')}</span>
              <span style="background:{ic}22;color:{ic};padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700">{ev.get('impact','')}</span>
            </div>"""

        next_picks_html = ""
        for p in wa.get("next_week_picks",[]):
            dc  = "#22c55e" if p.get("direction") == "CALL" else "#ef4444"
            sig = int(p.get("signal_strength",3))
            next_picks_html += f"""
            <div style="background:#0a0f1e;border-radius:8px;padding:12px;margin-bottom:8px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <span style="font-size:16px;font-weight:700;color:#f1f5f9">{p.get('ticker','')}</span>
                <span style="background:{dc}22;color:{dc};padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700">{p.get('direction','')}</span>
                <span style="color:#f59e0b;font-size:12px">{'★'*sig+'☆'*(5-sig)}</span>
                <span style="color:#64748b;font-size:11px">建議{p.get('entry_day','')}</span>
              </div>
              <div style="display:flex;gap:12px;font-size:12px;flex-wrap:wrap;margin-bottom:4px">
                <span style="color:#64748b">Strike：<span style="color:{dc};font-weight:600">{p.get('strike','—')}</span></span>
                <span style="color:#64748b">到期：<span style="color:#e2e8f0">{p.get('expiry','—')}</span></span>
                <span style="color:#64748b">策略：<span style="color:#e2e8f0">{p.get('strategy','—')}</span></span>
              </div>
              <div style="font-size:12px;color:#94a3b8">📅 {p.get('catalyst','—')}</div>
            </div>"""

        monday_html = " ".join(f'<span style="background:#3b82f622;color:#3b82f6;padding:3px 10px;border-radius:4px;font-size:13px;font-weight:700">{t}</span>' for t in wa.get("watchlist_for_monday",[]))

        weekend_html = f"""
        <div style="background:#0f172a;border:1px solid #f59e0b55;border-radius:12px;padding:18px;margin-bottom:16px">
          <div style="font-size:10px;color:#f59e0b;letter-spacing:2px;text-transform:uppercase;margin-bottom:14px">{day_label}</div>

          <div style="background:#0a0f1e;border-radius:8px;padding:14px;margin-bottom:12px">
            <div style="font-size:10px;color:#475569;text-transform:uppercase;margin-bottom:8px">本週市場回顧</div>
            <div style="font-size:13px;color:#94a3b8;line-height:1.6;margin-bottom:10px">{wa.get('this_week_recap','—')}</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px">
              <span style="font-size:11px;color:#475569">強勢：</span>{winners_html}
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <span style="font-size:11px;color:#475569">弱勢：</span>{losers_html}
            </div>
          </div>

          <div style="background:#0a0f1e;border-radius:8px;padding:14px;margin-bottom:12px">
            <div style="font-size:10px;color:#475569;text-transform:uppercase;margin-bottom:6px">下週展望 · {wa.get('next_week_theme','—')}</div>
            <div style="font-size:13px;color:#94a3b8;line-height:1.6">{wa.get('next_week_outlook','—')}</div>
          </div>

          <div style="margin-bottom:12px">
            <div style="font-size:10px;color:#475569;text-transform:uppercase;margin-bottom:8px">下週事件日曆</div>
            {week_cal_html or '<div style="color:#475569;font-size:12px">暫無事件數據</div>'}
          </div>

          <div style="margin-bottom:12px">
            <div style="font-size:10px;color:#475569;text-transform:uppercase;margin-bottom:8px">下週期權部署清單</div>
            {next_picks_html or '<div style="color:#475569;font-size:12px">暫無明確推薦</div>'}
          </div>

          <div style="background:#0a0f1e;border-radius:8px;padding:12px;margin-bottom:10px">
            <div style="font-size:10px;color:#475569;text-transform:uppercase;margin-bottom:6px">⚡ 週一開盤重點關注</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">{monday_html}</div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div style="background:#0a0f1e;border-radius:8px;padding:10px">
              <div style="font-size:10px;color:#475569;margin-bottom:4px">下週風險</div>
              <div style="font-size:12px;color:#f59e0b">{wa.get('risk_factors','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:8px;padding:10px">
              <div style="font-size:10px;color:#475569;margin-bottom:4px">操作建議</div>
              <div style="font-size:12px;color:#94a3b8">{wa.get('strategy_tip','—')}</div>
            </div>
          </div>
        </div>"""

    # ── 4. 星期五分析 HTML ──
    friday_html = ""
    fa = analysis.get("friday_analysis", {})
    if fa and is_friday:
        action      = fa.get("today_action","—")
        action_color= "#22c55e" if "放出" in action else "#f59e0b" if "部分" in action else "#3b82f6"
        buy_today   = fa.get("should_buy_today","謹慎")
        buy_color   = "#22c55e" if buy_today=="是" else "#ef4444" if buy_today=="否" else "#f59e0b"
        next_picks_html = ""
        for p in fa.get("next_week_picks",[]):
            dc  = "#22c55e" if p.get("direction") == "CALL" else "#ef4444"
            sig = int(p.get("signal_strength",3))
            next_picks_html += f"""
            <div style="background:#0a0f1e;border-radius:8px;padding:12px;margin-bottom:8px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <span style="font-size:16px;font-weight:700;color:#f1f5f9">{p.get('ticker','')}</span>
                <span style="background:{dc}22;color:{dc};padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700">{p.get('direction','')}</span>
                <span style="color:#f59e0b">{'★'*sig+'☆'*(5-sig)}</span>
              </div>
              <div style="display:flex;gap:12px;font-size:12px;flex-wrap:wrap;margin-bottom:4px">
                <span style="color:#64748b">Strike：<span style="color:{dc}">{p.get('strike','—')}</span></span>
                <span style="color:#64748b">到期：<span style="color:#e2e8f0">{p.get('expiry','—')}</span></span>
              </div>
              <div style="font-size:12px;color:#94a3b8">📅 {p.get('catalyst','—')}</div>
              <div style="font-size:11px;color:#64748b">⏰ {p.get('entry_note','—')}</div>
            </div>"""
        events_html = "".join(f'<div style="font-size:12px;color:#94a3b8;padding:3px 0">• {ev}</div>' for ev in fa.get("next_week_key_events",[]))
        friday_html = f"""
        <div style="background:#0f172a;border:1px solid #f59e0b55;border-radius:12px;padding:18px;margin-bottom:16px">
          <div style="font-size:10px;color:#f59e0b;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">📅 星期五特別分析</div>
          <div style="background:#0a0f1e;border-radius:8px;padding:14px;margin-bottom:12px;border-left:3px solid {action_color}">
            <div style="font-size:10px;color:#475569;margin-bottom:6px">今天應該怎樣做？</div>
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
            <div style="font-size:13px;color:#94a3b8;margin-bottom:8px">{fa.get('next_week_outlook','—')}</div>
            {events_html}
          </div>
          <div style="margin-bottom:10px">
            <div style="font-size:10px;color:#475569;margin-bottom:8px">下週期權部署推薦</div>
            {next_picks_html or '<div style="color:#475569;font-size:12px">暫無明確推薦</div>'}
          </div>
          <div style="border-top:1px solid #1e293b;padding-top:10px">
            <div style="font-size:11px;color:#ef4444">🚫 不宜過週末：{fa.get('avoid_reason','—')}</div>
          </div>
        </div>"""

    # ── 5. AI 精選期權 ──
    top = analysis.get("top_option_pick", {})
    top_html = ""
    if top.get("ticker"):
        tc = "#22c55e" if top.get("direction") == "CALL" else "#ef4444"
        top_html = f"""
        <div style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:18px;margin-bottom:20px">
          <div style="font-size:10px;color:#64748b;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">⭐ {'下週' if is_weekend else '今日'} AI 精選期權機會</div>
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

    # ── 6. 操作清單 ──
    trade_plans  = analysis.get("trade_plans", [])
    trade_label  = "📋 下週操作預備清單" if is_weekend else "📋 今日操作清單"
    trade_html   = ""
    for plan in trade_plans:
        dc  = "#22c55e" if plan.get("direction") == "CALL" else "#ef4444"
        sig = int(plan.get("signal_strength",3))
        stars = "★"*sig + "☆"*(5-sig)
        signals_html = "".join(
            f'<span style="background:#f59e0b22;color:#f59e0b;padding:2px 7px;border-radius:4px;font-size:11px;margin-right:4px">{s}</span>'
            for s in plan.get("signals",[])
        )
        sq     = plan.get("squeeze_risk","低")
        sq_col = "#ef4444" if sq=="高" else "#f59e0b" if sq=="中" else "#64748b"
        trade_html += f"""
        <div style="background:#0f172a;border-radius:10px;padding:16px;margin-bottom:10px;border:1px solid #1e293b">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
            <div>
              <div style="font-size:10px;color:#475569;margin-bottom:3px">#{plan.get('rank','')} · {plan.get('entry_timing','')}</div>
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <div style="font-size:20px;font-weight:800;color:#f1f5f9">{plan.get('ticker','')}</div>
                <span style="background:{dc}22;color:{dc};padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700">{plan.get('direction','')}</span>
                <span style="font-size:13px;color:#f59e0b">{stars}</span>
                <span style="background:{sq_col}22;color:{sq_col};font-size:10px;padding:2px 7px;border-radius:4px">軋空{sq}</span>
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
              <div style="font-size:10px;color:#475569;margin-bottom:2px">期權止損條件</div>
              <div style="font-size:12px;color:#f59e0b">{plan.get('stop_loss_option','—')}</div>
            </div>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">
            <div>{signals_html}</div>
            <div style="font-size:11px;color:#ef4444">⚠️ 風險：{plan.get('risk','—')}</div>
          </div>
        </div>"""

    # ── 7. 政治風向雷達 ──
    pol_analysis = analysis.get("political_realtime_analysis", {})
    trump_tickers_html = " ".join(f'<span style="background:#3b82f622;color:#3b82f6;padding:2px 8px;border-radius:4px;font-size:11px">{t}</span>' for t in pol_analysis.get("trump_affected_tickers", []))
    
    gov_contracts_html = ""
    for c in pol_analysis.get("gov_contract_picks", []):
        surg = c.get("suggestion", "觀望")
        scol = "#22c55e" if surg == "CALL" else "#ef4444" if surg == "PUT" else "#f59e0b"
        gov_contracts_html += f"""
        <div style="background:#0a0f1e;border-radius:6px;padding:8px;margin-bottom:6px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:700;color:#f1f5f9;font-size:13px">{c.get('ticker','—')}</span>
            <span style="background:{scol}22;color:{scol};padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700">{surg}</span>
          </div>
          <div style="font-size:11px;color:#94a3b8;margin-top:2px">{c.get('contract','—')} | {c.get('impact','—')}</div>
        </div>"""

    pol_html = f"""
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:18px;margin-bottom:20px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <div style="font-size:10px;color:#3b82f6;letter-spacing:2px;text-transform:uppercase;font-weight:700">🏛 政治風向雷達</div>
        <span style="background:{pol_color}22;color:{pol_color};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700">風向：{pol_sent}</span>
      </div>
      <div style="background:#0a0f1e;border-radius:8px;padding:12px;margin-bottom:10px">
        <div style="font-size:11px;color:#64748b;margin-bottom:4px">特朗普最新動態影響：</div>
        <div style="font-size:13px;color:#e2e8f0;line-height:1.5;margin-bottom:6px">{pol_analysis.get('trump_market_impact', analysis.get('political_summary', '—'))}</div>
        <div style="display:flex;gap:6px;align-items:center">{trump_tickers_html}</div>
      </div>
      <div style="margin-bottom:10px">
        <div style="font-size:11px;color:#64748b;margin-bottom:6px">政府最新合約利多：</div>
        {gov_contracts_html or '<div style="color:#475569;font-size:12px">近期無顯著政府大單</div>'}
      </div>
      <div style="background:#0a0f1e;border-radius:8px;padding:10px">
        <span style="font-size:11px;color:#f59e0b">💡 最佳政治驅動交易：</span>
        <span style="font-size:12px;color:#e2e8f0">{pol_analysis.get('best_political_trade', '—')}</span>
      </div>
    </div>"""

    # ── 8. FDA 行事曆 ──
    fda_analysis_list = analysis.get("fda_analysis", [])
    fda_cards = ""
    for fda in fda_analysis_list:
        tk = fda.get("ticker", "—")
        fda_cards += f"""
        <div style="background:#0a0f1e;border-radius:8px;padding:12px;margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <span style="font-size:15px;font-weight:700;color:#f1f5f9">{tk} · {fda.get('company','—')}</span>
            <span style="background:#a78bfa22;color:#a78bfa;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700">{fda.get('event_type','—')}</span>
          </div>
          <div style="font-size:12px;color:#cbd5e1;margin-bottom:6px">💊 藥物：{fda.get('drug','—')}（預計日期：{fda.get('expected_date','—')} | 通過率：{fda.get('approval_prob','—')}）</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;margin-bottom:6px">
            <div style="background:#0f172a;padding:6px;border-radius:4px;color:#22c55e">看漲 Strike: {fda.get('call_strike','—')}<br>建議: {fda.get('if_approved','—')}</div>
            <div style="background:#0f172a;padding:6px;border-radius:4px;color:#ef4444">看跌 Strike: {fda.get('put_strike','—')}<br>建議: {fda.get('if_rejected','—')}</div>
          </div>
          <div style="font-size:11px;color:#64748b">⏰ 入場：{fda.get('entry_timing','—')} | 到期建議：{fda.get('expiry_suggest','—')} | 風險：{fda.get('risk_note','—')}</div>
        </div>"""

    fda_html = f"""
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:18px;margin-bottom:20px">
      <div style="font-size:10px;color:#a78bfa;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px;font-weight:700">💊 FDA 生技行事曆與分析</div>
      {fda_cards or '<div style="color:#475569;font-size:12px">近期無關鍵 FDA 事件</div>'}
    </div>"""

    # ── 9. 分析師評級與市場新聞 ──
    analyst_items = analysis.get("analyst_highlights", [])
    analyst_html = ""
    for ah in analyst_items:
        act_col = "#22c55e" if "升" in ah.get("action","") else "#ef4444"
        analyst_html += f"""
        <div style="background:#0a0f1e;border-radius:6px;padding:8px;margin-bottom:6px;font-size:12px">
          <div style="display:flex;justify-content:space-between">
            <span style="font-weight:700;color:#f1f5f9">{ah.get('ticker','')} ({ah.get('firm','')})</span>
            <span style="color:{act_col};font-weight:700">{ah.get('action','')} → 目標 {ah.get('price_target','—')}</span>
          </div>
          <div style="color:#94a3b8;margin-top:2px">{ah.get('impact','—')} (建議: {ah.get('trade_suggestion','—')})</div>
        </div>"""

    news_items = analysis.get("market_news_analysis", [])
    news_html = ""
    for n in news_items:
        ndc = "#22c55e" if n.get("direction") == "利多" else "#ef4444" if n.get("direction") == "利空" else "#f59e0b"
        news_html += f"""
        <div style="background:#0a0f1e;border-radius:6px;padding:10px;margin-bottom:6px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <span style="font-size:12px;font-weight:700;color:#e2e8f0">{n.get('zh_summary','')}</span>
            <span style="background:{ndc}22;color:{ndc};padding:2px 6px;border-radius:4px;font-size:10px">{n.get('direction','')}</span>
          </div>
          <div style="font-size:11px;color:#64748b;margin-bottom:2px">{n.get('title','')}</div>
          <div style="font-size:11px;color:#94a3b8">影響: {n.get('impact','')} | 建議: {n.get('action','')}</div>
        </div>"""

    # ── 10. 組合與風險總結 ──
    ps = analysis.get("portfolio_suggestion", {})
    risk_warn = analysis.get("risk_warning", "無")
    summary_txt = analysis.get("summary", "")

    # ── 11. 匯整完整 HTML 頁面 ──
    html = f"""<!DOCTYPE html>
<html lang="zh-HK">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI 美股盤前分析 - {analysis.get('date', '')}</title>
</head>
<body style="margin:0;padding:20px;background:#030712;color:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
  <div style="max-width:800px;margin:0 auto">
    
    <!-- 頂部 Header -->
    <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1f2937;padding-bottom:16px;margin-bottom:20px">
      <div>
        <div style="font-size:12px;color:#3b82f6;font-weight:700;letter-spacing:1px">{day_label}</div>
        <div style="font-size:24px;font-weight:800;color:#f9fafb;margin-top:4px">{analysis.get('headline', '美股 AI 期權決策日報')}</div>
        <div style="font-size:12px;color:#6b7280;margin-top:2px">{analysis.get('date', '')}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:12px;color:#9ca3af">市場情緒</div>
        <div style="font-size:18px;font-weight:800;color:{mood_color}">{analysis.get('market_mood','震盪')} ({score}分)</div>
      </div>
    </div>

    {weekend_banner}
    {prev_rec_html}
    {weekend_html}
    {friday_html}
    {top_html}

    <!-- 關鍵情緒與數據儀表板 -->
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">
      <div style="background:#0f172a;border-radius:8px;padding:12px;border:1px solid #1e293b;text-align:center">
        <div style="font-size:11px;color:#64748b">Fear & Greed Index</div>
        <div style="font-size:20px;font-weight:800;color:#f59e0b;margin-top:4px">{fear_greed.get('score',50)}</div>
        <div style="font-size:10px;color:#94a3b8">{fear_greed.get('label','中性')}</div>
      </div>
      <div style="background:#0f172a;border-radius:8px;padding:12px;border:1px solid #1e293b;text-align:center">
        <div style="font-size:11px;color:#64748b">VIX 指數</div>
        <div style="font-size:20px;font-weight:800;color:#3b82f6;margin-top:4px">{vix_data.get('current',20)}</div>
        <div style="font-size:10px;color:#94a3b8">{vix_data.get('level','正常')}</div>
      </div>
      <div style="background:#0f172a;border-radius:8px;padding:12px;border:1px solid #1e293b;text-align:center">
        <div style="font-size:11px;color:#64748b">組合策略風險</div>
        <div style="font-size:20px;font-weight:800;color:#a78bfa;margin-top:4px">{ps.get('risk_level','平衡')}</div>
        <div style="font-size:10px;color:#94a3b8">預算: {ps.get('total_budget','10%')}</div>
      </div>
    </div>

    <!-- 主要操作清單 -->
    <div style="margin-bottom:20px">
      <div style="font-size:14px;font-weight:700;color:#f3f4f6;margin-bottom:12px">{trade_label}</div>
      {trade_html or '<div style="color:#64748b;font-size:13px">今日無高確信度期權交易訊號</div>'}
    </div>

    {pol_html}
    {fda_html}

    <!-- 分析師評級與新聞分析雙欄 -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">
      <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:14px">
        <div style="font-size:10px;color:#22c55e;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;font-weight:700">📊 分析師最新評級</div>
        {analyst_html or '<div style="color:#475569;font-size:12px">無顯著評級變動</div>'}
      </div>
      <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:14px">
        <div style="font-size:10px;color:#3b82f6;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;font-weight:700">📰 重點財經新聞解讀</div>
        {news_html or '<div style="color:#475569;font-size:12px">暫無重大財經新聞解讀</div>'}
      </div>
    </div>

    <!-- 風險提示與每日總結 -->
    <div style="background:#0f172a;border:1px solid #ef444455;border-radius:12px;padding:16px;margin-bottom:20px">
      <div style="font-size:11px;color:#ef4444;font-weight:700;margin-bottom:4px">⚠️ 今日核心風險警告</div>
      <div style="font-size:13px;color:#fca5a5;margin-bottom:12px">{risk_warn}</div>
      <div style="font-size:11px;color:#94a3b8;font-weight:700;margin-bottom:4px">📝 每日 AI 分析總結</div>
      <div style="font-size:13px;color:#cbd5e1;line-height:1.6">{summary_txt}</div>
    </div>

    <!-- 頁腳 -->
    <div style="text-align:center;font-size:11px;color:#4b5563;border-top:1px solid #1f2937;padding-top:16px">
      AI 美股盤前分析系統 · 自動生成於 HKT {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · 僅供參考，不構成投資建議
    </div>

  </div>
</body>
</html>"""

    return html
