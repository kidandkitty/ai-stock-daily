#!/usr/bin/env python3
"""
AI 美股盤前分析 完整版
模組：自選股 + 期權掃描 + FDA行事曆 + 政治風向雷達
依賴: pip install google-generativeai yfinance requests python-dotenv
"""

import os, json, datetime, time, random, requests, re
import google.generativeai as genai
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
POLITICAL_NEWS_LIMIT = 8    # 最多抓幾條政治新聞

GEMINI_API_KEY    = os.environ["ANTHROPIC_API_KEY"]  # 用同一個 Secret 名稱
EMAIL_FROM        = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]
EMAIL_TO          = os.environ["EMAIL_TO"]
QUIVER_API_KEY    = os.environ.get("QUIVER_API_KEY", "")

# 初始化 Gemini
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash")


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
def score_option(s: dict) -> tuple:
    score, flags = 0, []
    pre = s.get("pre_change") or 0
    if abs(pre) >= 5:
        score += 30; flags.append(f"盤前異動 {pre:+.1f}%")
    elif abs(pre) >= SCAN_MIN_PRE_MOVE:
        score += 15; flags.append(f"盤前異動 {pre:+.1f}%")

    avg_vol = s.get("avg_volume") or 1
    pre_vol = s.get("pre_volume") or 0
    vol_ratio = (pre_vol / avg_vol) * (390 / 90) if avg_vol > 0 else 0
    if vol_ratio >= SCAN_MIN_VOL_RATIO:
        score += 20; flags.append(f"成交量 {vol_ratio:.1f}x 均量")

    iv_cur, iv_prev = s.get("iv_current"), s.get("iv_prev")
    if iv_cur and iv_prev and iv_prev > 0:
        spike = (iv_cur - iv_prev) / iv_prev
        if spike >= SCAN_MIN_IV_SPIKE:
            score += 25; flags.append(f"IV 飆升 {spike:.0%}")

    pc = s.get("put_call")
    if pc is not None:
        if pc < 0.35:
            score += 15; flags.append(f"P/C={pc:.2f} 大量買Call")
        elif pc > 1.5:
            score += 15; flags.append(f"P/C={pc:.2f} 大量買Put")

    direction = "PUT" if (pc and pc > 1.0) or pre < -3 else "CALL"
    return score, flags, direction


# ══════════════════════════════════════════════════════════
# 4. 全市場掃描
# ══════════════════════════════════════════════════════════
def scan_options(tickers: list) -> list:
    print(f"[掃描] {len(tickers)} 支股票...")
    results = []
    for i, ticker in enumerate(tickers):
        if i % 50 == 0:
            print(f"  進度: {i}/{len(tickers)}")
        data = fetch_stock_data(ticker)
        if not data:
            continue
        sc, flags, direction = score_option(data)
        if sc >= 20 and flags:
            results.append({**data, "score": sc, "flags": flags, "direction": direction})
        time.sleep(0.15 + random.uniform(0, 0.1))
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:SCAN_TOP_N]


def fetch_watchlist_data() -> list:
    results = []
    for ticker in WATCHLIST:
        data = fetch_stock_data(ticker)
        if data:
            results.append(data)
        time.sleep(0.2)
    return results


# ══════════════════════════════════════════════════════════
# 5. FDA 行事曆
# ══════════════════════════════════════════════════════════
def fetch_fda_calendar() -> list:
    events = []
    try:
        url = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drug-approvals-and-databases/rss.xml"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                events.append({
                    "title": item.findtext("title", "")[:80],
                    "date":  item.findtext("pubDate", "")[:16],
                    "link":  item.findtext("link", ""),
                })
            events = events[:5]
    except Exception as e:
        print(f"[WARN] FDA: {e}")
    if not events:
        events = [{"title": "FDA行事曆暫時無法取得", "date": "", "link": "https://www.fda.gov"}]
    return events


