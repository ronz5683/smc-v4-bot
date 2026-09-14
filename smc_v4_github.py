import requests, json, datetime, time, os
from datetime import timezone

# === CONFIG V5 MULTI-CONFLUENCE ===
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 0.5
MIN_CONFLUENCE_SCORE = 6  # dari 10, minimal 6 baru VALID
LIMIT = 100
MAX_POSITIONS = 5
POSITIONS_FILE = "positions.json"
PAPER_TRADE = True  # V5: mode paper trading 1 bulan

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

# === CONFLUENCE MODULES ===
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
    # FVG
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
    # simple RSI check tanpa library
    if len(candles) < 14: return {"score":0}
    closes = [c['close'] for c in candles[-15:]]
    gains = [max(0, closes[i]-closes[i-1]) for i in range(1,len(closes))]
    losses = [max(0, closes[i-1]-closes[i]) for i in range(1,len(closes))]
    avg_gain = sum(gains)/14
    avg_loss = sum(losses)/14 if sum(losses)!=0 else 0.001
    rs = avg_gain/avg_loss
    rsi = 100 - (100/(1+rs))
    if 35 < rsi < 65:
        return {"score":1, "rsi": round(rsi,1), "reason": f"RSI healthy {rsi:.1f}"}
    if rsi < 30 or rsi > 70:
        return {"score":0, "rsi": round(rsi,1), "reason": f"RSI extreme {rsi:.1f} skip"}
    return {"score":0.5, "rsi": round(rsi,1), "reason": f"RSI neutral {rsi:.1f}"}

def analyze_symbol_multi(symbol):
    price = get_current_price(symbol)
    k15 = get_klines(symbol, "15m", LIMIT)
    k1h = get_klines(symbol, "1h", LIMIT)
    if not k15 or not k1h or not price:
        return {"symbol": symbol, "status": "ERROR", "reason": "API fail", "price": price or 0, "confluence_score":0}
    
    sweep = detect_sweep(k15)
    choch = detect_choch(k15)
    zone_data = detect_zones(k15)
    htf = detect_htf_trend(k1h)
    vol = detect_volume(k15)
    rsi = detect_rsi_filter(k15)
    
    best_zone = zone_data['best']
    dist = 999
    if best_zone: dist = abs(price-best_zone['price'])/price*100
    
    # CONFLUENCE SCORING 0-10
    total_score = sweep['score'] + choch['score'] + zone_data['score'] + htf['score'] + vol['score'] + rsi['score']
    
    # Details
    confluences = []
    if sweep['sweep']: confluences.append(f"SWEEP({sweep['score']})")
    if choch['choch']: confluences.append(f"CHOCH({choch['score']})")
    if best_zone: confluences.append(f"{best_zone['type']}({zone_data['score']})")
    if htf['score']>=1: confluences.append(f"HTF({htf['score']})")
    if vol['score']>=0.5: confluences.append(f"VOL({vol['score']})")
    if rsi['score']>=0.5: confluences.append(f"RSI({rsi['score']})")
    
    base = {
        "symbol":symbol,
        "price":round(price,4),
        "confluence_score": round(total_score,1),
        "confluences": confluences,
        "sweep": sweep,
        "choch": choch,
        "zone": best_zone,
        "zones_all": zone_data['zones'][:3],
        "htf": htf,
        "vol": vol,
        "rsi": rsi,
        "distance_pct": round(dist,3),
        "timestamp": datetime.datetime.now(timezone.utc).isoformat()
    }
    
    # FILTERS
    hour_wib = (datetime.datetime.now(timezone.utc).hour+7)%24
    if 7 <= hour_wib <= 9:
        return {**base,"status":"SKIP","reason":f"Blacklist jam {hour_wib}","filter":"time"}
    if dist > MAX_DISTANCE_PCT:
        return {**base,"status":"SKIP","reason":f"Distance {dist:.3f}% > {MAX_DISTANCE_PCT}%","filter":"distance"}
    if not best_zone:
        return {**base,"status":"SKIP","reason":"No zone","filter":"zone"}
    if total_score < MIN_CONFLUENCE_SCORE:
        return {**base,"status":"SKIP","reason":f"Confluence {total_score} < {MIN_CONFLUENCE_SCORE} need more TA","filter":"confluence","confluence_details":confluences}
    
    # VALID - tentukan direction dari zone + choch
    direction = "LONG"
    if best_zone['type'] in ["BREAKER","OB"] and "BEARISH" in best_zone['subtype']:
        direction = "SHORT"
    elif choch.get('type') == "BEARISH_CHOCH":
        direction = "SHORT"
    
    # Risk
    entry = best_zone['price']
    if direction=="LONG":
        sl = entry - (entry * 0.004)  # 0.4% SL
        tp1 = entry + (entry - sl)*1.5
        tp2 = entry + (entry - sl)*3
    else:
        sl = entry + (entry * 0.004)
        tp1 = entry - (sl-entry)*1.5
        tp2 = entry - (sl-entry)*3
    
    return {
        **base,
        "status":"VALID",
        "reason": f"SCORE {total_score}/10: {'+'.join(confluences)}",
        "direction": direction,
        "entry": round(entry,4),
        "sl": round(sl,4),
        "tp1": round(tp1,4),
        "tp2": round(tp2,4),
        "rrr": 1.5,
        "order_type":"LIMIT",
        "order_status":"WAITING_LIMIT",
        "filter":"none"
    }

