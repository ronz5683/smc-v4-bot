"""
smc_okx_demo.py V7.1 FINAL REAL VERIFIED
- Fix 51121 lotSz with math.floor
- Auto-detect hedge/net mode (retry without posSide)
- Real ctVal handling from /public/instruments
- Balance check before order (no fake EXECUTED)
- Verification: positions_api + bills_api + orders_history_api
- x-simulated-trading: 1 = DEMO, real behavior same as real API
"""
import os, json, time, hmac, base64, hashlib, datetime, requests, math
from datetime import timezone
from urllib.parse import urlencode

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

def okx_request(method, path, params=None, body_dict=None):
    if not API_KEY or not API_SECRET or not PASSPHRASE:
        return {"code":"999","msg":"NO_KEYS"}
    # Build query string for signing
    query_string = ""
    body_str = ""
    if method == "GET" and params:
        query_string = "?" + urlencode(params)
    elif method != "GET" and body_dict:
        body_str = json.dumps(body_dict)

    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    request_path_for_sign = path + query_string
    signature = sign(ts, method, request_path_for_sign, body_str)
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
            r = requests.get(url, headers=headers, params=params, timeout=15)
        else:
            r = requests.post(url, headers=headers, data=body_str, timeout=15)
        return r.json()
    except Exception as e:
        return {"code":"998","msg":f"request_error: {e}"}

def get_instruments():
    try:
        r = requests.get(f"{BASE_URL}/api/v5/public/instruments", params={"instType":"SWAP"}, timeout=15)
        data = r.json()
        if data.get("code")=="0":
            return {x["instId"]: x for x in data["data"]}
    except:
        pass
    return {}

def get_account_config():
    return okx_request("GET","/api/v5/account/config")

def get_balance():
    return okx_request("GET","/api/v5/account/balance")

def set_leverage(instId, lever, mgnMode="isolated"):
    return okx_request("POST","/api/v5/account/set-leverage", body_dict={"instId": instId, "lever": str(lever), "mgnMode": mgnMode})

def place_order(instId, side, sz, posSide=None, tdMode="isolated"):
    body = {"instId": instId, "tdMode": tdMode, "side": side, "ordType": "market", "sz": str(sz)}
    if posSide:
        body["posSide"] = posSide
    return okx_request("POST","/api/v5/trade/order", body_dict=body)

def place_order_safe(instId, side, sz, posSide, tdMode="isolated"):
    # Try hedge mode first
    res = place_order(instId, side, sz, posSide=posSide, tdMode=tdMode)
    if not res:
        return res, False
    # If error about posSide (net mode), retry without posSide
    data = res.get("data", [{}])[0] if res.get("data") else {}
    sCode = data.get("sCode","")
    sMsg = data.get("sMsg","")
    if res.get("code")!="0" or sCode!="0":
        if "posSide" in str(sMsg) or "Position side" in str(sMsg) or sCode in ("59001","59011","51023"):
            # retry net mode
            res2 = place_order(instId, side, sz, posSide=None, tdMode=tdMode)
            return res2, True  # True = retried net mode
    return res, False

def place_algo_tp_sl(instId, side, sz, tpTriggerPx, slTriggerPx, posSide=None):
    body = {"instId": instId, "tdMode": "isolated", "side": side, "ordType": "conditional", "sz": str(sz), "tpTriggerPx": str(tpTriggerPx), "tpOrdPx": "-1", "slTriggerPx": str(slTriggerPx), "slOrdPx": "-1"}
    if posSide:
        body["posSide"] = posSide
    return okx_request("POST","/api/v5/trade/order-algo", body_dict=body)

def place_algo_safe(instId, side, sz, tp, sl, posSide):
    res = place_algo_tp_sl(instId, side, sz, tp, sl, posSide=posSide)
    if not res:
        return res, False
    # if net mode error, retry without posSide
    if res.get("code")!="0":
        if "posSide" in str(res):
            res2 = place_algo_tp_sl(instId, side, sz, tp, sl, posSide=None)
            return res2, True
    # check data sCode
    data = res.get("data", [{}])[0] if res.get("data") else {}
    if data.get("sCode") and data.get("sCode")!="0" and "posSide" in str(data.get("sMsg","")):
        res2 = place_algo_tp_sl(instId, side, sz, tp, sl, posSide=None)
        return res2, True
    return res, False

