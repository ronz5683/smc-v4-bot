"""
SMC V12 NO-PAPER - Scanner murni, fokus 【entity-OKX¦canonical_name=OKX】 DEMO $2.5 MAX
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

# ... (sweep, choch, zones, htf, volume functions sama kayak sebelumnya) ...

def analyze_symbol_multi(symbol):
    k_m1 = get_klines(symbol, "1m", LIMIT)
    k_m5 = get_klines(symbol, "5m", LIMIT)
    if not k_m1 or not k_m5: return {"symbol":symbol,"status":"SKIP","reason":"No klines","confluence_score":0}
    # ... hitung score, zone, direction, SL/TP ...
    # return VALID dengan entry, sl, tp, score

if __name__=="__main__":
    results=[]; valid_new=[]
    for sym in SYMBOLS:
        r=analyze_symbol_multi(sym)
        if r['status']=="VALID": valid_new.append(r)
        results.append(r)
    out={"last_scan_utc":datetime.datetime.now(timezone.utc).isoformat(),"bot_version":"V12_NO_PAPER","valid_new_positions":valid_new,"results":results}
    with open("last_scan.json","w") as f: json.dump(out,f,indent=2)
    print(f"DONE V12 NO-PAPER: {len(valid_new)} SIGNAL for OKX")
