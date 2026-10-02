"""
SMC V12 NO-PAPER - Scanner murni, fokus OKX DEMO $2.5 MAX
- NO paper_trading.json, NO positions.json
- Hanya output last_scan.json -> valid_new_positions
"""
import requests, json, datetime
from datetime import timezone

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 2.0
MIN_CONFLUENCE_SCORE = 4.0
LIMIT = 100
BUFFER_PCT = 0.002
MIN_SL_PCT = 0.0025
MAX_SL_PCT = 0.05

def get_klines(symbol, interval, limit=100):
    try:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, dict): return []
        return [{"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),"close":float(x[4]),"volume":float(x[5])} for x in data]
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
            return {"sweep": True, "type": "BULLISH_SWEEP", "level": sl['price'], "score": 2, "reason": f"Bull sweep {c['low']:.2f}"}
        if c['high'] > sh['price'] * 1.0005 and c['close'] < sh['price']:
            return {"sweep": True, "type": "BEARISH_SWEEP", "level": sh['price'], "score": 2, "reason": f"Bear sweep {c['high']:.2f}"}
    return {"sweep": False, "score":0}

def detect_choch(candles):
    if len(candles) < 25: return {"choch": False, "score":0}
    closes = [c['close'] for c in candles[-20:]]
    last_close = closes[-1]
    prev_high = max(closes[-11:-1]); prev_low = min(closes[-11:-1])
    if last_close > prev_high * 1.0005: return {"choch": True, "type": "BULLISH_CHOCH", "score": 2, "reason": f"Bull CHOCH"}
    if last_close < prev_low * 0.9995: return {"choch": True, "type": "BEARISH_CHOCH", "score": 2, "reason": f"Bear CHOCH"}
    return {"choch": False, "score":0}

def detect_zones(candles):
    if len(candles) < 20: return {"zones":[], "best":None, "score":0}
    zones = []
    for i in range(len(candles)-20, len(candles)-1):
        c = candles[i]; nc = candles[i+1]
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['close'] > c['high']:
            zones.append({"type": "OB", "subtype": "BULLISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":1.5})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['high'] < c['low']:
            zones.append({"type": "OB", "subtype": "BEARISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":1.5})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['low'] < c['low']:
            zones.append({"type": "BREAKER", "subtype": "BULLISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":2})
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['low'] > c['high']:
            zones.append({"type": "BREAKER", "subtype": "BEARISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low'], "score":2})
    for i in range(len(candles)-20, len(candles)-2):
        c1 = candles[i]; c3 = candles[i+2]
        if c1['high'] < c3['low']: zones.append({"type": "FVG", "subtype": "BULLISH_FVG", "price": (c1['high']+c3['low'])/2, "score":1})
        if c1['low'] > c3['high']: zones.append({"type": "FVG", "subtype": "BEARISH_FVG", "price": (c1['low']+c3['high'])/2, "score":1})
    if not zones: return {"zones":[], "best":None, "score":0}
    best = min(zones, key=lambda z: abs(z['price']-candles[-1]['close']))
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
    if candles[-1]['volume'] > avg_vol * 1.5: return {"score":1}
    if candles[-1]['volume'] > avg_vol * 1.2: return {"score":0.5}
    return {"score":0}

def analyze_symbol_multi(symbol):
    k_m1 = get_klines(symbol, "1m", LIMIT)
    k_m5 = get_klines(symbol, "5m", LIMIT)
    if not k_m1 or not k_m5: return {"symbol":symbol,"status":"SKIP","reason":"No klines","confluence_score":0}
    price = k_m1[-1]['close']
    curr = get_current_price(symbol) or price
    sweep = detect_sweep(k_m1); choch = detect_choch(k_m1); zone_res = detect_zones(k_m1); htf = detect_htf_trend(k_m5); vol = detect_volume(k_m1)
    total_score = sweep['score'] + choch['score'] + zone_res['score'] + htf['score'] + vol['score']
    if total_score < MIN_CONFLUENCE_SCORE:
        return {"symbol":symbol,"status":"SKIP","reason":f"Score {total_score} < {MIN_CONFLUENCE_SCORE}","confluence_score":total_score}
    best = zone_res['best']
    if not best:
        swing_low = min(c['low'] for c in k_m1[-20:]); swing_high = max(c['high'] for c in k_m1[-20:])
        best = {"subtype": "BULLISH_SWING" if curr>k_m1[-2]['close'] else "BEARISH_SWING", "price": swing_low if curr>k_m1[-2]['close'] else swing_high, "score":0.5}
    direction = "LONG" if "BULLISH" in best['subtype'] else "SHORT"
    zone_price = best['price']
    dist_pct = abs(curr - zone_price)/curr*100
    if dist_pct > MAX_DISTANCE_PCT: return {"symbol":symbol,"status":"SKIP","reason":f"Dist {dist_pct:.2f}%","confluence_score":total_score}
    sl_dist = abs(curr - zone_price)/curr + BUFFER_PCT
    if sl_dist < MIN_SL_PCT: sl_dist = MIN_SL_PCT
    if sl_dist > MAX_SL_PCT: sl_dist = MAX_SL_PCT
    if direction=="LONG": sl = zone_price*(1-BUFFER_PCT); entry = zone_price; tp = entry*(1+sl_dist*2)
    else: sl = zone_price*(1+BUFFER_PCT); entry = zone_price; tp = entry*(1-sl_dist*2)
    return {"symbol":symbol,"status":"VALID","direction":direction,"entry":entry,"sl":sl,"tp":tp,"rrr":2.0,"sl_pct":sl_dist,"tp_pct":sl_dist*2,"price":curr,"zone":best,"confluence_score":total_score,"reason":f"VALID M1 {direction} score {total_score} zone {best['subtype']} dist {dist_pct:.2f}%"}

if __name__=="__main__":
    results=[]; valid_new=[]
    for sym in SYMBOLS:
        try:
            r=analyze_symbol_multi(sym)
            results.append(r)
            print(f"{sym}: {r['status']} SCORE {r.get('confluence_score',0)} - {r.get('reason','')[:150]}")
            if r['status']=="VALID": valid_new.append(r)
        except Exception as e:
            results.append({"symbol":sym,"status":"ERROR","reason":str(e),"confluence_score":0})
    out={"last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),"bot_version":"V12_NO_PAPER","summary":{"total_scanned":len(results),"valid_new":len(valid_new)},"results":results,"valid_new_positions":valid_new,"valid_trades":valid_new}
    with open("last_scan.json","w") as f: json.dump(out,f,indent=2)
    print(f"\nDONE V12 NO-PAPER: {len(valid_new)} NEW SIGNAL for OKX demo")
