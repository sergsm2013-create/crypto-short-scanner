import os
import requests
import pandas as pd
import mplfinance as mpf
import yfinance as yf
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

def send_telegram_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=10)
    except Exception as e: print(f"TG text error: {e}")

# ==========================================
# БЛОК ДЛЯ ЗОЛОТА (XAUUSD)
# ==========================================
def check_gold():
    try:
        # Загружаем данные по фьючерсу на золото (GC=F)
        ticker = yf.Ticker("GC=F")
        
        # 1. Получаем 15-минутные данные за 2 дня
        df_15m = ticker.history(period="2d", interval="15m")
        if df_15m.empty or len(df_15m) < 5: return

        # Проверяем импульс на последней свече (для золота 0.3% - это много)
        current_open = df_15m['Open'].iloc[-1]
        current_close = df_15m['Close'].iloc[-1]
        change_pct = ((current_close - current_open) / current_open) * 100

        direction = None
        if change_pct >= 0.3: direction = 'SHORT'
        elif change_pct <= -0.3: direction = 'LONG'
        if not direction: return

        # 2. Получаем данные старших ТФ для поиска стен
        df_1h = ticker.history(period="1mo", interval="1h")
        df_1d = ticker.history(period="1y", interval="1d")

        walls = []
        # Считаем EMA для 1H
        for span in [50, 100, 200]:
            if len(df_1h) >= span:
                ema = df_1h['Close'].ewm(span=span, adjust=False).mean().iloc[-1]
                if direction == 'SHORT' and ema > current_close:
                    walls.append({'name': f'1H EMA {span}', 'val': ema, 'diff': ((ema - current_close) / current_close) * 100})
                elif direction == 'LONG' and ema < current_close:
                    walls.append({'name': f'1H EMA {span}', 'val': ema, 'diff': ((current_close - ema) / current_close) * 100})
        
        # Считаем EMA для 1D
        for span in [7, 14, 28]:
            if len(df_1d) >= span:
                ema = df_1d['Close'].ewm(span=span, adjust=False).mean().iloc[-1]
                if direction == 'SHORT' and ema > current_close:
                    walls.append({'name': f'1D EMA {span}', 'val': ema, 'diff': ((ema - current_close) / current_close) * 100})
                elif direction == 'LONG' and ema < current_close:
                    walls.append({'name': f'1D EMA {span}', 'val': ema, 'diff': ((current_close - ema) / current_close) * 100})

        if not walls: return # Если стены нет, пропускаем

        # Ищем ближайшую стену
        best_wall = min(walls, key=lambda x: x['diff'])
        if best_wall['diff'] > 1.5: return # Для золота ищем стену не дальше 1.5%

        # 3. Рисуем график
        chart_file = "GOLD_chart.png"
        df_plot = df_15m.copy()
        df_plot.index = df_plot.index.tz_localize(None) # Убираем часовой пояс для mplfinance
        hlines = dict(hlines=[best_wall['val']], colors=['red' if direction=='SHORT' else 'green'], linestyle='-', linewidths=2.0)
        mpf.plot(df_plot.tail(30), type='candle', style='yahoo', volume=True, savefig=chart_file, title="XAUUSD 15m", tight_layout=True, hlines=hlines)

        # 4. Формируем сообщение
        if direction == 'SHORT':
            stop_loss = best_wall['val'] * 1.002 # Стоп 0.2% выше стены
            tp = best_wall['val'] * 0.995 # Тейк 0.5% ниже
            emoji = "🔴"
        else:
            stop_loss = best_wall['val'] * 0.998
            tp = best_wall['val'] * 1.005
            emoji = "🟢"

        text = f"{emoji} <b>[GOLD] СИГНАЛ: {direction}</b>\n"
        text += f"🏦 <b>XAUUSD (Forex)</b>\n"
        text += f"📊 Изм. 15м: <b>{change_pct:+.2f}%</b>\n"
        text += f"💰 Цена: <code>{current_close:.2f}</code>\n\n"
        text += f"🧱 <b>Стена:</b> {best_wall['name']}\n"
        text += f"├ <b>Лимитка: {best_wall['val']:.2f}</b>\n"
        text += f"└ Осталось: <b>{best_wall['diff']:.2f}%</b>\n\n"
        text += f"🛑 <b>Стоп-лосс:</b> <code>{stop_loss:.2f}</code>\n"
        text += f"🟢 <b>Тейк-профит:</b> <code>{tp:.2f}</code>\n\n"
        text += f"⚡️ <i>Данные Yahoo Finance. Возможна задержка!</i>"

        send_telegram_photo(chart_file, text)
        if os.path.exists(chart_file): os.remove(chart_file)
        print(f"[GOLD] Сигнал отправлен: {direction}")

    except Exception as e:
        print(f"Ошибка при анализе золота: {e}")

