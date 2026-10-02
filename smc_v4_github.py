"""
SMC V10 M1 FAST TEST - Target 100 trades / 2 hari untuk cek bug paper=demo risk $2
- TF: M1 utama, M5 HTF (bukan M15/H1)
- LOOSE: MIN_CONF 4.0, MAX_DIST 2.0%, SL swing 0.1%-5% (biar size auto hitung $2)
- NO blacklist, NO daily_stop, MAX_POS 10, cek fill 10 candle M1
- SL = swing low/high + buffer, bukan fix $2. Size yang auto adjust ke $2 loss
"""
import requests, json, datetime, time, os
from datetime import timezone

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 2.0  # LOOSE M1: 2% biar cepat fill
MIN_CONFLUENCE_SCORE = 4.0  # SUPER LOOSE M1: 4.0 biar 100 trade/2hari tercapai
LIMIT = 100
MAX_POSITIONS = 10  # M1 test banyakin
MAX_DAILY_SL = 100  # NO LIMIT untuk test
POSITIONS_FILE = "positions.json"
PAPER_FILE = "paper_trading.json"
PAPER_START_CAPITAL = 1000.0
PAPER_RISK_USD = 2.0
MAX_LEVERAGE = {"BTCUSDT":100,"ETHUSDT":100,"SOLUSDT":50,"BNBUSDT":50,"XRPUSDT":50,"ADAUSDT":50,"DOGEUSDT":50,"AVAXUSDT":50,"LINKUSDT":50,"OPUSDT":20,"ARBUSDT":20,"MATICUSDT":20}
DEFAULT_MAX_LEV = 50
BUFFER_PCT = 0.002
MIN_SL_PCT = 0.0025  # V10.9 anti MC - 0.25% min biar gak noise M1, size auto $2  # M1 0.1% min
MAX_SL_PCT = 0.05   # M1 5% max - biarin, executor akan auto size ke $2 atau SKIP kalau terlalu lebar

RUN_AWAY_PCT = {"BTCUSDT":0.6,"ETHUSDT":0.6,"BNBUSDT":0.7,"SOLUSDT":0.8,"LINKUSDT":0.8,"ADAUSDT":1.0,"DOGEUSDT":1.0,"AVAXUSDT":0.8,"ARBUSDT":1.2,"OPUSDT":1.2,"XRPUSDT":0.8,"MATICUSDT":1.0,"DEFAULT":1.0}

