
import requests, json, datetime, time
from datetime import timezone
import os

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 0.5
MIN_CONFIDENCE = 6  # lowered to 6 for tuning phase
LIMIT = 100

def get_klines(symbol, interval, limit=100):
    try:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, dict):
            print(f"API returned dict for {symbol}: {data}")
            return []
        return [{"open": float(x[1]), "high": float(x[2]), "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])} for x in data]
    except Exception as e:
        print(f"Error klines {symbol} {interval}: {e}")
        return []

def get_current_price(symbol):
    try:
        r = requests.get(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}", timeout=5)
        return float(r.json()['price'])
    except:
        return None

def find_swings(candles, lookback=20):
    # V4.2 FIX: exclude last 5 candles so sweep has room
    if len(candles) < lookback+10: return None, None
    # swing from 20 candles before last 5
    slice_c = candles[-(lookback+5):-5]
    highs = [c['high'] for c in slice_c]
    lows = [c['low'] for c in slice_c]
    return {"price": max(highs)}, {"price": min(lows)}

def detect_sweep(candles):
    if len(candles) < 30: return {"sweep": False, "reason": "Not enough candles"}
    sh, sl = find_swings(candles, 20)
    if not sh or not sl: return {"sweep": False, "reason": "No swing"}
    last5 = candles[-5:]
    for c in last5:
        # V4.1: looser sweep 0.05% (was 0.2%) for ranging market
        if c['low'] < sl['price'] * 0.9995 and c['close'] > sl['price']:
            return {"sweep": True, "type": "BULLISH_SWEEP", "level": sl['price'], "reason": f"Wick {c['low']:.2f} below swing low {sl['price']:.2f} then close {c['close']:.2f} (0.05% sweep)"}
        if c['high'] > sh['price'] * 1.0005 and c['close'] < sh['price']:
            return {"sweep": True, "type": "BEARISH_SWEEP", "level": sh['price'], "reason": f"Wick {c['high']:.2f} above swing high {sh['price']:.2f} then close {c['close']:.2f} (0.05% sweep)"}
    return {"sweep": False, "reason": f"No sweep H:{sh['price']:.2f} L:{sl['price']:.2f}"}

def detect_choch(candles):
    if len(candles) < 25: return {"choch": False, "reason": "short"}
    # V4.3: check both close and high/low break
    closes = [c['close'] for c in candles[-20:]]
    highs = [c['high'] for c in candles[-20:]]
    lows = [c['low'] for c in candles[-20:]]
    last_close = closes[-1]
    last_high = highs[-1]
    last_low = lows[-1]
    prev_high_close = max(closes[-11:-1])
    prev_low_close = min(closes[-11:-1])
    prev_high = max(highs[-11:-1])
    prev_low = min(lows[-11:-1])
    # close break 0.03%
    if last_close > prev_high_close * 1.0003:
        return {"choch": True, "type": "BULLISH_CHOCH", "reason": f"Close {last_close:.2f} breaks prev high close {prev_high_close:.2f} (0.03%)"}
    if last_close < prev_low_close * 0.9997:
        return {"choch": True, "type": "BEARISH_CHOCH", "reason": f"Close {last_close:.2f} breaks prev low close {prev_low_close:.2f} (0.03%)"}
    # high/low break (more sensitive)
    if last_high > prev_high * 1.0003:
        return {"choch": True, "type": "BULLISH_CHOCH", "reason": f"High {last_high:.2f} breaks prev high {prev_high:.2f} (0.03% high break)"}
    if last_low < prev_low * 0.9997:
        return {"choch": True, "type": "BEARISH_CHOCH", "reason": f"Low {last_low:.2f} breaks prev low {prev_low:.2f} (0.03% low break)"}
    return {"choch": False, "reason": f"No ChoCh range {prev_low_close:.2f}-{prev_high_close:.2f} (H:{prev_high:.2f} L:{prev_low:.2f})"}

def detect_zones(candles):
    if len(candles) < 10: return None
    zones = []
    for i in range(len(candles)-6, len(candles)-1):
        c = candles[i]
        nc = candles[i+1]
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['close'] > c['high']:
            zones.append({"type": "OB", "subtype": "BULLISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low']})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['low'] < c['low']:
            zones.append({"type": "BREAKER", "subtype": "BULLISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low']})
    for i in range(len(candles)-4, len(candles)-1):
        c1 = candles[i]
        c3 = candles[i+2] if i+2 < len(candles) else None
        if not c3: continue
        if c1['high'] < c3['low']:
            zones.append({"type": "FVG", "subtype": "BULLISH_FVG", "price": (c1['high']+c3['low'])/2, "high": c3['low'], "low": c1['high']})
        if c1['low'] > c3['high']:
            zones.append({"type": "FVG", "subtype": "BEARISH_FVG", "price": (c1['low']+c3['high'])/2, "high": c1['low'], "low": c3['high']})
    if not zones: return None
    price = candles[-1]['close']
    return min(zones, key=lambda z: abs(z['price']-price))

def analyze_symbol_real(symbol):
    price = get_current_price(symbol)
    k15 = get_klines(symbol, "15m", LIMIT)
    k1h = get_klines(symbol, "1h", LIMIT)
    if not k15 or not k1h or not price:
        return {"symbol": symbol, "status": "ERROR", "reason": "API fail", "price": price or 0}
    htf = "NEUTRAL"
    if k1h[-1]['close'] > k1h[-20]['close']*1.01: htf="BULLISH"
    elif k1h[-1]['close'] < k1h[-20]['close']*0.99: htf="BEARISH"
    sweep = detect_sweep(k15)
    choch = detect_choch(k15)
    zone = detect_zones(k15)
    hour_wib = (datetime.datetime.now(timezone.utc).hour+7)%24
    dist = 999
    if zone: dist = abs(price-zone['price'])/price*100
    conf = 5 + (2 if sweep['sweep'] else 0) + (2 if choch['choch'] else 0) + (1 if zone and zone['type']=="BREAKER" else 0) + (1 if htf!="NEUTRAL" else 0)
    conf = min(10, conf)
    base = {"symbol":symbol,"price":round(price,4),"htf_trend":htf,"sweep":sweep,"choch":choch,"zone":zone,"distance_pct":round(dist,3),"confidence":conf,"hour_wib":hour_wib,"timestamp":datetime.datetime.now(timezone.utc).isoformat()}
    if zone and zone['type']=="FVG" and conf<8:
        return {**base,"status":"SKIP","reason":f"FVG conf {conf}<8 banned","filter":"setup_ban"}
    if 7<=hour_wib<=9:
        return {**base,"status":"SKIP","reason":f"Blacklist jam {hour_wib}:00 WIB","filter":"time"}
    # V4.3: BREAKER_ONLY mode for tuning - if breaker + distance <0.3% + conf>=6, allow even without sweep/choch
    if not sweep['sweep'] and not choch['choch']:
        if zone and zone['type']=="BREAKER" and dist<0.3 and conf>=6:
            # allow as BREAKER_ONLY
            pass
        else:
            return {**base,"status":"SKIP","reason":f"{sweep['reason']} + {choch['reason']}","filter":"sweep"}
    if not choch['choch']:
        if zone and zone['type']=="BREAKER" and dist<0.3 and conf>=6 and sweep['sweep']:
            pass
        elif zone and zone['type']=="BREAKER" and dist<0.15 and conf>=7:
            # pure breaker only
            pass
        else:
            return {**base,"status":"SKIP","reason":choch['reason'],"filter":"choch"}
    if not zone: return {**base,"status":"SKIP","reason":"No OB/Breaker/FVG","filter":"zone"}
    if dist>MAX_DISTANCE_PCT: return {**base,"status":"SKIP","reason":f"Distance {dist:.2f}% > {MAX_DISTANCE_PCT}%","filter":"distance"}
    if conf<MIN_CONFIDENCE: return {**base,"status":"SKIP","reason":f"Confidence {conf}<{MIN_CONFIDENCE}","filter":"confidence"}
    entry=zone['price']
    # Determine direction from sweep if exists, else from choch
    sweep_type = sweep.get('type','')
    choch_type = choch.get('type','')
    if not sweep['sweep']:
        # CHOCH_ONLY mode
        if 'BULLISH' in choch_type:
            sweep_type = "BULLISH_SWEEP"
        elif 'BEARISH' in choch_type:
            sweep_type = "BEARISH_SWEEP"
        else:
            sweep_type = "BULLISH_SWEEP"
    if sweep_type=="BULLISH_SWEEP":
        sl=zone['low']*0.998
        tp1=entry+(entry-sl)*1.5
        tp2=entry+(entry-sl)*3
        direction="LONG"
    else:
        sl=zone['high']*1.002
        tp1=entry-(sl-entry)*1.5
        tp2=entry-(sl-entry)*3
        direction="SHORT"
    # Add sweep vs choch_only tag
    tag = "SWEEP+CHOCH" if sweep["sweep"] else "CHOCH_ONLY"
    return {**base,"status":"VALID","reason":f"{tag} {sweep_type}+{choch.get('type','NO_SWEEP')}+{zone['type']}","direction":direction,"entry":round(entry,4),"sl":round(sl,4),"tp1":round(tp1,4),"tp2":round(tp2,4),"rrr":round(abs(tp1-entry)/abs(entry-sl),2) if entry!=sl else 0,"order_type":"LIMIT","order_status":"WAITING_LIMIT","zone_price":round(zone['price'],4),"filter":"none"}

results=[]
for sym in SYMBOLS:
    try:
        r=analyze_symbol_real(sym)
        results.append(r)
        print(f"{sym}: {r['status']} - {r['reason'][:80]}")
        time.sleep(0.4)
    except Exception as e:
        results.append({"symbol":sym,"status":"ERROR","reason":str(e)})

valid=[x for x in results if x['status']=="VALID"]
skip=[x for x in results if x['status']=="SKIP"]

out={"last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),"bot_version":"V4.3_BREAKER_ONLY","params":{"MAX_DISTANCE_PCT":MAX_DISTANCE_PCT,"MIN_CONFIDENCE":MIN_CONFIDENCE},"summary":{"total_scanned":len(results),"valid":len(valid),"skip":len(skip),"by_filter":{"sweep":len([r for r in skip if r.get("filter")=="sweep"]),"choch":len([r for r in skip if r.get("filter")=="choch"]),"zone":len([r for r in skip if r.get("filter")=="zone"]),"distance":len([r for r in skip if r.get("filter")=="distance"]),"confidence":len([r for r in skip if r.get("filter")=="confidence"]),"time":len([r for r in skip if r.get("filter")=="time"]),"setup_ban":len([r for r in skip if r.get("filter")=="setup_ban"])}},"results":results,"valid_trades":valid,"watchlist":skip,"running_positions":[],"tuning_notes":"Full report for tuning"}

with open("last_scan.json","w") as f:
    json.dump(out,f,indent=2)
print("saved")
