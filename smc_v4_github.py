import requests, json, datetime, time, os
from datetime import timezone

# === CONFIG V6.3 FIXED - OPSI B 24JAM + WICK CHECK ===
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 0.5
MIN_CONFLUENCE_SCORE = 6
LIMIT = 100
MAX_POSITIONS = 3
POSITIONS_FILE = "positions.json"
PAPER_TRADE = True

PAPER_START_CAPITAL = 20.0
PAPER_RISK_USD = 2.0
PAPER_RISK_PCT = 0.10
PAPER_FILE = "paper_trading.json"

MAX_LEVERAGE = {
    "BTCUSDT": 100,
    "ETHUSDT": 100,
    "SOLUSDT": 50,
    "BNBUSDT": 50,
    "XRPUSDT": 50,
    "ADAUSDT": 50,
    "DOGEUSDT": 50,
    "AVAXUSDT": 50,
    "LINKUSDT": 50,
    "OPUSDT": 20,
    "ARBUSDT": 20,
    "MATICUSDT": 20,
}
DEFAULT_MAX_LEV = 50
BUFFER_PCT = 0.0015
MIN_SL_PCT = 0.0015
MAX_SL_PCT = 0.012

def get_klines(symbol, interval, limit=100):
    try:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, dict): return []
        return [{"open": float(x[1]), "high": float(x[2]), "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])} for x in data]
    except: return []

def get_current_price(symbol):
    try:
        r = requests.get(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}", timeout=5)
        return float(r.json()['price'])
    except: return None

def find_swings(candles, lookback=20):
    if len(candles) < lookback+10: return None, None
    slice_c = candles[-(lookback+5):-5]
    return {"price": max(c['high'] for c in slice_c)}, {"price": min(c['low'] for c in slice_c)}

def detect_sweep(candles):
    if len(candles) < 30: return {"sweep": False, "score":0}
    sh, sl = find_swings(candles, 20)
    if not sh or not sl: return {"sweep": False, "score":0}
    last5 = candles[-5:]
    for c in last5:
        if c['low'] < sl['price'] * 0.9995 and c['close'] > sl['price']:
            return {"sweep": True, "type": "BULLISH_SWEEP", "level": sl['price'], "score": 2, "reason": f"Bull sweep wick {c['low']:.2f} below {sl['price']:.2f}"}
        if c['high'] > sh['price'] * 1.0005 and c['close'] < sh['price']:
            return {"sweep": True, "type": "BEARISH_SWEEP", "level": sh['price'], "score": 2, "reason": f"Bear sweep wick {c['high']:.2f} above {sh['price']:.2f}"}
    return {"sweep": False, "score":0, "reason": f"No sweep"}

def detect_choch(candles):
    if len(candles) < 25: return {"choch": False, "score":0}
    closes = [c['close'] for c in candles[-20:]]
    last_close = closes[-1]
    prev_high_close = max(closes[-11:-1])
    prev_low_close = min(closes[-11:-1])
    if last_close > prev_high_close * 1.0005:
        return {"choch": True, "type": "BULLISH_CHOCH", "score": 2, "reason": f"Bullish CHOCH {last_close:.2f} > {prev_high_close:.2f}"}
    if last_close < prev_low_close * 0.9995:
        return {"choch": True, "type": "BEARISH_CHOCH", "score": 2, "reason": f"Bearish CHOCH {last_close:.2f} < {prev_low_close:.2f}"}
    return {"choch": False, "score":0, "reason": "No ChoCh"}

def detect_zones(candles):
    if len(candles) < 20: return {"zones":[], "best":None, "score":0}
    zones = []
    for i in range(len(candles)-20, len(candles)-1):
        c = candles[i]
        nc = candles[i+1]
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['close'] > c['high']:
            zones.append({"type": "OB", "subtype": "BULLISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":1.5})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['low'] < c['low']:
            zones.append({"type": "BREAKER", "subtype": "BULLISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":2})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['high'] < c['low']:
            zones.append({"type": "OB", "subtype": "BEARISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":1.5})
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['low'] > c['high']:
            zones.append({"type": "BREAKER", "subtype": "BEARISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":2})
    for i in range(len(candles)-20, len(candles)-2):
        c1 = candles[i]
        c3 = candles[i+2]
        if c1['high'] < c3['low']:
            zones.append({"type": "FVG", "subtype": "BULLISH_FVG", "price": (c1['high']+c3['low'])/2, "high": c3['low'], "low": c1['high'], "score":1})
        if c1['low'] > c3['high']:
            zones.append({"type": "FVG", "subtype": "BEARISH_FVG", "price": (c1['low']+c3['high'])/2, "high": c1['low'], "low": c3['high'], "score":1})
    if not zones: return {"zones":[], "best":None, "score":0}
    price = candles[-1]['close']
    best = min(zones, key=lambda z: abs(z['price']-price))
    return {"zones":zones, "best":best, "score":best['score']}

def detect_htf_trend(k1h):
    if len(k1h) < 50: return {"trend":"NEUTRAL","score":0}
    ema20 = sum(c['close'] for c in k1h[-20:])/20
    ema50 = sum(c['close'] for c in k1h[-50:])/50
    last = k1h[-1]['close']
    if last > ema20 > ema50:
        return {"trend":"BULLISH","score":1, "reason": f"HTF bullish EMA20>EMA50"}
    if last < ema20 < ema50:
        return {"trend":"BEARISH","score":1, "reason": f"HTF bearish EMA20<EMA50"}
    return {"trend":"NEUTRAL","score":0.5}

def detect_volume(candles):
    if len(candles) < 20: return {"score":0}
    avg_vol = sum(c['volume'] for c in candles[-20:-1])/19
    last_vol = candles[-1]['volume']
    if last_vol > avg_vol * 1.5:
        return {"score":1, "reason": f"Volume spike {last_vol:.0f} > 1.5x avg {avg_vol:.0f}"}
    if last_vol > avg_vol * 1.2:
        return {"score":0.5, "reason": f"Volume up {last_vol:.0f} > 1.2x avg"}
    return {"score":0}

def detect_rsi_filter(candles):
    if len(candles) < 14: return {"score":0}
    closes = [c['close'] for c in candles[-15:]]
    gains = 0
    losses = 0
    for i in range(1,len(closes)):
        diff = closes[i]-closes[i-1]
        if diff>0: gains+=diff
        else: losses+=-diff
    if losses==0: return {"score":1, "reason":"RSI strong"}
    rs = gains/losses
    rsi = 100 - (100/(1+rs))
    if rsi>70 or rsi<30:
        return {"score":0.5, "reason": f"RSI extreme {rsi:.1f}"}
    return {"score":0}

def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE) as f:
                data = json.load(f)
                return data
        except: pass
    return {"active":[],"closed":[],"stats":{"wins":0,"losses":0,"total_pnl":0,"paper_trades":0}}

def save_positions(data):
    with open(POSITIONS_FILE,'w') as f:
        json.dump(data,f,indent=2)

positions_data = load_positions()
active_positions = positions_data.get("active",[])
closed_positions = positions_data.get("closed",[])

def check_active_positions():
    global active_positions, closed_positions
    new_active = []
    for pos in active_positions:
        sym = pos["symbol"]
        klines = get_klines(sym, "15m", 20)
        if not klines:
            new_active.append(pos)
            continue
        high = max(c['high'] for c in klines[-10:])
        low = min(c['low'] for c in klines[-10:])
        # wick check
        if pos["direction"]=="LONG":
            if low <= pos["sl"]:
                pos["close_status"]="SL_HIT"
                pos["pnl_rrr"]=-1
                pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat()
                closed_positions.append(pos)
                positions_data["stats"]["losses"]+=1
                positions_data["stats"]["total_pnl"]+=-1
            elif high >= pos["tp"]:
                pos["close_status"]="TP_HIT"
                pos["pnl_rrr"]=2
                pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat()
                closed_positions.append(pos)
                positions_data["stats"]["wins"]+=1
                positions_data["stats"]["total_pnl"]+=2
            else:
                # check expiry 24h
                created = datetime.datetime.fromisoformat(pos.get("created_at","2000-01-01T00:00:00+00:00"))
                if (datetime.datetime.now(timezone.utc)-created).total_seconds()>86400 and pos.get("status")=="WAITING_LIMIT":
                    pos["close_status"]="EXPIRED"
                    pos["pnl_rrr"]=0
                    pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat()
                    closed_positions.append(pos)
                else:
                    # check if entry touched for WAITING_LIMIT
                    if pos.get("status")=="WAITING_LIMIT":
                        entry = pos["entry"]
                        touched = any(c['low'] <= entry <= c['high'] for c in klines[-96:])
                        if touched:
                            pos["status"]="FILLED"
                            new_active.append(pos)
                        else:
                            new_active.append(pos)
                    else:
                        new_active.append(pos)
        else: # SHORT
            if high >= pos["sl"]:
                pos["close_status"]="SL_HIT"
                pos["pnl_rrr"]=-1
                pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat()
                closed_positions.append(pos)
                positions_data["stats"]["losses"]+=1
                positions_data["stats"]["total_pnl"]+=-1
            elif low <= pos["tp"]:
                pos["close_status"]="TP_HIT"
                pos["pnl_rrr"]=2
                pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat()
                closed_positions.append(pos)
                positions_data["stats"]["wins"]+=1
                positions_data["stats"]["total_pnl"]+=2
            else:
                created = datetime.datetime.fromisoformat(pos.get("created_at","2000-01-01T00:00:00+00:00"))
                if (datetime.datetime.now(timezone.utc)-created).total_seconds()>86400 and pos.get("status")=="WAITING_LIMIT":
                    pos["close_status"]="EXPIRED"
                    pos["pnl_rrr"]=0
                    pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat()
                    closed_positions.append(pos)
                else:
                    if pos.get("status")=="WAITING_LIMIT":
                        entry = pos["entry"]
                        touched = any(c['low'] <= entry <= c['high'] for c in klines[-96:])
                        if touched:
                            pos["status"]="FILLED"
                            new_active.append(pos)
                        else:
                            new_active.append(pos)
                    else:
                        new_active.append(pos)
    active_positions = new_active

check_active_positions()
results = []
valid_new = []
active_symbols = [p["symbol"] for p in active_positions]

for sym in SYMBOLS:
    if sym in active_symbols and len(active_positions)>=MAX_POSITIONS:
        results.append({"symbol":sym,"status":"SKIP","reason":f"Max pos {MAX_POSITIONS}","filter":"max_pos","confluence_score":0})
        continue
    k15 = get_klines(sym, "15m", LIMIT)
    k1h = get_klines(sym, "1h", 100)
    if not k15 or not k1h:
        results.append({"symbol":sym,"status":"ERROR","reason":"no klines","confluence_score":0})
        continue
    price = k15[-1]['close']
    sweep = detect_sweep(k15)
    choch = detect_choch(k15)
    zones = detect_zones(k15)
    htf = detect_htf_trend(k1h)
    vol = detect_volume(k15)
    rsi = detect_rsi_filter(k15)

    confluence_score = sweep["score"] + choch["score"] + zones["score"] + htf["score"] + vol["score"] + rsi["score"]
    confluences = []
    if sweep["score"]>0: confluences.append(f"SWEEP({sweep['score']})")
    if choch["score"]>0: confluences.append(f"CHOCH({choch['score']})")
    if zones["best"]: confluences.append(f"{zones['best']['subtype']}({zones['score']})")
    if htf["score"]>0: confluences.append(f"HTF({htf['score']})")
    if vol["score"]>0: confluences.append(f"VOL({vol['score']})")

    if zones["best"] is None:
        results.append({"symbol":sym,"status":"SKIP","reason":"No zone","filter":"zone","confluence_score":confluence_score})
        continue
    best_zone = zones["best"]
    zone_price = best_zone["price"]
    distance_pct = abs(price-zone_price)/price*100
    if distance_pct > MAX_DISTANCE_PCT:
        results.append({"symbol":sym,"status":"SKIP","reason":f"Distance {distance_pct:.2f}%","filter":"distance","confluence_score":confluence_score})
        continue
    if confluence_score < MIN_CONFLUENCE_SCORE:
        results.append({"symbol":sym,"status":"SKIP","reason":f"Score {confluence_score}<{MIN_CONFLUENCE_SCORE}","filter":"confluence","confluence_score":confluence_score})
        continue

    # Determine direction
    if "BULLISH" in best_zone["subtype"]:
        direction = "LONG"
        sl_level = best_zone["low"] if best_zone["low"] else zone_price*0.995
        # SL structure + sweep + buffer
        sweep_level = sweep.get("level", sl_level)
        if sweep.get("sweep") and sweep["type"]=="BULLISH_SWEEP":
            sl = min(sl_level, sweep_level) * (1-BUFFER_PCT)
        else:
            sl = sl_level * (1-BUFFER_PCT)
        sl_pct = abs(price-sl)/price
        if sl_pct < MIN_SL_PCT:
            sl = price * (1-MIN_SL_PCT)
            sl_pct = MIN_SL_PCT
        if sl_pct > MAX_SL_PCT:
            results.append({"symbol":sym,"status":"SKIP","reason":f"SL wide {sl_pct*100:.2f}%","filter":"sl_wide","confluence_score":confluence_score})
            continue
        tp = price + (price-sl)*2
        entry = zone_price
    else:
        direction = "SHORT"
        sl_level = best_zone["high"] if best_zone["high"] else zone_price*1.005
        sweep_level = sweep.get("level", sl_level)
        if sweep.get("sweep") and sweep["type"]=="BEARISH_SWEEP":
            sl = max(sl_level, sweep_level) * (1+BUFFER_PCT)
        else:
            sl = sl_level * (1+BUFFER_PCT)
        sl_pct = abs(sl-price)/price
        if sl_pct < MIN_SL_PCT:
            sl = price * (1+MIN_SL_PCT)
            sl_pct = MIN_SL_PCT
        if sl_pct > MAX_SL_PCT:
            results.append({"symbol":sym,"status":"SKIP","reason":f"SL wide {sl_pct*100:.2f}%","filter":"sl_wide","confluence_score":confluence_score})
            continue
        tp = price - (sl-price)*2
        entry = zone_price

    leverage = MAX_LEVERAGE.get(sym, DEFAULT_MAX_LEV)
    risk_usd = PAPER_RISK_USD
    sl_dist = abs(entry-sl)
    if sl_dist==0:
        results.append({"symbol":sym,"status":"SKIP","reason":"SL zero","filter":"sl_narrow","confluence_score":confluence_score})
        continue
    qty = risk_usd / sl_dist
    notional = qty * entry
    margin_needed = notional / leverage

    pos = {
        "symbol": sym,
        "direction": direction,
        "entry": round(entry,6),
        "sl": round(sl,6),
        "tp": round(tp,6),
        "status": "WAITING_LIMIT",
        "created_at": datetime.datetime.now(timezone.utc).isoformat(),
        "confluence_score": confluence_score,
        "confluences": confluences,
        "zone": best_zone,
        "sizing": {
            "risk_usd": risk_usd,
            "risk_note": "$2 fix belum fee/slippage/tax",
            "sl_pct": round(sl_pct*100,4),
            "notional_usd": round(notional,2),
            "qty": round(qty,6),
            "leverage": leverage,
            "leverage_mode": "ALWAYS_MAX",
            "margin_needed": round(margin_needed,2),
            "tp_usd": 4.0,
            "tp_note": "$4 fix belum fee"
        }
    }
    # position guard
    if any(p["symbol"]==sym and p["direction"]==direction for p in active_positions):
        results.append({"symbol":sym,"status":"SKIP","reason":"Already active same dir","filter":"position_guard","confluence_score":confluence_score})
        continue

    results.append({"symbol":sym,"status":"VALID","direction":direction,"entry":pos["entry"],"sl":pos["sl"],"tp":pos["tp"],"confluence_score":confluence_score,"confluences":confluences,"sizing":pos["sizing"]})
    if len(active_positions)+len(valid_new) < MAX_POSITIONS:
        valid_new.append(pos)
        active_positions.append(pos)
    time.sleep(0.3)

positions_data['active'] = active_positions
positions_data['closed'] = closed_positions[-200:]
save_positions(positions_data)

def build_paper_trading():
    try:
        capital = PAPER_START_CAPITAL
        history = []
        equity_curve = [capital]
        for pos in sorted(closed_positions, key=lambda x: x.get('closed_at','')):
            r = pos.get('pnl_rrr',0)
            if r > 0:
                pnl_usd = PAPER_RISK_USD * r
                sizing = pos.get('sizing', {})
                notional = sizing.get('notional_usd', 200)
                fee = notional * 0.001
                pnl_usd -= fee
            elif r < 0:
                pnl_usd = -PAPER_RISK_USD
                sizing = pos.get('sizing', {})
                notional = sizing.get('notional_usd', 200)
                fee = notional * 0.001
                pnl_usd -= fee
            else:
                pnl_usd = 0
            capital += pnl_usd
            capital = max(capital, 0.1)
            equity_curve.append(round(capital,2))
            history.append({
                "symbol": pos.get('symbol'),
                "direction": pos.get('direction'),
                "entry": pos.get('entry'),
                "sl": pos.get('sl'),
                "tp": pos.get('tp'),
                "close_status": pos.get('close_status'),
                "pnl_r": r,
                "pnl_usd": round(pnl_usd,4),
                "sizing": pos.get('sizing', {}),
                "capital_after": round(capital,4),
                "confluence_score": pos.get('confluence_score',0),
                "confluences": pos.get('confluences',[]),
                "closed_at": pos.get('closed_at','')[:19]
            })
        wins = len([h for h in history if h['pnl_r']>0])
        losses = len([h for h in history if h['pnl_r']<0])
        total_r = sum(h['pnl_r'] for h in history)
        paper_data = {
            "generated_at": datetime.datetime.now(timezone.utc).isoformat(),
            "bot_version": "V6_FINAL_STRUCTURE_BUFFER_MAXLEV_RISK2",
            "config": {
                "start_capital": PAPER_START_CAPITAL,
                "risk_usd": PAPER_RISK_USD,
                "risk_note": "Risk $2 fix per trade belum fee/slippage/tax - sizing auto, always max leverage",
                "buffer": f"{BUFFER_PCT*100}% anti hunter",
                "leverage": "ALWAYS_MAX 100/50/20",
                "tp": "Single TP 2R",
                "sl": "Structure SL (zone low/high + sweep + buffer 0.15%)"
            },
            "summary": {
                "initial": PAPER_START_CAPITAL,
                "final": round(capital,2),
                "pnl_usd": round(capital - PAPER_START_CAPITAL,2),
                "pnl_pct": round((capital/PAPER_START_CAPITAL-1)*100,2),
                "total_trades": len(history),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins/len(history)*100,1) if history else 0,
                "total_r": round(total_r,2),
                "expectancy_r": round(total_r/len(history),3) if history else 0,
                "equity_curve": equity_curve
            },
            "trades": history,
            "active_positions": active_positions
        }
        with open(PAPER_FILE,'w') as f:
            json.dump(paper_data,f,indent=2)
        print(f"PAPER TRADING V6: ${PAPER_START_CAPITAL} -> ${capital:.2f} | PnL ${capital-PAPER_START_CAPITAL:.2f} | {total_r}R | WR {wins}/{len(history)} | saved {PAPER_FILE}")
    except Exception as e:
        print(f"Paper trading build error: {e}")

