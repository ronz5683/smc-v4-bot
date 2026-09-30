"""
SMC V10 PRO LIMIT LOOSE - TEST MODE PAPER=DEMO RISK $2 FIX
- Tujuan: cepat dapat setup, biar demo ada trade dulu
- LOOSE: MIN_CONFLUENCE 5.0 (dari 7.0), MAX_DISTANCE 1.5% (dari 0.5%), SL 0.3%-3% (dari 0.5%-1.2%)
- NO Blacklist jam, NO daily_stop ketat, MAX_POS 5
- Risk $2 fix di executor yang akan auto adjust size, bukan di screener
- Kalau min size aja risk > $2.5, executor SKIP
"""
import requests, json, datetime, time, os
from datetime import timezone

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 1.5  # LOOSE dari 0.5
MIN_CONFLUENCE_SCORE = 5.0  # LOOSE dari 7.0 - biar cepat dapat trade
LIMIT = 100
MAX_POSITIONS = 5  # LOOSE dari 3
MAX_DAILY_SL = 10  # LOOSE dari 3
POSITIONS_FILE = "positions.json"
PAPER_FILE = "paper_trading.json"
PAPER_START_CAPITAL = 20.0
PAPER_RISK_USD = 2.0
MAX_LEVERAGE = {"BTCUSDT":100,"ETHUSDT":100,"SOLUSDT":50,"BNBUSDT":50,"XRPUSDT":50,"ADAUSDT":50,"DOGEUSDT":50,"AVAXUSDT":50,"LINKUSDT":50,"OPUSDT":20,"ARBUSDT":20,"MATICUSDT":20}
DEFAULT_MAX_LEV = 50
BUFFER_PCT = 0.002
MIN_SL_PCT = 0.003  # LOOSE 0.3%
MAX_SL_PCT = 0.03   # LOOSE 3% - biarin executor yang hitung risk $2

RUN_AWAY_PCT = {"BTCUSDT":0.8,"ETHUSDT":0.8,"BNBUSDT":0.9,"SOLUSDT":1.0,"LINKUSDT":1.0,"ADAUSDT":1.2,"DOGEUSDT":1.2,"AVAXUSDT":1.0,"ARBUSDT":1.5,"OPUSDT":1.5,"XRPUSDT":1.0,"MATICUSDT":1.2,"DEFAULT":1.0}

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

def count_sl_today(closed_positions):
    today = datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d')
    cnt = 0
    for c in closed_positions:
        if c.get('closed_at','').startswith(today) and c.get('pnl_rrr',0) < 0:
            cnt+=1
    return cnt

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

def detect_htf_trend(k1h):
    if len(k1h) < 50: return {"trend":"NEUTRAL","score":0}
    ema20 = sum(c['close'] for c in k1h[-20:])/20
    ema50 = sum(c['close'] for c in k1h[-50:])/50
    last = k1h[-1]['close']
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
    if len(candles) < 20: return {"score":0}
    gains=0; losses=0
    for i in range(len(candles)-14, len(candles)):
        ch = candles[i]['close']-candles[i-1]['close']
        if ch>0: gains+=ch
        else: losses-=ch
    if losses==0: rsi=100
    else:
        rs=gains/losses if losses!=0 else 0
        rsi=100 - (100/(1+rs))
    if rsi < 35: return {"score":1, "reason": f"RSI oversold {rsi:.1f}"}
    if rsi > 65: return {"score":1, "reason": f"RSI overbought {rsi:.1f}"}
    if rsi < 45 or rsi > 55: return {"score":0.5, "reason": f"RSI {rsi:.1f}"}
    return {"score":0, "reason": f"RSI neutral {rsi:.1f}"}

def load_positions():
    try:
        with open(POSITIONS_FILE) as f: return json.load(f)
    except: return {"active":[],"closed":[],"stats":{"wins":0,"losses":0,"total_pnl":0}}

def save_positions(data):
    with open(POSITIONS_FILE,'w') as f: json.dump(data,f,indent=2)