# ══════════════════════════════════════════════════════════
# 6. 政治風向雷達（免費新聞版）
# ══════════════════════════════════════════════════════════
def fetch_political_intelligence() -> dict:
    """
    抓取政治相關新聞，回傳結構化數據。
    - 免費版：Google News RSS
    - 付費版：若有 QUIVER_API_KEY 則額外抓精確申報
    """
    print("[政治雷達] 抓取中...")
    news_items  = []
    congress_trades = []
    trump_signals   = []

    # ── A. Google News RSS（免費）──
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

    # 去重（同標題只保留一條）
    seen = set()
    unique_news = []
    for n in news_items:
        key = n["title"][:40]
        if key not in seen:
            seen.add(key)
            unique_news.append(n)
    news_items = unique_news[:POLITICAL_NEWS_LIMIT]

    # ── B. Quiver API（付費，有 key 才執行）──
    if QUIVER_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {QUIVER_API_KEY}"}

            # 國會最新申報
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
            print(f"[Quiver] 國會申報 {len(congress_trades)} 筆")

            # Trump 相關交易
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
            print(f"[Quiver] Trump信號 {len(trump_signals)} 筆")

        except Exception as e:
            print(f"[WARN] Quiver API: {e}")

    return {
        "news":           news_items,
        "congress_trades": congress_trades,
        "trump_signals":   trump_signals,
        "data_source":    "Quiver API + Google News" if QUIVER_API_KEY else "Google News RSS（免費版）",
    }


