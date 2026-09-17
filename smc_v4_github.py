import requests, json, datetime, time, os
from datetime import timezone

# === CONFIG V6.3 FIXED - OPSI B 24JAM + WICK CHECK ===
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 0.5
MIN_CONFLUENCE_SCORE = 6
LIMIT = 100
MAX_POSITIONS = 3  # untuk $20, max 3 posisi bareng biar tidak overtrade
POSITIONS_FILE = "positions.json"
PAPER_TRADE = True

# === PAPER TRADING - REAL IDENTICAL LOGIC ===
PAPER_START_CAPITAL = 20.0
PAPER_RISK_USD = 2.0  # risk $2 fix per trade, belum fee
PAPER_RISK_PCT = 0.10
PAPER_FILE = "paper_trading.json"

# === LEVERAGE CONFIG - ALWAYS MAX ===
# Sesuai request: always max leverage, sizing yang atur $2 SL
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
BUFFER_PCT = 0.0015  # 0.15% buffer anti liquidity hunter
MIN_SL_PCT = 0.0015  # min SL 0.15% biar tidak kesweep wick
MAX_SL_PCT = 0.012   # max SL 1.2% biar margin $20 cukup

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
    
    total_score = sweep['score'] + choch['score'] + zone_data['score'] + htf['score'] + vol['score'] + rsi['score']
    
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
    
    hour_wib = (datetime.datetime.now(timezone.utc).hour+7)%24
    if 7 <= hour_wib <= 9:
        return {**base,"status":"SKIP","reason":f"Blacklist jam {hour_wib}","filter":"time"}
    if dist > MAX_DISTANCE_PCT:
        return {**base,"status":"SKIP","reason":f"Distance {dist:.3f}% > {MAX_DISTANCE_PCT}%","filter":"distance"}
    if not best_zone:
        return {**base,"status":"SKIP","reason":"No zone","filter":"zone"}
    if total_score < MIN_CONFLUENCE_SCORE:
        return {**base,"status":"SKIP","reason":f"Confluence {total_score} < {MIN_CONFLUENCE_SCORE} need more TA","filter":"confluence","confluence_details":confluences}
    
    direction = "LONG"
    if best_zone['type'] in ["BREAKER","OB"] and "BEARISH" in best_zone['subtype']:
        direction = "SHORT"
    elif choch.get('type') == "BEARISH_CHOCH":
        direction = "SHORT"
    
    # === SL STRUKTUR + BUFFER ANTI HUNTER 0.15% ===
    entry = best_zone['price']
    zone_low = best_zone.get('low', entry*0.996)
    zone_high = best_zone.get('high', entry*1.004)
    sweep_level = sweep.get('level') if sweep.get('sweep') else None
    
    if direction=="LONG":
        struct_sl = zone_low * (1 - BUFFER_PCT)  # 0.15% di bawah zone low
        if sweep_level and sweep['type']=="BULLISH_SWEEP":
            struct_sl = min(struct_sl, sweep_level * (1 - BUFFER_PCT))  # di bawah sweep low + buffer
        # filter min/max SL
        sl_pct_check = abs(entry - struct_sl)/entry
        if sl_pct_check < MIN_SL_PCT:
            return {**base,"status":"SKIP","reason":f"SL too narrow {sl_pct_check*100:.3f}% < {MIN_SL_PCT*100}% rawan wick","filter":"sl_narrow"}
        if sl_pct_check > MAX_SL_PCT:
            return {**base,"status":"SKIP","reason":f"SL too wide {sl_pct_check*100:.3f}% > {MAX_SL_PCT*100}% butuh margin besar","filter":"sl_wide"}
        sl = struct_sl
        tp = entry + (entry - sl)*2.0
    else:
        struct_sl = zone_high * (1 + BUFFER_PCT)
        if sweep_level and sweep['type']=="BEARISH_SWEEP":
            struct_sl = max(struct_sl, sweep_level * (1 + BUFFER_PCT))
        sl_pct_check = abs(sl - entry)/entry if 'sl' in locals() else abs(struct_sl - entry)/entry
        sl_pct_check = abs(struct_sl - entry)/entry
        if sl_pct_check < MIN_SL_PCT:
            return {**base,"status":"SKIP","reason":f"SL too narrow {sl_pct_check*100:.3f}% < {MIN_SL_PCT*100}%","filter":"sl_narrow"}
        if sl_pct_check > MAX_SL_PCT:
            return {**base,"status":"SKIP","reason":f"SL too wide {sl_pct_check*100:.3f}% > {MAX_SL_PCT*100}%","filter":"sl_wide"}
        sl = struct_sl
        tp = entry - (sl-entry)*2.0
    
    # === POSITION SIZING - ALWAYS MAX LEVERAGE, RISK $2 FIX ===
    sl_pct = abs(entry - sl)/entry
    notional = PAPER_RISK_USD / sl_pct  # misal SL 0.5% => $2/0.005 = $400
    qty = notional / entry
    max_lev = MAX_LEVERAGE.get(symbol, DEFAULT_MAX_LEV)
    margin_needed = notional / max_lev
    leverage_used = max_lev  # always max
    
    # TP/SL RRR
    tp_pct = abs(tp - entry)/entry
    
    return {
        **base,
        "status":"VALID",
        "reason": f"SCORE {total_score}/10: {'+'.join(confluences)} | SL struktur {sl:.4f} (buffer {BUFFER_PCT*100}%) | TP 2R {tp:.4f} | Size ${notional:.0f} lev {leverage_used}x risk $2",
        "direction": direction,
        "entry": round(entry,4),
        "sl": round(sl,4),
        "tp": round(tp,4),
        "tp1": round(tp,4),
        "tp2": round(tp,4),
        "rrr": 2.0,
        "sl_type": "STRUCTURE_BUFFER",
        "sl_pct": round(sl_pct*100,4),
        "tp_pct": round(tp_pct*100,4),
        "buffer_pct": BUFFER_PCT*100,
        "sl_zone_low": round(zone_low,4),
        "sl_zone_high": round(zone_high,4),
        # sizing info - identik real
        "sizing": {
            "risk_usd": PAPER_RISK_USD,
            "risk_note": "$2 fix belum fee/slippage/tax",
            "sl_pct": round(sl_pct*100,4),
            "notional_usd": round(notional,2),
            "qty": round(qty,6),
            "leverage": leverage_used,
            "leverage_mode": "ALWAYS_MAX",
            "margin_needed": round(margin_needed,2),
            "tp_usd": round(PAPER_RISK_USD*2,2),
            "tp_note": "$4 fix belum fee"
        },
        "order_type":"LIMIT",
        "order_status":"WAITING_LIMIT",
        "filter":"none"
    }