def get_klines(symbol, interval, limit=100):
    try:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, dict): return []
        return [{"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),"close":float(x[4]),"volume":float(x[5]),"close_time":x[6]} for x in data]
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
            return {"sweep": True, "type": "BULLISH_SWEEP", "level": sl['price'], "score": 2, "reason": f"Bull sweep {c['low']:.2f} below {sl['price']:.2f}"}
        if c['high'] > sh['price'] * 1.0005 and c['close'] < sh['price']:
            return {"sweep": True, "type": "BEARISH_SWEEP", "level": sh['price'], "score": 2, "reason": f"Bear sweep {c['high']:.2f} above {sh['price']:.2f}"}
    return {"sweep": False, "score":0, "reason": "No sweep"}

def detect_choch(candles):
    if len(candles) < 25: return {"choch": False, "score":0}
    closes = [c['close'] for c in candles[-20:]]
    last_close = closes[-1]
    prev_high = max(closes[-11:-1])
    prev_low = min(closes[-11:-1])
    if last_close > prev_high * 1.0005:
        return {"choch": True, "type": "BULLISH_CHOCH", "score": 2, "reason": f"Bullish CHOCH {last_close:.2f} > {prev_high:.2f}"}
    if last_close < prev_low * 0.9995:
        return {"choch": True, "type": "BEARISH_CHOCH", "score": 2, "reason": f"Bearish CHOCH {last_close:.2f} < {prev_low:.2f}"}
    return {"choch": False, "score":0, "reason": "No ChoCh"}

def detect_zones(candles):
    if len(candles) < 20: return {"zones":[], "best":None, "score":0}
    zones = []
    for i in range(len(candles)-20, len(candles)-1):
        c = candles[i]; nc = candles[i+1]
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['close'] > c['high']:
            zones.append({"type": "OB", "subtype": "BULLISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":1.5})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['low'] < c['low']:
            zones.append({"type": "BREAKER", "subtype": "BULLISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":2})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['high'] < c['low']:
            zones.append({"type": "OB", "subtype": "BEARISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":1.5})
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['low'] > c['high']:
            zones.append({"type": "BREAKER", "subtype": "BEARISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":2})
    for i in range(len(candles)-20, len(candles)-2):
        c1 = candles[i]; c3 = candles[i+2]
        if c1['high'] < c3['low']:
            zones.append({"type": "FVG", "subtype": "BULLISH_FVG", "price": (c1['high']+c3['low'])/2, "high": c3['low'], "low": c1['high'], "score":1})
        if c1['low'] > c3['high']:
            zones.append({"type": "FVG", "subtype": "BEARISH_FVG", "price": (c1['low']+c3['high'])/2, "high": c1['low'], "low": c3['high'], "score":1})
    if not zones: return {"zones":[], "best":None, "score":0}
    price = candles[-1]['close']
    best = min(zones, key=lambda z: abs(z['price']-price))
    return {"zones":zones, "best":best, "score":best['score']}

def detect_htf_trend(k_htf):
    if len(k_htf) < 20: return {"trend":"NEUTRAL","score":0}
    ema20 = sum(c['close'] for c in k_htf[-20:])/20
    ema50 = sum(c['close'] for c in k_htf[-50:])/50 if len(k_htf)>=50 else ema20
    last = k_htf[-1]['close']
    if last > ema20 > ema50: return {"trend":"BULLISH","score":1, "reason":"HTF bullish"}
    if last < ema20 < ema50: return {"trend":"BEARISH","score":1, "reason":"HTF bearish"}
    return {"trend":"NEUTRAL","score":0.5}

def detect_volume(candles):
    if len(candles) < 20: return {"score":0}
    avg_vol = sum(c['volume'] for c in candles[-20:-1])/19
    last_vol = candles[-1]['volume']
    if last_vol > avg_vol * 1.5: return {"score":1, "reason":"Volume spike"}
    if last_vol > avg_vol * 1.2: return {"score":0.5, "reason":"Volume up"}
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
    if 35 <= rsi <= 65: return {"score":1, "rsi": round(rsi,1), "reason": f"RSI healthy {rsi:.1f}"}
    if rsi < 30 or rsi > 70: return {"score":0, "rsi": round(rsi,1), "reason": f"RSI extreme {rsi:.1f}"}
    return {"score":0.5, "rsi": round(rsi,1), "reason": f"RSI neutral {rsi:.1f}"}

def load_positions():
    try:
        with open(POSITIONS_FILE,'r') as f: return json.load(f)
    except: return {"active": [], "closed": [], "stats": {"wins":0,"losses":0,"total_pnl":0}}

def save_positions(data):
    with open(POSITIONS_FILE,'w') as f: json.dump(data,f,indent=2)

def analyze_symbol_multi(symbol):
    # M1 TEST: M1 utama, M5 HTF
    candles_1m = get_klines(symbol, "1m", 100)
    candles_5m = get_klines(symbol, "5m", 100)
    if not candles_1m or not candles_5m:
        return {"symbol":symbol,"status":"SKIP","reason":"No klines","filter":"no_data","confluence_score":0,"price":0}
    price = candles_1m[-1]['close']
    # NO BLACKLIST for M1 test

    sweep = detect_sweep(candles_1m)
    choch = detect_choch(candles_1m)
    zone_res = detect_zones(candles_1m)
    htf = detect_htf_trend(candles_5m)
    vol = detect_volume(candles_1m)
    rsi = detect_rsi_filter(candles_1m)

    confluences = []; total_score = 0
    if sweep['score']>0: confluences.append(f"SWEEP({sweep['score']})"); total_score+=sweep['score']
    if choch['score']>0: confluences.append(f"CHOCH({choch['score']})"); total_score+=choch['score']
    if zone_res['score']>0 and zone_res['best']: confluences.append(f"{zone_res['best']['type']}({zone_res['score']})"); total_score+=zone_res['score']
    if htf['score']>0: confluences.append(f"HTF({htf['score']})"); total_score+=htf['score']
    if vol['score']>0: confluences.append(f"VOL({vol['score']})"); total_score+=vol['score']
    if rsi['score']>0: confluences.append(f"RSI({rsi['score']})"); total_score+=rsi['score']

    direction = None
    if sweep.get('type') == 'BULLISH_SWEEP' or choch.get('type') == 'BULLISH_CHOCH': direction = 'LONG'
    elif sweep.get('type') == 'BEARISH_SWEEP' or choch.get('type') == 'BEARISH_CHOCH': direction = 'SHORT'
    else:
        if zone_res['best']:
            direction = 'LONG' if 'BULLISH' in zone_res['best']['subtype'] else 'SHORT'
        else:
            # M1 fallback: kalau gak ada zone, pakai choch terakhir
            direction = 'LONG' if candles_1m[-1]['close'] > candles_1m[-2]['close'] else 'SHORT'

    # M1: kurangi penalty HTF biar lebih banyak trade
    if direction == 'LONG' and htf['trend'] == 'BEARISH': total_score -= 0.5
    if direction == 'SHORT' and htf['trend'] == 'BULLISH': total_score -= 0.5

    if total_score < MIN_CONFLUENCE_SCORE:
        return {"symbol":symbol,"price":price,"confluence_score":total_score,"confluences":confluences,"sweep":sweep,"choch":choch,"zone":zone_res['best'],"zones_all":zone_res['zones'][:3],"htf":htf,"vol":vol,"rsi":rsi,"status":"SKIP","reason":f"Score {total_score} < {MIN_CONFLUENCE_SCORE}","filter":"confluence"}

    best_zone = zone_res['best']
    if not best_zone:
        # M1 fallback: buat zone dari swing low/high terdekat kalau tidak ada OB/Breaker
        swing_low = min(c['low'] for c in candles_1m[-20:])
        swing_high = max(c['high'] for c in candles_1m[-20:])
        best_zone = {"type": "SWING", "subtype": f"{direction}_SWING", "price": swing_low if direction=='LONG' else swing_high, "high": swing_high, "low": swing_low}

    zone_price = best_zone['price']
    # SL = SWING + BUFFER (bukan fix $2) - $2 itu risk nya, bukan jarak
    sl_dist_pct = abs(price - zone_price) / price + BUFFER_PCT
    if sl_dist_pct < MIN_SL_PCT: sl_dist_pct = MIN_SL_PCT
    if sl_dist_pct > MAX_SL_PCT: 
        # Jangan SKIP langsung di M1 test, biarin executor yang SKIP kalau risk > $2.5
        sl_dist_pct = MAX_SL_PCT

    if direction == 'LONG':
        sl = zone_price * (1 - BUFFER_PCT)  # SL di bawah swing
        entry = zone_price  # LIMIT di swing
        tp = entry * (1 + sl_dist_pct*2)  # TP 2R
    else:
        sl = zone_price * (1 + BUFFER_PCT)
        entry = zone_price
        tp = entry * (1 - sl_dist_pct*2)

    dist_pct = abs(price - zone_price)/price*100
    if dist_pct > MAX_DISTANCE_PCT:
        return {"symbol":symbol,"price":price,"confluence_score":total_score,"status":"SKIP","reason":f"Distance {dist_pct:.2f}% > {MAX_DISTANCE_PCT}%","filter":"distance","sweep":sweep,"choch":choch,"zone":best_zone,"zones_all":zone_res['zones'][:3],"htf":htf,"vol":vol,"rsi":rsi}

    lev = MAX_LEVERAGE.get(symbol, DEFAULT_MAX_LEV)
    notional = PAPER_RISK_USD / sl_dist_pct
    qty = notional / entry
    sizing = {"risk_usd": PAPER_RISK_USD, "sl_pct": round(sl_dist_pct*100,4), "notional_usd": round(notional,2), "qty": round(qty,6), "leverage": lev, "leverage_mode": "ALWAYS_MAX", "margin_needed": round(notional/lev,2)}

    return {"symbol":symbol,"price":price,"confluence_score":total_score,"confluences":confluences,"sweep":sweep,"choch":choch,"zone":best_zone,"zones_all":zone_res['zones'][:3],"htf":htf,"vol":vol,"rsi":rsi,"direction":direction,"entry":entry,"sl":sl,"tp":tp,"rrr":2.0,"sl_pct":sl_dist_pct,"tp_pct":sl_dist_pct*2,"sizing":sizing,"status":"VALID","reason":f"M1 SCORE {total_score}/10: {'+'.join(confluences)} | LIMIT SWING {entry:.4f} SL {sl_dist_pct*100:.2f}% | HTF {htf['trend']}"}

positions_data = load_positions()
active_positions = positions_data.get('active',[])
closed_positions = positions_data.get('closed',[])
results = []
valid_new = []

# === 1. CEK WAITING_LIMIT M1: 10 candle M1 ===
for pos in active_positions[:]:
    if pos.get('order_status') == 'WAITING_LIMIT':
        sym = pos['symbol']
        entry = pos['entry']
        klines = get_klines(sym, "1m", 15)
        if not klines: continue
        last_10 = klines[-10:]
        filled = False
        for c in last_10:
            if c['low'] <= entry <= c['high']:
                filled = True
                break
        
        curr = get_current_price(sym)
        if curr:
            run_away_thresh = RUN_AWAY_PCT.get(sym, RUN_AWAY_PCT["DEFAULT"])/100
            dist_from_entry = abs(curr - entry)/entry
            if dist_from_entry > run_away_thresh:
                pos['close_status'] = 'CANCELED_PRICE_RAN_AWAY'
                pos['closed_at'] = datetime.datetime.now(timezone.utc).isoformat()
                pos['cancel_reason'] = f"M1 Price ran away {dist_from_entry*100:.2f}% > {run_away_thresh*100}%"
                closed_positions.append(pos)
                active_positions.remove(pos)
                results.append({"symbol":sym,"status":"CANCELED","reason":pos['cancel_reason'],"filter":"price_ran_away","confluence_score":pos.get('confluence_score',0)})
                continue
        
        if filled:
            pos['order_status'] = 'FILLED'
            pos['status'] = 'ACTIVE'
            pos['filled_at'] = datetime.datetime.now(timezone.utc).isoformat()
            pos['filled_price'] = entry
            results.append({"symbol":sym,"status":"FILLED","reason":f"M1 LIMIT filled at {entry} within 10 candles","confluence_score":pos.get('confluence_score',0)})
        else:
            results.append({"symbol":sym,"status":"WAITING_LIMIT","reason":f"M1 Waiting LIMIT {entry} - 10 candle belum kena","confluence_score":pos.get('confluence_score',0)})

# === 2. CEK ACTIVE TP/SL M1 ===
for pos in active_positions[:]:
    if pos.get('order_status') != 'FILLED': continue
    try:
        curr = get_current_price(pos['symbol'])
        if not curr: continue
        entry = pos['entry']; sl = pos['sl']; tp = pos['tp']; direction = pos['direction']
        hit = None
        if direction == 'LONG':
            if curr <= sl: hit='SL_HIT'
            elif curr >= tp: hit='TP_HIT'
        else:
            if curr >= sl: hit='SL_HIT'
            elif curr <= tp: hit='TP_HIT'
        if hit:
            pos['close_status']=hit
            pos['pnl_rrr']= 2.0 if 'TP' in hit else -1.0
            pos['closed_at']=datetime.datetime.now(timezone.utc).isoformat()
            pos['close_price']=curr
            closed_positions.append(pos)
            active_positions.remove(pos)
            results.append({"symbol":pos['symbol'],"status":hit,"reason":hit,"confluence_score":0})
        else:
            results.append({"symbol":pos['symbol'],"status":"HOLD","reason":f"Holding {pos.get('direction')}","confluence_score":pos.get('confluence_score',0)})
    except Exception as e:
        results.append({"symbol":pos['symbol'],"status":"ERROR","reason":str(e),"confluence_score":0})

# === 3. SCAN BARU M1 ===
slots_left = MAX_POSITIONS - len(active_positions)
active_symbols = [p['symbol'] for p in active_positions]

if slots_left > 0:
    for sym in SYMBOLS:
        if len(valid_new) >= slots_left: break
        if sym in active_symbols: continue
        try:
            r=analyze_symbol_multi(sym)
            results.append(r)
            print(f"{sym}: {r['status']} SCORE {r.get('confluence_score',0)} - {r['reason'][:150]}")
            if r['status']=="VALID" and len(valid_new)<slots_left:
                new_pos = {
                    "symbol": sym, "direction": r['direction'], "entry": r['entry'], "sl": r['sl'], "tp": r['tp'], "rrr": r['rrr'], "sl_pct": r['sl_pct'], "tp_pct": r['tp_pct'],
                    "zone_price": r['zone']['price'], "confluence_score": r['confluence_score'], "confluences": r['confluences'],
                    "sizing": r['sizing'], "reason": r['reason'],
                    "open_price": r['price'], "open_time": datetime.datetime.now(timezone.utc).isoformat(),
                    "status": "WAITING_LIMIT", "order_status": "WAITING_LIMIT", "paper_trade": True,
                    "limit_order": True, "fill_check": "10 candles 1m"
                }
                valid_new.append(new_pos)
                active_positions.append(new_pos)
        except Exception as e:
            results.append({"symbol":sym,"status":"ERROR","reason":str(e),"confluence_score":0})
else:
    for sym in SYMBOLS:
        if sym not in active_symbols:
            results.append({"symbol":sym,"status":"SKIP","reason":f"Max pos {MAX_POSITIONS}","filter":"max_pos","confluence_score":0})

positions_data['active'] = active_positions
positions_data['closed'] = closed_positions[-500:]
save_positions(positions_data)

def build_paper_trading():
    try:
        capital = PAPER_START_CAPITAL
        history = []
        for pos in sorted(closed_positions, key=lambda x: x.get('closed_at','')):
            if pos.get('close_status') not in ('TP_HIT','SL_HIT'): continue
            r = pos.get('pnl_rrr',0)
            pnl_usd = PAPER_RISK_USD * r if r>0 else -PAPER_RISK_USD
            notional = pos.get('sizing', {}).get('notional_usd', 200)
            fee = notional * 0.001
            pnl_usd -= fee
            capital += pnl_usd
            capital = max(capital, 0.1)
            history.append({"symbol": pos.get('symbol'), "direction": pos.get('direction'), "entry": pos.get('entry'), "sl": pos.get('sl'), "tp": pos.get('tp'), "close_status": pos.get('close_status'), "pnl_r": r, "pnl_usd": round(pnl_usd,4), "sizing": pos.get('sizing', {}), "capital_after": round(capital,4), "confluence_score": pos.get('confluence_score',0), "closed_at": pos.get('closed_at','')[:19]})
        wins = len([h for h in history if h['pnl_r']>0])
        losses = len([h for h in history if h['pnl_r']<0])
        total_r = sum(h['pnl_r'] for h in history)
        paper_data = {"generated_at": datetime.datetime.now(timezone.utc).isoformat(), "bot_version": "V10_M1_TEST_100TRADE", "config": {"start_capital": PAPER_START_CAPITAL, "risk_usd": PAPER_RISK_USD, "min_sl_pct": MIN_SL_PCT*100, "min_confluence": MIN_CONFLUENCE_SCORE, "tf": "M1/M5", "limit_logic": "M1 10 candles"}, "summary": {"initial": PAPER_START_CAPITAL, "final": round(capital,2), "total_trades": len(history), "wins": wins, "losses": losses, "win_rate": round(wins/len(history)*100,1) if history else 0, "total_r": round(total_r,2)}, "trades": history, "active_positions": active_positions, "waiting_limit": [p for p in active_positions if p.get('order_status')=='WAITING_LIMIT']}
        with open(PAPER_FILE,'w') as f: json.dump(paper_data,f,indent=2)
        print(f"PAPER M1: ${PAPER_START_CAPITAL} -> ${capital:.2f} | {total_r}R | WR {wins}/{len(history)} | WAITING {len([p for p in active_positions if p.get('order_status')=='WAITING_LIMIT'])}")
    except Exception as e:
        print(f"Paper build error: {e}")

build_paper_trading()

valid=[x for x in results if x['status']=="VALID"]
skip=[x for x in results if x['status']=="SKIP"]
out={"last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),"bot_version":"V10_M1_TEST_100TRADE","params":{"MIN_CONFLUENCE_SCORE":MIN_CONFLUENCE_SCORE,"MAX_POSITIONS":MAX_POSITIONS,"MIN_SL_PCT":MIN_SL_PCT,"MAX_SL_PCT":MAX_SL_PCT,"TF":"M1/M5"},"summary":{"total_scanned":len(results), "valid_new":len(valid_new), "valid":len(valid), "skip":len(skip), "active_positions":len(active_positions), "waiting_limit":len([p for p in active_positions if p.get('order_status')=='WAITING_LIMIT']), "avg_confluence": round(sum(r.get('confluence_score',0) for r in results)/len(results),2) if results else 0, "by_filter":{"confluence":len([r for r in skip if r.get("filter")=="confluence"]), "distance":len([r for r in skip if r.get("filter")=="distance"]), "zone":len([r for r in skip if r.get("filter")=="zone"])}}, "results":results, "valid_trades":valid, "valid_new_positions":valid_new, "running_positions":active_positions, "closed_positions":closed_positions[-30:], "stats":positions_data['stats']}
with open("last_scan.json","w") as f: json.dump(out,f,indent=2)
print(f"\nDONE M1 TEST: {len(valid_new)} NEW LIMIT, {len([p for p in active_positions if p.get('order_status')=='WAITING_LIMIT'])} WAITING")
