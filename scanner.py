import os
import requests
import urllib.request
import urllib.parse
import sys
import pandas as pd
import mplfinance as mpf
from datetime import datetime

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def get_bybit_15m_pumps():
    """Ищем монеты с резким ростом за последние 15 минут"""
    try:
        # Получаем данные по всем парам
        res = requests.get("https://api.bybit.com/v5/market/tickers?category=linear", timeout=10).json()
        pumps = []
        
        for item in res.get('result', {}).get('list', []):
            sym = item['symbol']
            if sym.endswith("USDT"):
                # В Bybit изменение цены за 24ч, но мы проверим 15-минутные свечи ниже
                vol = float(item['turnover24h'])
                if vol > 5000000: # Фильтр по объему > 5 млн
                    pumps.append({"sym": sym, "price": float(item['lastPrice'])})
        return pumps[:30] # Берем топ-30 ликвидных монет для проверки
    except Exception as e:
        print(f"Bybit error: {e}")
        return []

def check_15m_change(sym):
    """Проверяем изменение цены за последние 15 минут (1 свеча 15m)"""
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=2"
        res = requests.get(url, timeout=5).json()
        klines = res['result']['list']
        if len(klines) >= 2:
            # klines[0] - текущая свеча, klines[1] - прошлая
            current_open = float(klines[0][1])
            current_close = float(klines[0][4])
            change = ((current_close - current_open) / current_open) * 100
            
            # Если рост за 15 минут больше 3%
            if change > 3.0:
                return change
    except Exception:
        pass
    return None

def draw_chart(sym):
    """Рисуем график 15-минутных свечей"""
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=50"
        res = requests.get(url, timeout=10).json()
        klines = res['result']['list']
        
        # Подготовка данных для графика (Bybit отдает от новых к старым, переворачиваем)
        df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df = df.iloc[::-1] # Переворачиваем
        
        # Конвертируем в нужные типы
        df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
        df.set_index('time', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
            
        # Рисуем график
        filename = f"{sym}_chart.png"
        mpf.plot(df, type='candle', style='yahoo', volume=True, savefig=filename, 
                 title=f"{sym} 15m", tight_layout=True)
        return filename
    except Exception as e:
        print(f"Chart error {sym}: {e}")
        return None

def send_telegram_photo(photo_path, caption):
    """Отправка фото в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}
            response = requests.post(url, files=files, data=data)
            if response.status_code != 200:
                print(f"Telegram error: {response.text}")
    except Exception as e:
        print(f"Send error: {e}")

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ОШИБКА: Нет токенов!")
        sys.exit(1)

    print("Сканируем Bybit на спайки (15m)...")
    
    coins = get_bybit_15m_pumps()
    found_signals = 0
    
    for coin in coins:
        sym = coin['sym']
        change = check_15m_change(sym)
        
        if change:
            print(f"Найден памп: {sym} +{change:.2f}%")
            chart_file = draw_chart(sym)
            
            if chart_file:
                text = f"⚡️ <b>ИМПУЛЬС (15m)</b>\n"
                text += f"🪙 <b>{sym}</b>\n"
                text += f"📈 Рост: <code>+{change:.2f}%</code>\n"
                text += f"💰 Цена: <code>{coin['price']}</code>"
                
                send_telegram_photo(chart_file, text)
                os.remove(chart_file) # Удаляем картинку, чтобы не засорять сервер
                found_signals += 1
                
    if found_signals == 0:
        print("Спайков за 15 минут не найдено.")

if __name__ == "__main__":
    main()