# ══════════════════════════════════════════════════════════
# 7. AI 分析（整合所有模組）
# ══════════════════════════════════════════════════════════
def ai_analyze(watchlist_data, scan_results, fda_events, political_data) -> dict:
    today = datetime.date.today().strftime("%Y年%m月%d日")

    payload = {
        "date":            today,
        "watchlist":       watchlist_data[:10],
        "top_options":     scan_results[:5],
        "fda_events":      fda_events[:3],
        "political_news":  political_data.get("news", [])[:6],
        "congress_trades": political_data.get("congress_trades", [])[:5],
        "trump_signals":   political_data.get("trump_signals", [])[:3],
    }

    prompt = f"""你是專業美股分析師，專注期權交易機會與政治風向。以下是 {today} 的盤前全套數據：

{json.dumps(payload, ensure_ascii=False, indent=2)}

請用繁體中文回傳純 JSON（不要 markdown 或任何其他文字）：
{{
  "date": "{today}",
  "market_mood": "多頭/空頭/震盪",
  "mood_score": 0到100的整數,
  "headline": "今日最重要一句話20字以內",
  "top_option_pick": {{
    "ticker": "最值得關注的期權標的代碼",
    "direction": "CALL或PUT",
    "reason": "原因30字以內",
    "key_strike": "建議關注的Strike",
    "risk": "主要風險20字以內"
  }},
  "political_summary": "政治風向對今日股市的關鍵影響50字以內",
  "political_hot_tickers": ["受政治消息影響最大的3支股票代碼"],
  "political_sentiment": "利多/利空/中性",
  "congress_highlight": "最值得關注的國會議員持倉動作30字（若無申報數據則分析新聞）",
  "fda_watch": "本週FDA事件影響30字，若無則填無重大事件",
  "sector_rotation": "板塊輪動觀察40字",
  "key_movers": [
    {{"ticker": "代碼", "signal": "強勢或弱勢或觀察", "reason": "原因20字"}}
  ],
  "risk_warning": "今日最大風險30字",
  "summary": "整體摘要100字"
}}"""

    response = gemini_model.generate_content(prompt)
    raw = response.text.strip()
    # 移除可能的 markdown 代碼塊
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
# 8. 生成 HTML 報告
# ══════════════════════════════════════════════════════════
def build_html(watchlist_data, scan_results, fda_events, political_data, analysis) -> str:
    mood_color = {"多頭": "#22c55e", "空頭": "#ef4444", "震盪": "#f59e0b"}.get(
        analysis.get("market_mood", "震盪"), "#6b7280")
    score = analysis.get("mood_score", 50)

    # ── 自選股表格 ──
    watch_rows = ""
    for s in watchlist_data:
        chg = s["change_pct"]
        cc  = "#22c55e" if chg >= 0 else "#ef4444"
        cs  = f"+{chg}%" if chg >= 0 else f"{chg}%"
        pre_str = "—"
        if s.get("pre_change") is not None:
            pc = s["pre_change"]
            pre_str = f'<span style="color:{"#22c55e" if pc>=0 else "#ef4444"}">{pc:+.1f}%</span>'
        iv_str = f"{s['iv_current']:.0%}" if s.get("iv_current") else "—"
        watch_rows += f"""<tr>
          <td style="font-weight:700;color:#e2e8f0">{s['ticker']}</td>
          <td style="color:#94a3b8">${s['last_close']}</td>
          <td style="color:{cc}">{cs}</td>
          <td>{pre_str}</td>
          <td style="color:#f59e0b">{iv_str}</td>
        </tr>"""

    # ── 期權掃描卡片 ──
    scan_cards = ""
    for rank, s in enumerate(scan_results, 1):
        sc     = s.get("score", 0)
        dc     = "#22c55e" if s.get("direction") == "CALL" else "#ef4444"
        flags  = "".join(
            f'<span style="background:#f59e0b22;color:#f59e0b;padding:2px 7px;border-radius:4px;font-size:11px;margin-right:4px">{f}</span>'
            for f in s.get("flags", [])
        )
        pre    = s.get("pre_change")
        iv_str = f"{s['iv_current']:.0%}" if s.get("iv_current") else "—"
        exps   = " · ".join(s.get("exp_dates", [])[:3])
        scan_cards += f"""
        <div style="background:#0f172a;border-radius:8px;padding:14px 16px;margin-bottom:10px;border:1px solid #1e293b">
          <div style="display:flex;justify-content:space-between;margin-bottom:10px">
            <div><div style="font-size:11px;color:#475569">#{rank}</div>
                 <div style="font-size:17px;font-weight:700;color:#f1f5f9">{s['ticker']}</div></div>
            <div style="text-align:right">
              <div style="font-size:22px;font-weight:700;color:{dc}">{sc}</div>
              <div style="font-size:10px;color:#475569">/ 100</div>
              <div style="width:70px;height:4px;background:#1e293b;border-radius:2px;margin-top:4px;margin-left:auto">
                <div style="width:{min(sc,100)}%;height:4px;background:{dc};border-radius:2px"></div></div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:10px">
            <div style="background:#0a0f1e;border-radius:6px;padding:7px 8px">
              <div style="font-size:10px;color:#475569">盤前</div>
              <div style="color:{'#22c55e' if (pre or 0)>=0 else '#ef4444'};font-size:13px;font-weight:600">{f"{pre:+.1f}%" if pre is not None else "—"}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:7px 8px">
              <div style="font-size:10px;color:#475569">IV</div>
              <div style="color:#f59e0b;font-size:13px;font-weight:600">{iv_str}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:7px 8px">
              <div style="font-size:10px;color:#475569">P/C</div>
              <div style="color:#e2e8f0;font-size:13px;font-weight:600">{s.get('put_call','—')}</div>
            </div>
            <div style="background:#0a0f1e;border-radius:6px;padding:7px 8px">
              <div style="font-size:10px;color:#475569">方向</div>
              <div style="color:{dc};font-size:13px;font-weight:700">{s.get('direction','—')}</div>
            </div>
          </div>
          <div style="margin-bottom:6px">{flags}</div>
          <div style="font-size:11px;color:#475569">到期：{exps}</div>
        </div>"""

    # ── FDA ──
    fda_html = ""
    for ev in fda_events:
        fda_html += f"""
        <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #1e293b">
          <div style="width:6px;height:6px;border-radius:50%;background:#a78bfa;margin-top:5px;flex-shrink:0"></div>
          <div>
            <div style="font-size:13px;color:#e2e8f0">{ev.get('title','')}</div>
            <div style="font-size:11px;color:#475569;margin-top:2px">{ev.get('date','')}</div>
          </div>
        </div>"""

    # ── 政治風向 ──
    pol_sentiment = analysis.get("political_sentiment", "中性")
    pol_color = {"利多": "#22c55e", "利空": "#ef4444", "中性": "#f59e0b"}.get(pol_sentiment, "#6b7280")
    hot_tickers = analysis.get("political_hot_tickers", [])
    hot_tickers_html = " ".join(
        f'<span style="background:#3b82f622;color:#3b82f6;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700">{t}</span>'
        for t in hot_tickers
    )

    # 新聞列表
    news_html = ""
    for n in political_data.get("news", [])[:6]:
        cat_color = {
            "特朗普/股票": "#ef4444",
            "國會申報":    "#22c55e",
            "政策板塊":    "#f59e0b",
            "Pelosi持倉":  "#a78bfa",
        }.get(n.get("category", ""), "#64748b")
        news_html += f"""
        <div style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #1e293b">
          <span style="background:{cat_color}22;color:{cat_color};padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;height:fit-content;margin-top:1px">{n.get('category','')}</span>
          <div style="flex:1">
            <div style="font-size:13px;color:#e2e8f0;line-height:1.4">{n.get('title','')}</div>
            <div style="font-size:11px;color:#475569;margin-top:2px">{n.get('date','')}</div>
          </div>
        </div>"""

    # 國會申報（Quiver付費版）
    congress_html = ""
    trades = political_data.get("congress_trades", [])
    if trades:
        for t in trades[:4]:
            tx = t.get("transaction", "")
            tc = "#22c55e" if "Purchase" in tx or "Buy" in tx else "#ef4444"
            congress_html += f"""
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #1e293b">
              <span style="background:{tc}22;color:{tc};font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px;white-space:nowrap">{tx}</span>
              <span style="font-weight:700;color:#f1f5f9;min-width:56px">{t.get('ticker','')}</span>
              <span style="color:#94a3b8;font-size:12px;flex:1">{t.get('politician','')}</span>
              <span style="color:#64748b;font-size:11px">{t.get('filed','')}</span>
            </div>"""
    else:
        congress_html = f'<div style="color:#475569;font-size:12px;padding:8px 0">{analysis.get("congress_highlight","—")}</div>'

    # AI 精選
    top = analysis.get("top_option_pick", {})
    top_html = ""
    if top.get("ticker"):
        tc = "#22c55e" if top.get("direction") == "CALL" else "#ef4444"
        top_html = f"""
        <div style="background:#0f172a;border:1px solid #334155;border-radius:10px;padding:16px;margin-bottom:20px">
          <div style="font-size:10px;color:#64748b;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px">AI 精選期權機會</div>
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
            <div style="font-size:24px;font-weight:800;color:#f1f5f9">{top['ticker']}</div>
            <div style="background:{tc}22;color:{tc};padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700">{top.get('direction','')}</div>
          </div>
          <div style="color:#cbd5e1;font-size:14px;margin-bottom:6px">{top.get('reason','')}</div>
          <div style="display:flex;gap:16px;font-size:12px">
            <span style="color:#64748b">Strike: <span style="color:#e2e8f0;font-weight:600">{top.get('key_strike','—')}</span></span>
            <span style="color:#64748b">風險: <span style="color:#ef4444">{top.get('risk','—')}</span></span>
          </div>
        </div>"""

    # 自選股動向
    movers_html = ""
    for m in analysis.get("key_movers", []):
        sc_color = {"強勢":"#22c55e","弱勢":"#ef4444","觀察":"#f59e0b"}.get(m.get("signal",""),"#94a3b8")
        movers_html += f"""
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #0f172a">
          <span style="font-weight:700;color:#e2e8f0;width:60px">{m['ticker']}</span>
          <span style="background:{sc_color}22;color:{sc_color};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{m['signal']}</span>
          <span style="color:#94a3b8;font-size:13px">{m['reason']}</span>
        </div>"""

    data_src = political_data.get("data_source", "Google News RSS")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 美股日報 · {analysis['date']}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0f1e;color:#e2e8f0;font-family:'Helvetica Neue',Arial,sans-serif;padding:20px}}