build_paper_trading()

valid=[x for x in results if x['status']=="VALID"]
skip=[x for x in results if x['status']=="SKIP"]

out={
    "last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),
    "bot_version":"V6_FINAL_STRUCTURE_BUFFER_MAXLEV_RISK2",
    "params":{"MIN_CONFLUENCE_SCORE":MIN_CONFLUENCE_SCORE,"MAX_POSITIONS":MAX_POSITIONS,"BUFFER":BUFFER_PCT,"RISK_USD":PAPER_RISK_USD},
    "summary":{
        "total_scanned":len(results),
        "valid_new":len(valid_new),
        "valid":len(valid),
        "skip":len(skip),
        "active_positions":len(active_positions),
        "closed_today":len([c for c in closed_positions if c.get('closed_at','').startswith(datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d'))]),
        "avg_confluence": round(sum(r.get('confluence_score',0) for r in results)/len(results),2) if results else 0,
        "by_filter":{
            "confluence":len([r for r in skip if r.get("filter")=="confluence"]),
            "distance":len([r for r in skip if r.get("filter")=="distance"]),
            "zone":len([r for r in skip if r.get("filter")=="zone"]),
            "time":len([r for r in skip if r.get("filter")=="time"]),
            "sl_narrow":len([r for r in skip if r.get("filter")=="sl_narrow"]),
            "sl_wide":len([r for r in skip if r.get("filter")=="sl_wide"]),
            "position_guard":len([r for r in skip if r.get("filter")=="position_guard"]),
            "max_pos":len([r for r in skip if r.get("filter")=="max_pos"])
        }
    },
    "results":results,
    "valid_trades":valid,
    "valid_new_positions":valid_new,
    "running_positions":active_positions,
    "closed_positions":closed_positions[-20:],
    "stats":positions_data['stats'],
    "tuning_notes":"V6 FINAL: SL struktur (zone low/high + sweep + buffer 0.15% anti hunter) + Single TP 2R + Always Max Lev 100/50/20 + Risk $2 fix sizing auto - identik real"
}

with open("last_scan.json","w") as f:
    json.dump(out,f,indent=2)

print(f"\nDONE V6 FINAL: {len(valid_new)} NEW, {len(active_positions)} ACTIVE, {len(closed_positions)} CLOSED | PnL R:{positions_data['stats']['total_pnl']} | Avg Score {out['summary']['avg_confluence']}")
print("saved last_scan.json + positions.json - REAL IDENTICAL MODE")
