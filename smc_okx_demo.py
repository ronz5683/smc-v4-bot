"""
V7.2 LIMIT ORDER - Identik dengan Paper Trading
- Pakai limit order biar entry = signal, bukan market slippage 14 point
- TP/SL recalculate dari avgPx real biar risk tetap $2
- Fix set_leverage posSide error -> leverage MAX beneran
- Verifikasi real: positions_api + bills_api
"""
import os, json, time, hmac, base64, hashlib, datetime, requests, math
from datetime import timezone
from urllib.parse import urlencode

SYMBOL_MAP = {
    "BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP", "SOLUSDT": "SOL-USDT-SWAP",
    "XRPUSDT": "XRP-USDT-SWAP", "BNBUSDT": "BNB-USDT-SWAP", "ADAUSDT": "ADA-USDT-SWAP",
    "DOGEUSDT": "DOGE-USDT-SWAP", "AVAXUSDT": "AVAX-USDT-SWAP", "LINKUSDT": "LINK-USDT-SWAP",
    "OPUSDT": "OP-USDT-SWAP", "ARBUSDT": "ARB-USDT-SWAP", "MATICUSDT": "MATIC-USDT-SWAP",
}
MAX_LEVERAGE = {"BTCUSDT":100,"ETHUSDT":100,"SOLUSDT":50,"BNBUSDT":50,"XRPUSDT":50,"ADAUSDT":50,"DOGEUSDT":50,"AVAXUSDT":50,"LINKUSDT":50,"OPUSDT":20,"ARBUSDT":20,"MATICUSDT":20}

API_KEY = os.getenv("OKX_DEMO_API_KEY")
API_SECRET = os.getenv("OKX_DEMO_API_SECRET")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE")
BASE_URL = "https://www.okx.com"
LOG_FILE = "okx_demo_log.json"

def sign(ts, method, path, body=""):
    msg = f"{ts}{method}{path}{body}"
    return base64.b64encode(hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).digest()).decode()

def okx_request(method, path, params=None, body_dict=None):
    if not API_KEY: return {"code":"999","msg":"NO_KEYS"}
    qs = ""
    body_str = ""
    if method=="GET" and params:
        qs = "?" + urlencode(params)
    elif body_dict:
        body_str = json.dumps(body_dict)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]+"Z"
    sig = sign(ts, method, path+qs, body_str)
    headers = {"OK-ACCESS-KEY":API_KEY,"OK-ACCESS-SIGN":sig,"OK-ACCESS-TIMESTAMP":ts,"OK-ACCESS-PASSPHRASE":PASSPHRASE,"Content-Type":"application/json","x-simulated-trading":"1"}
    url = BASE_URL+path
    try:
        if method=="GET":
            r = requests.get(url, headers=headers, params=params, timeout=15)
        else:
            r = requests.post(url, headers=headers, data=body_str, timeout=15)
        return r.json()
    except Exception as e:
        return {"code":"998","msg":str(e)}

def get_instruments():
    try:
        r = requests.get(f"{BASE_URL}/api/v5/public/instruments", params={"instType":"SWAP"}, timeout=15)
        d = r.json()
        if d.get("code")=="0": return {x["instId"]:x for x in d["data"]}
    except: pass
    return {}

def set_leverage_safe(instId, lever):
    # Try with posSide long
    res = okx_request("POST","/api/v5/account/set-leverage", body_dict={"instId":instId,"lever":str(lever),"mgnMode":"isolated","posSide":"long"})
    if res and res.get("code")=="51000" and "posSide" in str(res.get("msg","")):
        # retry net mode
        res = okx_request("POST","/api/v5/account/set-leverage", body_dict={"instId":instId,"lever":str(lever),"mgnMode":"isolated"})
    if res and res.get("code")=="51000":
        # try short side too for hedge mode
        okx_request("POST","/api/v5/account/set-leverage", body_dict={"instId":instId,"lever":str(lever),"mgnMode":"isolated","posSide":"short"})
    return res

def place_limit_order(instId, side, sz, px, posSide=None):
    body = {"instId":instId,"tdMode":"isolated","side":side,"ordType":"limit","sz":str(sz),"px":str(px)}
    if posSide: body["posSide"]=posSide
    return okx_request("POST","/api/v5/trade/order", body_dict=body)

