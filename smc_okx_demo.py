"""
SMC OKX Demo Executor V7.3 - LIMIT_IDENTIK_PAPER + CANCELED REPORT
- FIX: Leverage 50x ALWAYS_MAX with posSide handling (hedge mode)
- FIX: Lot size rounding (lotSz/minSz/ctVal)
- NEW V7.2: LIMIT order + SL/TP recalc dari avgPx real biar risk $2 tetap identik paper
- NEW V7.3: LIMIT_NOT_FILLED_CANCELED dengan distance_pct & cancel_reason detail

Log format:
- FILLED: status VERIFIED, real_avgPx, real_sl, real_tp, margin 11.15 untuk 50x
- CANCELED: status LIMIT_NOT_FILLED_CANCELED, signal_entry, real_last_price, distance_pct, cancel_reason
"""
import os, json, time, hmac, base64, hashlib, requests
from datetime import datetime, timezone

# CONFIG - support both OKX_DEMO_* (workflow kamu) dan OKX_* (standard)
API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"

# Debug biar keliatan di log workflow kalau secret kebaca atau tidak (tanpa print secretnya)
print(f"ENV CHECK: API_KEY={'SET' if API_KEY else 'MISSING'} SECRET={'SET' if SECRET else 'MISSING'} PASS={'SET' if PASSPHRASE else 'MISSING'}")

# Threshold jarak lari dianggap PRICE_RAN_AWAY (bisa diatur per coin)
# BTC/ETH volatil kecil, alt volatil besar
DISTANCE_THRESHOLD_PCT = {
    "BTCUSDT": 0.15,
    "ETHUSDT": 0.18,
    "BNBUSDT": 0.20,
    "SOLUSDT": 0.25,
    "LINKUSDT": 0.30,
    "ADAUSDT": 0.35,
    "DOGEUSDT": 0.40,
    "AVAXUSDT": 0.30,
    "ARBUSDT": 0.50,
    "DEFAULT": 0.30
}

def now_utc():
    return datetime.now(timezone.utc).isoformat()

def sign(timestamp, method, request_path, body=""):
    if not SECRET:
        raise ValueError("OKX_SECRET_KEY is None - check GitHub Secrets")
    message = timestamp + method + request_path + body
    mac = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def request_okx(method, path, body=None):
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body_str = json.dumps(body) if body else ""
    headers = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sign(timestamp, method, path, body_str),
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }
    url = BASE_URL + path
    if method == "GET":
        r = requests.get(url, headers=headers)
    else:
        r = requests.post(url, headers=headers, data=body_str)
    try:
        return r.json()
    except:
        return {"code": "1", "msg": r.text}

def get_instruments(instId):
    res = request_okx("GET", f"/api/v5/public/instruments?instType=SWAP&instId={instId}")
    if res.get("code")=="0" and res["data"]:
        d = res["data"][0]
        return float(d["lotSz"]), float(d["minSz"]), float(d["ctVal"])
    return 1.0, 1.0, 0.01

def set_leverage_safe(instId, lever):
    # Try hedge long first, then net
    for mode in ["isolated"]:
        for posSide in ["long", ""]:
            try:
                body = {"instId": instId, "lever": str(lever), "mgnMode": mode}
                if posSide:
                    body["posSide"] = posSide
                res = request_okx("POST", "/api/v5/account/set-leverage", body)
                if res.get("code")=="0":
                    return res
            except:
                pass
    return {"code": "51000", "msg": "Parameter posSide error"}

def place_limit_order(instId, side, sz, px, reduceOnly=False):
    body = {
        "instId": instId,
        "tdMode": "isolated",
        "side": side,
        "ordType": "limit",
        "sz": str(sz),
        "px": str(px)
    }
    if reduceOnly:
        body["reduceOnly"] = True
    return request_okx("POST", "/api/v5/trade/order", body)

def get_order(instId, ordId):
    return request_okx("GET", f"/api/v5/trade/order?instId={instId}&ordId={ordId}")