def analyze_symbol_multi(symbol):
    candles_15m = get_klines(symbol, "15m", 100)
    candles_1h = get_klines(symbol, "1h", 100)
    if not candles_15m or not candles_1h:
        return {"symbol":symbol,"status":"SKIP","reason":"No klines","filter":"no_data","confluence_score":0,"price":0}
    price = candles_15m[-1]['close']
    # NO BLACKLIST JAM - LOOSE MODE

    sweep = detect_sweep(candles_15m)
    choch = detect_choch(candles_15m)
    zone_res = detect_zones(candles_15m)
    htf = detect_htf_trend(candles_1h)
    vol = detect_volume(candles_15m)
    rsi = detect_rsi_filter(candles_15m)

    confluences = []; total_score = 0
    if sweep['score']>0: confluences.append(f"SWEEP({sweep['score']})"); total_score+=sweep['score']
    if choch['score']>0: confluences.append(f"CHOCH({choch['score']})"); total_score+=choch['score']
    if zone_res['score']>0 and zone_res['best']: confluences.append(f"{zone_res['best']['type']}({zone_res['score']})"); total_score+=zone_res['score']
    if htf['score']>0: confluences.append(f"HTF({htf['score']})"); total_score+=htf['score']
    if vol['score']>0: confluences.append(f"VOL({vol['score']})"); total_score+=vol['score']
    if rsi['score']>0: confluences.append(f"RSI({rsi['score']})"); total_score+=rsi['score']

    # LOOSE: score 5.0 aja udah VALID (dari 7.0)
    if total_score < MIN_CONFLUENCE_SCORE:
        return {"symbol":symbol,"price":price,"confluence_score":total_score,"status":"SKIP","reason":f"Score {total_score} < {MIN_CONFLUENCE_SCORE}","filter":"confluence","sweep":sweep,"choch":choch,"zone":zone_res['best'],"zones_all":zone_res['zones'],"htf":htf,"vol":vol,"rsi":rsi,"confluences":confluences}

    if not zone_res['best']:
        return {"symbol":symbol,"price":price,"confluence_score":total_score,"status":"SKIP","reason":"No zone","filter":"zone","sweep":sweep,"choch":choch,"zone":None,"zones_all":[],"htf":htf,"vol":vol,"rsi":rsi,"confluences":confluences}

    zone = zone_res['best']
    distance_pct = abs(price - zone['price'])/price*100
    if distance_pct > MAX_DISTANCE_PCT:
        return {"symbol":symbol,"price":price,"confluence_score":total_score,"status":"SKIP","reason":f"Distance {distance_pct:.2f}% > {MAX_DISTANCE_PCT}%","filter":"distance","sweep":sweep,"choch":choch,"zone":zone,"zones_all":zone_res['zones'],"htf":htf,"vol":vol,"rsi":rsi,"confluences":confluences}

    # Direction dari sweep/choch/zone
    is_bull = "BULLISH" in (zone.get('subtype','') + sweep.get('type','') + choch.get('type',''))
    is_bear = "BEARISH" in (zone.get('subtype','') + sweep.get('type','') + choch.get('type',''))
    direction = "LONG" if is_bull else "SHORT" if is_bear else ("LONG" if price < zone['price'] else "SHORT")

    # SL/TP
    sl_dist = abs(price - zone['price']) * 0.5 + price * 0.003
    if direction=="LONG":
        sl = zone['price'] - sl_dist
        tp = price + abs(price - sl)*2
    else:
        sl = zone['price'] + sl_dist
        tp = price - abs(price - sl)*2

    sl_pct = abs(price - sl)/price
    # LOOSE SL check
    if sl_pct < MIN_SL_PCT:
        return {"symbol":symbol,"price":price,"confluence_score":total_score,"status":"SKIP","reason":f"SL too narrow {sl_pct*100:.2f}%","filter":"sl_narrow","sweep":sweep,"choch":choch,"zone":zone,"zones_all":zone_res['zones'],"htf":htf,"vol":vol,"rsi":rsi,"confluences":confluences}
    if sl_pct > MAX_SL_PCT:
        return {"symbol":symbol,"price":price,"confluence_score":total_score,"status":"SKIP","reason":f"SL too wide {sl_pct*100:.2f}% > {MAX_SL_PCT*100}% but will be auto sized to $2 risk","filter":"sl_wide","sweep":sweep,"choch":choch,"zone":zone,"zones_all":zone_res['zones'],"htf":htf,"vol":vol,"rsi":rsi,"confluences":confluences}

    lev = MAX_LEVERAGE.get(symbol, DEFAULT_MAX_LEV)
    risk_usd = PAPER_RISK_USD
    notional = risk_usd / sl_pct if sl_pct>0 else 0
    qty = notional / price

    sizing = {"risk_usd": risk_usd, "sl_pct": round(sl_pct*100,4), "notional_usd": round(notional,2), "qty": round(qty,6), "leverage": lev, "leverage_mode": "ALWAYS_MAX", "margin_needed": round(notional/lev,2)}

    return {"symbol":symbol,"price":price,"confluence_score":total_score,"status":"VALID","reason":f"SCORE {total_score}/10: {'+'.join(confluences)} | LIMIT {zone['price']:.4f} SL {sl_pct*100:.2f}% | {htf.get('trend')}","direction":direction,"entry":zone['price'],"sl":sl,"tp":tp,"rrr":2.0,"sl_pct":sl_pct,"tp_pct":abs(price-tp)/price,"zone":zone,"sweep":sweep,"choch":choch,"htf":htf,"vol":vol,"rsi":rsi,"confluences":confluences,"sizing":sizing}

