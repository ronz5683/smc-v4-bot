"""
SMC OKX Demo V10.6 FIX EMPTY TP/SL - FASE 1 CRITICAL FIX
BUG FOUND 21:50 WIB: TP/SL kosong semua -> loss -13.58 > $2
ROOT CAUSE: place_algo_sl_tp gagal silent karena sz dari pos API '' atau posSide mismatch, tidak ada fallback close

FIX:
1. Ambil sz real dari /account/positions avgPx & pos, kalau gagal pakai contracts dari signal (sudah quantize)
2. Coba 3 format algo: cross+posSide, cross tanpa posSide, isolated+posSide
3. Kalau semua gagal -> MARKET CLOSE langsung biar loss max $2, log FAILED_TPSL_CLOSED
4. Log detail algo_res untuk debug
5. Quantize SL/TP price ke tickSz
6. Pastikan RISK SEARCH final tetap $2
"""
import os, json, time, hmac, base64, hashlib, requests, math
from datetime import datetime, timezone

API_KEY = os.getenv("OKX_DEMO_API_KEY") or os.getenv("OKX_API_KEY")
SECRET = os.getenv("OKX_DEMO_API_SECRET") or os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_DEMO_PASSPHRASE") or os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"
MIN_CONFLUENCE_V10 = 4.0  # Samakan dengan scanner biar gak skip 4.0-4.9
RISK_USD = 2.0
MAX_RISK_USD = 2.5

RUN_AWAY_PCT = {"BTCUSDT":0.8,"ETHUSDT":0.8,"BNBUSDT":0.9,"SOLUSDT":1.0,"LINKUSDT":1.0,"ADAUSDT":1.2,"DOGEUSDT":1.2,"AVAXUSDT":1.0,"ARBUSDT":1.5,"OPUSDT":1.5,"XRPUSDT":1.0,"MATICUSDT":1.2,"DEFAULT":1.0}
print(f"V10.6 FIX EMPTY TPSL - MIN_CONF {MIN_CONFLUENCE_V10} RISK ${RISK_USD}")