def cancel_order(instId, ordId):
    body = {"instId": instId, "ordId": ordId}
    return request_okx("POST", "/api/v5/trade/cancel-order", body)

def get_positions(instId):
    return request_okx("GET", f"/api/v5/account/positions?instId={instId}")

def place_algo_sl_tp(instId, side, sz, slPx, tpPx):
    # side opposite for close
    body = {
        "instId": instId,
        "tdMode": "isolated",
        "side": side,
        "ordType": "conditional",
        "sz": str(sz),
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
        return {"generated_at": now_utc(), "bot_version": "V7.3_LIMIT_IDENTIK_PAPER_CANCELED", "total_executed":0, "total_verified":0, "trades":[]}

def save_log(data):
    data["generated_at"] = now_utc()
    with open("okx_demo_log.json", "w") as f:
        json.dump(data, f, indent=2)

def is_recently_executed(log_trades, symbol, hours=24):
    now = datetime.now(timezone.utc)
    for t in log_trades[-20:]:
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
        print("WARNING: OKX_API_KEY / SECRET / PASSPHRASE not set in env")
        print("Skipping OKX demo executor - check workflow yaml env injection")
        # jangan crash workflow, tetap buat log kosong
        log_data = load_okx_log()
        log_data["bot_version"] = "V7.3_LIMIT_IDENTIK_PAPER_CANCELED"
        log_data["generated_at"] = now_utc()
        save_log()
        return

    scan = load_last_scan()
    valid_new = scan.get("valid_new_positions", []) or scan.get("valid_new", []) or []
    log_data = load_okx_log()
    log_data["bot_version"] = "V7.3_LIMIT_IDENTIK_PAPER_CANCELED"
    
    print(f"Scan valid_new: {len(valid_new)}")
    
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
            print(f"SKIP {symbol} - recently executed <24h")
            continue
        
        lotSz, minSz, ctVal = get_instruments(instId)
        # calc qty from risk $2
        sl_pct = abs(entry - sl) / entry
        risk_usd = 2.0
        notional = risk_usd / sl_pct if sl_pct>0 else 0
        qty_raw = notional / entry / ctVal if ctVal else notional/entry
        # round to lotSz
        import math
        contracts = math.floor(qty_raw / lotSz) * lotSz
        if contracts < minSz:
            contracts = minSz
        
        sz = contracts
        side = "buy" if direction=="LONG" else "sell"
        
        # Set leverage
        lev_res = set_leverage_safe(instId, leverage)
        print(f"{symbol} set lev {leverage}: {lev_res.get('code')}")
        
        # Place LIMIT
        order_res = place_limit_order(instId, side, sz, entry)
        if order_res.get("code")!="0":
            print(f"{symbol} FAILED_ORDER {order_res}")
            trade = {
                "timestamp": now_utc(),
                "symbol": symbol,
                "instId": instId,
                "direction": direction,
                "status": "FAILED_ORDER",
                "reason": str(order_res),
                "entry": entry, "sl": sl, "tp": tp,
                "sizing": sizing,
                "contracts": sz
            }
            log_data["trades"].append(trade)
            continue
        
        ordId = order_res["data"][0]["ordId"]
        print(f"{symbol} LIMIT placed {ordId} px {entry} sz {sz}")
        
        # Wait fill 30s
        filled = False
        real_avgPx = None
        last_px = entry
        last_state = "live"
        
        for i in range(6):
            time.sleep(5)
            o = get_order(instId, ordId)
            if o.get("code")=="0" and o["data"]:
                d = o["data"][0]
                last_state = d.get("state","live")
                last_px = float(d.get("lastPx") or d.get("px") or entry)
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
            
            if distance_pct >= threshold:
                cancel_reason = "PRICE_RAN_AWAY"
                note = f"Paper ENTRY {entry} -> real CANCEL karena harga lari ke {last_px} ({distance_pct:.3f}%) > threshold {threshold}%. Paper TP/SL tidak valid."
            else:
                cancel_reason = "TIMEOUT_30S"
                note = f"LIMIT {entry} tidak ter-fill dalam 30s, lastPx {last_px} ({distance_pct:.3f}%). Cancel biar identik paper tidak hold."
            
            trade = {
                "timestamp": now_utc(),
                "symbol": symbol,
                "instId": instId,
                "direction": direction,
                "status": "LIMIT_NOT_FILLED_CANCELED",
                "order_type": "LIMIT",
                "verified_by_api": True,
                "order_id": ordId,
                "order_res": order_res,
                "cancel_res": c_res,
                "signal_entry": entry,
                "signal_sl": sl,
                "signal_tp": tp,
                "real_last_price": last_px,
                "real_state": last_state,
                "distance_pct": round(distance_pct, 4),
                "threshold_pct": threshold,
                "cancel_reason": cancel_reason,
                "sizing": sizing,
                "contracts": sz,
                "lotSz": str(lotSz), "minSz": str(minSz), "ctVal": str(ctVal),
                "leverage": leverage,
                "risk_note": note,
                "paper_note": "CANCELED ini harus dianggap tidak entry di paper juga supaya backtest tetap identik"
            }
            log_data["trades"].append(trade)
            print(f"{symbol} CANCELED {cancel_reason} dist {distance_pct:.3f}%")
            continue
        
        # FILLED - get avgPx and recalc SL/TP biar risk $2 tetap
        pos = get_positions(instId)
        avgPx = entry
        if pos.get("code")=="0" and pos["data"]:
            avgPx = float(pos["data"][0].get("avgPx", entry))
            margin = pos["data"][0].get("margin")
            lever_real = pos["data"][0].get("lever")
        else:
            margin = None
            lever_real = leverage
        
        dist_sl = abs(entry - sl)
        dist_tp = abs(tp - entry)
        real_sl = avgPx - dist_sl if direction=="LONG" else avgPx + dist_sl
        real_tp = avgPx + dist_tp if direction=="LONG" else avgPx - dist_tp
        
        # Place SL/TP algo
        close_side = "sell" if direction=="LONG" else "buy"
        algo_res = place_algo_sl_tp(instId, close_side, sz, real_sl, real_tp)
        
        trade = {
            "timestamp": now_utc(),
            "symbol": symbol,
            "instId": instId,
            "direction": direction,
            "status": "VERIFIED",
            "order_type": "LIMIT",
            "verified_by_api": True,
            "order_id": ordId,
            "order_res": order_res,
            "lev_res": lev_res,
            "algo_res": algo_res,
            "pos_snapshot": pos,
            "signal_entry": entry,
            "signal_sl": sl,
            "signal_tp": tp,
            "real_avgPx": avgPx,
            "real_sl": real_sl,
            "real_tp": real_tp,
            "sizing": sizing,
            "contracts": float(sz),
            "raw": float(qty_raw),
            "lotSz": str(lotSz), "minSz": str(minSz), "ctVal": str(ctVal),
            "leverage": leverage,
            "margin_needed": float(margin) if margin else None,
            "risk_note": f"Limit order + SL recalc dari avgPx {avgPx} biar risk tetap $2 identik paper (signal {entry} -> real {avgPx})"
        }
        log_data["trades"].append(trade)
        print(f"{symbol} VERIFIED avgPx {avgPx} SL {real_sl} TP {real_tp}")
    
    log_data["total_executed"] = len(log_data["trades"])
    log_data["total_verified"] = len([t for t in log_data["trades"] if t["status"] in ("VERIFIED","LIMIT_FILLED")])
    log_data["total_canceled"] = len([t for t in log_data["trades"] if t["status"]=="LIMIT_NOT_FILLED_CANCELED"])
    save_log()
    print(f"Done V7.3 verified {log_data['total_verified']} canceled {log_data['total_canceled']}")

if __name__ == "__main__":
    main()