# === POSITION MANAGER V6 ===
def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE,'r') as f:
                data = json.load(f)
                if 'stats' not in data: data['stats'] = {}
                data['stats'].setdefault('wins',0)
                data['stats'].setdefault('losses',0)
                data['stats'].setdefault('total_pnl',0)
                data['stats'].setdefault('paper_trades',0)
                data.setdefault('active', [])
                data.setdefault('closed', [])
                return data
        except: pass
    return {"active":[],"closed":[],"stats":{"wins":0,"losses":0,"total_pnl":0,"paper_trades":0}}

def save_positions(data):
    with open(POSITIONS_FILE,'w') as f: json.dump(data,f,indent=2)

def check_position_status(pos, current_price, klines_15m=None):
    # === WAITING_LIMIT = limit order belum ke-fill - OPSI B 24 JAM ===
    if pos.get('order_status') == "WAITING_LIMIT":
        # cek fill pakai wick high/low 15m - 96 candle = 24 jam (fix bug #1)
        if klines_15m:
            touched = any(c['low'] <= pos['entry'] <= c['high'] for c in klines_15m[-96:])
            if touched:
                pos['order_status'] = "FILLED"
                pos['filled_at'] = datetime.datetime.now(timezone.utc).isoformat()
                pos['filled_price'] = pos['entry']
                # setelah FILLED, lanjut ke pengecekan TP/SL di bawah
            else:
                # belum ke-fill, cek expiry 24 jam
                try:
                    open_time = datetime.datetime.fromisoformat(pos['open_time'].replace('Z','+00:00'))
                except:
                    open_time = datetime.datetime.now(timezone.utc)
                age_hours = (datetime.datetime.now(timezone.utc) - open_time).total_seconds()/3600
                if age_hours > 24:
                    return "EXPIRED", 0
                return "WAITING_LIMIT", 0
        else:
            # bug #3 fix: kalau klines None, tetap cek expiry jangan nyangkut selamanya
            try:
                open_time = datetime.datetime.fromisoformat(pos['open_time'].replace('Z','+00:00'))
            except:
                open_time = datetime.datetime.now(timezone.utc)
            age_hours = (datetime.datetime.now(timezone.utc) - open_time).total_seconds()/3600
            if age_hours > 24:
                return "EXPIRED", 0
            return "WAITING_LIMIT", 0
    
    # === FILLED / ACTIVE = sudah ke-fill, wajib sampai TP atau SL, tidak pakai waktu ===
    tp_price = pos.get('tp', pos.get('tp1', pos.get('tp2', 0)))
    sl_price = pos.get('sl', 0)

    # bug #2 fix: cek wick high/low juga, bukan cuma current_price ticker
    if klines_15m and len(klines_15m) > 0:
        # cek candle terakhir dan beberapa candle setelah filled untuk wick
        recent_klines = klines_15m[-4:]  # 1 jam terakhir
        for c in recent_klines:
            if pos['direction']=="LONG":
                if c['low'] <= sl_price:
                    return "SL_HIT", -1
                if c['high'] >= tp_price:
                    return "TP_HIT", 2.0
            else:
                if c['high'] >= sl_price:
                    return "SL_HIT", -1
                if c['low'] <= tp_price:
                    return "TP_HIT", 2.0

    # fallback cek current_price juga
    if pos['direction']=="LONG":
        if current_price <= sl_price: return "SL_HIT", -1
        if current_price >= tp_price: return "TP_HIT", 2.0
    else:
        if current_price >= sl_price: return "SL_HIT", -1
        if current_price <= tp_price: return "TP_HIT", 2.0
    return "ACTIVE", 0