# ==========================================
# БЛОК ДЛЯ КРИПТЫ (MEXC / Bitget)
# ==========================================
def get_htf_emas(sym, exchange, current_price, direction):
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
                    if direction == 'SHORT' and val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 5.0: emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
                    elif direction == 'LONG' and val < current_price:
                        diff_pct = ((current_price - val) / current_price) * 100
                        if diff_pct <= 5.0: emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs)).iloc[-1]

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
    
    # 1. Сначала проверяем ЗОЛОТО
    print("Проверка золота (XAUUSD)...")
    check_gold()

    # 2. Затем проверяем КРИПТУ
    print("Проверка криптовалют...")
    start_time = time.time()
    all_coins = get_all_coins()
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    found = 0
    for coin in list(unique_coins):
        if found >= 3: break
        if time.time() - start_time > 180: break # Ограничиваем время крипты, чтобы уложиться в лимит GitHub
            
        sym, ex, current_price = coin['sym'], coin['ex'], coin['price']
        
        df_15m = get_15m_data(sym, ex)
        if df_15m is None or len(df_15m) < 30: continue
        
        open_p = df_15m['open'].iloc[-1]
        change_pct = ((current_price - open_p) / open_p) * 100
        
        direction = None
        if change_pct >= 2.0: direction = 'SHORT'
        elif -3.0 <= change_pct <= -1.5: direction = 'LONG'
        
        if not direction: continue

        rsi = calculate_rsi(df_15m)
        
        wall = get_htf_emas(sym, ex, current_price, direction)
        if not wall: continue
        
        best_wall_name, best_wall_data = min(wall.items(), key=lambda x: x[1]['diff'])

        chart_file = f"{sym}_chart.png"
        df = df_15m.copy()
        df.set_index('time', inplace=True)
        color = 'red' if direction == 'SHORT' else 'green'
        hlines = dict(hlines=[best_wall_data['val']], colors=[color], linestyle='-', linewidths=2.0)
        mpf.plot(df.tail(50), type='candle', style='yahoo', volume=True, savefig=chart_file, title=f"{sym} 15m | {direction}", tight_layout=True, hlines=hlines)
        
        if chart_file:
            base_coin = sym.split('_')[0].replace('USDT', '')
            
            if direction == 'SHORT':
                stop_loss = best_wall_data['val'] * 1.015
                tp = best_wall_data['val'] * 0.95
                emoji = "🔴"
                action = "ШОРТ"
            else:
                stop_loss = best_wall_data['val'] * 0.985
                tp = best_wall_data['val'] * 1.05
                emoji = "🟢"
                action = "ЛОНГ"

            text = f"{emoji} <b>[CRYPTO] СИГНАЛ: {action}</b>\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{base_coin}</b>\n"
            text += f"📊 Изм. 15м: <b>{change_pct:+.2f}%</b> | RSI: <b>{rsi:.1f}</b>\n"
            text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
            
            text += f"🧱 <b>Стена:</b> {best_wall_name}\n"
            text += f"├ <b>Лимитка: {best_wall_data['val']:.4f}</b>\n"
            text += f"└ Осталось: <b>{best_wall_data['diff']:.2f}%</b>\n\n"
            
            text += f"🛑 <b>Стоп-лосс:</b> <code>{stop_loss:.4f}</code>\n"
            text += f"🟢 <b>Тейк-профит:</b> <code>{tp:.4f}</code>\n\n"

            send_telegram_photo(chart_file, text)
            if os.path.exists(chart_file): os.remove(chart_file)
            found += 1
            time.sleep(1)

if __name__ == "__main__":
    main()
