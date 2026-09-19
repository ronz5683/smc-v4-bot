import os, json, time, hmac, base64, hashlib, datetime, requests, math
from datetime import timezone

PAPER_RISK_USD = 2.0
SYMBOL_MAP = {
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
    "SOLUSDT": "SOL-USDT-SWAP",
    "XRPUSDT": "XRP-USDT-SWAP",
    "BNBUSDT": "BNB-USDT-SWAP",
    "ADAUSDT": "ADA-USDT-SWAP",
    "DOGEUSDT": "DOGE-USDT-SWAP",
    "AVAXUSDT": "AVAX-USDT-SWAP",
    "LINKUSDT": "LINK-USDT-SWAP",
    "OPUSDT": "OP-USDT-SWAP",
    "ARBUSDT": "ARB-USDT-SWAP",
    "MATICUSDT": "MATIC-USDT-SWAP",
}
MAX_LEVERAGE = {
    "BTCUSDT": 100, "ETHUSDT": 100, "SOLUSDT": 50, "BNBUSDT": 50,
    "XRPUSDT": 50, "ADAUSDT": 50, "DOGEUSDT": 50, "AVAXUSDT": 50,
    "LINKUSDT": 50, "OPUSDT": 20, "ARBUSDT": 20, "MATICUSDT": 20,
}

API_KEY = os.getenv("OKX_DEMO_API_KEY")
API_SECRET = os.getenv("OKX_DEMO_API_SECRET")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE")
BASE_URL = "https://www.okx.com"
LOG_FILE = "okx_demo_log.json"

def sign(timestamp, method, request_path, body=""):
    message = f"{timestamp}{method}{request_path}{body}"
    mac = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def okx_request(method, path, body_dict=None):
    if not API_KEY:
        return None
    body = ""
    if body_dict:
        body = json.dumps(body_dict)
    from datetime import datetime as dt
    ts = dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    signature = sign(ts, method, path, body)
    headers = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
        "x-simulated-trading": "1"
    }
    url = BASE_URL + path
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=body_dict, timeout=10)
        else:
            r = requests.post(url, headers=headers, data=body, timeout=10)
        return r.json()
    except Exception as e:
        return {"code":"999", "msg": str(e)}

def get_instruments():
    try:
        r = requests.get(f"{BASE_URL}/api/v5/public/instruments", params={"instType":"SWAP"}, timeout=10)
        data = r.json()
        if data.get("code")=="0":
            return {x["instId"]: x for x in data["data"]}
    except: pass
    return {}

def set_leverage(instId, lever, mgnMode="isolated"):
    path = "/api/v5/account/set-leverage"
    body = {"instId": instId, "lever": str(lever), "mgnMode": mgnMode}
    return okx_request("POST", path, body)

def place_order(instId, side, sz, posSide=None, tdMode="isolated"):
    path = "/api/v5/trade/order"
    body = {"instId": instId, "tdMode": tdMode, "side": side, "ordType": "market", "sz": str(sz)}
    if posSide:
        body["posSide"] = posSide
    return okx_request("POST", path, body)

def place_algo_tp_sl(instId, side, sz, tpTriggerPx, slTriggerPx, posSide=None):
    path = "/api/v5/trade/order-algo"
    body = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "conditional", "sz": str(sz), "tpTriggerPx": str(tpTriggerPx), "tpOrdPx": "-1", "slTriggerPx": str(slTriggerPx), "slOrdPx": "-1"}
    if posSide:
        body["posSide"] = posSide
    return okx_request("POST", path, body)

def round_to_lot_size(qty, lotSz_str, minSz_str):
    try:
        lotSz = float(lotSz_str)
        minSz = float(minSz_str)
        if lotSz == 0:
            return qty
        lots = math.floor(qty / lotSz)
        rounded = lots * lotSz
        if rounded < minSz:
            rounded = minSz
        dec_str = lotSz_str.split('.')
        decimals = len(dec_str[1]) if len(dec_str) > 1 else 0
        return float(f"{rounded:.{decimals}f}") if decimals <= 8 else rounded
    except:
        return qty

def main():
    if not API_KEY:
        with open(LOG_FILE, "w") as f:
            json.dump({"generated_at": datetime.datetime.now(timezone.utc).isoformat(), "status":"NO_KEYS", "trades":[]}, f, indent=2)
        return
    if not os.path.exists("last_scan.json"):
        return
    with open("last_scan.json") as f:
        scan = json.load(f)
    valid_new = scan.get("valid_new_positions", []) or scan.get("valid_trades", [])
    instruments = get_instruments()
    log = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                old = json.load(f)
                log = old.get("trades", [])
        except: log = []

    for pos in valid_new:
        sym = pos.get("symbol")
        instId = SYMBOL_MAP.get(sym)
        if not instId:
            continue
        recent = [x for x in log if x["symbol"]==sym and x["instId"]==instId and (datetime.datetime.now(timezone.utc) - datetime.datetime.fromisoformat(x["timestamp"])).total_seconds() < 86400]
        if recent and any(r.get("status")=="EXECUTED" for r in recent):
            continue
        direction = pos.get("direction", "LONG")
        entry = pos.get("entry")
        sl = pos.get("sl")
        tp = pos.get("tp")
        sizing = pos.get("sizing", {})
        qty = sizing.get("qty")
        leverage = sizing.get("leverage", MAX_LEVERAGE.get(sym, 20))
        inst_info = instruments.get(instId, {})
        ctVal = float(inst_info.get("ctVal", "1"))
        minSz = inst_info.get("minSz", "1")
        lotSz = inst_info.get("lotSz", "1")
        try:
            raw_contracts = qty / ctVal if ctVal else qty
        except:
            raw_contracts = qty
        contracts = round_to_lot_size(raw_contracts, lotSz, minSz)
        if contracts <= 0:
            contracts = float(minSz)
        side = "buy" if direction=="LONG" else "sell"
        opp_side = "sell" if side=="buy" else "buy"
        posSide = "long" if direction=="LONG" else "short"
        set_leverage(instId, leverage, "isolated")
        time.sleep(0.5)
        order_res = place_order(instId, side, contracts, posSide=posSide, tdMode="isolated")
        order_id = None
        if order_res and order_res.get("code")=="0":
            order_id = order_res["data"][0].get("ordId")
        else:
            log.append({"timestamp": datetime.datetime.now(timezone.utc).isoformat(), "symbol": sym, "instId": instId, "direction": direction, "status": "FAILED_ORDER", "reason": str(order_res), "entry": entry, "sl": sl, "tp": tp, "sizing": sizing, "contracts": contracts, "lotSz": lotSz, "minSz": minSz, "ctVal": ctVal})
            continue
        time.sleep(0.8)
        algo_res = place_algo_tp_sl(instId, opp_side, contracts, tp, sl, posSide=posSide)
        log.append({"timestamp": datetime.datetime.now(timezone.utc).isoformat(), "symbol": sym, "instId": instId, "direction": direction, "status": "EXECUTED", "order_id": order_id, "order_res": order_res, "algo_res": algo_res, "entry": entry, "sl": sl, "tp": tp, "sizing": sizing, "contracts": contracts, "leverage": leverage, "risk_usd": PAPER_RISK_USD})
    out = {"generated_at": datetime.datetime.now(timezone.utc).isoformat(), "bot_version": "V6_OKX_DEMO_EXEC_FIXED_LOT", "total_executed": len([x for x in log if x["status"]=="EXECUTED"]), "trades": log[-200:]}
    with open(LOG_FILE, "w") as f:
        json.dump(out, f, indent=2)

if __name__ == "__main__":
    main()
