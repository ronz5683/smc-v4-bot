"""
SMC OKX Demo Executor V10.3 LOOSE TEST - PAPER=DEMO RISK $2 FIX
- LOOSE MODE: MIN_CONF 5.0 biar cepat dapat setup, tujuan test paper=demo risk $2
- FIX: auto search size sampai SL = $2, berapapun jarak SL, kalau min size aja >$2.5 SKIP
- FIX: timestamp 3ms, posMode auto, lev max auto 100x
"""
import os, json, time, hmac, base64, hashlib, requests
from datetime import datetime, timezone
import math

API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"
MIN_CONFLUENCE_V10 = 5.0  # LOOSE TEST dari 7.0 -> 5.0 biar cepat dapat trade
RISK_USD = 2.0
MAX_RISK_USD = 2.5  # HARD LIMIT - kalau risk > ini, SKIP. Jamin gak pernah SL $5, $10

RUN_AWAY_PCT = {"BTCUSDT":0.8,"ETHUSDT":0.8,"BNBUSDT":0.9,"SOLUSDT":1.0,"LINKUSDT":1.0,"ADAUSDT":1.2,"DOGEUSDT":1.2,"AVAXUSDT":1.0,"ARBUSDT":1.5,"OPUSDT":1.5,"XRPUSDT":1.0,"MATICUSDT":1.2,"DEFAULT":1.0}

print(f"ENV CHECK: API_KEY={'SET' if API_KEY else 'MISSING'} SECRET={'SET' if SECRET else 'MISSING'} PASS={'SET' if PASSPHRASE else 'MISSING'} | V10.2 FIX MIN_CONF {MIN_CONFLUENCE_V10}")

def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace("+00:00","Z")

def get_okx_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace("+00:00","Z")

def sign(timestamp, method, request_path, body=""):
    if not SECRET:
        raise ValueError("SECRET missing")
    message = timestamp + method + request_path + body
    mac = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def request_okx(method, path, body=None, retry=0):
    timestamp = get_okx_timestamp()
    body_str = json.dumps(body) if body else ""
    headers = {"OK-ACCESS-KEY": API_KEY, "OK-ACCESS-SIGN": sign(timestamp, method, path, body_str), "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": PASSPHRASE, "Content-Type": "application/json"}
    url = BASE_URL + path
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=10)
        else:
            r = requests.post(url, headers=headers, data=body_str, timeout=10)
        j = r.json()
    except Exception as e:
        return {"code": "1", "msg": str(e)}
    # Auto retry kalau timestamp error 50112 / 50114
    if j.get("code") in ["50112","50114"] and retry < 2:
        time.sleep(1.2)
        return request_okx(method, path, body, retry+1)
    return j

def get_instruments(instId):
    res = request_okx("GET", f"/api/v5/public/instruments?instType=SWAP&instId={instId}")
    if res.get("code")=="0" and res["data"]:
        d = res["data"][0]
        return float(d["lotSz"]), float(d["minSz"]), float(d["ctVal"])
    return 1.0, 1.0, 0.01

def calc_actual_risk(contracts, ctVal, entry, sl):
    return float(contracts) * float(ctVal) * abs(float(entry) - float(sl))

def calc_contracts_for_risk(entry, sl, ctVal, lotSz, minSz):
    """
    INTI YANG KAMU MAU:
    - Bot cari sendiri size tepat sebelum pasang order
    - Misal size $50 ke SL kena $3, turunkan ke 40 sampai kena SL cuma $2
    - Bukan jarak SL $2, tapi kerugian $2
    """
    price_diff = abs(float(entry) - float(sl))
    if price_diff <= 0:
        return minSz, RISK_USD * 10

    # 1. Hitung contracts mentah untuk risk $2
    qty_raw = RISK_USD / (ctVal * price_diff)
    contracts = math.floor(qty_raw / lotSz) * lotSz
    if contracts < minSz:
        contracts = minSz

    actual_risk = calc_actual_risk(contracts, ctVal, entry, sl)
    print(f"  [RISK SEARCH] start: price_diff {price_diff:.6f} ctVal {ctVal} -> qty_raw {qty_raw:.4f} -> contracts {contracts} -> risk ${actual_risk:.2f}")

    # 2. Iterative: kalau risk masih $3, turunin terus sampai $2
    # Contoh: $50 -> $3, turun $40 -> $2.1, turun $38 -> $2.0
    iterations = 0
    while actual_risk > MAX_RISK_USD and contracts > minSz and iterations < 100:
        contracts -= lotSz
        if contracts < minSz:
            contracts = minSz
            break
        actual_risk = calc_actual_risk(contracts, ctVal, entry, sl)
        iterations += 1
        print(f"  [RISK SEARCH] iter {iterations}: contracts {contracts} -> risk ${actual_risk:.2f} (target ${RISK_USD})")

    # 3. Kalau masih kegedean tapi udah minSz, berarti SL terlalu lebar untuk $2
    if actual_risk > MAX_RISK_USD:
        print(f"  [RISK SEARCH] FAIL: minSz {minSz} aja risk ${actual_risk:.2f} > ${MAX_RISK_USD} -> SL terlalu lebar")

    # 4. Kalau risk kekecilan (< $1.8), coba naikin 1 lot biar mendekati $2
    while actual_risk < 1.8 and iterations < 100:
        next_contracts = contracts + lotSz
        next_risk = calc_actual_risk(next_contracts, ctVal, entry, sl)
        if next_risk <= MAX_RISK_USD:
            contracts = next_contracts
            actual_risk = next_risk
            iterations += 1
            print(f"  [RISK SEARCH] up {iterations}: contracts {contracts} -> risk ${actual_risk:.2f}")
        else:
            break

    print(f"  [RISK SEARCH] FINAL: contracts {contracts} risk ${actual_risk:.2f} untuk entry {entry} SL {sl}")
    return contracts, actual_risk