def place_limit_safe(instId, side, sz, px, posSide):
    res = place_limit_order(instId, side, sz, px, posSide)
    if res and res.get("code")!="0":
        # if posSide error, retry net
        if "posSide" in str(res):
            res2 = place_limit_order(instId, side, sz, px, None)
            return res2, True
        # check data sCode
        d0 = res.get("data",[{}])[0] if res.get("data") else {}
        if "posSide" in str(d0.get("sMsg","")):
            res2 = place_limit_order(instId, side, sz, px, None)
            return res2, True
    # also check sCode inside
    if res and res.get("data"):
        d0 = res["data"][0]
        if d0.get("sCode")!="0" and "posSide" in str(d0.get("sMsg","")):
            res2 = place_limit_order(instId, side, sz, px, None)
            return res2, True
    return res, False

def get_order(instId, ordId):
    return okx_request("GET","/api/v5/trade/order", params={"instId":instId,"ordId":ordId})

def cancel_order(instId, ordId):
    return okx_request("POST","/api/v5/trade/cancel-order", body_dict={"instId":instId,"ordId":ordId})

def place_algo_safe(instId, side, sz, tp, sl, posSide):
    body = {"instId":instId,"tdMode":"isolated","side":side,"ordType":"conditional","sz":str(sz),"tpTriggerPx":str(tp),"tpOrdPx":"-1","slTriggerPx":str(sl),"slOrdPx":"-1"}
    if posSide: body["posSide"]=posSide
    res = okx_request("POST","/api/v5/trade/order-algo", body_dict=body)
    if res and "posSide" in str(res):
        body.pop("posSide",None)
        res = okx_request("POST","/api/v5/trade/order-algo", body_dict=body)
        return res, True
    if res and res.get("data"):
        d0=res["data"][0]
        if d0.get("sCode")!="0" and "posSide" in str(d0.get("sMsg","")):
            body.pop("posSide",None)
            res2 = okx_request("POST","/api/v5/trade/order-algo", body_dict=body)
            return res2, True
    return res, False

def get_positions(): return okx_request("GET","/api/v5/account/positions", params={"instType":"SWAP"})
def get_bills(): return okx_request("GET","/api/v5/account/bills", params={"instType":"SWAP","limit":"50"})
def get_balance(): return okx_request("GET","/api/v5/account/balance")

def round_lot(qty, lotSz, minSz):
    try:
        ls=float(lotSz); ms=float(minSz)
        if ls<=0: return max(qty,ms)
        lots=math.floor(qty/ls+1e-9)
        rnd=lots*ls
        if rnd<ms-1e-9: rnd=ms
        if "." in lotSz:
            dec=len(lotSz.split(".")[1].rstrip("0"))
            if dec>0:
                f=10**dec
                rnd=math.floor(rnd*f+1e-9)/f
                return float(f"{rnd:.{dec}f}")
        return float(rnd)
    except: return qty

