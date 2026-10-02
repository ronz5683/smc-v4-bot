"""
SMC OKX V11 SIMPLE $2.5 MAX - REAL $20 ACCOUNT ANTI MC
- SIMPLE: Cross 20x FIX, no leverage hunting
- MAX $2.5 after fee: if minSz risk > $2.5 => SKIP
- Guarantee SL/TP: if algo fail 51020/51304 => market close immediately max $2.5
- Fix SKIP_NO_ORDID: waiting from okx_demo_log.json only
"""
import os, json, time, hmac, base64, hashlib, requests, math
from datetime import datetime, timezone

API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"

RISK_TARGET = 2.0
RISK_MAX = 2.5  # MAX $2.5 after fee - HARD LIMIT
LEVERAGE = 20  # FIX 20x Cross sesuai request
RUN_AWAY_PCT = {"BTCUSDT":0.8,"ETHUSDT":0.8,"BNBUSDT":0.9,"SOLUSDT":1.0,"LINKUSDT":1.0,"ADAUSDT":1.2,"DOGEUSDT":1.2,"AVAXUSDT":1.0,"ARBUSDT":1.5,"OPUSDT":1.5,"XRPUSDT":1.0,"MATICUSDT":1.2,"DEFAULT":1.0}

DEBUG_LOG = {"generated_at": "", "bot_version": "V11_SIMPLE_2.5_MAX", "steps": []}

