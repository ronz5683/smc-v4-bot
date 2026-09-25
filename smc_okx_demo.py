"""
SMC OKX Demo Executor V7.7 - 25 Sep 2026 08:23 WIB FIX posSide
FIX dari log 24-25 Sep:
- 18x FAILED 51000 posSide error -> FIX: place_limit_order + algo WAJIB kirim posSide long/short kalau akun hedge mode
- Sebelumnya hanya set_leverage yang pakai posSide, ordernya tidak -> OKX reject

Support env OKX_DEMO_* + x-simulated-trading header
"""
import os, json, time, hmac, base64, hashlib, requests, math
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone

API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"
IS_DEMO = bool(os.getenv("OKX_DEMO_API_KEY"))

print(f"V7.7 ENV CHECK: API_KEY={'SET' if API_KEY else 'MISSING'} IS_DEMO={IS_DEMO}")

DISTANCE_THRESHOLD_PCT = {
    "BTCUSDT": 0.15, "ETHUSDT": 0.18, "BNBUSDT": 0.20,
    "SOLUSDT": 0.25, "LINKUSDT": 0.30, "ADAUSDT": 0.35,
    "DOGEUSDT": 0.40, "AVAXUSDT": 0.30, "ARBUSDT": 0.50,
    "DEFAULT": 0.30
}

def now_utc():
    return datetime.now(timezone.utc).isoformat()

def get_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace("+00:00", "Z")

def sign(timestamp, method, request_path, body=""):
    if not SECRET:
        raise ValueError("SECRET missing")
    msg = timestamp + method + request_path + body
    mac = hmac.new(SECRET.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode('utf-8')

def request_okx(method, path, body=None):
    timestamp = get_timestamp()
    body_str = json.dumps(body) if body else ""
    headers = {
        "OK-ACCESS-KEY": API_KEY or "",
        "OK-ACCESS-SIGN": sign(timestamp, method, path, body_str),
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE or "",
        "Content-Type": "application/json"
    }
    if IS_DEMO:
        headers["x-simulated-trading"] = "1"
    url = BASE_URL + path
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=15)
        else:
            r = requests.post(url, headers=headers, data=body_str, timeout=15)
        return r.json()
    except Exception as e:
        return {"code": "1", "msg": str(e)}

def get_instruments(instId):
    res = request_okx("GET", f"/api/v5/public/instruments?instType=SWAP&instId={instId}")
    if res.get("code")=="0" and res.get("data"):
        d = res["data"][0]
        return float(d["lotSz"]), float(d["minSz"]), float(d["ctVal"]), d
    return 0.01, 0.01, 1.0, {}

def set_leverage_safe(instId, lever):
    # hedge mode: set long & short
    results = []
    for extra in [
        {"mgnMode": "isolated", "posSide": "long", "lever": str(lever)},
        {"mgnMode": "isolated", "posSide": "short", "lever": str(lever)},
        {"mgnMode": "isolated", "lever": str(lever)},
    ]:
        body = {"instId": instId, **extra}
        res = request_okx("POST", "/api/v5/account/set-leverage", body)
        results.append(res)
        if res.get("code")=="0" and "posSide" not in extra:
            return res
    for r in results:
        if r.get("code")=="0":
            return r
    return results[-1] if results else {"code":"1"}

def format_sz(contracts, lotSz_str):
    try:
        lot = Decimal(str(lotSz_str))
        d = Decimal(str(contracts)).quantize(lot, rounding=ROUND_DOWN)
        if d < lot:
            d = lot
        return format(d, 'f')
    except:
        return str(contracts)

def place_limit_order(instId, side, sz_str, px, posSide):
    # FIX V7.7: WAJIB posSide kalau hedge mode
    body = {
        "instId": instId,
        "tdMode": "isolated",
        "side": side,
        "posSide": posSide,
        "ordType": "limit",
        "sz": sz_str,
        "px": str(px)
    }
    return request_okx("POST", "/api/v5/trade/order", body)

def get_order(instId, ordId):
    return request_okx("GET", f"/api/v5/trade/order?instId={instId}&ordId={ordId}")

def cancel_order(instId, ordId):
    return request_okx("POST", "/api/v5/trade/cancel-order", {"instId": instId, "ordId": ordId})

def get_positions(instId):
    return request_okx("GET", f"/api/v5/account/positions?instId={instId}")