def main():
    now=datetime.datetime.now(timezone.utc).isoformat()
    if not API_KEY:
        open(LOG_FILE,"w").write(json.dumps({"generated_at":now,"status":"NO_KEYS","trades":[]},indent=2))
        return
    if not os.path.exists("last_scan.json"): return
    scan=json.load(open("last_scan.json"))
    valid=scan.get("valid_new_positions",[]) or scan.get("valid_trades",[])
    insts=get_instruments()
    log=[]
    if os.path.exists(LOG_FILE):
        try: log=json.load(open(LOG_FILE)).get("trades",[])
        except: log=[]
    balance=get_balance()

    for pos in valid:
        sym=pos.get("symbol")
        instId=SYMBOL_MAP.get(sym)
        if not instId: continue
        # skip if same symbol executed in 24h
        recent=[x for x in log if x["symbol"]==sym and (datetime.datetime.now(timezone.utc)-datetime.datetime.fromisoformat(x["timestamp"])).total_seconds()<86400 and x["status"] in ("VERIFIED","LIMIT_FILLED")]
        if recent: continue

        direction=pos.get("direction","LONG")
        entry=pos.get("entry")
        sl=pos.get("sl")
        tp=pos.get("tp")
        sizing=pos.get("sizing",{})
        qty=sizing.get("qty") or pos.get("qty") or 0
        lev=sizing.get("leverage",MAX_LEVERAGE.get(sym,20))

        info=insts.get(instId,{})
        ctVal=info.get("ctVal","1"); minSz=info.get("minSz","1"); lotSz=info.get("lotSz","1")
        try: ct=float(ctVal)
        except: ct=1.0
        try: raw=float(qty)/ct
        except: raw=float(qty)
        contracts=round_lot(raw, lotSz, minSz)
        if contracts<=0: contracts=float(minSz)

        side="buy" if direction=="LONG" else "sell"
        opp="sell" if side=="buy" else "buy"
        pSide="long" if direction=="LONG" else "short"

        # set leverage MAX (fixed)
        set_leverage_safe(instId, lev)
        time.sleep(0.5)

        # === LIMIT ORDER identik paper ===
        order_res, retried = place_limit_safe(instId, side, contracts, entry, pSide)
        ordId=None
        status="FAILED_ORDER"
        avgPx_real=None

        if order_res and order_res.get("code")=="0" and order_res.get("data"):
            d0=order_res["data"][0]
            if d0.get("sCode")=="0":
                ordId=d0.get("ordId")
                # tunggu fill 30 detik, cek order status
                filled=False
                for _ in range(6):
                    time.sleep(5)
                    od=get_order(instId, ordId)
                    if od and od.get("data"):
                        st=od["data"][0].get("state")
                        if st=="filled":
                            avgPx_real=float(od["data"][0].get("avgPx") or entry)
                            filled=True
                            break
                        elif st in ("canceled","mmp_canceled"):
                            break
                if not filled:
                    # cancel if not filled, biar identik paper (paper gak kejar harga)
                    cancel_order(instId, ordId)
                    status="LIMIT_NOT_FILLED_CANCELED"
                else:
                    status="LIMIT_FILLED"
            else:
                status="FAILED_ORDER"
        else:
            status="FAILED_ORDER"

        # === Recalculate TP/SL dari avgPx real biar risk tetap $2 ===
        real_sl=sl; real_tp=tp
        if avgPx_real and status=="LIMIT_FILLED":
            # jarak SL dari signal
            dist_sl = entry - sl if direction=="LONG" else sl - entry
            dist_tp = tp - entry if direction=="LONG" else entry - tp
            # terapkan jarak yang sama dari avgPx real
            if direction=="LONG":
                real_sl = avgPx_real - dist_sl
                real_tp = avgPx_real + dist_tp
            else:
                real_sl = avgPx_real + dist_sl
                real_tp = avgPx_real - dist_tp

        algo_res=None
        pos_after=None
        verified=False
        if status=="LIMIT_FILLED":
            time.sleep(0.8)
            algo_res,_ = place_algo_safe(instId, opp, contracts, real_tp, real_sl, pSide if not retried else None)
            time.sleep(1)
            pos_after=get_positions()
            if pos_after and pos_after.get("code")=="0":
                for p in pos_after.get("data",[]):
                    if p.get("instId")==instId and abs(float(p.get("pos","0")))>1e-9:
                        verified=True
                        status="VERIFIED"
                        break

        log.append({
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
            "symbol": sym, "instId": instId, "direction": direction,
            "status": status,
            "order_type": "LIMIT",
            "verified_by_api": verified,
            "retried_net_mode": retried,
            "order_id": ordId,
            "order_res": order_res,
            "algo_res": algo_res,
            "pos_snapshot": pos_after,
            "signal_entry": entry, "signal_sl": sl, "signal_tp": tp,
            "real_avgPx": avgPx_real,
            "real_sl": real_sl, "real_tp": real_tp,
            "sizing": sizing, "contracts": contracts, "raw": raw,
            "lotSz": lotSz, "minSz": minSz, "ctVal": ctVal, "leverage": lev,
            "risk_note": "Limit order + SL recalc dari avgPx biar risk tetap $2 identik paper"
        })
        time.sleep(1)

    final_pos=get_positions()
    final_bills=get_bills()
    final_bal=get_balance()
    out={
        "generated_at": datetime.datetime.now(timezone.utc).isoformat(),
        "bot_version": "V7.2_LIMIT_IDENTIK_PAPER",
        "total_executed": len([x for x in log if x["status"] in ("VERIFIED","LIMIT_FILLED")]),
        "total_verified": len([x for x in log if x["status"]=="VERIFIED"]),
        "trades": log[-200:],
        "verification": {
            "positions_api": final_pos,
            "bills_api": final_bills,
            "balance_api": final_bal,
            "note": "LIMIT order identik paper. Jika LIMIT_NOT_FILLED_CANCELED, berarti harga sudah lewat, paper TP/SL tidak valid di real. Risk tetap $2 karena SL dihitung dari avgPx real."
        }
    }
    open(LOG_FILE,"w").write(json.dumps(out,indent=2))
    print(f"Done V7.2 verified {out['total_verified']}")

if __name__=="__main__":
    main()