def set_leverage_max(instId, posSide):
    """
    AUTO SET LEV MAX: coba 100x dulu, kalau gagal turun ke 50,20
    """
    for lev in [100, 75, 50, 30, 20, 10]:
        res = set_leverage_safe(instId, lev, posSide)
        if res.get("code") == "0":
            print(f"  LEV MAX set {lev}x OK")
            return res, lev
    # fallback
    res = set_leverage_safe(instId, 20, posSide)
    return res, 20

def get_account_mode():
    try:
        res = request_okx("GET", "/api/v5/account/config")
        if res.get("code")=="0" and res["data"]:
            return res["data"][0].get("posMode", "net_mode")
    except:
        pass
    return "net_mode"

def set_leverage_safe(instId, lever, posSide):
    pos_mode = get_account_mode()
    print(f"Account posMode: {pos_mode}")
    attempts = []
    if pos_mode == "long_short_mode":
        attempts = [
            {"instId": instId, "lever": str(lever), "mgnMode": "isolated", "posSide": posSide},
            {"instId": instId, "lever": str(lever), "mgnMode": "cross", "posSide": posSide},
            {"instId": instId, "lever": str(lever), "mgnMode": "isolated"},
        ]
    else:
        attempts = [
            {"instId": instId, "lever": str(lever), "mgnMode": "isolated"},
            {"instId": instId, "lever": str(lever), "mgnMode": "cross"},
            {"instId": instId, "lever": str(lever), "mgnMode": "isolated", "posSide": posSide},
        ]
    last = None
    for body in attempts:
        try:
            res = request_okx("POST", "/api/v5/account/set-leverage", body)
            if res.get("code")=="0":
                return res
            last = res
            if "posSide" in str(res):
                continue
        except Exception as e:
            last = {"code":"error","msg":str(e)}
    return last or {"code": "51000", "msg": "leverage fail"}

def place_limit_order(instId, side, sz, px, posSide):
    # Coba dengan posSide dulu, kalau net_mode error -> retry tanpa posSide
    body_with = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "limit", "sz": str(sz), "px": str(px), "posSide": posSide}
    res = request_okx("POST", "/api/v5/trade/order", body_with)
    if res.get("code")=="0":
        return res
    msg = str(res)
    if "posSide" in msg or "51000" in msg:
        print(f"Retry without posSide for {instId} because {msg}")
        body_without = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "limit", "sz": str(sz), "px": str(px)}
        res2 = request_okx("POST", "/api/v5/trade/order", body_without)
        return res2
    return res

def get_order(instId, ordId):
    return request_okx("GET", f"/api/v5/trade/order?instId={instId}&ordId={ordId}")

def cancel_order(instId, ordId):
    body = {"instId": instId, "ordId": ordId}
    return request_okx("POST", "/api/v5/trade/cancel-order", body)

def get_positions(instId):
    return request_okx("GET", f"/api/v5/account/positions?instId={instId}")

def get_ticker_price(instId):
    try:
        res = request_okx("GET", f"/api/v5/market/ticker?instId={instId}")
        if res.get("code")=="0" and res["data"]:
            return float(res["data"][0]["last"])
    except:
        pass
    return None

def place_algo_sl_tp(instId, side, sz, slPx, tpPx, posSide):
    # Coba dengan posSide dulu
    body_with = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "conditional", "sz": str(sz), "slTriggerPx": str(slPx), "tpTriggerPx": str(tpPx), "slOrdPx": "-1", "tpOrdPx": "-1", "posSide": posSide}
    res = request_okx("POST", "/api/v5/trade/order-algo", body_with)
    if res.get("code")=="0":
        return res
    if "posSide" in str(res):
        body_without = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "conditional", "sz": str(sz), "slTriggerPx": str(slPx), "tpTriggerPx": str(tpPx), "slOrdPx": "-1", "tpOrdPx": "-1"}
        res2 = request_okx("POST", "/api/v5/trade/order-algo", body_without)
        return res2
    return res