# === MAIN ===
print(f"=== SMC V6.3 FIXED | OPSI B 24JAM | WICK TP/SL CHECK | STRUCTURE SL BUFFER {BUFFER_PCT*100}% | 2R ===")
positions_data = load_positions()
active_positions = positions_data.get("active", [])
closed_positions = positions_data.get("closed", [])
valid_new = []
results = []

if active_positions:
    symbols_to_check = set([p['symbol'] for p in active_positions] + SYMBOLS)
else:
    symbols_to_check = SYMBOLS

active_symbols = [p['symbol'] for p in active_positions]
slots_left = MAX_POSITIONS - len(active_positions)

for pos in list(active_positions):
    try:
        cur_price = get_current_price(pos['symbol'])
        k15 = get_klines(pos['symbol'], "15m", 50)
        if cur_price is None:
            results.append({"symbol":pos['symbol'],"status":"HOLD","reason":"price fail","confluence_score":pos.get('confluence_score',0)})
            continue
        status, pnl_r = check_position_status(pos, cur_price, k15)
        if status in ["SL_HIT","TP_HIT"]:
            pos['close_status']=status
            pos['pnl_rrr']=pnl_r
            pos['closed_at']=datetime.datetime.now(timezone.utc).isoformat()
            # bug #4 fix: close_price harus TP/SL exact, bukan ticker cur_price
            if status == "TP_HIT":
                pos['close_price']=pos.get('tp', cur_price)
            else:
                pos['close_price']=pos.get('sl', cur_price)
            closed_positions.append(pos)
            active_positions.remove(pos)
            if pnl_r>0: positions_data['stats']['wins']+=1
            else: positions_data['stats']['losses']+=1
            positions_data['stats']['total_pnl']+=pnl_r
            print(f"CLOSED {pos['symbol']} {status} {pnl_r}R @ {pos['close_price']} (ticker {cur_price})")
            results.append({"symbol":pos['symbol'],"status":status,"reason":f"Closed {status} {pnl_r}R","confluence_score":pos.get('confluence_score',0)})
        elif status in ["EXPIRED"]:
            pos['close_status']=status
            pos['pnl_rrr']=0
            pos['closed_at']=datetime.datetime.now(timezone.utc).isoformat()
            closed_positions.append(pos)
            active_positions.remove(pos)
            results.append({"symbol":pos['symbol'],"status":status,"reason":status,"confluence_score":0})
        else:
            results.append({"symbol":pos['symbol'],"status":"HOLD","reason":f"Holding {pos.get('direction')} {status}","confluence_score":pos.get('confluence_score',0)})
    except Exception as e:
        results.append({"symbol":pos['symbol'],"status":"ERROR","reason":str(e),"confluence_score":0})

