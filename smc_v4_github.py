import requests, json, datetime, time, os
from datetime import timezone

# === CONFIG ===
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 0.5
MIN_CONFIDENCE = 6
LIMIT = 100
MAX_POSITIONS = 5  # limit entry biar akun growth, tidak overtrade
POSITIONS_FILE = "positions.json"  # file untuk jaga posisi sampai TP/SL

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
    if len(candles) < lookback+10: return None, None
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
        if c['low'] < sl['price'] * 0.9995 and c['close'] > sl['price']:
            return {"sweep": True, "type": "BULLISH_SWEEP", "level": sl['price'], "reason": f"Wick {c['low']:.2f} below swing low {sl['price']:.2f} then close {c['close']:.2f} (0.05% sweep)"}
        if c['high'] > sh['price'] * 1.0005 and c['close'] < sh['price']:
            return {"sweep": True, "type": "BEARISH_SWEEP", "level": sh['price'], "reason": f"Wick {c['high']:.2f} above swing high {sh['price']:.2f} then close {c['close']:.2f} (0.05% sweep)"}
    return {"sweep": False, "reason": f"No sweep H:{sh['price']:.2f} L:{sl['price']:.2f}"}

def detect_choch(candles):
    if len(candles) < 25: return {"choch": False, "reason": "short"}
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
    if last_close > prev_high_close * 1.0003:
        return {"choch": True, "type": "BULLISH_CHOCH", "reason": f"Close {last_close:.2f} breaks prev high close {prev_high_close:.2f} (0.03%)"}
    if last_close < prev_low_close * 0.9997:
        return {"choch": True, "type": "BEARISH_CHOCH", "reason": f"Close {last_close:.2f} breaks prev low close {prev_low_close:.2f} (0.03%)"}
    if last_high > prev_high * 1.0003:
        return {"choch": True, "type": "BULLISH_CHOCH", "reason": f"High {last_high:.2f} breaks prev high {prev_high:.2f} (0.03% high break)"}
    if last_low < prev_low * 0.9997:
        return {"choch": True, "type": "BEARISH_CHOCH", "reason": f"Low {last_low:.2f} breaks prev low {prev_low:.2f} (0.03% low break)"}
    return {"choch": False, "reason": f"No ChoCh range {prev_low_close:.2f}-{prev_high_close:.2f} (H:{prev_high:.2f} L:{prev_low:.2f})"}

