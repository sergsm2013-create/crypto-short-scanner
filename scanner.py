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

def send_telegram_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=10)
    except Exception as e: print(f"TG text error: {e}")

# --- ФУНКЦИЯ СБОРА ДАННЫХ ---
def get_15m_data(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=3).json()
            if res.get('success'):
                data = res['data']
                return pd.DataFrame({'time': pd.to_datetime(data['time'], unit='s'), 'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close'], 'volume': data['vol']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=200", headers=HEADERS, timeout=3).json()
            if res.get('code') == '00000':
                data = res['data']
                df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df = df.iloc[::-1]
                df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = df[col].astype(float)
                return df
    except: pass
    return None

# --- ПОИСК EMA СТАРШИХ ТФ ---
def get_htf_emas(sym, exchange, current_price):
    emas = {}
    timeframes = {"MEXC": [("Hour4", "4H"), ("Day1", "1D")], "Bitget": [("4H", "4H"), ("1D", "1D")]}
    for tf, tf_name in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=3).json()
                if res.get('success') and 'close' in res['data']: df = pd.DataFrame({'close': res['data']['close']})
                else: continue
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=200", headers=HEADERS, timeout=3).json()
                if res.get('code') == '00000' and res.get('data'):
                    df = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                    df['close'] = df['close'].astype(float)
                else: continue

            for span in [7, 14, 28, 50, 100, 200]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 5.0: 
                            emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

# --- ГЛАВНЫЙ АНАЛИЗ ---
def get_all_coins():
    coins = []
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            for c in res['data']:
                if c['symbol'].endswith('_USDT') and float(c.get('amount24', 0)) > 5000000:
                    coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice']), 'change24h': float(c.get('riseFallRate', 0))*100})
    except: pass
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                if float(c.get('usdtVolume', 0)) > 5000000:
                    coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr']), 'change24h': float(c.get('change24h', 0))*100})
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
            
        sym, ex, current_price, change24h = coin['sym'], coin['ex'], coin['price'], coin['change24h']
        
        df = get_15m_data(sym, ex)
        if df is None or len(df) < 100: continue
        
        # 1. Фильтр 24-часового роста. Если выросло больше 25% - НЕ ШОРТИМ!
        if change24h > 25.0:
            print(f"Пропуск {sym}: сильный рост за 24ч ({change24h:.1f}%)")
            continue

        # 2. Ловим памп на 15-минутке (от 2%)
        open_p = df['open'].iloc[-1]
        change_pct = ((current_price - open_p) / open_p) * 100
        if change_pct < 2.0: continue

        # 3. Ищем исторический максимум за последние 200 свечей (50 часов)
        recent_high = df['high'].iloc[-200:].max()
        
        # Если текущая цена УЖЕ выше исторического максимума, мы не шортим (пробой)
        if current_price >= recent_high * 0.995:
            continue

        # 4. Ищем EMA
        emas = get_htf_emas(sym, ex, current_price)
        if not emas: continue
        
        best_ema_name, best_ema_data = min(emas.items(), key=lambda x: x[1]['diff'])
        
        # 5. Выбираем цель: Исторический хай или EMA (что ближе)
        if abs(current_price - recent_high) / current_price < 0.05: # Хай близко (до 5%)
            target_name = "Исторический хай (200 свечей)"
            target_val = recent_high
        else:
            target_name = best_ema_name
            target_val = best_ema_data['val']

        # 6. Лимитка ставится на 0.2% ВЫШЕ цели (для гарантированного срабатывания)
        entry_price = target_val * 1.002
        
        # 7. Расчет стопа и тейка
        stop_loss = entry_price * 1.02 # Стоп 2% выше входа
        tp = entry_price * 0.95 # Тейк 5% ниже

        # --- РИСОВАНИЕ ---
        chart_file = f"{sym}_chart.png"
        df_plot = df.copy()
        df_plot.set_index('time', inplace=True)
        hlines = dict(hlines=[entry_price, target_val], colors=['blue', 'red'], linestyle='--', linewidths=1.5)
        mpf.plot(df_plot.tail(60), type='candle', style='yahoo', volume=True, savefig=chart_file, title=f"{sym} 15m | Target: {target_name}", tight_layout=True, hlines=hlines)

        # --- ТЕКСТ ---
        text = f"🔴 <b>СИГНАЛ: ШОРТ (Умный вход)</b>\n"
        text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym.split('_')[0]}</b>\n"
        text += f"📊 Рост 15м: <b>{change_pct:+.2f}%</b>\n"
        text += f"🌍 Рост за 24ч: <b>{change24h:+.1f}%</b>\n"
        text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
        
        text += f"🧱 <b>Цель (Сопротивление):</b> {target_name}\n"
        text += f"├ <b>Лимитка: {entry_price:.4f}</b>\n"
        text += f"└ Осталось: <b>{((entry_price-current_price)/current_price)*100:.2f}%</b>\n\n"
        
        text += f"🛑 <b>Стоп-лосс:</b> <code>{stop_loss:.4f}</code>\n"
        text += f"🟢 <b>Тейк-профит:</b> <code>{tp:.4f}</code>\n\n"
        text += f"⚡️ <i>Если цена пробьет {target_name} и закрепится выше — ОТМЕНИ лимитку.</i>"

        send_telegram_photo(chart_file, text)
        os.remove(chart_file)
        found += 1
        time.sleep(1)

if __name__ == "__main__":
    main()
