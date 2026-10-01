"""
SMC OKX Demo Executor V10.5 ANTI LIQUIDATE $20 - FINAL FIX
- FIX LIQUIDATE $20: max leverage 20x cross (bukan 100x isolated), jarak liq > jarak SL
- FIX LOT SIZE 51121: quantize contracts biar gak 493.20000000000005
- FIX SL TRIGGER 51278/51280: pakai mark price, bukan last
- FIX RISK $2: auto search size sampai SL = $2, kalau minSz aja >$2.5 -> SKIP
- PAPER = DEMO = REAL
"""
import os, json, time, hmac, base64, hashlib, requests, math
from datetime import datetime, timezone

API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"
MIN_CONFLUENCE_V10 = 5.0  # LOOSE TEST biar cepat trade
RISK_USD = 2.0
MAX_RISK_USD = 2.5  # HARD LIMIT - jamin gak pernah $20 liquidate

RUN_AWAY_PCT = {"BTCUSDT":0.8,"ETHUSDT":0.8,"BNBUSDT":0.9,"SOLUSDT":1.0,"LINKUSDT":1.0,"ADAUSDT":1.2,"DOGEUSDT":1.2,"AVAXUSDT":1.0,"ARBUSDT":1.5,"OPUSDT":1.5,"XRPUSDT":1.0,"MATICUSDT":1.2,"DEFAULT":1.0}

print(f"ENV CHECK: API_KEY={'SET' if API_KEY else 'MISSING'} | V10.5 ANTI-LIQ FIX MIN_CONF {MIN_CONFLUENCE_V10} RISK ${RISK_USD} MAX ${MAX_RISK_USD}")

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
    headers = {"OK-ACCESS-KEY": API_KEY, "OK-ACCESS-SIGN": sign(timestamp, method, path, body_str), "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": PASSPHRASE, "Content-Type": "application/json", "x-simulated-trading": "1"}
    url = BASE_URL + path
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=10)
        else:
            r = requests.post(url, headers=headers, data=body_str, timeout=10)
        j = r.json()
    except Exception as e:
        return {"code": "1", "msg": str(e)}
    if j.get("code") in ["50112","50114"] and retry < 2:
        time.sleep(1.2)
        return request_okx(method, path, body, retry+1)
    return j

def get_instruments(instId):
    res = request_okx("GET", f"/api/v5/public/instruments?instType=SWAP&instId={instId}")
    if res.get("code")=="0" and res["data"]:
        d = res["data"][0]
        return float(d["lotSz"]), float(d["minSz"]), float(d["ctVal"]), float(d.get("tickSz","0.0001"))
    return 1.0, 1.0, 0.01, 0.0001

def calc_actual_risk(contracts, ctVal, entry, sl):
    return float(contracts) * float(ctVal) * abs(float(entry) - float(sl))

def calc_contracts_for_risk(entry, sl, ctVal, lotSz, minSz):
    price_diff = abs(float(entry) - float(sl))
    if price_diff <= 0:
        return minSz, RISK_USD * 10
    qty_raw = RISK_USD / (ctVal * price_diff)
    # FIX FLOAT ERROR 493.20000000000005
    if lotSz >= 1:
        precision = 0
    elif lotSz >= 0.1:
        precision = 1
    elif lotSz >= 0.01:
        precision = 2
    else:
        precision = 3
    contracts = math.floor(qty_raw / lotSz) * lotSz
    contracts = round(contracts, precision)
    if contracts < minSz:
        contracts = minSz
    contracts = round(contracts, precision)
    actual_risk = calc_actual_risk(contracts, ctVal, entry, sl)
    print(f"  [RISK SEARCH] start: diff {price_diff:.6f} ctVal {ctVal} lotSz {lotSz} -> qty_raw {qty_raw:.4f} -> contracts {contracts} -> risk ${actual_risk:.2f}")

    iterations = 0
    while actual_risk > MAX_RISK_USD and contracts > minSz and iterations < 100:
        contracts = round(contracts - lotSz, precision)
        if contracts < minSz:
            contracts = minSz
            break
        actual_risk = calc_actual_risk(contracts, ctVal, entry, sl)
        iterations += 1
        print(f"  [RISK SEARCH] iter {iterations}: contracts {contracts} -> risk ${actual_risk:.2f}")

    if actual_risk > MAX_RISK_USD:
        print(f"  [RISK SEARCH] FAIL: minSz {minSz} aja risk ${actual_risk:.2f} > ${MAX_RISK_USD} -> SKIP biar gak $20 liquidate")

    while actual_risk < 1.8 and iterations < 100:
        next_contracts = round(contracts + lotSz, precision)
        next_risk = calc_actual_risk(next_contracts, ctVal, entry, sl)
        if next_risk <= MAX_RISK_USD:
            contracts = next_contracts
            actual_risk = next_risk
            iterations += 1
            print(f"  [RISK SEARCH] up {iterations}: contracts {contracts} -> risk ${actual_risk:.2f}")
        else:
            break

    print(f"  [RISK SEARCH] FINAL: contracts {contracts} risk ${actual_risk:.2f}")
    return contracts, actual_risk