def main():
    print(f"=== SMC V10 LOOSE TEST MODE {datetime.datetime.now(timezone.utc)} ===")
    print(f"MIN_CONF {MIN_CONFLUENCE_SCORE} | MAX_DIST {MAX_DISTANCE_PCT}% | SL {MIN_SL_PCT*100}%-{MAX_SL_PCT*100}% | NO BLACKLIST")
    positions_data = load_positions()
    active_positions = positions_data.get('active',[])
    closed_positions = positions_data.get('closed',[])
    results=[]; valid_new=[]

    # Check existing positions for TP/SL
    for pos in active_positions[:]:
        try:
            curr_price = get_current_price(pos['symbol'])
            if not curr_price: continue
            direction = pos['direction']
            sl = pos['sl']; tp = pos['tp']
            hit = None
            if direction=="LONG":
                if curr_price <= sl: hit="SL_HIT"
                elif curr_price >= tp: hit="TP_HIT"
            else:
                if curr_price >= sl: hit="SL_HIT"
                elif curr_price <= tp: hit="TP_HIT"
            if hit:
                pos['close_status']=hit
                pos['pnl_rrr']=2.0 if hit=="TP_HIT" else -1.0
                pos['closed_at']=datetime.datetime.now(timezone.utc).isoformat()
                pos['close_price']=curr_price
                closed_positions.append(pos)
                active_positions.remove(pos)
                results.append({"symbol":pos['symbol'],"status":hit,"reason":hit,"confluence_score":0})
            else:
                results.append({"symbol":pos['symbol'],"status":"HOLD","reason":f"Holding {pos.get('direction')}","confluence_score":pos.get('confluence_score',0)})
        except Exception as e:
            results.append({"symbol":pos['symbol'],"status":"ERROR","reason":str(e),"confluence_score":0})

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
                        "limit_order": True, "fill_check": "2-3 candles 15m"
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
    positions_data['closed'] = closed_positions[-200:]
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
            paper_data = {"generated_at": datetime.datetime.now(timezone.utc).isoformat(), "bot_version": "V10_LOOSE_TEST_RISK2", "config": {"start_capital": PAPER_START_CAPITAL, "risk_usd": PAPER_RISK_USD, "min_sl_pct": MIN_SL_PCT*100, "min_confluence": MIN_CONFLUENCE_SCORE, "limit_logic": "LOOSE TEST MODE"}, "summary": {"initial": PAPER_START_CAPITAL, "final": round(capital,2), "total_trades": len(history), "wins": wins, "losses": losses, "win_rate": round(wins/len(history)*100,1) if history else 0, "total_r": round(total_r,2)}, "trades": history, "active_positions": active_positions, "waiting_limit": [p for p in active_positions if p.get('order_status')=='WAITING_LIMIT']}
            with open(PAPER_FILE,'w') as f: json.dump(paper_data,f,indent=2)
            print(f"PAPER LOOSE: ${PAPER_START_CAPITAL} -> ${capital:.2f} | {total_r}R | WR {wins}/{len(history)} | WAITING {len([p for p in active_positions if p.get('order_status')=='WAITING_LIMIT'])}")
        except Exception as e:
            print(f"Paper build error: {e}")

    build_paper_trading()

    valid=[x for x in results if x['status']=="VALID"]
    skip=[x for x in results if x['status']=="SKIP"]
    out={"last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),"bot_version":"V10_LOOSE_TEST_RISK2","params":{"MIN_CONFLUENCE_SCORE":MIN_CONFLUENCE_SCORE,"MAX_POSITIONS":MAX_POSITIONS,"MIN_SL_PCT":MIN_SL_PCT,"MAX_SL_PCT":MAX_SL_PCT,"LIMIT_LOGIC":"LOOSE TEST"},"summary":{"total_scanned":len(results), "valid_new":len(valid_new), "valid":len(valid), "skip":len(skip), "active_positions":len(active_positions), "waiting_limit":len([p for p in active_positions if p.get('order_status')=='WAITING_LIMIT']), "avg_confluence": round(sum(r.get('confluence_score',0) for r in results)/len(results),2) if results else 0, "by_filter":{"confluence":len([r for r in skip if r.get("filter")=="confluence"]), "distance":len([r for r in skip if r.get("filter")=="distance"]), "zone":len([r for r in skip if r.get("filter")=="zone"]), "sl_narrow":len([r for r in skip if r.get("filter")=="sl_narrow"]), "daily_stop":len([r for r in skip if r.get("filter")=="daily_stop"]), "price_ran_away":len([r for r in results if r.get("filter")=="price_ran_away"])}}, "results":results, "valid_trades":valid, "valid_new_positions":valid_new, "running_positions":active_positions, "closed_positions":closed_positions[-20:], "stats":positions_data['stats']}
    with open("last_scan.json","w") as f: json.dump(out,f,indent=2)
    print(f"\nDONE LOOSE: {len(valid_new)} NEW LIMIT, {len([p for p in active_positions if p.get('order_status')=='WAITING_LIMIT'])} WAITING")

if __name__ == "__main__":
    main()