def load_last_scan():
    try:
        with open("last_scan.json") as f:
            return json.load(f)
    except:
        return {"valid_new_positions": [], "running_positions":[]}

def load_okx_log():
    try:
        with open("okx_demo_log.json") as f:
            return json.load(f)
    except:
        return {"generated_at": now_utc(), "bot_version": "V10.2_FIXED", "total_executed":0, "total_verified":0, "trades":[]}

def save_log(data):
    data["generated_at"] = now_utc()
    with open("okx_demo_log.json", "w") as f:
        json.dump(data, f, indent=2)

def is_recently_executed(log_trades, symbol, hours=24):
    now = datetime.now(timezone.utc)
    for t in log_trades[-20:]:
        if t["symbol"]==symbol and t["status"] in ("VERIFIED","WAITING_LIMIT","LIMIT_FILLED","LIMIT_CANCELED_PRICE_RAN_AWAY"):
            try:
                ts = datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
                if (now - ts).total_seconds() < hours*3600:
                    return True
            except:
                pass
    return False

def main():
    if not API_KEY or not SECRET or not PASSPHRASE:
        print("WARNING: OKX keys not set")
        log_data = load_okx_log()
        log_data["bot_version"] = "V10.2_FIXED_NO_KEYS"
        save_log(log_data)
        return

    scan = load_last_scan()
    valid_new = scan.get("valid_new_positions", []) or []
    running = scan.get("running_positions", [])
    log_data = load_okx_log()
    log_data["bot_version"] = "V10.2_PAPER=DEMO=REAL_FIXED"
    print(f"BOT V10.2 FIX | MIN_CONF {MIN_CONFLUENCE_V10} | posMode auto | timestamp ms fix")

    # === 1. CEK WAITING_LIMIT ===
    waiting_in_scan = [p for p in running if p.get('order_status')=='WAITING_LIMIT']
    print(f"Checking {len(waiting_in_scan)} WAITING_LIMIT for fill / ran away")

    for pos in waiting_in_scan:
        symbol = pos['symbol']
        instId = pos.get('instId', symbol.replace("USDT","-USDT-SWAP"))
        entry = float(pos['entry'])
        ordId = pos.get('order_id') or pos.get('ordId')
        if not ordId:
            for t in log_data["trades"][-10:]:
                if t["symbol"]==symbol and t.get("order_id"):
                    ordId = t["order_id"]
                    break
        if not ordId:
            continue

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
            print(f"{symbol} LIMIT FILLED {ordId} at {last_px} -> placing SL/TP")
            pos_api = get_positions(instId)
            avgPx = entry
            sz_from_pos = None
            if pos_api.get("code")=="0" and pos_api["data"]:
                for p in pos_api["data"]:
                    # support both net and hedge
                    if p.get("instId")==instId:
                        avgPx = float(p.get("avgPx", entry))
                        sz_from_pos = p.get("pos") or p.get("availPos")
                        break
            sl = float(pos["sl"]); tp = float(pos["tp"])
            dist_sl = abs(entry - sl)
            real_sl = avgPx - dist_sl if pos["direction"]=="LONG" else avgPx + dist_sl
            real_tp = avgPx + (tp-entry) if pos["direction"]=="LONG" else avgPx - (entry-tp)
            close_side = "sell" if pos["direction"]=="LONG" else "buy"
            posSide = "long" if pos["direction"]=="LONG" else "short"
            sz = pos.get("contracts") or sz_from_pos or 1
            try:
                algo_res = place_algo_sl_tp(instId, close_side, sz, real_sl, real_tp, posSide)
                print(f"{symbol} SL/TP {real_sl}/{real_tp} res {algo_res.get('code')}")
            except Exception as e:
                algo_res = {"code":"error","msg":str(e)}
            trade = {
                "timestamp": now_utc(),
                "symbol": symbol,
                "instId": instId,
                "direction": pos["direction"],
                "status": "LIMIT_FILLED",
                "order_id": ordId,
                "real_avgPx": avgPx,
                "real_sl": real_sl,
                "real_tp": real_tp,
                "algo_res": algo_res,
                "confluence_score": pos.get("confluence_score",0),
                "note": f"V10.2 FIX FILLED {last_px} -> SLTP {real_sl}/{real_tp}"
            }
            log_data["trades"].append(trade)
            continue

        curr_px = get_ticker_price(instId) or last_px
        run_away_thresh = RUN_AWAY_PCT.get(symbol, RUN_AWAY_PCT["DEFAULT"])/100
        dist_pct = abs(curr_px - entry)/entry if entry else 0
        if dist_pct > run_away_thresh:
            c_res = cancel_order(instId, ordId)
            print(f"{symbol} CANCELED RAN_AWAY {dist_pct*100:.2f}% > {run_away_thresh*100}%")
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
                "cancel_res": c_res
            }
            log_data["trades"].append(trade)
        else:
            print(f"{symbol} WAITING {ordId} entry {entry} curr {curr_px} dist {dist_pct*100:.2f}%")

    # === 2. ENTRY BARU ===
    print(f"\nScan valid_new: {len(valid_new)} | MIN_CONF {MIN_CONFLUENCE_V10} | RISK ${RISK_USD} AUTO SEARCH")
    executed = 0
    for item in valid_new:
        symbol = item["symbol"]
        confluence = float(item.get("confluence_score", 0))
        if confluence < MIN_CONFLUENCE_V10:
            print(f"SKIP {symbol} score {confluence} < {MIN_CONFLUENCE_V10}")
            continue
        if is_recently_executed(log_data["trades"], symbol, 24):
            print(f"SKIP {symbol} recently executed <24h")
            continue
        instId = item.get("instId", symbol.replace("USDT","-USDT-SWAP"))
        direction = item["direction"]
        entry = float(item["entry"])
        sl = float(item["sl"])
        tp = float(item["tp"])
        posSide = "long" if direction=="LONG" else "short"
        side = "buy" if direction=="LONG" else "sell"

        lotSz, minSz, ctVal = get_instruments(instId)
        # === BOT CARI SENDIRI SIZE TEPAT BIAR SL = $2 ===
        contracts, actual_risk = calc_contracts_for_risk(entry, sl, ctVal, lotSz, minSz)
        
        if actual_risk > MAX_RISK_USD:
            print(f"SKIP {symbol} risk ${actual_risk:.2f} > MAX ${MAX_RISK_USD} - SL terlalu lebar, skip biar gak $10-30")
            trade = {"timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": direction, "status": "SKIP_RISK_TOO_HIGH", "reason": f"risk ${actual_risk:.2f} > ${MAX_RISK_USD}", "entry": entry, "sl": sl, "tp": tp, "actual_risk_usd": round(actual_risk,4), "contracts": float(contracts), "confluence_score": confluence}
            log_data["trades"].append(trade)
            continue

        sz = contracts

        # AUTO SET LEV MAX
        lev_res, lev_used = set_leverage_max(instId, posSide)
        print(f"{symbol} LEV MAX {lev_used}x {posSide}: {lev_res.get('code')} | LIMIT {entry} SL {sl} TP {tp} conf {confluence} | RISK FINAL ${actual_risk:.2f} sz {sz}")

        order_res = place_limit_order(instId, side, sz, entry, posSide)
        if order_res.get("code")!="0":
            print(f"{symbol} FAILED {order_res}")
            trade = {"timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": direction, "status": "FAILED_ORDER", "reason": str(order_res), "entry": entry, "sl": sl, "tp": tp, "contracts": sz, "actual_risk_usd": round(actual_risk,4), "confluence_score": confluence}
            log_data["trades"].append(trade)
            continue

        ordId = order_res["data"][0]["ordId"]
        print(f"{symbol} LIMIT GTC placed {ordId} {direction} entry {entry} sz {sz} risk ${actual_risk:.2f} conf {confluence}")

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
            "leverage_used": lev_used,
            "signal_entry": entry,
            "signal_sl": sl,
            "signal_tp": tp,
            "contracts": float(sz),
            "actual_risk_usd": round(actual_risk,4),
            "risk_note": f"AUTO SIZE SEARCH: final risk ${actual_risk:.2f} (target ${RISK_USD}) - bot cari size sampai pas $2, bukan jarak $2",
            "lotSz": str(lotSz), "minSz": str(minSz), "ctVal": str(ctVal),
            "confluence_score": confluence,
            "confluences": item.get("confluences", []),
        }
        log_data["trades"].append(trade)
        executed+=1

    log_data["total_executed"] = len(log_data["trades"])
    log_data["total_verified"] = len([t for t in log_data["trades"] if t["status"]=="VERIFIED"])
    log_data["total_waiting"] = len([t for t in log_data["trades"] if t["status"]=="WAITING_LIMIT"])
    log_data["total_canceled_ran_away"] = len([t for t in log_data["trades"] if "PRICE_RAN_AWAY" in t["status"]])
    save_log(log_data)
    print(f"Done V10.2 FIX: new LIMIT {executed} | waiting {log_data['total_waiting']} | canceled {log_data['total_canceled_ran_away']}")

if __name__ == "__main__":
    main()