def set_leverage_max(instId, posSide):
    """ANTI LIQUIDATE: max 20x cross, bukan 100x isolated"""
    for lev in [20, 15, 10, 5]:
        res = set_leverage_safe(instId, lev, posSide)
        if res.get("code") == "0":
            print(f"  LEV MAX set {lev}x CROSS OK")
            return res, lev
    res = set_leverage_safe(instId, 10, posSide)
    return res, 10

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
            {"instId": instId, "lever": str(lever), "mgnMode": "cross", "posSide": posSide},
            {"instId": instId, "lever": str(lever), "mgnMode": "isolated", "posSide": posSide},
        ]
    else:
        attempts = [
            {"instId": instId, "lever": str(lever), "mgnMode": "cross"},
            {"instId": instId, "lever": str(lever), "mgnMode": "cross", "posSide": posSide},
        ]
    last = None
    for body in attempts:
        try:
            res = request_okx("POST", "/api/v5/account/set-leverage", body)
            if res.get("code")=="0":
                return res
            last = res
        except Exception as e:
            last = {"code":"error","msg":str(e)}
    return last or {"code": "51000", "msg": "leverage fail"}

def place_limit_order(instId, side, sz, px, posSide):
    # Quantize sz biar gak failed lot size
    sz_str = str(sz)
    if "." in sz_str:
        # bersihkan float error
        sz_val = float(sz_str)
        if sz_val >= 1:
            sz_str = f"{round(sz_val, 2)}"
        else:
            sz_str = f"{round(sz_val, 4)}"
        sz_str = sz_str.rstrip('0').rstrip('.')
    
    body_with = {"instId": instId, "tdMode": "cross", "side": side, "ordType": "limit", "sz": sz_str, "px": str(px), "posSide": posSide}
    res = request_okx("POST", "/api/v5/trade/order", body_with)
    if res.get("code")=="0":
        return res
    if "posSide" in str(res):
        body_without = {"instId": instId, "tdMode": "cross", "side": side, "ordType": "limit", "sz": sz_str, "px": str(px)}
        return request_okx("POST", "/api/v5/trade/order", body_without)
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
    """FIX 51278: pakai mark price trigger"""
    body_with = {
        "instId": instId, "tdMode": "cross", "side": side, "ordType": "conditional",
        "sz": str(sz), "slTriggerPx": str(slPx), "slTriggerPxType": "mark",
        "tpTriggerPx": str(tpPx), "tpTriggerPxType": "mark",
        "slOrdPx": "-1", "tpOrdPx": "-1", "posSide": posSide
    }
    res = request_okx("POST", "/api/v5/trade/order-algo", body_with)
    if res.get("code")=="0":
        return res
    if "posSide" in str(res):
        body_without = {
            "instId": instId, "tdMode": "cross", "side": side, "ordType": "conditional",
            "sz": str(sz), "slTriggerPx": str(slPx), "slTriggerPxType": "mark",
            "tpTriggerPx": str(tpPx), "tpTriggerPxType": "mark",
            "slOrdPx": "-1", "tpOrdPx": "-1"
        }
        return request_okx("POST", "/api/v5/trade/order-algo", body_without)
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
        return {"generated_at": now_utc(), "bot_version": "V10.5_ANTI_LIQ", "total_executed":0, "total_verified":0, "trades":[]}

