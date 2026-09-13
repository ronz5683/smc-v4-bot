import requests, json, time, datetime
from datetime import timezone

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","OPUSDT","ARBUSDT","MATICUSDT"]
MAX_DISTANCE_PCT = 0.3
MIN_CONFIDENCE = 8

def get_price(symbol):
    try:
        r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=5)
        return float(r.json()['price'])
    except: return None

def analyze_symbol(symbol):
    price = get_price(symbol)
    if not price: return {"symbol":symbol,"status":"ERROR","reason":"Price fetch fail"}

    # === INI OTAK DUMMY V4 ===
    # Simulasi deteksi SMC (nanti kamu ganti dengan logic sweep+choch real kamu)
    # Untuk sekarang kita buat logic dummy yang sama seperti di dashboard chat
    import random
    random.seed(hash(symbol+str(int(time.time()/900)))) # ganti tiap 15 menit

    distance = round(random.uniform(0.05, 1.5), 2)
    has_sweep = random.choice([True, False])
    has_choch = random.choice([True, False])
    zone_type = random.choice(["Breaker","OB+FVG","OB+FVG"]) # OB+FVG yang dulu WR 0% sudah di-ban di filter bawah
    confidence = random.randint(5,10)
    hour_wib = (datetime.datetime.now(timezone.utc).hour + 7) % 24

    # === FILTER TUNING HASIL CSV KAMU ===
    if zone_type == "OB+FVG":
        return {"symbol":symbol,"status":"SKIP","reason":"Setup banned - OB+FVG WR 0% di data 25 trades","price":price,"distance":f"{distance}%","zone":zone_type,"confidence":confidence}
    if 7 <= hour_wib <= 9:
        return {"symbol":symbol,"status":"SKIP","reason":f"Blacklist jam {hour_wib}:00 WIB - likuiditas tipis","price":price,"distance":f"{distance}%","zone":zone_type,"confidence":confidence}
    if confidence < MIN_CONFIDENCE:
        return {"symbol":symbol,"status":"SKIP","reason":f"Confidence {confidence} < {MIN_CONFIDENCE} - WR rendah","price":price,"distance":f"{distance}%","zone":zone_type,"confidence":confidence}
    if distance > MAX_DISTANCE_PCT:
        return {"symbol":symbol,"status":"SKIP","reason":f"Distance {distance}% > {MAX_DISTANCE_PCT}% - terlalu jauh dari zona","price":price,"distance":f"{distance}%","zone":zone_type,"confidence":confidence}
    if not (has_sweep and has_choch):
        return {"symbol":symbol,"status":"SKIP","reason":f"No Sweep/ChoCh - sweep:{has_sweep} choch:{has_choch}","price":price,"distance":f"{distance}%","zone":zone_type,"confidence":confidence}

    # VALID - hitung Entry SL TP seperti dummy
    entry = round(price * (0.998 if random.choice([True]) else 1.002), 2)
    sl = round(entry * 0.992, 2)
    tp1 = round(entry * 1.015, 2)
    tp2 = round(entry * 1.03, 2)

    return {
        "symbol":symbol,
        "status":"VALID",
        "reason":"Sweep+ChoCh+Breaker - VALID",
        "price":price,
        "entry":entry,
        "sl":sl,
        "tp1":tp1,
        "tp2":tp2,
        "distance":f"{distance}%",
        "zone":zone_type,
        "confidence":confidence,
        "order_type":"LIMIT",
        "order_status":"WAITING LIMIT"
    }

results = [analyze_symbol(s) for s in SYMBOLS]
valid = [r for r in results if r['status']=="VALID"]
skip = [r for r in results if r['status']=="SKIP"]

output = {
    "last_scan_utc": datetime.datetime.now(timezone.utc).isoformat(),
    "summary": {"total": len(results), "valid": len(valid), "skip": len(skip), "watchlist": len(results)},
    "results": results,
    "valid_trades": valid,
    "watchlist": skip,
    "running_positions": [], # nanti keisi kalau ada yang fill
    "report_note": "Otak sama dengan dummy V4 Reset 0 - lengkap dengan alasan skip untuk tuning"
}

with open("last_scan.json","w") as f:
    json.dump(output, f, indent=2)

print(f"Scan done: {len(valid)} VALID, {len(skip)} SKIP")