def place_algo_sl_tp(instId, side, sz_str, slPx, tpPx, posSide):
    body = {
        "instId": instId,
        "tdMode": "isolated",
        "side": side,
        "posSide": posSide,
        "ordType": "conditional",
        "sz": sz_str,
        "slTriggerPx": str(slPx),
        "tpTriggerPx": str(tpPx),
        "slOrdPx": "-1",
        "tpOrdPx": "-1"
    }
    return request_okx("POST", "/api/v5/trade/order-algo", body)

def load_last_scan():
    try:
        with open("last_scan.json") as f:
            return json.load(f)
    except:
        return {"valid_new_positions": []}

def load_okx_log():
    try:
        with open("okx_demo_log.json") as f:
            return json.load(f)
    except:
        return {"generated_at": now_utc(), "bot_version": "V7.7", "total_executed":0, "trades":[]}

def save_log(data):
    data["generated_at"] = now_utc()
    data["bot_version"] = "V7.7_POS_SIDE_FIX"
    with open("okx_demo_log.json", "w") as f:
        json.dump(data, f, indent=2)

def is_recently_executed(log_trades, symbol, hours=24):
    now = datetime.now(timezone.utc)
    for t in log_trades[-30:]:
        if t["symbol"]==symbol and t["status"] in ("VERIFIED",):
            try:
                ts = datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
                if (now - ts).total_seconds() < hours*3600:
                    return True
            except:
                pass
    return False


def is_duplicate_entry(log_trades, symbol, entry, hours=2):
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    for t in log_trades[-20:]:
        if t["symbol"]==symbol:
            try:
                ts = __import__('datetime').datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
                same_price = abs(float(t.get("entry",0) or t.get("signal_entry",0)) - entry) < entry*0.001
                if same_price and (now - ts).total_seconds() < hours*3600:
                    return True
            except:
                pass
    return False

def count_failed_today(log_trades):
    today = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).date()
    cnt=0
    for t in log_trades[-30:]:
        try:
            ts = __import__('datetime').datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
            if ts.date()==today and t["status"] in ("FAILED_ORDER","LIMIT_NOT_FILLED_CANCELED"):
                cnt+=1
        except:
            pass
    return cnt