def detect_zones(candles):
    if len(candles) < 20: return None
    zones = []
    # V4.6: expand lookback 20 candles (was 6) biar zone tidak cepat hilang
    for i in range(len(candles)-20, len(candles)-1):
        c = candles[i]
        nc = candles[i+1]
        if c['close'] < c['open'] and nc['close'] > nc['open'] and nc['close'] > c['high']:
            zones.append({"type": "OB", "subtype": "BULLISH_OB", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low']})
        if c['close'] > c['open'] and nc['close'] < nc['open'] and nc['low'] < c['low']:
            zones.append({"type": "BREAKER", "subtype": "BULLISH_BREAKER", "price": (c['high']+c['low'])/2, "high": c['high'], "low": c['low']})
    for i in range(len(candles)-20, len(candles)-1):
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
    # V4.6: BREAKER_ONLY loosen - allow breaker distance <0.5% even without sweep+choch for tuning
    if not sweep['sweep'] and not choch['choch']:
        if zone and zone['type']=="BREAKER" and dist<0.5 and conf>=6:
            pass
        elif zone and zone['type']=="OB" and dist<0.3 and conf>=6:
            pass
        else:
            return {**base,"status":"SKIP","reason":f"{sweep['reason']} + {choch['reason']}","filter":"sweep"}
    if not choch['choch']:
        if zone and zone['type']=="BREAKER" and dist<0.3 and conf>=6 and sweep['sweep']:
            pass
        elif zone and zone['type']=="BREAKER" and dist<0.15 and conf>=7:
            pass
        else:
            return {**base,"status":"SKIP","reason":choch['reason'],"filter":"choch"}
    if not zone: return {**base,"status":"SKIP","reason":"No OB/Breaker/FVG","filter":"zone"}
    if dist>MAX_DISTANCE_PCT: return {**base,"status":"SKIP","reason":f"Distance {dist:.2f}% > {MAX_DISTANCE_PCT}%","filter":"distance"}
    if conf<MIN_CONFIDENCE: return {**base,"status":"SKIP","reason":f"Confidence {conf}<{MIN_CONFIDENCE}","filter":"confidence"}
    entry=zone['price']
    sweep_type = sweep.get('type','')
    choch_type = choch.get('type','')
    if not sweep['sweep']:
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
    tag = "SWEEP+CHOCH" if sweep["sweep"] else "CHOCH_ONLY"
    return {**base,"status":"VALID","reason":f"{tag} {sweep_type}+{choch.get('type','NO_SWEEP')}+{zone['type']}","direction":direction,"entry":round(entry,4),"sl":round(sl,4),"tp1":round(tp1,4),"tp2":round(tp2,4),"rrr":round(abs(tp1-entry)/abs(entry-sl),2) if entry!=sl else 0,"order_type":"LIMIT","order_status":"WAITING_LIMIT","zone_price":round(zone['price'],4),"filter":"none"}

# === POSITION MANAGER (NEW - untuk growth akun) ===
def load_positions():
    """Load posisi yang masih aktif dari file"""
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE,'r') as f:
                return json.load(f)
        except:
            return {"active": [], "closed": [], "stats": {"wins":0,"losses":0,"total_pnl":0}}
    return {"active": [], "closed": [], "stats": {"wins":0,"losses":0,"total_pnl":0}}

def save_positions(data):
    with open(POSITIONS_FILE,'w') as f:
        json.dump(data,f,indent=2)

def check_position_status(pos, current_price, klines_15m=None):
    """V4.5 LIMIT GUARD: cek apakah limit kena, expired, atau missed"""
    # 1. Jika masih WAITING_LIMIT, cek apakah entry pernah tersentuh
    if pos.get('order_status') == "WAITING_LIMIT":
        if klines_15m:
            # cek apakah ada candle yang high>=entry>=low
            touched = any(c['low'] <= pos['entry'] <= c['high'] for c in klines_15m[-12:])  # cek 12 candle terakhir (3 jam)
            if touched:
                pos['order_status'] = "FILLED"
                pos['filled_at'] = datetime.datetime.now(timezone.utc).isoformat()
                pos['filled_price'] = pos['entry']
                print(f"  FILLED {pos['symbol']} at {pos['entry']}")
            else:
                # belum tersentuh - cek apakah sudah lewat TP tanpa fill (missed) atau expired
                age_candles = len(klines_15m)  # simplifikasi
                open_time = datetime.datetime.fromisoformat(pos['open_time'].replace('Z','+00:00')) if 'T' in pos.get('open_time','') else datetime.datetime.now(timezone.utc)
                age_hours = (datetime.datetime.now(timezone.utc) - open_time).total_seconds()/3600
                # Jika sudah 4 jam belum fill
                if age_hours > 4:
                    return "EXPIRED", 0
                # Jika harga sudah lewat TP1 tanpa fill (missed opportunity)
                if pos['direction']=="LONG" and current_price >= pos['tp1'] * 0.999:
                    return "MISSED_TP", 0
                if pos['direction']=="SHORT" and current_price <= pos['tp1'] * 1.001:
                    return "MISSED_TP", 0
                return "WAITING_LIMIT", 0
    
    # 2. Jika sudah FILLED / ACTIVE, baru cek TP/SL
    if pos['direction']=="LONG":
        if current_price <= pos['sl']:
            return "SL_HIT", -1
        if current_price >= pos['tp2']:
            return "TP2_HIT", 3
        if current_price >= pos['tp1']:
            return "TP1_HIT", 1.5
    else: # SHORT
        if current_price >= pos['sl']:
            return "SL_HIT", -1
        if current_price <= pos['tp2']:
            return "TP2_HIT", 3
        if current_price <= pos['tp1']:
            return "TP1_HIT", 1.5
    return "ACTIVE", 0

# === MAIN SCAN ===
print(f"=== SMC V4.5 LIMIT GUARD + POSITION MANAGER | MAX {MAX_POSITIONS} POS ===")
positions_data = load_positions()
active_positions = positions_data.get("active", [])
closed_positions = positions_data.get("closed", [])
active_symbols = [p['symbol'] for p in active_positions]

# 1. Update status posisi aktif - V4.5 with klines check
print(f"Checking {len(active_positions)} active positions...")
for pos in active_positions[:]:
    curr = get_current_price(pos['symbol'])
    if not curr:
        continue
    klines = get_klines(pos['symbol'], "15m", 50)
    status, rrr = check_position_status(pos, curr, klines)
    # WAITING_LIMIT tetap dijaga, jangan close
    if status == "WAITING_LIMIT":
        pos['current_price'] = curr
        pos['last_check'] = datetime.datetime.now(timezone.utc).isoformat()
        print(f"  WAITING {pos['symbol']} Entry {pos['entry']} Current {curr} (limit belum kena)")
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
        if rrr>0:
            positions_data['stats']['wins']+=1
        else:
            positions_data['stats']['losses']+=1
        positions_data['stats']['total_pnl']+=rrr
        print(f"  CLOSED {pos['symbol']} {status} R:{rrr}")

# 2. Scan baru hanya jika slot masih ada
slots_left = MAX_POSITIONS - len(active_positions)
print(f"Slots: {len(active_positions)}/{MAX_POSITIONS} active, {slots_left} slots left")

results=[]
valid_new=[]
if slots_left>0:
    for sym in SYMBOLS:
        if sym in active_symbols:
            print(f"{sym}: SKIP - already in active position (dijaga sampai TP/SL)")
            results.append({"symbol":sym,"status":"SKIP","reason":f"Already in active position {sym} - dijaga sampai TP/SL","filter":"position_guard"})
            continue
        try:
            r=analyze_symbol_real(sym)
            results.append(r)
            print(f"{sym}: {r['status']} - {r['reason'][:80]}")
            if r['status']=="VALID" and len(valid_new)<slots_left:
                # Buat posisi baru, dijaga sampai TP/SL
                new_pos = {
                    "symbol": sym,
                    "direction": r['direction'],
                    "entry": r['entry'],
                    "sl": r['sl'],
                    "tp1": r['tp1'],
                    "tp2": r['tp2'],
                    "rrr": r['rrr'],
                    "zone_price": r['zone_price'],
                    "confidence": r['confidence'],
                    "reason": r['reason'],
                    "open_price": r['price'],
                    "open_time": datetime.datetime.now(timezone.utc).isoformat(),
                    "status": "ACTIVE",
                    "order_status": "WAITING_LIMIT"
                }
                valid_new.append(new_pos)
                active_positions.append(new_pos)
                print(f"  -> NEW POSITION OPEN {sym} {r['direction']} Entry {r['entry']}")
            time.sleep(0.4)
        except Exception as e:
            results.append({"symbol":sym,"status":"ERROR","reason":str(e)})
else:
    print(f"MAX POSITIONS REACHED ({MAX_POSITIONS}) - skip new scan, jaga posisi existing")
    for sym in SYMBOLS:
        if sym not in active_symbols:
            results.append({"symbol":sym,"status":"SKIP","reason":f"Max positions {MAX_POSITIONS} reached - guard mode","filter":"max_pos"})

# 3. Save positions
positions_data['active'] = active_positions
positions_data['closed'] = closed_positions[-100:]  # keep last 100
save_positions(positions_data)

valid=[x for x in results if x['status']=="VALID"]
skip=[x for x in results if x['status']=="SKIP"]

out={
    "last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),
    "bot_version":"V4.6_EXPANDED_ZONE",
    "params":{"MAX_DISTANCE_PCT":MAX_DISTANCE_PCT,"MIN_CONFIDENCE":MIN_CONFIDENCE,"MAX_POSITIONS":MAX_POSITIONS},
    "summary":{
        "total_scanned":len(results),
        "valid_new":len(valid_new),
        "valid":len(valid),
        "skip":len(skip),
        "active_positions":len(active_positions),
        "closed_today":len([c for c in closed_positions if c.get('closed_at','').startswith(datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d'))]),
        "by_filter":{
            "sweep":len([r for r in skip if r.get("filter")=="sweep"]),
            "choch":len([r for r in skip if r.get("filter")=="choch"]),
            "zone":len([r for r in skip if r.get("filter")=="zone"]),
            "distance":len([r for r in skip if r.get("filter")=="distance"]),
            "confidence":len([r for r in skip if r.get("filter")=="confidence"]),
            "time":len([r for r in skip if r.get("filter")=="time"]),
            "setup_ban":len([r for r in skip if r.get("filter")=="setup_ban"]),
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
    "tuning_notes":"V4.6 expanded zone 20 candles + breaker 0.5% - koin dijaga sampai TP/SL"
}

with open("last_scan.json","w") as f:
    json.dump(out,f,indent=2)

print(f"\nDONE: {len(valid_new)} NEW, {len(active_positions)} ACTIVE, {len(closed_positions)} CLOSED | PnL R:{positions_data['stats']['total_pnl']}")
print("saved last_scan.json + positions.json")