if slots_left > 0:
    for sym in SYMBOLS:
        if len(valid_new) >= slots_left: break
        if sym in active_symbols: continue
        try:
            r=analyze_symbol_multi(sym)
            results.append(r)
            print(f"{sym}: {r['status']} SCORE {r.get('confluence_score',0)} - {r['reason'][:120]}")
            if r['status']=="VALID" and len(valid_new)<slots_left:
                new_pos = {
                    "symbol": sym, "direction": r['direction'], "entry": r['entry'], "sl": r['sl'],
                    "tp": r['tp'], "tp1": r['tp'], "tp2": r['tp'], "rrr": r['rrr'],
                    "sl_pct": r['sl_pct'], "tp_pct": r['tp_pct'],
                    "zone_price": r['zone']['price'] if r['zone'] else r['entry'],
                    "confluence_score": r['confluence_score'],
                    "confluences": r['confluences'],
                    "sizing": r['sizing'],
                    "reason": r['reason'],
                    "open_price": r['price'], "open_time": datetime.datetime.now(timezone.utc).isoformat(),
                    "status": "ACTIVE", "order_status": "WAITING_LIMIT",
                    "paper_trade": PAPER_TRADE
                }
                valid_new.append(new_pos)
                active_positions.append(new_pos)
                print(f"  -> NEW {sym} {r['direction']} SCORE {r['confluence_score']} Entry {r['entry']} SL {r['sl']} TP {r['tp']} | Size ${r['sizing']['notional_usd']} Lev {r['sizing']['leverage']}x Margin ${r['sizing']['margin_needed']}")
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

# === PAPER TRADING SIMULATION - REAL FEE INCLUDED ===
def build_paper_trading():
    try:
        capital = PAPER_START_CAPITAL
        history = []
        equity_curve = [capital]
        for pos in sorted(closed_positions, key=lambda x: x.get('closed_at','')):
            r = pos.get('pnl_rrr',0)
            # real risk $2 fix, TP $4 fix
            if r > 0:
                pnl_usd = PAPER_RISK_USD * r  # +$4
                # fee 0.05% * 2 = 0.1% dari notional, simulasi
                sizing = pos.get('sizing', {})
                notional = sizing.get('notional_usd', 200)
                fee = notional * 0.001  # 0.1% total fee
                pnl_usd -= fee
            elif r < 0:
                pnl_usd = -PAPER_RISK_USD  # -$2
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