def main():
    if not API_KEY or not SECRET or not PASSPHRASE:
        print("WARNING: keys missing")
        log_data = load_okx_log()
        save_log(log_data)
        return

    scan = load_last_scan()
    valid_new = scan.get("valid_new_positions", []) or []
    log_data = load_okx_log()
    failed_today = count_failed_today(log_data.get("trades",[]))
    if failed_today >= 3:
        print(f"!!! DAILY STOP: {failed_today} FAILED/CANCELED today, skip all")
        save_log(log_data)
        return
    print(f"Scan valid_new: {len(valid_new)} | existing: {len(log_data.get('trades',[]))} | failed_today: {failed_today}")

    for item in valid_new:
        symbol = item["symbol"]
        instId = item.get("instId", symbol.replace("USDT","-USDT-SWAP"))
        direction = item["direction"]
        entry = float(item["entry"])
        sl = float(item["sl"])
        tp = float(item["tp"])
        sizing = item.get("sizing", {})
        leverage = int(sizing.get("leverage", 50))

        if is_recently_executed(log_data["trades"], symbol, 24):
            print(f"SKIP {symbol} <24h")
            continue
        if is_duplicate_entry(log_data["trades"], symbol, entry, 2):
            print(f"SKIP {symbol} duplicate entry {entry} <2h anti OP 3x")
            continue
        sl_pct_check = abs(entry - sl) / entry if entry else 0
        if sl_pct_check < 0.005:
            print(f"SKIP {symbol} SL too tight {sl_pct_check*100:.2f}% <0.5%")
            continue

        lotSz, minSz, ctVal, inst_data = get_instruments(instId)
        lotSz_str = inst_data.get("lotSz", str(lotSz))
        print(f"{symbol} lotSz={lotSz_str} minSz={minSz} ctVal={ctVal}")

        sl_pct = abs(entry - sl) / entry if entry else 0
        notional = 2.0 / sl_pct if sl_pct>0 else 0
        qty_raw = notional / entry / ctVal if ctVal else notional/entry
        contracts_float = math.floor(qty_raw / lotSz) * lotSz if lotSz else qty_raw
        if contracts_float < minSz:
            contracts_float = minSz
        sz_str = format_sz(contracts_float, lotSz_str)

        side = "buy" if direction=="LONG" else "sell"
        posSide = "long" if direction=="LONG" else "short"
        close_side = "sell" if direction=="LONG" else "buy"

        lev_res = set_leverage_safe(instId, leverage)
        print(f"{symbol} lev {leverage}: {lev_res.get('code')}")

        order_res = place_limit_order(instId, side, sz_str, entry, posSide)
        if order_res.get("code")!="0":
            print(f"{symbol} FAILED {order_res}")
            trade = {
                "timestamp": now_utc(), "symbol": symbol, "instId": instId,
                "direction": direction, "status": "FAILED_ORDER",
                "reason": str(order_res), "entry": entry, "sl": sl, "tp": tp,
                "sizing": sizing, "contracts": sz_str, "posSide": posSide,
                "lotSz": lotSz_str
            }
            log_data["trades"].append(trade)
            save_log(log_data)
            continue

        ordId = order_res["data"][0]["ordId"]
        print(f"{symbol} LIMIT {ordId} px {entry} sz {sz_str} posSide {posSide}")

        filled = False
        last_px = entry
        last_state = "live"
        for i in range(6):
            time.sleep(5)
            o = get_order(instId, ordId)
            if o.get("code")=="0" and o.get("data"):
                d = o["data"][0]
                last_state = d.get("state","live")
                try:
                    last_px = float(d.get("lastPx") or d.get("px") or entry)
                except:
                    pass
                if last_state == "filled":
                    filled = True
                    break
                if last_state in ("canceled","mmp_canceled"):
                    break
            print(f"  wait {i+1}/6 {last_state} {last_px}")

        if not filled:
            c_res = cancel_order(instId, ordId)
            threshold = DISTANCE_THRESHOLD_PCT.get(symbol, DISTANCE_THRESHOLD_PCT["DEFAULT"])
            distance_pct = abs(last_px - entry) / entry * 100 if entry else 0
            cancel_reason = "PRICE_RAN_AWAY" if distance_pct >= threshold else "TIMEOUT_30S"
            trade = {
                "timestamp": now_utc(), "symbol": symbol, "instId": instId,
                "direction": direction, "status": "LIMIT_NOT_FILLED_CANCELED",
                "order_id": ordId, "order_res": order_res, "cancel_res": c_res,
                "signal_entry": entry, "signal_sl": sl, "signal_tp": tp,
                "real_last_price": last_px, "real_state": last_state,
                "distance_pct": round(distance_pct,4), "cancel_reason": cancel_reason,
                "sizing": sizing, "contracts": sz_str, "posSide": posSide
            }
            log_data["trades"].append(trade)
            print(f"{symbol} CANCELED {cancel_reason}")
            save_log(log_data)
            continue

        pos = get_positions(instId)
        avgPx = entry
        margin = None
        if pos.get("code")=="0" and pos.get("data"):
            try:
                avgPx = float(pos["data"][0].get("avgPx", entry))
                margin = pos["data"][0].get("margin")
            except:
                pass

        dist_sl = abs(entry - sl)
        dist_tp = abs(tp - entry)
        real_sl = avgPx - dist_sl if direction=="LONG" else avgPx + dist_sl
        real_tp = avgPx + dist_tp if direction=="LONG" else avgPx - dist_tp

        algo_res = place_algo_sl_tp(instId, close_side, sz_str, real_sl, real_tp, posSide)

        trade = {
            "timestamp": now_utc(), "symbol": symbol, "instId": instId,
            "direction": direction, "status": "VERIFIED", "order_type": "LIMIT",
            "verified_by_api": True, "order_id": ordId,
            "order_res": order_res, "lev_res": lev_res, "algo_res": algo_res,
            "pos_snapshot": pos,
            "signal_entry": entry, "signal_sl": sl, "signal_tp": tp,
            "real_avgPx": avgPx, "real_sl": real_sl, "real_tp": real_tp,
            "sizing": sizing, "contracts": sz_str, "raw": float(qty_raw),
            "lotSz": lotSz_str, "leverage": leverage, "posSide": posSide,
            "margin_needed": float(margin) if margin else None
        }
        log_data["trades"].append(trade)
        print(f"{symbol} VERIFIED avgPx {avgPx} posSide {posSide}")
        save_log(log_data)

    log_data["total_executed"] = len(log_data["trades"])
    log_data["total_verified"] = len([t for t in log_data["trades"] if t["status"]=="VERIFIED"])
    log_data["total_canceled"] = len([t for t in log_data["trades"] if t["status"]=="LIMIT_NOT_FILLED_CANCELED"])
    log_data["total_failed"] = len([t for t in log_data["trades"] if t["status"]=="FAILED_ORDER"])
    save_log(log_data)
    print(f"Done V7.7 verified {log_data['total_verified']} canceled {log_data['total_canceled']} failed {log_data['total_failed']}")

if __name__ == "__main__":
    main()
