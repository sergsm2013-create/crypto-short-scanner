import os
import requests
import pandas as pd
import mplfinance as mpf
import sys

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def send_telegram_text(text):
    """Отправка текстового сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def send_telegram_photo(photo_path, caption):
    """Отправка картинки в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}
            requests.post(url, files=files, data=data)
    except Exception as e:
        print(f"Ошибка отправки фото: {e}")

def draw_chart(sym):
    """Рисует график 15-минутных свечей"""
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=50"
        res = requests.get(url, timeout=10).json()
        klines = res['result']['list']
        
        # Подготовка данных (Bybit отдает от новых к старым, переворачиваем)
        df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df = df.iloc[::-1] 
        
        # Конвертируем в нужные типы
        df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
        df.set_index('time', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
            
        filename = f"{sym}_chart.png"
        # Рисуем график
        mpf.plot(df, type='candle', style='yahoo', volume=True, savefig=filename, 
                 title=f"{sym} 15m", tight_layout=True)
        return filename
    except Exception as e:
        print(f"Ошибка графика {sym}: {e}")
        return None

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ОШИБКА: Нет токенов!")
        sys.exit(1)

    print("Сканируем рынок Bybit...")
    
    try:
        res = requests.get("https://api.bybit.com/v5/market/tickers?category=linear", timeout=10).json()
        tickers = res.get('result', {}).get('list', [])
    except Exception as e:
        print(f"Ошибка сети: {e}")
        sys.exit(1)

    found_signals = 0

    for item in tickers:
        sym = item['symbol']
        vol = float(item['turnover24h'])
        
        # Берем только USDT пары с объемом от 5 млн
        if sym.endswith("USDT") and vol > 5000000:
            try:
                # Проверяем изменение за 15 минут
                k_url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=2"
                k_res = requests.get(k_url, timeout=5).json()
                klines = k_res['result']['list']
                
                if len(klines) >= 2:
                    current_open = float(klines[0][1])
                    current_close = float(klines[0][4])
                    change = ((current_close - current_open) / current_open) * 100
                    
                    # ТЕСТОВЫЙ РЕЖИМ: порог 0.1% (потом вернем на 3.0)
                    if change > 0.1:
                        print(f"Найдена монета: {sym} (+{change:.2f}%)")
                        chart_file = draw_chart(sym)
                        
                        if chart_file:
                            text = f"⚡️ <b>ИМПУЛЬС (15m)</b>\n"
                            text += f"🪙 <b>{sym}</b>\n"
                            text += f"📈 Рост: <code>+{change:.2f}%</code>\n"
                            text += f"💰 Цена: <code>{current_close}</code>"
                            
                            send_telegram_photo(chart_file, text)
                            os.remove(chart_file) # Удаляем файл после отправки
                            found_signals += 1
                            
                            # Чтобы не заспамить, отправляем максимум 3 сигнала за раз
                            if found_signals >= 3:
                                break
            except Exception:
                pass

    if found_signals == 0:
        print("Сигналов нет.")
        send_telegram_text("🤖 Бот работает. Сейчас на рынке нет сильных импульсов.")
    else:
        print("Сигналы отправлены!")

if __name__ == "__main__":
    main()
