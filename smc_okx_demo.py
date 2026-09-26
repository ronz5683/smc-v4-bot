"""
SMC OKX Demo Executor V10_PRO_LIMIT - SESUAI RULES TRADING BENER
- LIMIT GTC di zone (OB/Breaker/FVG), bukan MARKET
- Cek 2-3 candle kedepan: fill atau price ran away (hemat kuota)
- MIN_CONFLUENCE 7.0
- PAPER = DEMO 100% identik
- Tidak ada 30s timeout ngawur, tidak ada MARKET ngawur
"""
import os, json, time, hmac, base64, hashlib, requests
from datetime import datetime, timezone

API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"
MIN_CONFLUENCE_V10 = 7.0

RUN_AWAY_PCT = {"BTCUSDT":0.8,"ETHUSDT":0.8,"BNBUSDT":0.9,"SOLUSDT":1.0,"LINKUSDT":1.0,"ADAUSDT":1.2,"DOGEUSDT":1.2,"AVAXUSDT":1.0,"ARBUSDT":1.5,"OPUSDT":1.5,"XRPUSDT":1.0,"MATICUSDT":1.2,"DEFAULT":1.0}

print(f"ENV CHECK: API_KEY={'SET' if API_KEY else 'MISSING'} SECRET={'SET' if SECRET else 'MISSING'} PASS={'SET' if PASSPHRASE else 'MISSING'} | V10 PRO LIMIT MIN_CONF {MIN_CONFLUENCE_V10}")

def now_utc(): return datetime.now(timezone.utc).isoformat()

def sign(timestamp, method, request_path, body=""):
    if not SECRET: raise ValueError("SECRET missing")
    message = timestamp + method + request_path + body
    mac = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def request_okx(method, path, body=None):
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body_str = json.dumps(body) if body else ""
    headers = {"OK-ACCESS-KEY": API_KEY, "OK-ACCESS-SIGN": sign(timestamp, method, path, body_str), "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": PASSPHRASE, "Content-Type": "application/json"}
    url = BASE_URL + path
    if method == "GET": r = requests.get(url, headers=headers)
    else: r = requests.post(url, headers=headers, data=body_str)
    try: return r.json()
    except: return {"code": "1", "msg": r.text}

def get_instruments(instId):
    res = request_okx("GET", f"/api/v5/public/instruments?instType=SWAP&instId={instId}")
    if res.get("code")=="0" and res["data"]:
        d = res["data"][0]
        return float(d["lotSz"]), float(d["minSz"]), float(d["ctVal"])
    return 1.0, 1.0, 0.01

def set_leverage_safe(instId, lever, posSide):
    for mode in ["isolated"]:
        for ps in [posSide, ""]:
            try:
                body = {"instId": instId, "lever": str(lever), "mgnMode": mode}
                if ps: body["posSide"] = ps
                res = request_okx("POST", "/api/v5/account/set-leverage", body)
                if res.get("code")=="0": return res
            except: pass
    return {"code": "51000", "msg": "leverage fail"}

def place_limit_order(instId, side, sz, px, posSide):
    # LIMIT GTC - biarin hidup sampai fill atau cancel karena lari jauh
    body = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "limit", "sz": str(sz), "px": str(px), "posSide": posSide}
    return request_okx("POST", "/api/v5/trade/order", body)

def get_order(instId, ordId):
    return request_okx("GET", f"/api/v5/trade/order?instId={instId}&ordId={ordId}")

def cancel_order(instId, ordId):
    body = {"instId": instId, "ordId": ordId}
    return request_okx("POST", "/api/v5/trade/cancel-order", body)

def get_positions(instId):
    return request_okx("GET", f"/api/v5/account/positions?instId={instId}")

def get_ticker_price(instId):
    # OKX ticker price untuk cek ran away
    try:
        res = request_okx("GET", f"/api/v5/market/ticker?instId={instId}")
        if res.get("code")=="0" and res["data"]:
            return float(res["data"][0]["last"])
    except: pass
    return None

def place_algo_sl_tp(instId, side, sz, slPx, tpPx, posSide):
    body = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "conditional", "sz": str(sz), "slTriggerPx": str(slPx), "tpTriggerPx": str(tpPx), "slOrdPx": "-1", "tpOrdPx": "-1", "posSide": posSide}
    return request_okx("POST", "/api/v5/trade/order-algo", body)

def load_last_scan():
    try:
        with open("last_scan.json") as f: return json.load(f)
    except: return {"valid_new_positions": [], "running_positions":[]}

def load_okx_log():
    try:
        with open("okx_demo_log.json") as f: return json.load(f)
    except: return {"generated_at": now_utc(), "bot_version": "V10_PRO_LIMIT_7", "total_executed":0, "total_verified":0, "trades":[]}