# === POSITION MANAGER V5 ===
def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE,'r') as f:
                data = json.load(f)
                # backward compat V4.6 -> V5
                if 'stats' not in data: data['stats'] = {}
                data['stats'].setdefault('wins',0)
                data['stats'].setdefault('losses',0)
                data['stats'].setdefault('total_pnl',0)
                data['stats'].setdefault('paper_trades',0)
                data.setdefault('active', [])
                data.setdefault('closed', [])
                return data
        except Exception as e:
            print(f"load_positions error {e}")
    return {"active": [], "closed": [], "stats": {"wins":0,"losses":0,"total_pnl":0,"paper_trades":0}}

def save_positions(data):
    with open(POSITIONS_FILE,'w') as f: json.dump(data,f,indent=2)

def check_position_status(pos, current_price, klines_15m=None):
    if pos.get('order_status') == "WAITING_LIMIT":
        if klines_15m:
            touched = any(c['low'] <= pos['entry'] <= c['high'] for c in klines_15m[-12:])
            if touched:
                pos['order_status'] = "FILLED"
                pos['filled_at'] = datetime.datetime.now(timezone.utc).isoformat()
                pos['filled_price'] = pos['entry']
            else:
                open_time = datetime.datetime.fromisoformat(pos['open_time'].replace('Z','+00:00')) if 'T' in pos.get('open_time','') else datetime.datetime.now(timezone.utc)
                age_hours = (datetime.datetime.now(timezone.utc) - open_time).total_seconds()/3600
                if age_hours > 6:
                    return "EXPIRED", 0
                if pos['direction']=="LONG" and current_price >= pos['tp1'] * 0.999:
                    return "MISSED_TP", 0
                if pos['direction']=="SHORT" and current_price <= pos['tp1'] * 1.001:
                    return "MISSED_TP", 0
                return "WAITING_LIMIT", 0
    if pos['direction']=="LONG":
        if current_price <= pos['sl']: return "SL_HIT", -1
        if current_price >= pos['tp2']: return "TP2_HIT", 3
        if current_price >= pos['tp1']: return "TP1_HIT", 1.5
    else:
        if current_price >= pos['sl']: return "SL_HIT", -1
        if current_price <= pos['tp2']: return "TP2_HIT", 3
        if current_price <= pos['tp1']: return "TP1_HIT", 1.5
    return "ACTIVE", 0

# === MAIN ===
print(f"=== SMC V5 MULTI-CONFLUENCE | SCORE >= {MIN_CONFLUENCE_SCORE}/10 | PAPER={PAPER_TRADE} ===")
positions_data = load_positions()
active_positions = positions_data.get("active", [])
closed_positions = positions_data.get("closed", [])
active_symbols = [p['symbol'] for p in active_positions]

