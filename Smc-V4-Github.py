"""
SMC V4 GitHub Version - Single Scan (bukan infinite loop)
Dijalankan GitHub Actions tiap 15 menit
File ini yang ditaruh di repo sebagai smc_v4_github.py
"""

import requests
import pandas as pd
import csv
import json
import os
from datetime import datetime

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
CSV_FILE = "smc_v4_live_trades.csv"
JSON_FILE = "last_scan.json"
INTERVAL = "15m"
LIMIT = 100
MAX_DISTANCE_PCT = 0.3

def get_klines(symbol):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={INTERVAL}&limit={LIMIT}"
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        df = pd.DataFrame(data, columns=['open_time','open','high','low','close','vol','close_time','qav','trades','tbba','tbqa','ignore'])
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['open'] = df['open'].astype(float)
        df['low'] = df['low'].astype(float)
        return df
    except Exception as e:
        print(f"Error {symbol}: {e}")
        return None

def detect_and_check(symbol, df):
    if df is None or len(df) < 50:
        return None
    
    last = df.iloc[-20:]
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    
    recent_high = last['high'].max()
    recent_low = last['low'].min()
    curr_price = curr['close']
    
    # Simplified detection
    sweep_low = curr['low'] < recent_low * 0.998 and curr['close'] > recent_low
    sweep_high = curr['high'] > recent_high * 1.002 and curr['close'] < recent_high
    choch_bull = curr['close'] > last.iloc[-10:-1]['high'].max()
    choch_bear = curr['close'] < last.iloc[-10:-1]['low'].min()
    
    setup = None
    direction = None
    zone = None
    
    if sweep_low and choch_bull:
        setup = "Sweep + ChoCh + Breaker"
        direction = "LONG"
        zone = recent_low
    elif sweep_high and choch_bear:
        setup = "Sweep + ChoCh + Breaker"
        direction = "SHORT"
        zone = recent_high
    else:
        return None
    
    distance_pct = abs(curr_price - zone) / zone * 100
    if distance_pct > MAX_DISTANCE_PCT:
        return {
            "symbol": symbol,
            "status": "WATCHLIST",
            "distance": distance_pct,
            "reason": f"Jarak {distance_pct:.2f}% >0.3% - Menunggu pullback"
        }
    
    # Reaction check
    bullish_engulf = curr['close'] > curr['open'] and prev['close'] < prev['open'] and curr['close'] > prev['open']
    bearish_engulf = curr['close'] < curr['open'] and prev['close'] > prev['open'] and curr['close'] < prev['open']
    hl = curr['low'] > prev2['low']
    lh = curr['high'] < prev2['high']
    
    is_valid = (direction == "LONG" and bullish_engulf and hl) or (direction == "SHORT" and bearish_engulf and lh)
    
    if not is_valid:
        return {
            "symbol": symbol,
            "status": "WATCHLIST",
            "distance": distance_pct,
            "reason": "Sudah di zona tapi belum ada HL+Engulfing"
        }
    
    # VALID!
    return {
        "symbol": symbol,
        "status": "VALID",
        "direction": direction,
        "setup": setup,
        "zone": zone,
        "entry": curr_price,
        "distance": distance_pct,
        "reason": f"HL+Engulfing OK - Dist {distance_pct:.3f}%",
        "confidence": 9 if distance_pct < 0.15 else 8
    }

def main():
    print(f"=== SMC V4 Scan {datetime.utcnow()} UTC ===")
    results = []
    valid_signals = []
    
    for symbol in SYMBOLS:
        df = get_klines(symbol)
        res = detect_and_check(symbol, df)
        if res:
            results.append(res)
            if res['status'] == 'VALID':
                valid_signals.append(res)
                print(f"✅ VALID: {res}")
            else:
                print(f"👀 WATCH: {res}")
    
    # Save last scan json for dashboard
    scan_data = {
        "last_scan_utc": datetime.utcnow().isoformat(),
        "results": results,
        "valid_count": len(valid_signals)
    }
    with open(JSON_FILE, 'w') as f:
        json.dump(scan_data, f, indent=2)
    
    # Append valid signals to CSV (avoid duplicate within 1 hour)
    existing = set()
    if os.path.isfile(CSV_FILE):
        try:
            df_old = pd.read_csv(CSV_FILE)
            # Create key to avoid duplicate
            for _, row in df_old.tail(20).iterrows():
                key = f"{row['Symbol']}_{row['Direction']}_{row['Timestamp'][:13]}"  # same hour
                existing.add(key)
        except:
            pass
    
    for sig in valid_signals:
        key = f"{sig['symbol']}_{sig['direction']}_{datetime.utcnow().strftime('%Y-%m-%d %H')}"
        if key in existing:
            print(f"Skip duplicate {key}")
            continue
        
        row = {
            "Timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") + " UTC",
            "Symbol": sig['symbol'],
            "Direction": sig['direction'],
            "Setup": sig['setup'],
            "Zone": f"{sig['zone']:.2f}",
            "Entry": f"{sig['entry']:.2f}",
            "SL": f"{sig['zone']*0.995 if sig['direction']=='LONG' else sig['zone']*1.005:.2f}",
            "TP1": f"{sig['entry']*1.015 if sig['direction']=='LONG' else sig['entry']*0.985:.2f}",
            "TP2": f"{sig['entry']*1.028 if sig['direction']=='LONG' else sig['entry']*0.972:.2f}",
            "TP3": f"{sig['entry']*1.042 if sig['direction']=='LONG' else sig['entry']*0.958:.2f}",
            "Status": "VALID - LIMIT WAITING",
            "PnL": "0R",
            "Confidence": sig['confidence'],
            "Reason": sig['reason'],
            "HoldTime": "0h",
            "Distance": f"{sig['distance']:.3f}%"
        }
        
        file_exists = os.path.isfile(CSV_FILE)
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
        print(f"Written to CSV: {sig['symbol']}")

    print(f"Done. Valid: {len(valid_signals)}")

if __name__ == "__main__":
    main()
