import requests, json, datetime, time, os
from datetime import timezone

# === CONFIG V6.3 FINAL - 12 COIN - REAL IDENTICAL ===
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 0.5
MIN_CONFLUENCE_SCORE = 6
LIMIT = 100
MAX_POSITIONS = 3
POSITIONS_FILE = "positions.json"
PAPER_START_CAPITAL = 20.0
PAPER_RISK_USD = 2.0
PAPER_FILE = "paper_trading.json"
MAX_LEVERAGE = {"BTCUSDT":100,"ETHUSDT":100,"SOLUSDT":50,"BNBUSDT":50,"XRPUSDT":50,"ADAUSDT":50,"DOGEUSDT":50,"AVAXUSDT":50,"LINKUSDT":50,"OPUSDT":20,"ARBUSDT":20,"MATICUSDT":20}
BUFFER_PCT = 0.0015
MIN_SL_PCT = 0.0015
MAX_SL_PCT = 0.012

def get_klines(symbol, interval, limit=100):
    try:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, dict): return []
        return [{"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),"close":float(x[4]),"volume":float(x[5])} for x in data]
    except: return []

def find_swings(candles, lookback=20):
    if len(candles) < lookback+10: return None, None
    slice_c = candles[-(lookback+5):-5]
    return {"price":max(c['high'] for c in slice_c)}, {"price":min(c['low'] for c in slice_c)}

def detect_sweep(candles):
    if len(candles) < 30: return {"sweep":False,"score":0}
    sh, sl = find_swings(candles, 20)
    if not sh or not sl: return {"sweep":False,"score":0}
    for c in candles[-5:]:
        if c['low'] < sl['price']*0.9995 and c['close'] > sl['price']:
            return {"sweep":True,"type":"BULLISH_SWEEP","level":sl['price'],"score":2}
        if c['high'] > sh['price']*1.0005 and c['close'] < sh['price']:
            return {"sweep":True,"type":"BEARISH_SWEEP","level":sh['price'],"score":2}
    return {"sweep":False,"score":0}

def detect_choch(candles):
    if len(candles) < 25: return {"choch":False,"score":0}
    closes=[c['close'] for c in candles[-20:]]
    if closes[-1] > max(closes[-11:-1])*1.0005: return {"choch":True,"type":"BULLISH_CHOCH","score":2}
    if closes[-1] < min(closes[-11:-1])*0.9995: return {"choch":True,"type":"BEARISH_CHOCH","score":2}
    return {"choch":False,"score":0}

def detect_zones(candles):
    zones=[]
    for i in range(len(candles)-20,len(candles)-1):
        c=candles[i]; nc=candles[i+1]
        if c['close']<c['open'] and nc['close']>nc['open'] and nc['close']>c['high']:
            zones.append({"subtype":"BULLISH_OB","price":(c['high']+c['low'])/2,"high":c['high'],"low":c['low'],"score":1.5})
        if c['close']>c['open'] and nc['close']<nc['open'] and nc['low']<c['low']:
            zones.append({"subtype":"BULLISH_BREAKER","price":(c['high']+c['low'])/2,"high":c['high'],"low":c['low'],"score":2})
        if c['close']>c['open'] and nc['close']<nc['open'] and nc['high']<c['low']:
            zones.append({"subtype":"BEARISH_OB","price":(c['high']+c['low'])/2,"high":c['high'],"low":c['low'],"score":1.5})
        if c['close']<c['open'] and nc['close']>nc['open'] and nc['low']>c['high']:
            zones.append({"subtype":"BEARISH_BREAKER","price":(c['high']+c['low'])/2,"high":c['high'],"low":c['low'],"score":2})
    if not zones: return {"best":None,"score":0}
    price=candles[-1]['close']
    best=min(zones,key=lambda z: abs(z['price']-price))
    return {"best":best,"score":best['score']}

def detect_htf(k1h):
    if len(k1h)<50: return {"score":0}
    ema20=sum(c['close'] for c in k1h[-20:])/20
    ema50=sum(c['close'] for c in k1h[-50:])/50
    last=k1h[-1]['close']
    if last>ema20>ema50: return {"score":1}
    if last<ema20<ema50: return {"score":1}
    return {"score":0.5}

def detect_vol(candles):
    if len(candles)<20: return {"score":0}
    avg=sum(c['volume'] for c in candles[-20:-1])/19
    if candles[-1]['volume']>avg*1.5: return {"score":1}
    if candles[-1]['volume']>avg*1.2: return {"score":0.5}
    return {"score":0}

def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE) as f: return json.load(f)
        except: pass
    return {"active":[],"closed":[],"stats":{"wins":0,"losses":0,"total_pnl":0}}

def save_positions(d):
    with open(POSITIONS_FILE,'w') as f: json.dump(d,f,indent=2)

positions_data=load_positions()
active_positions=positions_data.get("active",[])
closed_positions=positions_data.get("closed",[])

# check active
new_active=[]
for pos in active_positions:
    kl=get_klines(pos["symbol"],"15m",20)
    if not kl: new_active.append(pos); continue
    high=max(c['high'] for c in kl[-10:]); low=min(c['low'] for c in kl[-10:])
    if pos["direction"]=="LONG":
        if low<=pos["sl"]:
            pos["close_status"]="SL_HIT"; pos["pnl_rrr"]=-1; pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat(); closed_positions.append(pos); positions_data["stats"]["losses"]+=1; positions_data["stats"]["total_pnl"]-=1
        elif high>=pos["tp"]:
            pos["close_status"]="TP_HIT"; pos["pnl_rrr"]=2; pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat(); closed_positions.append(pos); positions_data["stats"]["wins"]+=1; positions_data["stats"]["total_pnl"]+=2
        else:
            created=datetime.datetime.fromisoformat(pos.get("created_at","2000-01-01T00:00:00+00:00"))
            if (datetime.datetime.now(timezone.utc)-created).total_seconds()>86400 and pos.get("status")=="WAITING_LIMIT":
                pos["close_status"]="EXPIRED"; pos["pnl_rrr"]=0; pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat(); closed_positions.append(pos)
            else:
                if pos.get("status")=="WAITING_LIMIT" and any(c['low']<=pos["entry"]<=c['high'] for c in kl[-96:]): pos["status"]="FILLED"
                new_active.append(pos)
    else:
        if high>=pos["sl"]:
            pos["close_status"]="SL_HIT"; pos["pnl_rrr"]=-1; pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat(); closed_positions.append(pos); positions_data["stats"]["losses"]+=1; positions_data["stats"]["total_pnl"]-=1
        elif low<=pos["tp"]:
            pos["close_status"]="TP_HIT"; pos["pnl_rrr"]=2; pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat(); closed_positions.append(pos); positions_data["stats"]["wins"]+=1; positions_data["stats"]["total_pnl"]+=2
        else:
            created=datetime.datetime.fromisoformat(pos.get("created_at","2000-01-01T00:00:00+00:00"))
            if (datetime.datetime.now(timezone.utc)-created).total_seconds()>86400 and pos.get("status")=="WAITING_LIMIT":
                pos["close_status"]="EXPIRED"; pos["pnl_rrr"]=0; pos["closed_at"]=datetime.datetime.now(timezone.utc).isoformat(); closed_positions.append(pos)
            else:
                if pos.get("status")=="WAITING_LIMIT" and any(c['low']<=pos["entry"]<=c['high'] for c in kl[-96:]): pos["status"]="FILLED"
                new_active.append(pos)
active_positions=new_active

results=[]; valid_new=[]; active_symbols=[p["symbol"] for p in active_positions]
for sym in SYMBOLS:
    if sym in active_symbols and len(active_positions)>=MAX_POSITIONS: results.append({"symbol":sym,"status":"SKIP","filter":"max_pos","confluence_score":0}); continue
    k15=get_klines(sym,"15m",LIMIT); k1h=get_klines(sym,"1h",100)
    if not k15 or not k1h: results.append({"symbol":sym,"status":"ERROR","confluence_score":0}); continue
    price=k15[-1]['close']; sweep=detect_sweep(k15); choch=detect_choch(k15); zones=detect_zones(k15); htf=detect_htf(k1h); vol=detect_vol(k15)
    score=sweep["score"]+choch["score"]+zones["score"]+htf["score"]+vol["score"]
    if zones["best"] is None: results.append({"symbol":sym,"status":"SKIP","filter":"zone","confluence_score":score}); continue
    zone_price=zones["best"]["price"]; dist=abs(price-zone_price)/price*100
    if dist>MAX_DISTANCE_PCT: results.append({"symbol":sym,"status":"SKIP","filter":"distance","confluence_score":score}); continue
    if score<MIN_CONFLUENCE_SCORE: results.append({"symbol":sym,"status":"SKIP","filter":"confluence","confluence_score":score}); continue
    direction="LONG" if "BULLISH" in zones["best"]["subtype"] else "SHORT"
    if direction=="LONG":
        sl_level=zones["best"].get("low",zone_price*0.995)*(1-BUFFER_PCT); sl_pct=abs(price-sl_level)/price
        if sl_pct<MIN_SL_PCT: sl_level=price*(1-MIN_SL_PCT)
        if sl_pct>MAX_SL_PCT: results.append({"symbol":sym,"status":"SKIP","filter":"sl_wide","confluence_score":score}); continue
        tp=price+(price-sl_level)*2; entry=zone_price; sl=sl_level
    else:
        sl_level=zones["best"].get("high",zone_price*1.005)*(1+BUFFER_PCT); sl_pct=abs(sl_level-price)/price
        if sl_pct<MIN_SL_PCT: sl_level=price*(1+MIN_SL_PCT)
        if sl_pct>MAX_SL_PCT: results.append({"symbol":sym,"status":"SKIP","filter":"sl_wide","confluence_score":score}); continue
        tp=price-(sl_level-price)*2; entry=zone_price; sl=sl_level
    lev=MAX_LEVERAGE.get(sym,50); qty=PAPER_RISK_USD/abs(entry-sl); notional=qty*entry
    pos={"symbol":sym,"direction":direction,"entry":round(entry,6),"sl":round(sl,6),"tp":round(tp,6),"status":"WAITING_LIMIT","created_at":datetime.datetime.now(timezone.utc).isoformat(),"confluence_score":score,"sizing":{"risk_usd":2.0,"qty":round(qty,6),"notional_usd":round(notional,2),"leverage":lev}}
    if any(p["symbol"]==sym and p["direction"]==direction for p in active_positions): results.append({"symbol":sym,"status":"SKIP","filter":"position_guard","confluence_score":score}); continue
    results.append({"symbol":sym,"status":"VALID","direction":direction,"entry":pos["entry"],"sl":pos["sl"],"tp":pos["tp"],"confluence_score":score,"sizing":pos["sizing"]})
    if len(active_positions)+len(valid_new)<MAX_POSITIONS: valid_new.append(pos); active_positions.append(pos)

positions_data['active']=active_positions; positions_data['closed']=closed_positions[-200:]; save_positions(positions_data)

def build_paper():
    capital=PAPER_START_CAPITAL; history=[]; curve=[capital]
    for p in sorted(closed_positions, key=lambda x: x.get('closed_at','')):
        r=p.get('pnl_rrr',0); pnl=PAPER_RISK_USD*r - (p.get('sizing',{}).get('notional_usd',200)*0.001 if r!=0 else 0)
        capital+=pnl; capital=max(capital,0.1); curve.append(round(capital,2))
        history.append({"symbol":p.get('symbol'),"direction":p.get('direction'),"entry":p.get('entry'),"close_status":p.get('close_status'),"pnl_r":r,"capital_after":round(capital,4),"closed_at":p.get('closed_at','')[:19]})
    wins=len([h for h in history if h['pnl_r']>0]); losses=len([h for h in history if h['pnl_r']<0]); total_r=sum(h['pnl_r'] for h in history)
    data={"generated_at":datetime.datetime.now(timezone.utc).isoformat(),"bot_version":"V6_FINAL_12COIN","summary":{"initial":PAPER_START_CAPITAL,"final":round(capital,2),"total_trades":len(history),"wins":wins,"losses":losses,"win_rate":round(wins/len(history)*100,1) if history else 0,"total_r":round(total_r,2)},"trades":history}
    with open(PAPER_FILE,'w') as f: json.dump(data,f,indent=2)

build_paper()
out={"last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),"summary":{"valid_new":len(valid_new)},"results":results,"valid_new_positions":valid_new}
with open("last_scan.json","w") as f: json.dump(out,f,indent=2)
print(f"DONE V6 FINAL 12COIN: {len(valid_new)} NEW")