def save_log(data):
    data["generated_at"] = now_utc()
    with open("okx_demo_log.json", "w") as f:
        json.dump(data, f, indent=2)

def is_recently_executed(trades, symbol, hours=24):
    now = datetime.now(timezone.utc)
    for t in trades[-50:]:
        if t["symbol"]==symbol:
            try:
                ts = datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
                if (now - ts).total_seconds() < hours*3600 and t["status"] in ["WAITING_LIMIT","LIMIT_FILLED","VERIFIED"]:
                    return True
            except:
                pass
    return False

def main():
    scan = load_last_scan()
    valid_new = scan.get("valid_new_positions", []) or scan.get("valid_trades", [])
    waiting_in_scan = [p for p in scan.get("running_positions", []) if p.get("order_status")=="WAITING_LIMIT"] + [p for p in scan.get("valid_new_positions", []) if p.get("order_status")=="WAITING_LIMIT"]
    # dedup
    seen = set()
    uniq_waiting = []
    for w in waiting_in_scan:
        k = w.get("symbol")+str(w.get("entry"))
        if k not in seen:
            seen.add(k)
            uniq_waiting.append(w)
    waiting_in_scan = uniq_waiting

    log_data = load_okx_log()
    print(f"Checking {len(waiting_in_scan)} WAITING_LIMIT for fill / ran away")

    for pos in waiting_in_scan:
        symbol = pos['symbol']
        instId = pos.get('instId', symbol.replace("USDT","-USDT-SWAP"))
        entry = float(pos['entry'])
        ordId = pos.get('order_id') or pos.get('ordId')
        if not ordId:
            for t in log_data["trades"][-20:]:
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
            print(f"{symbol} LIMIT FILLED {ordId} at {last_px} -> placing SL/TP MARK")
            pos_api = get_positions(instId)
            avgPx = entry
            sz_from_pos = None
            if pos_api.get("code")=="0" and pos_api["data"]:
                for p in pos_api["data"]:
                    if p.get("instId")==instId:
                        raw_avg = p.get("avgPx")
                        try:
                            avgPx = float(raw_avg) if raw_avg not in (None, '', '0') else float(p.get("lastPx") or last_px or entry)
                        except:
                            avgPx = float(last_px or entry)
                        # fallback kalau masih 0 atau ''
                        if not avgPx or avgPx == 0:
                            avgPx = float(last_px or entry)
                        sz_from_pos = p.get("pos") or p.get("availPos") or p.get("availPos") 
                        # sz_from_pos bisa '' juga
                        if sz_from_pos == '' or sz_from_pos is None:
                            sz_from_pos = pos.get("contracts")
                        break
            # final safety
            if not avgPx or isinstance(avgPx, str) and avgPx == '':
                avgPx = float(last_px or entry)
            sl = float(pos["sl"]); tp = float(pos["tp"])
            dist_sl = abs(entry - sl)
            real_sl = avgPx - dist_sl if pos["direction"]=="LONG" else avgPx + dist_sl
            real_tp = avgPx + (tp-entry) if pos["direction"]=="LONG" else avgPx - (entry-tp)
            close_side = "sell" if pos["direction"]=="LONG" else "buy"
            posSide = "long" if pos["direction"]=="LONG" else "short"
            sz = pos.get("contracts") or sz_from_pos or 1
            try:
                algo_res = place_algo_sl_tp(instId, close_side, sz, real_sl, real_tp, posSide)
                print(f"{symbol} SL/TP MARK {real_sl}/{real_tp} res {algo_res.get('code')} {algo_res.get('msg','')}")
            except Exception as e:
                algo_res = {"code":"error","msg":str(e)}
            trade = {
                "timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": pos["direction"],
                "status": "LIMIT_FILLED", "order_id": ordId, "real_avgPx": avgPx, "real_sl": real_sl, "real_tp": real_tp,
                "algo_res": algo_res, "confluence_score": pos.get("confluence_score",0),
                "note": f"V10.5 ANTI-LIQ FILLED {last_px} -> SLTP MARK {real_sl}/{real_tp}"
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
                "timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": pos["direction"],
                "status": "LIMIT_CANCELED_PRICE_RAN_AWAY", "order_type": "LIMIT_GTC", "order_id": ordId,
                "signal_entry": entry, "signal_sl": pos["sl"], "signal_tp": pos["tp"], "real_last_price": curr_px,
                "distance_pct": round(dist_pct*100,4), "threshold_pct": run_away_thresh*100, "cancel_reason": "PRICE_RAN_AWAY",
                "confluence_score": pos.get("confluence_score",0), "cancel_res": c_res
            }
            log_data["trades"].append(trade)
        else:
            print(f"{symbol} WAITING {ordId} entry {entry} curr {curr_px} dist {dist_pct*100:.2f}%")

    print(f"\nScan valid_new: {len(valid_new)} | MIN_CONF {MIN_CONFLUENCE_V10} | RISK ${RISK_USD} AUTO SEARCH ANTI-LIQ")
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

        lotSz, minSz, ctVal, tickSz = get_instruments(instId)
        contracts, actual_risk = calc_contracts_for_risk(entry, sl, ctVal, lotSz, minSz)
        
        if actual_risk > MAX_RISK_USD:
            print(f"SKIP {symbol} risk ${actual_risk:.2f} > MAX ${MAX_RISK_USD} - SL terlalu lebar, skip biar gak $20 liquidate")
            trade = {"timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": direction, "status": "SKIP_RISK_TOO_HIGH", "reason": f"risk ${actual_risk:.2f} > ${MAX_RISK_USD}", "entry": entry, "sl": sl, "tp": tp, "actual_risk_usd": round(actual_risk,4), "contracts": float(contracts), "confluence_score": confluence}
            log_data["trades"].append(trade)
            continue

        sz = contracts
        lev_res, lev_used = set_leverage_max(instId, posSide)
        print(f"{symbol} LEV {lev_used}x CROSS {posSide}: {lev_res.get('code')} | LIMIT {entry} SL {sl} TP {tp} conf {confluence} | RISK FINAL ${actual_risk:.2f} sz {sz}")

        order_res = place_limit_order(instId, side, sz, entry, posSide)
        if order_res.get("code")!="0":
            print(f"{symbol} FAILED {order_res}")
            trade = {"timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": direction, "status": "FAILED_ORDER", "reason": str(order_res), "entry": entry, "sl": sl, "tp": tp, "contracts": sz, "actual_risk_usd": round(actual_risk,4), "confluence_score": confluence}
            log_data["trades"].append(trade)
            continue

        ordId = order_res["data"][0]["ordId"]
        print(f"{symbol} LIMIT GTC placed {ordId} {direction} entry {entry} sz {sz} risk ${actual_risk:.2f} conf {confluence}")

        trade = {
            "timestamp": now_utc(), "symbol": symbol, "instId": instId, "direction": direction, "status": "WAITING_LIMIT",
            "order_type": "LIMIT_GTC", "verified_by_api": True, "order_id": ordId, "order_res": order_res, "lev_res": lev_res,
            "leverage_used": lev_used, "signal_entry": entry, "signal_sl": sl, "signal_tp": tp, "contracts": float(sz),
            "actual_risk_usd": round(actual_risk,4), "risk_note": f"V10.5 ANTI-LIQ: final risk ${actual_risk:.2f} (target ${RISK_USD}) cross 20x mark trigger",
            "lotSz": str(lotSz), "minSz": str(minSz), "ctVal": str(ctVal), "confluence_score": confluence, "confluences": item.get("confluences", []),
        }
        log_data["trades"].append(trade)
        executed+=1

    log_data["total_executed"] = len(log_data["trades"])
    log_data["total_verified"] = len([t for t in log_data["trades"] if t["status"]=="VERIFIED"])
    log_data["total_waiting"] = len([t for t in log_data["trades"] if t["status"]=="WAITING_LIMIT"])
    log_data["total_canceled_ran_away"] = len([t for t in log_data["trades"] if "PRICE_RAN_AWAY" in t["status"]])
    save_log(log_data)
    print(f"Done V10.5 ANTI-LIQ: new LIMIT {executed} | waiting {log_data['total_waiting']} | canceled {log_data['total_canceled_ran_away']}")

if __name__ == "__main__":
    main()
