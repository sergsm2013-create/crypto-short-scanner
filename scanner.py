import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

def send_telegram_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=10)
    except Exception as e: print(f"TG text error: {e}")

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

def get_weekly_emas(sym, exchange):
    try:
        df_1w = None
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Week1", headers=HEADERS, timeout=10).json()
            if res.get('success'): df_1w = pd.DataFrame({'close': res['data']['close']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=1week&limit=200", headers=HEADERS, timeout=10).json()
            if res.get('code') == '00000':
                df_1w = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_1w['close'] = df_1w['close'].astype(float)

        if df_1w is None or len(df_1w) < 28: return None

        return {
            7: df_1w['close'].ewm(span=7, adjust=False).mean().iloc[-1],
            14: df_1w['close'].ewm(span=14, adjust=False).mean().iloc[-1],
            28: df_1w['close'].ewm(span=28, adjust=False).mean().iloc[-1]
        }
    except: return None

def draw_chart(sym, exchange, target_ema_val):
    try:
        filename = f"{sym}_chart.png"
        df_15m = None
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=10).json()
            if res.get('success'):
                data = res['data']
                df_15m = pd.DataFrame({'time': pd.to_datetime(data['time'], unit='s'), 'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close'], 'volume': data['vol']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=50", headers=HEADERS, timeout=10).json()
            if res.get('code') == '00000':
                data = res['data']
                df_15m = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_15m = df_15m.iloc[::-1]
                df_15m['time'] = pd.to_datetime(df_15m['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df_15m[col] = df_15m[col].astype(float)

        if df_15m is None or df_15m.empty: return None
        df_15m.set_index('time', inplace=True)
        hlines = dict(hlines=[target_ema_val], colors=['green'], linestyle='-', linewidths=2.0)
        mpf.plot(df_15m, type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m | Target: {target_ema_val:.4f}", tight_layout=True, hlines=hlines)
        return filename
    except: return None

# 🔥 ОБНОВЛЕННАЯ ФУНКЦИЯ СБОРА (ЛИКВИДНОСТЬ + ВОЛАТИЛЬНОСТЬ) 🔥
def get_all_coins():
    coins = []
    
    HIGH_LIQ = 50_000_000  # $50 млн
    MID_LIQ = 10_000_000   # $10 млн
    MIN_VOLATILITY = 8.0   # Волатильность минимум 8% за 24ч (High-Low)

    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            for c in res['data']:
                vol = float(c.get('amount24', 0))
                high = float(c.get('high24h', 0))
                low = float(c.get('low24h', 0))
                
                if c['symbol'].endswith('_USDT') and vol > MID_LIQ and low > 0:
                    volatility = ((high - low) / low) * 100
                    if volatility > MIN_VOLATILITY:
                        liq_type = "HIGH" if vol > HIGH_LIQ else "MID"
                        coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice']), 'liq': liq_type, 'volat': volatility})
    except: pass

    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                vol = float(c.get('usdtVolume', 0))
                high = float(c.get('high24h', 0))
                low = float(c.get('low24h', 0))
                
                if vol > MID_LIQ and low > 0:
                    volatility = ((high - low) / low) * 100
                    if volatility > MIN_VOLATILITY:
                        liq_type = "HIGH" if vol > HIGH_LIQ else "MID"
                        coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr']), 'liq': liq_type, 'volat': volatility})
    except: pass

    return coins

def check_spike(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=5).json()
            if res.get('success') and len(res['data']['close']) >= 2:
                change = ((float(res['data']['close'][-1]) - float(res['data']['close'][-2])) / float(res['data']['close'][-2])) * 100
                if change > 2.0: return change
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=2", headers=HEADERS, timeout=5).json()
            if res.get('code') == '00000' and len(res['data']) >= 2:
                change = ((float(res['data'][0][4]) - float(res['data'][0][1])) / float(res['data'][0][1])) * 100
                if change > 2.0: return change
    except: pass
    return None

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    print("Сканируем ликвидные и волатильные монеты...")
    
    all_coins = get_all_coins()
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    found = 0
    for coin in list(unique_coins):
        if found >= 3: break
        
        sym, ex, current_price, liq, volat = coin['sym'], coin['ex'], coin['price'], coin['liq'], coin['volat']
        
        spike_change = check_spike(sym, ex)
        if not spike_change: continue

        emas = get_weekly_emas(sym, ex)
        if not emas: continue

        best_ema_name, best_ema_val, min_diff_pct = None, 0, 999
        for span, val in emas.items():
            if val > current_price:
                diff_pct = ((val - current_price) / current_price) * 100
                if diff_pct < min_diff_pct:
                    min_diff_pct = diff_pct
                    best_ema_name = span
                    best_ema_val = val

        if best_ema_name and 0.5 <= min_diff_pct <= 5.0:
            chart_file = draw_chart(sym, ex, best_ema_val)
            if chart_file:
                liq_badge = "🟢 HIGH" if liq == "HIGH" else "🟡 MID"
                
                text = f"🚀 <b>АНОМАЛИЯ (ПАМП +{spike_change:.2f}%)</b>\n"
                text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
                text += f"💧 Ликвидность: <b>{liq_badge} LIQ</b>\n"
                text += f"🔥 Волатильность (24ч): <b>{volat:.1f}%</b>\n"
                text += f"💰 Цена: <code>{current_price}</code>\n\n"
                text += f"🎯 <b>Летит прямо в EMA {best_ema_name} (1W)!</b>\n"
                text += f"├ Уровень (Лимитка): <code>{best_ema_val:.4f}</code>\n"
                text += f"└ Осталось: <b><code>{min_diff_pct:.2f}%</code></b>\n\n"
                text += f"⚡️ <i>Срочно ставь лимитку на шорт!</i>"

                send_telegram_photo(chart_file, text)
                os.remove(chart_file)
                found += 1
                time.sleep(1)

    if found == 0:
        print("Нет подходящих аномалий.")

if __name__ == "__main__":
    main()