.wrap{{max-width:700px;margin:0 auto}}
.header{{text-align:center;padding:28px 0 20px;border-bottom:1px solid #1e293b;margin-bottom:20px}}
.eyebrow{{font-size:11px;letter-spacing:3px;color:#475569;text-transform:uppercase;margin-bottom:6px}}
h1{{font-size:26px;font-weight:800;color:#f8fafc}}
.date{{font-size:13px;color:#64748b;margin-top:4px}}
.sec{{font-size:10px;letter-spacing:2px;color:#475569;text-transform:uppercase;margin-bottom:12px;margin-top:24px}}
.mood-row{{display:flex;justify-content:space-between;margin-bottom:6px}}
.progress{{background:#1e293b;border-radius:4px;height:5px;margin-bottom:20px}}
.progress-fill{{background:{mood_color};height:5px;border-radius:4px;width:{score}%}}
.headline{{background:#0f172a;border-left:3px solid {mood_color};padding:12px 16px;margin-bottom:20px;font-size:15px;font-weight:600;color:#f1f5f9;line-height:1.5}}
.info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}}
.info-box{{background:#0f172a;border-radius:8px;padding:14px}}
.info-box .lbl{{font-size:10px;color:#475569;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px}}
.info-box .val{{font-size:13px;color:#cbd5e1;line-height:1.5}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:#475569;font-size:10px;letter-spacing:1px;text-transform:uppercase;padding-bottom:8px;font-weight:400}}
td{{padding:7px 0;border-bottom:1px solid #0f172a}}
.footer{{text-align:center;padding:24px 0;color:#334155;font-size:11px;border-top:1px solid #1e293b;margin-top:28px}}
@media(max-width:480px){{.info-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <div class="eyebrow">AI 美股日報</div>
    <h1>盤前分析 · 期權掃描 · 政治雷達</h1>
    <div class="date">{analysis['date']}</div>
  </div>

  <div class="mood-row">
    <span style="background:{mood_color}22;color:{mood_color};padding:3px 10px;border-radius:4px;font-size:13px;font-weight:700">{analysis.get('market_mood','—')}</span>
    <span style="color:#64748b;font-size:13px">市場情緒 {score}/100</span>
  </div>
  <div class="progress"><div class="progress-fill"></div></div>

  <div class="headline">💡 {analysis.get('headline','—')}</div>

  {top_html}

  <!-- 政治風向雷達 -->
  <div class="sec">🏛 政治風向雷達</div>
  <div style="background:#0f172a;border-radius:10px;padding:16px;border:1px solid #1e293b;margin-bottom:12px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
      <span style="background:{pol_color}22;color:{pol_color};padding:3px 10px;border-radius:4px;font-size:12px;font-weight:700">{pol_sentiment}</span>
      <span style="color:#94a3b8;font-size:13px;flex:1">{analysis.get('political_summary','—')}</span>
    </div>
    <div style="margin-bottom:10px">
      <span style="font-size:10px;color:#475569;letter-spacing:1px;text-transform:uppercase;margin-right:8px">受影響股票</span>
      {hot_tickers_html}
    </div>
    <div style="font-size:10px;color:#475569;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">最新消息</div>
    {news_html or '<div style="color:#475569;font-size:12px;padding:8px 0">暫無相關新聞</div>'}
  </div>

  <!-- 國會議員申報 -->
  <div class="sec">📋 國會議員持倉動態</div>
  <div style="background:#0f172a;border-radius:10px;padding:16px;border:1px solid #1e293b;margin-bottom:12px">
    {congress_html}
    <div style="font-size:11px;color:#334155;margin-top:8px">數據來源：{data_src} · STOCK Act申報延遲最長45天</div>
  </div>

  <!-- 期權掃描 -->
  <div class="sec">🔍 期權異動掃描 · 標普500全市場</div>
  {scan_cards or '<div style="color:#475569;padding:20px 0;text-align:center">今日無顯著期權異動</div>'}

  <!-- FDA -->
  <div class="sec">💊 FDA 本週事件</div>
  <div style="background:#0f172a;border-radius:8px;padding:14px">{fda_html}</div>

  <!-- 自選股 -->
  <div class="sec">📊 自選股動向</div>
  {movers_html}

  <div class="sec">自選股數據</div>
  <table>
    <thead><tr><th>代碼</th><th>收盤</th><th>昨日%</th><th>盤前%</th><th>IV</th></tr></thead>
    <tbody>{watch_rows}</tbody>
  </table>

  <div class="info-grid" style="margin-top:20px">
    <div class="info-box"><div class="lbl">板塊輪動</div><div class="val">{analysis.get('sector_rotation','—')}</div></div>
    <div class="info-box"><div class="lbl">風險警示</div><div class="val">{analysis.get('risk_warning','—')}</div></div>
    <div class="info-box" style="grid-column:1/-1"><div class="lbl">整體摘要</div><div class="val">{analysis.get('summary','—')}</div></div>
  </div>

  <div class="footer">
    由 Claude AI 自動生成 · 掃描標普500全市場 · 僅供參考，不構成投資建議<br>
    數據：Yahoo Finance · FDA.gov · Google News · {data_src}
  </div>
</div>
</body>
</html>"""


# ══════════════════════════════════════════════════════════
# 9. 儲存報告
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
# 10. 發送 Email
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


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════
def main():
    print("=== AI 美股日報 完整版 開始執行 ===")

    print("\n[1/6] 取得標普500成分股清單...")
    sp500 = get_sp500_tickers()

    print("\n[2/6] 抓取自選股數據...")
    watchlist_data = fetch_watchlist_data()

    print("\n[3/6] 全市場期權異動掃描...")
    scan_results = scan_options(sp500)
    print(f"  發現 {len(scan_results)} 支高評分異動股")

    print("\n[4/6] 抓取 FDA 行事曆...")
    fda_events = fetch_fda_calendar()

    print("\n[5/6] 政治風向雷達...")
    political_data = fetch_political_intelligence()
    print(f"  新聞 {len(political_data['news'])} 條 · 國會申報 {len(political_data['congress_trades'])} 筆")

    print("\n[6/6] Claude AI 整合分析...")
    analysis = ai_analyze(watchlist_data, scan_results, fda_events, political_data)
    print(f"  標題：{analysis.get('headline')}")
    print(f"  政治情緒：{analysis.get('political_sentiment')} · {analysis.get('political_summary','')[:40]}")

    print("\n[生成報告與發送]")
    html = build_html(watchlist_data, scan_results, fda_events, political_data, analysis)
    save_report(html, analysis)
    send_email(html, analysis)

    print("\n=== 完成 ✓ ===")


if __name__ == "__main__":
    main()