def now_utc(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace("+00:00","Z")
def get_okx_timestamp(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace("+00:00","Z")
def sign(timestamp, method, request_path, body=""):
    message = timestamp + method + request_path + body
    mac = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def request_okx(method, path, body=None, retry=0):
    timestamp = get_okx_timestamp()
    body_str = json.dumps(body) if body else ""
    headers = {"OK-ACCESS-KEY": API_KEY, "OK-ACCESS-SIGN": sign(timestamp, method, path, body_str), "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": PASSPHRASE, "Content-Type": "application/json", "x-simulated-trading": "1"}
    url = BASE_URL + path
    try:
        if method == "GET": r = requests.get(url, headers=headers, timeout=10)
        else: r = requests.post(url, headers=headers, data=body_str, timeout=10)
        j = r.json()
    except Exception as e: return {"code":"1","msg":str(e)}
    if j.get("code") in ["50112","50114"] and retry<2:
        time.sleep(1.2)
        return request_okx(method, path, body, retry+1)
    return j

def get_instruments(instId):
    res = request_okx("GET", f"/api/v5/public/instruments?instType=SWAP&instId={instId}")
    if res.get("code")=="0" and res["data"]:
        d=res["data"][0]
        return float(d["lotSz"]), float(d["minSz"]), float(d["ctVal"]), float(d.get("tickSz","0.0001"))
    return 1.0,1.0,0.01,0.0001

def calc_actual_risk(contracts, ctVal, entry, sl): return float(contracts)*float(ctVal)*abs(float(entry)-float(sl))

def calc_contracts_for_risk(entry, sl, ctVal, lotSz, minSz):
    price_diff=abs(float(entry)-float(sl))
    if price_diff<=0: return minSz, RISK_USD*10
    qty_raw=RISK_USD/(ctVal*price_diff)
    precision=0 if lotSz>=1 else 1 if lotSz>=0.1 else 2 if lotSz>=0.01 else 3
    contracts=math.floor(qty_raw/lotSz)*lotSz
    contracts=round(contracts, precision)
    if contracts<minSz: contracts=minSz
    contracts=round(contracts, precision)
    actual_risk=calc_actual_risk(contracts, ctVal, entry, sl)
    it=0
    while actual_risk>MAX_RISK_USD and contracts>minSz and it<100:
        contracts=round(contracts-lotSz, precision)
        if contracts<minSz: contracts=minSz; break
        actual_risk=calc_actual_risk(contracts, ctVal, entry, sl); it+=1
    if actual_risk>MAX_RISK_USD: print(f"  FAIL minSz {minSz} risk ${actual_risk:.2f} > ${MAX_RISK_USD}")
    while actual_risk<1.8 and it<100:
        nxt=round(contracts+lotSz, precision); nxt_risk=calc_actual_risk(nxt, ctVal, entry, sl)
        if nxt_risk<=MAX_RISK_USD: contracts=nxt; actual_risk=nxt_risk; it+=1
        else: break
    print(f"  RISK FINAL contracts {contracts} risk ${actual_risk:.2f}")
    return contracts, actual_risk

def quantize_price(price, tickSz):
    try:
        precision = len(str(tickSz).split('.')[-1]) if '.' in str(tickSz) else 0
        # floor/round to tick
        quantized = math.floor(float(price)/tickSz)*tickSz if price else price
        return round(quantized, precision)
    except:
        return price

def set_leverage_safe(instId, lever, posSide):
    # try cross long_short, cross net, isolated
    for body in [
        {"instId":instId,"lever":str(lever),"mgnMode":"cross","posSide":posSide},
        {"instId":instId,"lever":str(lever),"mgnMode":"cross"},
        {"instId":instId,"lever":str(lever),"mgnMode":"cross","posSide":posSide.lower()},
        {"instId":instId,"lever":str(lever),"mgnMode":"isolated","posSide":posSide},
    ]:
        try:
            res=request_okx("POST","/api/v5/account/set-leverage",body)
            if res.get("code")=="0": return res
        except: pass
    return {"code":"1","msg":"lev fail"}

def set_leverage_max(instId, posSide):
    for lev in [20,15,10,5]:
        res=set_leverage_safe(instId, lev, posSide)
        if res.get("code")=="0": return res, lev
    return set_leverage_safe(instId, 10, posSide),10

def place_limit_order(instId, side, sz, px, posSide):
    sz_str=str(sz).rstrip('0').rstrip('.') if '.' in str(sz) else str(sz)
    for body in [
        {"instId":instId,"tdMode":"cross","side":side,"ordType":"limit","sz":sz_str,"px":str(px),"posSide":posSide},
        {"instId":instId,"tdMode":"cross","side":side,"ordType":"limit","sz":sz_str,"px":str(px)},
    ]:
        res=request_okx("POST","/api/v5/trade/order",body)
        if res.get("code")=="0": return res
    return res

def get_order(instId, ordId): return request_okx("GET", f"/api/v5/trade/order?instId={instId}&ordId={ordId}")
def cancel_order(instId, ordId): return request_okx("POST","/api/v5/trade/cancel-order",{"instId":instId,"ordId":ordId})
def get_positions(instId): return request_okx("GET", f"/api/v5/account/positions?instId={instId}")
def get_ticker_price(instId):
    try:
        res=request_okx("GET", f"/api/v5/market/ticker?instId={instId}")
        if res.get("code")=="0" and res["data"]: return float(res["data"][0]["last"])
    except: pass
    return None

def place_market_close(instId, side, sz, posSide):
    sz_str=str(sz)
    for body in [
        {"instId":instId,"tdMode":"cross","side":side,"ordType":"market","sz":sz_str,"posSide":posSide},
        {"instId":instId,"tdMode":"cross","side":side,"ordType":"market","sz":sz_str},
    ]:
        res=request_okx("POST","/api/v5/trade/order",body)
        if res.get("code")=="0": return res
    return res

def place_algo_sl_tp(instId, side, sz, slPx, tpPx, posSide, tickSz):
    # quantize prices
    slPx = quantize_price(slPx, tickSz)
    tpPx = quantize_price(tpPx, tickSz)
    sz_str = str(sz)
    # OKX algo: need sz as pos size, slTriggerPx, tpTriggerPx, mark type
    attempts = [
        {"instId":instId,"tdMode":"cross","side":side,"ordType":"conditional","sz":sz_str,"slTriggerPx":str(slPx),"slTriggerPxType":"mark","tpTriggerPx":str(tpPx),"tpTriggerPxType":"mark","slOrdPx":"-1","tpOrdPx":"-1","posSide":posSide},
        {"instId":instId,"tdMode":"cross","side":side,"ordType":"conditional","sz":sz_str,"slTriggerPx":str(slPx),"slTriggerPxType":"mark","tpTriggerPx":str(tpPx),"tpTriggerPxType":"mark","slOrdPx":"-1","tpOrdPx":"-1"},
        {"instId":instId,"tdMode":"isolated","side":side,"ordType":"conditional","sz":sz_str,"slTriggerPx":str(slPx),"slTriggerPxType":"mark","tpTriggerPx":str(tpPx),"tpTriggerPxType":"mark","slOrdPx":"-1","tpOrdPx":"-1","posSide":posSide},
        # fallback hanya SL saja kalau TP bermasalah
        {"instId":instId,"tdMode":"cross","side":side,"ordType":"conditional","sz":sz_str,"slTriggerPx":str(slPx),"slTriggerPxType":"mark","slOrdPx":"-1","posSide":posSide},
    ]
    last=None
    for body in attempts:
        res=request_okx("POST","/api/v5/trade/order-algo",body)
        print(f"    ALGO attempt {body['tdMode']} posSide={body.get('posSide')} -> code {res.get('code')} msg {res.get('msg')}")
        if res.get("code")=="0": return res
        last=res
    return last

def load_last_scan():
    try:
        with open("last_scan.json") as f: return json.load(f)
    except: return {"valid_new_positions":[],"running_positions":[]}
def load_okx_log():
    try:
        with open("okx_demo_log.json") as f: return json.load(f)
    except: return {"generated_at":now_utc(),"bot_version":"V10.6","trades":[]}
def save_log(data):
    data["generated_at"]=now_utc()
    with open("okx_demo_log.json","w") as f: json.dump(data,f,indent=2)
def is_recently_executed(trades,symbol,hours=24):
    now=datetime.now(timezone.utc)
    for t in trades[-50:]:
        if t["symbol"]==symbol:
            try:
                ts=datetime.fromisoformat(t["timestamp"].replace("Z","+00:00"))
                if (now-ts).total_seconds()<hours*3600 and t["status"] in ["WAITING_LIMIT","LIMIT_FILLED","VERIFIED"]: return True
            except: pass
    return False

def main():
    scan=load_last_scan()
    valid_new=scan.get("valid_new_positions",[]) or scan.get("valid_trades",[])
    waiting_in_scan=[p for p in scan.get("running_positions",[]) if p.get("order_status")=="WAITING_LIMIT"] + [p for p in scan.get("valid_new_positions",[]) if p.get("order_status")=="WAITING_LIMIT"]
    seen=set(); uniq_waiting=[]
    for w in waiting_in_scan:
        k=w.get("symbol")+str(w.get("entry"))
        if k not in seen: seen.add(k); uniq_waiting.append(w)
    log_data=load_okx_log()
    log_data["bot_version"]="V10.6_FIX_EMPTY_TPSL"
    # CEK WAITING LIMIT
    for pos in uniq_waiting:
        symbol=pos["symbol"]; instId=symbol.replace("USDT","-USDT-SWAP")
        try:
            ordId=pos.get("order_id") or pos.get("orderId")
            if not ordId: continue
            o_res=get_order(instId, ordId)
            if o_res.get("code")!="0" or not o_res["data"]: continue
            o=o_res["data"][0]
            state=o.get("state"); last_px=o.get("lastPx") or o.get("avgPx") or o.get("fillPx")
            if state in ["filled","partially_filled"] and last_px:
                last_px=float(last_px)
                entry=float(pos["entry"])
                print(f"{symbol} FILLED {ordId} lastPx {last_px}")
                pos_api=get_positions(instId)
                avgPx=entry; sz_from_pos=None
                if pos_api.get("code")=="0" and pos_api["data"]:
                    for p in pos_api["data"]:
                        if p.get("instId")==instId:
                            try: avgPx=float(p.get("avgPx",entry))
                            except: avgPx=entry
                            sz_from_pos=p.get("pos") or p.get("availPos")
                            break
                if not avgPx: avgPx=float(last_px or entry)
                sl=float(pos["sl"]); tp=float(pos["tp"])
                dist_sl=abs(entry-sl)
                real_sl=avgPx-dist_sl if pos["direction"]=="LONG" else avgPx+dist_sl
                real_tp=avgPx+(tp-entry) if pos["direction"]=="LONG" else avgPx-(entry-tp)
                close_side="sell" if pos["direction"]=="LONG" else "buy"
                posSide="long" if pos["direction"]=="LONG" else "short"
                # sz: prioritaskan dari API, kalau kosong pakai contracts signal
                sz=pos.get("contracts")
                if sz_from_pos and str(sz_from_pos) not in ["","0"]:
                    try: sz=float(sz_from_pos)
                    except: pass
                if not sz or float(sz)==0: sz=pos.get("contracts") or 1
                lotSz,minSz,ctVal,tickSz=get_instruments(instId)
                # quantize sz to lot
                prec=0 if lotSz>=1 else 1 if lotSz>=0.1 else 2 if lotSz>=0.01 else 3
                sz=round(float(sz), prec)
                if sz<minSz: sz=minSz
                print(f"  Placing SL/TP sz {sz} real_sl {real_sl} real_tp {real_tp}")
                algo_res=place_algo_sl_tp(instId, close_side, sz, real_sl, real_tp, posSide, tickSz)
                if algo_res.get("code")!="0":
                    # CRITICAL: kalau SL gagal, close market biar gak -13 loss
                    print(f"  ALGO FAILED {algo_res} -> MARKET CLOSE to prevent -13 loss")
                    close_res=place_market_close(instId, close_side, sz, posSide)
                    trade={"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":pos["direction"],"status":"LIMIT_FILLED_TPSL_FAILED_CLOSED","order_id":ordId,"real_avgPx":avgPx,"real_sl":real_sl,"real_tp":real_tp,"algo_res":algo_res,"close_res":close_res,"note":"V10.6 FIX: TPSL empty -> closed market to enforce $2 max"}
                else:
                    trade={"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":pos["direction"],"status":"LIMIT_FILLED","order_id":ordId,"real_avgPx":avgPx,"real_sl":real_sl,"real_tp":real_tp,"algo_res":algo_res,"note":f"V10.6 FIXED SLTP MARK {real_sl}/{real_tp}"}
                log_data["trades"].append(trade)
                continue
            curr_px=get_ticker_price(instId) or last_px
            run_away_thresh=RUN_AWAY_PCT.get(symbol,RUN_AWAY_PCT["DEFAULT"])/100
            dist_pct=abs((curr_px or entry)-entry)/entry if entry else 0
            if dist_pct>run_away_thresh:
                c_res=cancel_order(instId, ordId)
                trade={"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":pos["direction"],"status":"LIMIT_CANCELED_PRICE_RAN_AWAY","order_id":ordId,"real_last_price":curr_px,"distance_pct":round(dist_pct*100,4),"cancel_res":c_res}
                log_data["trades"].append(trade)
        except Exception as e:
            print(f"{symbol} waiting error {e}")

    print(f"\nScan valid_new: {len(valid_new)} MIN_CONF {MIN_CONFLUENCE_V10}")
    executed=0
    for item in valid_new:
        symbol=item["symbol"]; confluence=float(item.get("confluence_score",0))
        if confluence<MIN_CONFLUENCE_V10: continue
        if is_recently_executed(log_data["trades"], symbol, 24): continue
        instId=item.get("instId", symbol.replace("USDT","-USDT-SWAP"))
        direction=item["direction"]; entry=float(item["entry"]); sl=float(item["sl"]); tp=float(item["tp"])
        posSide="long" if direction=="LONG" else "short"; side="buy" if direction=="LONG" else "sell"
        lotSz,minSz,ctVal,tickSz=get_instruments(instId)
        contracts, actual_risk=calc_contracts_for_risk(entry, sl, ctVal, lotSz, minSz)
        if actual_risk>MAX_RISK_USD:
            log_data["trades"].append({"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":direction,"status":"SKIP_RISK_TOO_HIGH","actual_risk_usd":round(actual_risk,4)})
            continue
        sz=contracts
        lev_res,lev_used=set_leverage_max(instId, posSide)
        print(f"{symbol} LEV {lev_used}x {posSide} LIMIT {entry} risk ${actual_risk:.2f} sz {sz}")
        order_res=place_limit_order(instId, side, sz, entry, posSide)
        if order_res.get("code")!="0":
            log_data["trades"].append({"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":direction,"status":"FAILED_ORDER","reason":str(order_res)})
            continue
        ordId=order_res["data"][0]["ordId"]
        log_data["trades"].append({"timestamp":now_utc(),"symbol":symbol,"instId":instId,"direction":direction,"status":"WAITING_LIMIT","order_id":ordId,"signal_entry":entry,"signal_sl":sl,"signal_tp":tp,"contracts":float(sz),"actual_risk_usd":round(actual_risk,4),"confluence_score":confluence})
        executed+=1
    log_data["total_executed"]=len(log_data["trades"])
    log_data["total_waiting"]=len([t for t in log_data["trades"] if t["status"]=="WAITING_LIMIT"])
    save_log(log_data)
    print(f"Done V10.6: new LIMIT {executed} waiting {log_data['total_waiting']}")

if __name__=="__main__": main()
