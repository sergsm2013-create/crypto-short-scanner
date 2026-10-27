import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {'User-Agent': 'Mozilla/5.0'}

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

# --- Считаем EMA и RSI на 1H графике ---
def get_1h_indicators(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min60", headers=HEADERS, timeout=5).json()
            if not res.get('success'): return None
            data = res['data']
            df = pd.DataFrame({'time': pd.to_datetime(data['time'], unit='s'), 'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=1H&limit=100", headers=HEADERS, timeout=5).json()
            if res.get('code') != '00000': return None
            data = res['data']
            df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df = df.iloc[::-1]
            df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
            for col in ['open', 'high', 'low', 'close']: df[col] = df[col].astype(float)

        if len(df) < 50: return None
        
        # Считаем EMA 7 и 14
        df['ema7'] = df['close'].ewm(span=7, adjust=False).mean()
        df['ema14'] = df['close'].ewm(span=14, adjust=False).mean()
        
        # Считаем RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df.dropna()
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

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    
    start_time = time.time()
    all_coins = get_all_coins()
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    found = 0
    for coin in list(unique_coins):
        if found >= 3: break
        if time.time() - start_time > 240: break
            
        sym, ex, current_price = coin['sym'], coin['ex'], coin['price']
        
        df = get_1h_indicators(sym, ex)
        if df is None or len(df) < 2: continue
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 1. Вычисляем отклонение от EMA 7
        deviation_pct = ((last['close'] - last['ema7']) / last['ema7']) * 100
        
        # 2. Условия для "Параболического истощения"
        is_overextended = deviation_pct > 15.0  # Цена выше EMA 7 на 15%+ (как на QUSDT)
        is_overbought = last['rsi'] > 80.0      # RSI в зоне экстремального перегрева
        is_red_candle = last['close'] < last['open'] # Текущая свеча красная (отторжение)
        
        # Если совпали все условия
        if is_overextended and is_overbought and is_red_candle:
            
            # Рисуем график 1H
            filename = f"{sym}_peak.png"
            df_plot = df.copy()
            df_plot.set_index('time', inplace=True)
            
            # Линия стопа на максимуме свечи
            peak_high = prev['high']
            hlines = dict(hlines=[peak_high], colors=['red'], linestyle='--', linewidths=2.0)
            mpf.plot(df_plot.tail(30), type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 1H | PARABOLIC EXHAUSTION", tight_layout=True, hlines=hlines)
            
            base_coin = sym.split('_')[0].replace('USDT', '')
            
            text = f"🔥 <b>ПАРАБОЛИЧЕСКИЙ ПАМП (ШОРТ)</b>\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{base_coin}</b>\n"
            text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
            text += f"📊 <b>Аномалия 1H:</b>\n"
            text += f"├ Отклонение от EMA 7: <b>+{deviation_pct:.1f}%</b> 🔥\n"
            text += f"└ RSI: <b>{last['rsi']:.1f}</b> 🔥 (Экстремальный перегрев)\n\n"
            
            text += f"🎯 <b>Вход:</b> По рынку (или лимитка на <code>{last['high']:.4f}</code>)\n"
            text += f"🛑 <b>Стоп-лосс:</b> <code>{peak_high * 1.01:.4f}</code> (Выше пика)\n"
            text += f"🟢 <b>Тейк-профит:</b> <code>{last['ema14']:.4f}</code> (Возврат к EMA 14)\n\n"
            text += f"⚡️ <i>Цена оторвалась от средней. Ждем обвала!</i>"

            send_telegram_photo(filename, text)
            os.remove(filename)
            found += 1
            time.sleep(1)

if __name__ == "__main__":
    main()