def get_positions():
    return okx_request("GET","/api/v5/account/positions", params={"instType":"SWAP"})

def get_bills(limit=30):
    return okx_request("GET","/api/v5/account/bills", params={"instType":"SWAP","limit":str(limit)})

def get_orders_history(limit=20):
    return okx_request("GET","/api/v5/trade/orders-history", params={"instType":"SWAP","limit":str(limit)})

def round_to_lot_size(qty, lotSz_str, minSz_str):
    try:
        lotSz = float(lotSz_str)
        minSz = float(minSz_str)
        if lotSz <= 0:
            return max(qty, minSz)
        # floor to lot
        lots = math.floor(qty / lotSz + 1e-9)
        rounded = lots * lotSz
        if rounded < minSz - 1e-9:
            # if floor < minSz, use minSz
            rounded = minSz
        # format to correct decimals
        if "." in lotSz_str:
            dec_part = lotSz_str.split(".")[1].rstrip("0")
            decimals = len(dec_part)
            if decimals > 0:
                factor = 10 ** decimals
                rounded = math.floor(rounded * factor + 1e-9) / factor
                return float(f"{rounded:.{decimals}f}")
        return float(rounded)
    except Exception:
        return qty

def main():
    now_iso = datetime.datetime.now(timezone.utc).isoformat()
    if not API_KEY:
        with open(LOG_FILE,"w") as f:
            json.dump({"generated_at":now_iso,"status":"NO_KEYS","bot_version":"V7.1_REAL_VERIFIED","trades":[],"verification":{}}, f, indent=2)
        print("NO_KEYS")
        return
    if not os.path.exists("last_scan.json"):
        print("no last_scan.json")
        return
    with open("last_scan.json") as f:
        scan = json.load(f)
    valid_new = scan.get("valid_new_positions", []) or scan.get("valid_trades", []) or scan.get("valid_new", [])
    instruments = get_instruments()
    acc_config = get_account_config()
    balance = get_balance()

    log_trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                old = json.load(f)
                log_trades = old.get("trades", [])
        except:
            log_trades = []

    for pos in valid_new:
        sym = pos.get("symbol")
        instId = SYMBOL_MAP.get(sym)
        if not instId:
            continue
        # skip duplicate within 24h
        recent = [x for x in log_trades if x["symbol"]==sym and (datetime.datetime.now(timezone.utc) - datetime.datetime.fromisoformat(x["timestamp"])).total_seconds() < 86400 and x["status"] in ("EXECUTED","VERIFIED")]
        if recent:
            continue

        direction = pos.get("direction","LONG")
        entry = pos.get("entry")
        sl = pos.get("sl")
        tp = pos.get("tp")
        sizing = pos.get("sizing",{})
        qty = sizing.get("qty") or pos.get("qty") or 0
        leverage = sizing.get("leverage", MAX_LEVERAGE.get(sym,20))

        inst_info = instruments.get(instId, {})
        ctVal = inst_info.get("ctVal","1")
        minSz = inst_info.get("minSz","1")
        lotSz = inst_info.get("lotSz","1")
        try:
            ctVal_f = float(ctVal)
        except:
            ctVal_f = 1.0
        # qty is notional/coin qty -> convert to contracts: contracts = qty / ctVal
        # For USDT-SWAP linear, ctVal is e.g. 0.01 BTC per contract, but qty from paper is coin qty, so:
        try:
            raw_contracts = float(qty) / ctVal_f if ctVal_f !=0 else float(qty)
        except:
            raw_contracts = float(qty)

        contracts = round_to_lot_size(raw_contracts, lotSz, minSz)
        if contracts <= 0:
            contracts = float(minSz)

        side = "buy" if direction=="LONG" else "sell"
        opp_side = "sell" if side=="buy" else "buy"
        posSide = "long" if direction=="LONG" else "short"

        # balance check
        equity = 0
        try:
            if balance and balance.get("code")=="0":
                for d in balance.get("data",[]):
                    for det in d.get("details",[]):
                        if det.get("ccy")=="USDT":
                            equity = float(det.get("availBal",0))
                            break
        except:
            pass
        margin_needed = (float(entry) * contracts * ctVal_f) / float(leverage) if entry else 0

        lev_res = set_leverage(instId, leverage, "isolated")
        time.sleep(0.6)

        if equity and margin_needed and equity < margin_needed * 0.9:
            log_trades.append({
                "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
                "symbol": sym, "instId": instId, "direction": direction,
                "status": "INSUFFICIENT_MARGIN",
                "verified_by_api": False,
                "reason": f"avail {equity} < needed {margin_needed}",
                "entry": entry, "sl": sl, "tp": tp,
                "sizing": sizing, "contracts": contracts, "raw_contracts": raw_contracts,
                "lotSz": lotSz, "minSz": minSz, "ctVal": ctVal,
                "balance_api": balance
            })
            continue

        order_res, retried_net = place_order_safe(instId, side, contracts, posSide, "isolated")
        order_id = None
        status = "FAILED_ORDER"
        verified = False
        pos_snapshot_after = None

        if order_res and order_res.get("code")=="0":
            d0 = order_res.get("data",[{}])[0]
            if d0.get("sCode")=="0":
                order_id = d0.get("ordId")
                status = "EXECUTED"
                time.sleep(1.2)
                pos_snapshot_after = get_positions()
                if pos_snapshot_after and pos_snapshot_after.get("code")=="0":
                    for p in pos_snapshot_after.get("data",[]):
                        if p.get("instId")==instId:
                            try:
                                if abs(float(p.get("pos","0"))) > 1e-9:
                                    verified = True
                                    status = "VERIFIED"
                                    break
                            except:
                                pass
            else:
                status = "FAILED_ORDER"
        else:
            status = "FAILED_ORDER"

        algo_res = None
        algo_retried = False
        if status in ("EXECUTED","VERIFIED"):
            time.sleep(0.8)
            algo_res, algo_retried = place_algo_safe(instId, opp_side, contracts, tp, sl, posSide if not retried_net else None)
            # if algo failed, we still keep position but mark warning
            if algo_res and algo_res.get("code")!="0":
                # try to check error
                status = "VERIFIED_ALGO_FAILED"

        log_trades.append({
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
            "symbol": sym, "instId": instId, "direction": direction,
            "status": status,
            "verified_by_api": verified,
            "retried_net_mode": retried_net,
            "algo_retried_net": algo_retried,
            "order_id": order_id,
            "order_res": order_res,
            "lev_res": lev_res,
            "algo_res": algo_res,
            "pos_snapshot_after_order": pos_snapshot_after,
            "entry": entry, "sl": sl, "tp": tp,
            "sizing": sizing, "contracts": contracts, "raw_contracts": raw_contracts,
            "lotSz": lotSz, "minSz": minSz, "ctVal": ctVal,
            "leverage": leverage, "margin_needed": margin_needed, "avail_equity": equity
        })
        time.sleep(1.0)

    # final verification
    final_pos = get_positions()
    final_bills = get_bills(50)
    final_orders = get_orders_history(30)

    out = {
        "generated_at": datetime.datetime.now(timezone.utc).isoformat(),
        "bot_version": "V7.1_FINAL_REAL_VERIFIED",
        "total_executed": len([x for x in log_trades if x["status"] in ("EXECUTED","VERIFIED","VERIFIED_ALGO_FAILED")]),
        "total_verified": len([x for x in log_trades if x["status"] in ("VERIFIED","VERIFIED_ALGO_FAILED")]),
        "trades": log_trades[-200:],
        "verification": {
            "account_config_api": acc_config,
            "balance_api": balance,
            "positions_api": final_pos,
            "bills_api": final_bills,
            "orders_history_api": final_orders,
            "note": "VERIFIED = posisi ada di positions_api. Jika sudah TP/SL, posisi hilang dari positions_api tapi ada di bills_api dengan PnL real. Bandingkan dengan app OKX Demo Trading > Bills."
        }
    }
    with open(LOG_FILE,"w") as f:
        json.dump(out, f, indent=2)
    print(f"Done V7.1 verified={out['total_verified']}")

if __name__ == "__main__":
    main()
