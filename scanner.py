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

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

def send_telegram_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=10)
    except Exception as e: print(f"TG text error: {e}")

# --- Получаем EMA для ВСЕХ таймфреймов ---
def get_all_emas(sym, exchange):
    emas = {}
    timeframes = {
        "MEXC": ["Min15", "Min60", "Hour4", "Day1", "Week1", "Month1"],
        "Bitget": ["15m", "1H", "4H", "1D", "1week", "1M"]
    }
    
    for tf in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=5).json()
                if res.get('success') and 'close' in res['data']:
                    df = pd.DataFrame({'close': res['data']['close']})
                else: continue
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=200", headers=HEADERS, timeout=5).json()
                if res.get('code') == '00000' and res.get('data'):
                    df = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                    df['close'] = df['close'].astype(float)
                else: continue
            
            # Считаем EMA 7, 14, 28 для текущего ТФ
            tf_name = tf.upper().replace('MIN', '').replace('HOUR', 'H').replace('DAY', 'D').replace('WEEK', 'W').replace('MONTH', 'M')
            for span in [7, 14, 28]:
                if len(df) >= span:
                    emas[f"{tf_name} EMA {span}"] = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
            
            time.sleep(0.1) # Пауза, чтобы не забанили
        except: pass
    return emas

# --- Рисуем график 15м с горизонтальной линией цели ---
def draw_chart(sym, exchange, target_ema_val, target_name):
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
        mpf.plot(df_15m, type='candle', style='yahoo', volume=True, savefig=filename, 
                 title=f"{sym} 15m | Target: {target_name}", tight_layout=True, hlines=hlines)
        return filename
    except: return None

def get_all_coins():
    coins = []
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            for c in res['data']:
                if c['symbol'].endswith('_USDT') and float(c.get('amount24', 0)) > 5000000:
                    coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice'])})
    except: pass
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                if float(c.get('usdtVolume', 0)) > 5000000:
                    coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr'])})
    except: pass
    return coins

# --- Проверка быстрого пампа на 15м ---
def check_fast_pump(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=5).json()
            if res.get('success') and len(res['data']['close']) >= 2:
                current_price = float(res['data']['close'][-1])
                open_price = float(res['data']['open'][-1])
                change = ((current_price - open_price) / open_price) * 100
                if change >= 3.0: return change # Порог 3% за 15 мин (снижен для частоты)
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=2", headers=HEADERS, timeout=5).json()
            if res.get('code') == '00000' and len(res['data']) >= 2:
                current_price = float(res['data'][0][4])
                open_price = float(res['data'][0][1])
                change = ((current_price - open_price) / open_price) * 100
                if change >= 3.0: return change
    except: pass
    return None

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    print("Сканируем пампы и ищем стены на ВСЕХ таймфреймах...")
    
    all_coins = get_all_coins()
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    found = 0
    # Ограничиваем проверку 40 монетами за раз, чтобы не привысить лимиты API
    for coin in list(unique_coins)[:40]: 
        if found >= 3: break
        
        sym, ex, current_price = coin['sym'], coin['ex'], coin['price']
        
        pump_change = check_fast_pump(sym, ex)
        if not pump_change: continue

        # Получаем EMA всех таймфреймов
        all_emas = get_all_emas(sym, ex)
        if not all_emas: continue

        # Ищем ближайшую линию сверху
        best_ema_name, best_ema_val, min_diff_pct = None, 0, 999
        for name, val in all_emas.items():
            if val > current_price:
                diff_pct = ((val - current_price) / current_price) * 100
                if diff_pct < min_diff_pct:
                    min_diff_pct = diff_pct
                    best_ema_name = name
                    best_ema_val = val

        # Если цена подошла близко к любой из стен (до 3%)
        if best_ema_name and 0.1 <= min_diff_pct <= 3.0:
            chart_file = draw_chart(sym, ex, best_ema_val, best_ema_name)
            if chart_file:
                text = f"🚀 <b>ПАМП 15м (+{pump_change:.2f}%) + ЦЕЛЬ</b>\n"
                text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
                text += f"💰 Цена: <code>{current_price}</code>\n\n"
                text += f"🎯 <b>Ближайшая стена: {best_ema_name}</b>\n"
                text += f"├ Уровень (Лимитка): <code>{best_ema_val:.4f}</code>\n"
                text += f"└ Осталось: <b><code>{min_diff_pct:.2f}%</code></b>\n\n"
                text += f"⚡️ <i>Ставь лимитку на этот уровень!</i>"

                send_telegram_photo(chart_file, text)
                os.remove(chart_file)
                found += 1
                time.sleep(1)

    if found == 0:
        print("Нет подходящих сигналов.")

if __name__ == "__main__":
    main()