print(f"Checking {len(active_positions)} active positions...")
for pos in active_positions[:]:
    curr = get_current_price(pos['symbol'])
    if not curr: continue
    klines = get_klines(pos['symbol'], "15m", 50)
    status, rrr = check_position_status(pos, curr, klines)
    if status == "WAITING_LIMIT":
        pos['current_price'] = curr
        pos['last_check'] = datetime.datetime.now(timezone.utc).isoformat()
        print(f"  WAITING {pos['symbol']} {pos['entry']} -> {curr}")
        continue
    pos['current_price'] = curr
    pos['last_check'] = datetime.datetime.now(timezone.utc).isoformat()
    if status != "ACTIVE":
        pos['close_status'] = status
        pos['close_price'] = curr
        pos['pnl_rrr'] = rrr
        pos['closed_at'] = datetime.datetime.now(timezone.utc).isoformat()
        active_positions.remove(pos)
        closed_positions.append(pos)
        positions_data['stats'].setdefault('wins',0)
        positions_data['stats'].setdefault('losses',0)
        positions_data['stats'].setdefault('total_pnl',0)
        positions_data['stats'].setdefault('paper_trades',0)
        if rrr>0: positions_data['stats']['wins']+=1
        else: positions_data['stats']['losses']+=1
        positions_data['stats']['total_pnl']+=rrr
        positions_data['stats']['paper_trades']+=1
        print(f"  CLOSED {pos['symbol']} {status} R:{rrr}")

slots_left = MAX_POSITIONS - len(active_positions)
print(f"Slots: {len(active_positions)}/{MAX_POSITIONS}, {slots_left} left")

results=[]
valid_new=[]
if slots_left>0:
    for sym in SYMBOLS:
        if sym in active_symbols:
            results.append({"symbol":sym,"status":"SKIP","reason":"Already active","filter":"position_guard","confluence_score":0})
            continue
        try:
            r=analyze_symbol_multi(sym)
            results.append(r)
            print(f"{sym}: {r['status']} SCORE {r.get('confluence_score',0)} - {r['reason'][:100]}")
            if r['status']=="VALID" and len(valid_new)<slots_left:
                new_pos = {
                    "symbol": sym, "direction": r['direction'], "entry": r['entry'], "sl": r['sl'],
                    "tp1": r['tp1'], "tp2": r['tp2'], "rrr": r['rrr'],
                    "zone_price": r['zone']['price'] if r['zone'] else r['entry'],
                    "confluence_score": r['confluence_score'],
                    "confluences": r['confluences'],
                    "reason": r['reason'],
                    "open_price": r['price'], "open_time": datetime.datetime.now(timezone.utc).isoformat(),
                    "status": "ACTIVE", "order_status": "WAITING_LIMIT",
                    "paper_trade": PAPER_TRADE
                }
                valid_new.append(new_pos)
                active_positions.append(new_pos)
                print(f"  -> NEW {sym} {r['direction']} SCORE {r['confluence_score']} Entry {r['entry']}")
            time.sleep(0.3)
        except Exception as e:
            results.append({"symbol":sym,"status":"ERROR","reason":str(e),"confluence_score":0})
else:
    for sym in SYMBOLS:
        if sym not in active_symbols:
            results.append({"symbol":sym,"status":"SKIP","reason":f"Max pos {MAX_POSITIONS}","filter":"max_pos","confluence_score":0})

positions_data['active'] = active_positions
positions_data['closed'] = closed_positions[-200:]
save_positions(positions_data)

valid=[x for x in results if x['status']=="VALID"]
skip=[x for x in results if x['status']=="SKIP"]

out={
    "last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),
    "bot_version":"V5_MULTI_CONFLUENCE",
    "params":{"MIN_CONFLUENCE_SCORE":MIN_CONFLUENCE_SCORE,"MAX_POSITIONS":MAX_POSITIONS,"PAPER_TRADE":PAPER_TRADE},
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
    "tuning_notes":"V5 multi-confluence: sweep(2)+choch(2)+breaker/ob(2)+fvg(1)+htf(1)+vol(1)+rsi(1)=10. High prob when 2 techniques at same point"
}

with open("last_scan.json","w") as f:
    json.dump(out,f,indent=2)

print(f"\nDONE V5: {len(valid_new)} NEW, {len(active_positions)} ACTIVE, {len(closed_positions)} CLOSED | PnL R:{positions_data['stats']['total_pnl']} | Avg Score {out['summary']['avg_confluence']}")
print("saved last_scan.json + positions.json - PAPER TRADE MODE")