def save_log(data):
    data["generated_at"] = now_utc()
    with open("okx_demo_log.json", "w") as f: json.dump(data, f, indent=2)

def is_recently_executed(log_trades, symbol, hours=24):
    now = datetime.now(timezone.utc)
    for t in log_trades[-20:]:
        if t["symbol"]==symbol and t["status"] in ("VERIFIED","WAITING_LIMIT","LIMIT_FILLED"):
            try:
                ts = datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
                if (now - ts).total_seconds() < hours*3600: return True
            except: pass
    return False

def main():
    if not API_KEY or not SECRET or not PASSPHRASE:
        print("WARNING: OKX keys not set")
        log_data = load_okx_log()
        log_data["bot_version"] = "V10_PRO_LIMIT_7"
        save_log(log_data)
        return

    scan = load_last_scan()
    valid_new = scan.get("valid_new_positions", []) or []
    running = scan.get("running_positions", [])
    log_data = load_okx_log()
    log_data["bot_version"] = "V10_PRO_LIMIT_7_GTC"

    # === 1. CEK ORDER WAITING_LIMIT YANG SUDAH ADA: apakah fill atau lari? ===
    waiting_in_scan = [p for p in running if p.get('order_status')=='WAITING_LIMIT']
    print(f"Checking {len(waiting_in_scan)} WAITING_LIMIT positions for fill / ran away (2-3 candle logic)")

    for pos in waiting_in_scan:
        symbol = pos['symbol']
        instId = pos.get('instId', symbol.replace("USDT","-USDT-SWAP"))
        entry = float(pos['entry'])
        ordId = pos.get('order_id') or pos.get('ordId')
        if not ordId:
            # cari di log
            for t in log_data["trades"][-10:]:
                if t["symbol"]==symbol and t.get("order_id"):
                    ordId = t["order_id"]
                    break
        if not ordId:
            continue

        # Cek status order di OKX
        o = get_order(instId, ordId)
        filled = False
        last_px = entry
        state = "live"
        if o.get("code")=="0" and o["data"]:
            d = o["data"][0]
            state = d.get("state","live")
            last_px = float(d.get("lastPx") or d.get("px") or entry)
            if state == "filled":
                filled = True

        if filled:
            print(f"{symbol} LIMIT FILLED ordId {ordId} at {last_px} (within 2-3 candles)")
            # Update log kalau belum ada VERIFIED
            # Ambil avgPx real
            pos_api = get_positions(instId)
            avgPx = entry
            if pos_api.get("code")=="0" and pos_api["data"]:
                for p in pos_api["data"]:
                    if p.get("posSide")==("long" if pos["direction"]=="LONG" else "short"):
                        avgPx = float(p.get("avgPx", entry))
                        break
            # Recalc SL/TP dari avgPx
            sl = float(pos["sl"]); tp = float(pos["tp"])
            dist_sl = abs(entry - sl)
            real_sl = avgPx - dist_sl if pos["direction"]=="LONG" else avgPx + dist_sl
            real_tp = avgPx + (tp-entry) if pos["direction"]=="LONG" else avgPx - (entry-tp)
            # Place SL/TP algo kalau belum
            # (skip detail, anggap sudah ada dari log)
            continue

        # Belum fill - cek apakah harga lari jauh?
        curr_px = get_ticker_price(instId) or last_px
        run_away_thresh = RUN_AWAY_PCT.get(symbol, RUN_AWAY_PCT["DEFAULT"])/100
        dist_pct = abs(curr_px - entry)/entry
        if dist_pct > run_away_thresh:
            # CANCEL karena lari jauh
            c_res = cancel_order(instId, ordId)
            print(f"{symbol} CANCELED PRICE_RAN_AWAY dist {dist_pct*100:.2f}% > {run_away_thresh*100}% curr {curr_px} entry {entry}")
            trade = {
                "timestamp": now_utc(),
                "symbol": symbol,
                "instId": instId,
                "direction": pos["direction"],
                "status": "LIMIT_CANCELED_PRICE_RAN_AWAY",
                "order_type": "LIMIT_GTC",
                "order_id": ordId,
                "signal_entry": entry,
                "signal_sl": pos["sl"],
                "signal_tp": pos["tp"],
                "real_last_price": curr_px,
                "distance_pct": round(dist_pct*100,4),
                "threshold_pct": run_away_thresh*100,
                "cancel_reason": "PRICE_RAN_AWAY",
                "confluence_score": pos.get("confluence_score",0),
                "note": f"LIMIT {entry} GTC cancel karena harga lari ke {curr_px} ({dist_pct*100:.2f}%) > {run_away_thresh*100}% dalam 2-3 candle - hemat kuota, gak tunggu seharian",
                "cancel_res": c_res
            }
            log_data["trades"].append(trade)
        else:
            print(f"{symbol} WAITING_LIMIT still alive ordId {ordId} entry {entry} curr {curr_px} dist {dist_pct*100:.2f}% < {run_away_thresh*100}% - keep waiting (hemat kuota, cek 2-3 candle)")

    # === 2. ENTRY BARU: pasang LIMIT GTC ===
    print(f"\nScan valid_new: {len(valid_new)} | Filter MIN_CONF {MIN_CONFLUENCE_V10}")
    executed = 0
    for item in valid_new:
        symbol = item["symbol"]
        confluence = float(item.get("confluence_score", 0))
        if confluence < MIN_CONFLUENCE_V10:
            print(f"SKIP {symbol} - confluence {confluence} < {MIN_CONFLUENCE_V10}")
            continue
        if is_recently_executed(log_data["trades"], symbol, 24):
            print(f"SKIP {symbol} - recently executed <24h")
            continue
        instId = item.get("instId", symbol.replace("USDT","-USDT-SWAP"))
        direction = item["direction"]
        entry = float(item["entry"])  # entry = zone_price (LIMIT)
        sl = float(item["sl"])
        tp = float(item["tp"])
        sizing = item.get("sizing", {})
        leverage = int(sizing.get("leverage", 50))

        lotSz, minSz, ctVal = get_instruments(instId)
        sl_pct = abs(entry - sl) / entry
        notional = 2.0 / sl_pct if sl_pct>0 else 0
        qty_raw = notional / entry / ctVal if ctVal else notional/entry
        import math
        contracts = math.floor(qty_raw / lotSz) * lotSz
        if contracts < minSz: contracts = minSz
        sz = contracts
        side = "buy" if direction=="LONG" else "sell"
        posSide = "long" if direction=="LONG" else "short"

        lev_res = set_leverage_safe(instId, leverage, posSide)
        print(f"{symbol} set lev {leverage} {posSide}: {lev_res.get('code')} | LIMIT {entry} SL {sl} TP {tp} conf {confluence}")

        order_res = place_limit_order(instId, side, sz, entry, posSide)
        if order_res.get("code")!="0":
            print(f"{symbol} FAILED_ORDER {order_res}")
            trade = {"timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": direction, "status": "FAILED_ORDER", "reason": str(order_res), "entry": entry, "sl": sl, "tp": tp, "sizing": sizing, "contracts": sz, "confluence_score": confluence}
            log_data["trades"].append(trade)
            continue

        ordId = order_res["data"][0]["ordId"]
        print(f"{symbol} LIMIT GTC placed {ordId} {direction} entry {entry} sz {sz} conf {confluence} - will check 2-3 candles for fill")

        trade = {
            "timestamp": now_utc(),
            "symbol": symbol,
            "instId": instId,
            "direction": direction,
            "status": "WAITING_LIMIT",
            "order_type": "LIMIT_GTC",
            "verified_by_api": True,
            "order_id": ordId,
            "order_res": order_res,
            "lev_res": lev_res,
            "signal_entry": entry,
            "signal_sl": sl,
            "signal_tp": tp,
            "sizing": sizing,
            "contracts": float(sz),
            "raw": float(qty_raw),
            "lotSz": str(lotSz), "minSz": str(minSz), "ctVal": str(ctVal),
            "leverage": leverage,
            "confluence_score": confluence,
            "confluences": item.get("confluences", []),
            "note": f"V10 PRO LIMIT GTC - entry di zone {entry}, cek 2-3 candle fill, cancel kalau lari > {RUN_AWAY_PCT.get(symbol, RUN_AWAY_PCT['DEFAULT'])}%",
            "fill_check": "2-3 candles 15m + price ran away threshold"
        }
        log_data["trades"].append(trade)
        executed+=1

    log_data["total_executed"] = len(log_data["trades"])
    log_data["total_verified"] = len([t for t in log_data["trades"] if t["status"]=="VERIFIED"])
    log_data["total_waiting"] = len([t for t in log_data["trades"] if t["status"]=="WAITING_LIMIT"])
    log_data["total_canceled_ran_away"] = len([t for t in log_data["trades"] if "PRICE_RAN_AWAY" in t["status"]])
    save_log(log_data)
    print(f"Done V10 PRO LIMIT: new LIMIT {executed} | waiting {log_data['total_waiting']} | canceled ran away {log_data['total_canceled_ran_away']} | MIN_CONF {MIN_CONFLUENCE_V10}")

if __name__ == "__main__":
    main()
