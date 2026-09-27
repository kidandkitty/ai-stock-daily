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
