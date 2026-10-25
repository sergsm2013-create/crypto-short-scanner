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
    except Exception as e:
        print(f"TG text error: {e}")

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e:
        print(f"TG photo error: {e}")

def draw_chart(sym, exchange):
    try:
        df = None
        if exchange == 'MEXC':
            # MEXC: интервал 15 минут, свечи идут от старых к новым
            url = f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            if res.get('success'):
                data = res['data']
                df = pd.DataFrame({
                    'time': pd.to_datetime(data['time'], unit='s'),
                    'open': data['open'],
                    'high': data['high'],
                    'low': data['low'],
                    'close': data['close'],
                    'volume': data['vol']
                })
        elif exchange == 'Bitget':
            # Bitget: интервал 15 минут, свечи идут от новых к старым
            url = f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=50"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            if res.get('code') == '00000':
                data = res['data']
                df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df = df.iloc[::-1] # Переворачиваем
                df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)

        if df is None or df.empty:
            return None

        df.set_index('time', inplace=True)
        filename = f"{sym}_chart.png"
        mpf.plot(df, type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m", tight_layout=True)
        return filename
    except Exception as e:
        print(f"Chart error {sym} ({exchange}): {e}")
        return None

def get_mexc_data():
    try:
        url = "https://contract.mexc.com/api/v1/contract/ticker"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if not res.get('success'):
            return []
        return [c for c in res['data'] if c['symbol'].endswith('_USDT') and float(c.get('amount24', 0)) > 5000000]
    except Exception as e:
        print(f"MEXC tickers error: {e}")
        return []

def get_bitget_data():
    try:
        url = "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if res.get('code') != '00000':
            return []
        return [c for c in res['data'] if float(c.get('usdtVolume', 0)) > 5000000]
    except Exception as e:
        print(f"Bitget tickers error: {e}")
        return []

def check_mexc_spike(sym):
    try:
        url = f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15"
        res = requests.get(url, headers=HEADERS, timeout=5).json()
        if not res.get('success'): return None
        data = res['data']
        if len(data['close']) >= 2:
            # Берем последнюю закрытую свечу
            last_close = float(data['close'][-1])
            prev_close = float(data['close'][-2])
            change = ((last_close - prev_close) / prev_close) * 100
            # ТЕСТОВЫЙ РЕЖИМ: порог 0.1%
            if change > 0.1:
                return change, last_close
    except Exception:
        pass
    return None

def check_bitget_spike(sym):
    try:
        url = f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=2"
        res = requests.get(url, headers=HEADERS, timeout=5).json()
        if res.get('code') != '00000': return None
        data = res['data']
        if len(data) >= 2:
            # data[0] - текущая свеча, data[1] - прошлая
            curr_open = float(data[0][1])
            curr_close = float(data[0][4])
            change = ((curr_close - curr_open) / curr_open) * 100
            # ТЕСТОВЫЙ РЕЖИМ: порог 0.1%
            if change > 0.1:
                return change, curr_close
    except Exception:
        pass
    return None

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ОШИБКА: Нет токенов!")
        sys.exit(1)

    print("Сканируем MEXC и Bitget...")
    found = 0
    coins_to_check = []

    # Собираем монеты с MEXC
    for c in get_mexc_data():
        coins_to_check.append({'ex': 'MEXC', 'sym': c['symbol']})
    
    # Собираем монеты с Bitget
    for c in get_bitget_data():
        coins_to_check.append({'ex': 'Bitget', 'sym': c['symbol']})

    # Убираем дубликаты и ограничиваем количество для теста
    unique_coins = {f"{c['ex']}_{c['sym']}": c for c in coins_to_check}.values()
    print(f"Найдено {len(list(unique_coins))} ликвидных пар для проверки.")

    for coin in list(unique_coins)[:50]: # Ограничим 50 монетами для теста
        if found >= 3: break
        
        sym = coin['sym']
        ex = coin['ex']
        spike = None
        
        if ex == 'MEXC':
            spike = check_mexc_spike(sym)
        elif ex == 'Bitget':
            spike = check_bitget_spike(sym)
            
        if spike:
            change, price = spike
            print(f"Импульс: {ex} {sym} (+{change:.2f}%)")
            chart_file = draw_chart(sym, ex)
            
            if chart_file:
                text = f"⚡️ <b>ИМПУЛЬС (15m)</b>\n"
                text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
                text += f"📈 Рост: <code>+{change:.2f}%</code>\n"
                text += f"💰 Цена: <code>{price}</code>"
                send_telegram_photo(chart_file, text)
                os.remove(chart_file)
                found += 1
                time.sleep(1) # Пауза между сообщениями

    if found == 0:
        print("Импульсов нет.")
        send_telegram_text("🤖 Бот работает на MEXC и Bitget. Сейчас на рынке нет сильных импульсов (рост > 0.1% за 15 мин).")

if __name__ == "__main__":
    main()