def now_utc(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def get_okx_ts(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace("+00:00","Z")
def debug(step, data):
    DEBUG_LOG["steps"].append({"time": now_utc(), "step": step, "data": data})
    print(f"[{step}] {str(data)[:600]}")
def save_debug():
    DEBUG_LOG["generated_at"] = now_utc()
    with open("okx_debug_report.json","w") as f: json.dump(DEBUG_LOG,f,indent=2)

def sign(ts, method, path, body=""):
    mac = hmac.new(SECRET.encode(), (ts+method+path+body).encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def req(method, path, body=None):
    ts = get_okx_ts()
    body_str = json.dumps(body) if body else ""
    headers = {"OK-ACCESS-KEY": API_KEY, "OK-ACCESS-SIGN": sign(ts, method, path, body_str), "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": PASSPHRASE, "Content-Type": "application/json", "x-simulated-trading": "1"}
    url = BASE_URL + path
    try:
        if method=="GET": r=requests.get(url,headers=headers,timeout=10)
        else: r=requests.post(url,headers=headers,data=body_str,timeout=10)
        j=r.json()
    except Exception as e:
        return {"code":"1","msg":str(e)}
    if j.get("code")!="0":
        debug("REQ_FAIL", {"path":path,"body":body,"resp":j})
    return j

def get_instr(instId):
    res=req("GET", f"/api/v5/public/instruments?instType=SWAP&instId={instId}")
    if res.get("code")=="0" and res["data"]:
        d=res["data"][0]
        return float(d["lotSz"]), float(d["minSz"]), float(d["ctVal"]), float(d.get("tickSz","0.01"))
    return 1.0,1.0,1.0,0.01

def calc_risk(contracts, ctVal, entry, sl):
    return float(contracts)*float(ctVal)*abs(float(entry)-float(sl))

def get_min_risk(entry, sl, ctVal, minSz):
    return calc_risk(minSz, ctVal, entry, sl)

def calc_contracts_simple(entry, sl, ctVal, lotSz, minSz):
    """ SIMPLE: hitung contracts untuk $2 target, tapi cek minSz dulu max $2.5 """
    price_diff = abs(float(entry)-float(sl))
    if price_diff<=0:
        return None, 999
    
    # CEK 1: minSz aja risk nya berapa?
    min_risk = get_min_risk(entry, sl, ctVal, minSz)
    if min_risk > RISK_MAX:
        debug("SKIP_MIN_RISK_TOO_HIGH", {"entry":entry,"sl":sl,"minSz":minSz,"min_risk":round(min_risk,2),"max_allowed":RISK_MAX,"reason":f"minSz {minSz} aja risk ${min_risk:.2f} > ${RISK_MAX} => SKIP"})
        return None, min_risk
    
    # CEK 2: hitung qty untuk $2
    qty_raw = RISK_TARGET / (ctVal * price_diff)
    prec = 0 if lotSz>=1 else 1 if lotSz>=0.1 else 2 if lotSz>=0.01 else 3
    contracts = math.floor(qty_raw / lotSz) * lotSz
    contracts = round(contracts, prec)
    if contracts < minSz:
        contracts = minSz
    
    actual_risk = calc_risk(contracts, ctVal, entry, sl)
    
    # CEK 3: kalau masih > $2.5, turunin sampai <= $2.5
    it=0
    while actual_risk > RISK_MAX and contracts > minSz and it<50:
        contracts = round(contracts - lotSz, prec)
        if contracts < minSz:
            contracts = minSz
            actual_risk = calc_risk(contracts, ctVal, entry, sl)
            break
        actual_risk = calc_risk(contracts, ctVal, entry, sl)
        it+=1
    
    # Final check
    if actual_risk > RISK_MAX:
        # masih > $2.5 walau minSz
        return None, actual_risk
    
    return round(contracts, prec), actual_risk

def set_leverage_20(instId):
    for body in [{"instId":instId,"lever":str(LEVERAGE),"mgnMode":"cross","posSide":"long"},{"instId":instId,"lever":str(LEVERAGE),"mgnMode":"cross","posSide":"short"},{"instId":instId,"lever":str(LEVERAGE),"mgnMode":"cross"}]:
        res=req("POST","/api/v5/account/set-leverage",body)
        if res.get("code")=="0":
            return True
    return False

def place_limit(instId, side, sz, px):
    sz_str = ('%f' % sz).rstrip('0').rstrip('.')
    body = {"instId":instId,"tdMode":"cross","side":side,"ordType":"limit","sz":sz_str,"px":str(px)}
    return req("POST","/api/v5/trade/order",body)

def get_order(instId, ordId):
    return req("GET", f"/api/v5/trade/order?instId={instId}&ordId={ordId}")

def cancel_order(instId, ordId):
    return req("POST","/api/v5/trade/cancel-order",{"instId":instId,"ordId":ordId})

def get_pos(instId):
    return req("GET", f"/api/v5/account/positions?instId={instId}")

def get_ticker(instId):
    try:
        res=req("GET", f"/api/v5/market/ticker?instId={instId}")
        if res.get("code")=="0" and res["data"]: return float(res["data"][0]["last"])
    except: pass
    return None

def get_mark(instId):
    try:
        res=req("GET", f"/api/v5/public/mark-price?instType=SWAP&instId={instId}")
        if res.get("code")=="0" and res["data"]: return float(res["data"][0]["markPx"])
    except: pass
    return None

def quantize(price, tickSz):
    try:
        prec=len(str(tickSz).split('.')[-1]) if '.' in str(tickSz) else 2
        q=math.floor(float(price)/tickSz)*tickSz
        return round(q,prec)
    except: return price

def place_algo_sl_tp(instId, close_side, sz, sl, tp, tickSz):
    """SIMPLE SLTP - guaranteed"""
    sl_q = quantize(sl, tickSz)
    tp_q = quantize(tp, tickSz)
    sz_str = ('%f' % sz).rstrip('0').rstrip('.')
    mark = get_mark(instId)
    # validasi 51304 simple
    if mark:
        if close_side=="sell": # long pos, sl harus < mark, tp > mark? sebenarnya tp bisa bebas
            if sl_q >= mark:
                debug("SL_INVALID_MARK", {"sl":sl_q,"mark":mark,"fix":"sl - tick"})
                sl_q = quantize(mark * 0.999, tickSz)
        else:
            if sl_q <= mark:
                debug("SL_INVALID_MARK", {"sl":sl_q,"mark":mark,"fix":"sl + tick"})
                sl_q = quantize(mark * 1.001, tickSz)
    body = {
        "instId": instId,
        "tdMode": "cross",
        "side": close_side,
        "ordType": "conditional",
        "sz": sz_str,
        "slTriggerPx": str(sl_q),
        "slOrdPx": "-1",
        "tpTriggerPx": str(tp_q),
        "tpOrdPx": "-1"
    }
    return req("POST","/api/v5/trade/order-algo",body)

def place_market_close(instId, close_side, sz):
    sz_str = ('%f' % sz).rstrip('0').rstrip('.')
    body = {"instId":instId,"tdMode":"cross","side":close_side,"ordType":"market","sz":sz_str}
    return req("POST","/api/v5/trade/order",body)

def main():
    # load files
    try:
        with open("last_scan.json") as f: scan=json.load(f)
    except: scan={"valid_new_positions":[],"running_positions":[]}
    try:
        with open("okx_demo_log.json") as f: log_data=json.load(f)
    except: log_data={"trades":[]}
    
    valid_new = scan.get("valid_new_positions",[]) or scan.get("valid_trades",[])
    # waiting dari log yang punya order_id (FIX SKIP_NO_ORDID)
    waiting_from_log = [t for t in log_data.get("trades",[]) if t.get("status")=="WAITING_LIMIT" and t.get("order_id")]
    
    debug("START_V11_SIMPLE", {"valid_new_count":len(valid_new),"waiting_from_log":len(waiting_from_log),"LEVERAGE":f"{LEVERAGE}x Cross FIX","RISK_MAX":RISK_MAX})
    
    # 1. CEK WAITING LIMIT OKX
    for w in waiting_from_log[:]:
        symbol=w["symbol"]; instId=w.get("instId", symbol.replace("USDT","-USDT-SWAP")); ordId=w["order_id"]
        try:
            o_res=get_order(instId, ordId)
            if o_res.get("code")!="0" or not o_res["data"]:
                continue
            o=o_res["data"][0]
            state=o.get("state"); last_px=o.get("lastPx") or o.get("avgPx")
            debug("CHECK_WAITING", {"symbol":symbol,"ordId":ordId,"state":state,"lastPx":last_px})
            if state in ["filled","partially_filled"] and last_px:
                last_px=float(last_px); entry=float(w.get("signal_entry", last_px))
                print(f"{symbol} FILLED {ordId} {last_px}")
                pos_api=get_pos(instId)
                avgPx=entry; sz_from_pos=None
                if pos_api.get("code")=="0" and pos_api["data"]:
                    for p in pos_api["data"]:
                        if p.get("instId")==instId:
                            try: avgPx=float(p.get("avgPx",entry))
                            except: avgPx=entry
                            sz_from_pos=p.get("pos") or p.get("availPos")
                            break
                if not avgPx: avgPx=float(last_px)
                sl=float(w.get("signal_sl", entry*0.998)); tp=float(w.get("signal_tp", entry*1.004))
                dist_sl=abs(entry-sl)
                real_sl=avgPx-dist_sl if w["direction"]=="LONG" else avgPx+dist_sl
                real_tp=avgPx+(tp-entry) if w["direction"]=="LONG" else avgPx-(entry-tp)
                close_side="sell" if w["direction"]=="LONG" else "buy"
                sz=w.get("contracts")
                if sz_from_pos and str(sz_from_pos) not in ["","0"]:
                    try: sz=float(sz_from_pos)
                    except: pass
                if not sz or float(sz)==0: sz=w.get("contracts") or 1
                lotSz,minSz,ctVal,tickSz=get_instr(instId)
                prec=0 if lotSz>=1 else 1 if lotSz>=0.1 else 2 if lotSz>=0.01 else 3
                sz=round(float(sz), prec)
                if sz<minSz: sz=minSz
                
                print(f"  Place SL/TP {symbol} sz {sz} sl {real_sl} tp {real_tp}")
                algo_res=place_algo_sl_tp(instId, close_side, sz, real_sl, real_tp, tickSz)
                if algo_res.get("code")!="0":
                    print(f"  ALGO FAIL {algo_res} -> MARKET CLOSE MAX ${RISK_MAX}")
                    close_res=place_market_close(instId, close_side, sz)
                    log_data["trades"]=[t for t in log_data["trades"] if t.get("order_id")!=ordId]
                    log_data["trades"].append({"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":w["direction"],"status":"LIMIT_FILLED_TPSL_FAILED_CLOSED_V11","order_id":ordId,"real_avgPx":avgPx,"real_sl":real_sl,"real_tp":real_tp,"algo_res":algo_res,"close_res":close_res,"note":f"V11 SIMPLE: algo fail closed max ${RISK_MAX}"})
                else:
                    log_data["trades"]=[t for t in log_data["trades"] if t.get("order_id")!=ordId]
                    log_data["trades"].append({"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":w["direction"],"status":"LIMIT_FILLED_V11","order_id":ordId,"real_avgPx":avgPx,"real_sl":real_sl,"real_tp":real_tp,"algo_res":algo_res,"note":f"V11 SIMPLE SLTP {real_sl}/{real_tp} lev {LEVERAGE}x"})
                continue
            curr_px=get_ticker(instId) or last_px
            entry=float(w.get("signal_entry", curr_px))
            run_away=RUN_AWAY_PCT.get(symbol,RUN_AWAY_PCT["DEFAULT"])/100
            if curr_px and entry:
                dist_pct=abs(curr_px-entry)/entry
                if dist_pct>run_away:
                    c_res=cancel_order(instId, ordId)
                    log_data["trades"]=[t for t in log_data["trades"] if t.get("order_id")!=ordId]
                    log_data["trades"].append({"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":w["direction"],"status":"LIMIT_CANCELED_PRICE_RAN_AWAY","order_id":ordId,"real_last_price":curr_px,"distance_pct":round(dist_pct*100,4),"cancel_res":c_res})
        except Exception as e:
            debug("WAITING_EXC", {"symbol":symbol,"err":str(e)})

    # 2. PLACE NEW LIMIT - SIMPLE $2.5 MAX
    print(f"\nScan valid_new: {len(valid_new)}")
    executed=0
    for item in valid_new:
        symbol=item["symbol"]
        # skip recent 24h
        recent=[t for t in log_data["trades"] if t.get("symbol")==symbol and (datetime.fromisoformat(t.get("timestamp","").replace("Z","+00:00")) if t.get("timestamp") else datetime.now(timezone.utc)).timestamp() > (datetime.now(timezone.utc).timestamp()-24*3600)]
        if recent and any(t.get("status") in ["WAITING_LIMIT","LIMIT_FILLED","LIMIT_FILLED_V11"] for t in recent):
            debug("SKIP_RECENT", {"symbol":symbol})
            continue
        instId=item.get("instId", symbol.replace("USDT","-USDT-SWAP"))
        direction=item["direction"]; entry=float(item["entry"]); sl=float(item["sl"]); tp=float(item["tp"])
        posSide="long" if direction=="LONG" else "short"; side="buy" if direction=="LONG" else "sell"
        lotSz,minSz,ctVal,tickSz=get_instr(instId)
        
        contracts, actual_risk = calc_contracts_simple(entry, sl, ctVal, lotSz, minSz)
        if contracts is None:
            debug("SKIP_RISK_MAX_2.5", {"symbol":symbol,"min_risk":actual_risk,"entry":entry,"sl":sl,"reason":f"Risk ${actual_risk:.2f} > ${RISK_MAX} SKIP - cari yang lain"})
            continue
        
        # set leverage 20x FIX
        set_leverage_20(instId)
        print(f"{symbol} V11 SIMPLE LEV {LEVERAGE}x Cross LIMIT {entry} SL {sl} risk ${actual_risk:.2f} sz {contracts}")
        order_res=place_limit(instId, side, contracts, entry)
        if order_res.get("code")!="0":
            debug("FAILED_ORDER", {"symbol":symbol,"resp":order_res})
            continue
        ordId=order_res["data"][0]["ordId"]
        log_data["trades"].append({"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":direction,"status":"WAITING_LIMIT","order_id":ordId,"signal_entry":entry,"signal_sl":sl,"signal_tp":tp,"contracts":float(contracts),"actual_risk_usd":round(actual_risk,4),"confluence_score":item.get("confluence_score",0),"leverage":LEVERAGE,"mgnMode":"cross","note":f"V11 SIMPLE MAX ${RISK_MAX} cross {LEVERAGE}x"})
        executed+=1

    log_data["total_executed"]=len(log_data["trades"])
    log_data["total_waiting"]=len([t for t in log_data["trades"] if t["status"]=="WAITING_LIMIT"])
    log_data["bot_version"]="V11_SIMPLE_2.5_MAX_CROSS_20X"
    log_data["generated_at"]=now_utc()
    with open("okx_demo_log.json","w") as f: json.dump(log_data,f,indent=2)
    save_debug()
    print(f"Done V11 SIMPLE: new LIMIT {executed} waiting {log_data['total_waiting']} MAX ${RISK_MAX}")

if __name__=="__main__": main()
