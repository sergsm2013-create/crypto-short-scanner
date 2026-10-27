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

# --- РАСЧЕТ ИНДИКАТОРОВ ---
def calculate_indicators(df):
    # Bollinger Bands (20, 2)
    df['ma20'] = df['close'].rolling(window=20).mean()
    df['std'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['ma20'] + (df['std'] * 2)
    
    # RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # MACD (12, 26, 9)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    return df

# --- ПОИСК СТЕН НА СТАРШИХ ТФ ---
def get_htf_emas(sym, exchange, current_price):
    emas = {}
    timeframes = {"MEXC": [("Hour4", "4H"), ("Day1", "1D"), ("Week1", "1W"), ("Month1", "1M")], 
                  "Bitget": [("4H", "4H"), ("1D", "1D"), ("1week", "1W"), ("1M", "1M")]}
    for tf, tf_name in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=5).json()
                if res.get('success') and 'close' in res['data']: df = pd.DataFrame({'close': res['data']['close']})
                else: continue
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=200", headers=HEADERS, timeout=5).json()
                if res.get('code') == '00000' and res.get('data'):
                    df = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                    df['close'] = df['close'].astype(float)
                else: continue

            for span in [7, 14, 28, 50, 100, 200]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    # Нам нужны только стены СВЕРХУ (сопротивление для шорта)
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 5.0: # Не дальше 5%
                            emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

def get_15m_data(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=3).json()
            if res.get('success'):
                data = res['data']
                return pd.DataFrame({'time': pd.to_datetime(data['time'], unit='s'), 'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close'], 'volume': data['vol']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=50", headers=HEADERS, timeout=3).json()
            if res.get('code') == '00000':
                data = res['data']
                df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df = df.iloc[::-1]
                df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = df[col].astype(float)
                return df
    except: pass
    return None

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
        
        df = get_15m_data(sym, ex)
        if df is None or len(df) < 30: continue
        
        # 1. Считаем индикаторы
        df = calculate_indicators(df)
        last = df.iloc[-1]
        
        # 2. Проверяем памп (рост от 3% за 15 минут)
        open_p = last['open']
        change_pct = ((current_price - open_p) / open_p) * 100
        if change_pct < 3.0: continue
        
        # 3. ПРОВЕРКА ИНДИКАТОРОВ (Фильтр Шорта)
        # BB: Цена должна быть выше верхней полосы
        is_above_bb = current_price >= last['bb_upper']
        # RSI: Должен быть выше 70 (перекупленность)
        is_overbought = last['rsi'] > 70
        # MACD: Гистограмма положительная (импульс вверх), но начинает падать
        is_macd_exhausting = last['macd_hist'] > 0 and last['macd_hist'] < df['macd_hist'].iloc[-2]
        
        # Если хотя бы 2 из 3 индикаторов подтверждают перегрев
        if not (is_above_bb and is_overbought):
            continue 

        # 4. Ищем стену (EMA старших ТФ)
        wall = get_htf_emas(sym, ex, current_price)
        if not wall: continue
        
        best_wall_name, best_wall_data = min(wall.items(), key=lambda x: x[1]['diff'])

        # 5. Рисуем график
        chart_file = f"{sym}_chart.png"
        df_plot = df.copy()
        df_plot.set_index('time', inplace=True)
        
        # Добавляем на график линии Боллинджера и EMA
        apds = [
            mpf.make_addplot(df_plot['bb_upper'], color='purple', width=1.5),
            mpf.make_addplot(df_plot['ma20'], color='gray', width=1.0)
        ]
        
        hlines = dict(hlines=[best_wall_data['val']], colors=['red'], linestyle='-', linewidths=2.0)
        mpf.plot(df_plot.tail(30), type='candle', style='yahoo', volume=True, addplot=apds, savefig=chart_file, title=f"{sym} 15m | SHORT Setup", tight_layout=True, hlines=hlines)
        
        if chart_file:
            base_coin = sym.split('_')[0].replace('USDT', '')
            
            stop_loss = best_wall_data['val'] * 1.015
            tp = best_wall_data['val'] * 0.95

            text = f"🔴 <b>СИГНАЛ: ШОРТ (Индикаторы + Стена)</b>\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{base_coin}</b>\n"
            text += f"📊 Памп: <b>{change_pct:+.2f}%</b>\n"
            text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
            
            # Выводим данные индикаторов
            text += f"📉 <b>Индикаторы (15м):</b>\n"
            text += f"├ RSI: <b>{last['rsi']:.1f}</b> {'🔥' if is_overbought else ''}\n"
            text += f"├ BB Upper: <b>{last['bb_upper']:.4f}</b> {'🔥' if is_above_bb else ''}\n"
            text += f"└ MACD Hist: <b>{last['macd_hist']:.4f}</b>\n\n"
            
            text += f"🧱 <b>Стена (Цель):</b> {best_wall_name}\n"
            text += f"├ <b>Лимитка: {best_wall_data['val']:.4f}</b>\n"
            text += f"└ Осталось: <b>{best_wall_data['diff']:.2f}%</b>\n\n"
            
            text += f"🛑 <b>Стоп-лосс:</b> <code>{stop_loss:.4f}</code>\n"
            text += f"🟢 <b>Тейк-профит:</b> <code>{tp:.4f}</code>\n\n"
            text += f"⚡️ <i>Перегрев + Стена = Идеальный шорт!</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

if __name__ == "__main__":
    main()
