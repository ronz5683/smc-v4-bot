"""
SMC OKX Demo Executor V7.5 - 24 Sep 2026
FIX dari log 23 Sep:
- 50101 APIKey does not match current environment -> FIX: tambah header x-simulated-trading: 1 otomatis
- 50112 Invalid TIMESTAMP -> FIX: get_timestamp() milliseconds fresh tiap request
- 51121 lot size -> FIX: Decimal quantize + format_sz
- lever 3x -> FIX: set_leverage hedge long+short isolated

Support env OKX_DEMO_* (workflow kamu) dan OKX_* (standard)
"""
import os, json, time, hmac, base64, hashlib, requests, math
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone

# CONFIG - support both naming
API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"

# Deteksi apakah ini akun demo (kalau env DEMO yang kepakai, otomatis pakai header demo)
IS_DEMO = bool(os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_DEMO_API_SECRET"))

print(f"V7.5 ENV CHECK: API_KEY={'SET' if API_KEY else 'MISSING'} SECRET={'SET' if SECRET else 'MISSING'} PASS={'SET' if PASSPHRASE else 'MISSING'} IS_DEMO={IS_DEMO}")

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
    message = timestamp + method + request_path + body
    mac = hmac.new(SECRET.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
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
    # WAJIB untuk akun demo OKX - tanpa ini akan 50101
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
    results = []
    attempts = [
        {"mgnMode": "isolated", "posSide": "long", "lever": str(lever)},
        {"mgnMode": "isolated", "posSide": "short", "lever": str(lever)},
        {"mgnMode": "isolated", "lever": str(lever)},
    ]
    for extra in attempts:
        body = {"instId": instId, **extra}
        res = request_okx("POST", "/api/v5/account/set-leverage", body)
        results.append((extra, res))
        if res.get("code")=="0":
            print(f"  leverage OK {extra} -> {res.get('code')}")
            if "posSide" not in extra:
                return res
    for _, r in results:
        if r.get("code")=="0":
            return r
    print(f"  leverage FAIL all: {results[-1]}")
    return results[-1][1] if results else {"code":"1","msg":"no attempt"}

def format_sz(contracts, lotSz_str):
    try:
        lot = Decimal(str(lotSz_str))
        d = Decimal(str(contracts)).quantize(lot, rounding=ROUND_DOWN)
        if d < lot:
            d = lot
        return format(d, 'f')
    except:
        return str(contracts)

def place_limit_order(instId, side, sz_str, px):
    body = {
        "instId": instId,
        "tdMode": "isolated",
        "side": side,
        "ordType": "limit",
        "sz": sz_str,
        "px": str(px)
    }
    return request_okx("POST", "/api/v5/trade/order", body)

def get_order(instId, ordId):
    return request_okx("GET", f"/api/v5/trade/order?instId={instId}&ordId={ordId}")

def cancel_order(instId, ordId):
    body = {"instId": instId, "ordId": ordId}
    return request_okx("POST", "/api/v5/trade/cancel-order", body)

def get_positions(instId):
    return request_okx("GET", f"/api/v5/account/positions?instId={instId}")

def place_algo_sl_tp(instId, side, sz_str, slPx, tpPx):
    body = {
        "instId": instId,
        "tdMode": "isolated",
        "side": side,
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
        return {"generated_at": now_utc(), "bot_version": "V7.5", "total_executed":0, "total_verified":0, "trades":[]}

def save_log(data):
    data["generated_at"] = now_utc()
    data["bot_version"] = "V7.5_DEMO_HEADER_TIMESTAMP_LEVERAGE_FIX"
    with open("okx_demo_log.json", "w") as f:
        json.dump(data, f, indent=2)

def is_recently_executed(log_trades, symbol, hours=24):
    now = datetime.now(timezone.utc)
    for t in log_trades[-30:]:
        if t["symbol"]==symbol and t["status"] in ("VERIFIED","LIMIT_FILLED","LIMIT_NOT_FILLED_CANCELED"):
            try:
                ts = datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
                if (now - ts).total_seconds() < hours*3600:
                    return True
            except:
                pass
    return False

def main():
    if not API_KEY or not SECRET or not PASSPHRASE:
        print("WARNING: OKX keys missing - skipping")
        log_data = load_okx_log()
        save_log(log_data)
        return

    scan = load_last_scan()
    valid_new = scan.get("valid_new_positions", []) or scan.get("valid_new", []) or []
    log_data = load_okx_log()

    print(f"Scan valid_new: {len(valid_new)} | existing trades: {len(log_data.get('trades',[]))}")

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
            print(f"SKIP {symbol} - already executed <24h")
            continue

        lotSz, minSz, ctVal, inst_data = get_instruments(instId)
        lotSz_str = inst_data.get("lotSz", str(lotSz))
        print(f"{symbol} inst: lotSz={lotSz_str} minSz={minSz} ctVal={ctVal}")

        sl_pct = abs(entry - sl) / entry if entry else 0
        risk_usd = 2.0
        notional = risk_usd / sl_pct if sl_pct>0 else 0
        qty_raw = notional / entry / ctVal if ctVal else notional/entry

        contracts_float = math.floor(qty_raw / lotSz) * lotSz if lotSz else qty_raw
        if contracts_float < minSz:
            contracts_float = minSz
        sz_str = format_sz(contracts_float, lotSz_str)

        side = "buy" if direction=="LONG" else "sell"

        lev_res = set_leverage_safe(instId, leverage)
        print(f"{symbol} set lev {leverage}: {lev_res.get('code')} {lev_res.get('msg','')}")

        order_res = place_limit_order(instId, side, sz_str, entry)
        if order_res.get("code")!="0":
            print(f"{symbol} FAILED_ORDER {order_res}")
            trade = {
                "timestamp": now_utc(),
                "symbol": symbol, "instId": instId, "direction": direction,
                "status": "FAILED_ORDER",
                "reason": str(order_res),
                "entry": entry, "sl": sl, "tp": tp,
                "sizing": sizing, "contracts": sz_str,
                "lotSz": lotSz_str, "minSz": str(minSz), "ctVal": str(ctVal)
            }
            log_data["trades"].append(trade)
            save_log(log_data)
            continue

        ordId = order_res["data"][0]["ordId"]
        print(f"{symbol} LIMIT placed {ordId} px {entry} sz {sz_str}")

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
                    last_px = entry
                if last_state == "filled":
                    filled = True
                    break
                if last_state in ("canceled","mmp_canceled"):
                    break
            print(f"  wait {i+1}/6 state={last_state} lastPx={last_px}")

        if not filled:
            c_res = cancel_order(instId, ordId)
            threshold = DISTANCE_THRESHOLD_PCT.get(symbol, DISTANCE_THRESHOLD_PCT["DEFAULT"])
            distance_pct = abs(last_px - entry) / entry * 100 if entry else 0
            cancel_reason = "PRICE_RAN_AWAY" if distance_pct >= threshold else "TIMEOUT_30S"
            trade = {
                "timestamp": now_utc(),
                "symbol": symbol, "instId": instId, "direction": direction,
                "status": "LIMIT_NOT_FILLED_CANCELED",
                "order_type": "LIMIT", "verified_by_api": True,
                "order_id": ordId, "order_res": order_res, "cancel_res": c_res,
                "signal_entry": entry, "signal_sl": sl, "signal_tp": tp,
                "real_last_price": last_px, "real_state": last_state,
                "distance_pct": round(distance_pct,4), "threshold_pct": threshold,
                "cancel_reason": cancel_reason,
                "sizing": sizing, "contracts": sz_str,
                "lotSz": lotSz_str, "minSz": str(minSz), "ctVal": str(ctVal),
                "leverage": leverage
            }
            log_data["trades"].append(trade)
            print(f"{symbol} CANCELED {cancel_reason} dist {distance_pct:.3f}%")
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

        close_side = "sell" if direction=="LONG" else "buy"
        algo_res = place_algo_sl_tp(instId, close_side, sz_str, real_sl, real_tp)

        trade = {
            "timestamp": now_utc(),
            "symbol": symbol, "instId": instId, "direction": direction,
            "status": "VERIFIED", "order_type": "LIMIT", "verified_by_api": True,
            "order_id": ordId, "order_res": order_res, "lev_res": lev_res, "algo_res": algo_res,
            "pos_snapshot": pos,
            "signal_entry": entry, "signal_sl": sl, "signal_tp": tp,
            "real_avgPx": avgPx, "real_sl": real_sl, "real_tp": real_tp,
            "sizing": sizing, "contracts": sz_str, "contracts_str": sz_str, "raw": float(qty_raw),
            "lotSz": lotSz_str, "minSz": str(minSz), "ctVal": str(ctVal),
            "leverage": leverage, "margin_needed": float(margin) if margin else None,
            "risk_note": f"LIMIT identik paper, SL recalc dari avgPx {avgPx}"
        }
        log_data["trades"].append(trade)
        print(f"{symbol} VERIFIED avgPx {avgPx} SL {real_sl} TP {real_tp} sz {sz_str}")
        save_log(log_data)

    log_data["total_executed"] = len(log_data["trades"])
    log_data["total_verified"] = len([t for t in log_data["trades"] if t["status"] in ("VERIFIED","LIMIT_FILLED")])
    log_data["total_canceled"] = len([t for t in log_data["trades"] if t["status"]=="LIMIT_NOT_FILLED_CANCELED"])
    log_data["total_failed"] = len([t for t in log_data["trades"] if t["status"]=="FAILED_ORDER"])
    save_log(log_data)
    print(f"Done V7.5 verified {log_data['total_verified']} canceled {log_data['total_canceled']} failed {log_data['total_failed']}")

if __name__ == "__main__":
    main()
