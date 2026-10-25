import os
import requests
import urllib.request
import urllib.parse
import sys

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ОШИБКА: Не найдены BOT_TOKEN или CHAT_ID в секретах!")
        sys.exit(1)

    print("Сканируем рынок...")
    tickers_url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    
    try:
        response = requests.get(tickers_url, timeout=10)
        res = response.json()
    except Exception as e:
        print(f"ОШИБКА СЕТИ: {e}")
        sys.exit(1)

    # ЖЕСТКАЯ ПРОВЕРКА НА БЛОКИРОВКУ
    if not isinstance(res, list):
        print(f"ОШИБКА BINANCE (сервер заблокирован или неверный ответ): {res}")
        sys.exit(1)

    pumps = []
    for item in res:
        try:
            symbol = item.get('symbol', '')
            if symbol.endswith("USDT"):
                price_change = float(item.get('priceChangePercent', 0))
                volume = float(item.get('quoteVolume', 0))
                if price_change > 10 and volume > 5000000:
                    pumps.append({"sym": symbol, "change": price_change, "current_price": float(item.get('lastPrice', 0))})
        except Exception:
            pass

    pumps.sort(key=lambda x: x['change'], reverse=True)
    top_pumps = pumps[:5]

    funding_url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    try:
        funding_res = requests.get(funding_url, timeout=10).json()
        if not isinstance(funding_res, list):
             raise Exception("Ошибка API фандинга")
        funding_dict = {item['symbol']: float(item['lastFundingRate']) * 100 for item in funding_res}
    except Exception as e:
        print(f"Ошибка фандинга: {e}")
        funding_dict = {}

    final_list = []
    for p in top_pumps:
        sym = p['sym']
        try:
            klines_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=168"
            klines = requests.get(klines_url, timeout=5).json()
            if not isinstance(klines, list):
                raise Exception("Ошибка API свечей")
            high_7d = max([float(k[2]) for k in klines[:-1]]) 
            current_price = p['current_price']
            
            if current_price >= high_7d:
                target_level = high_7d
                status = "🚀 ПРОБОЙ"
            else:
                target_level = high_7d
                status = "📈 ЛЕТИМ К МАКС"
                
            p['funding'] = funding_dict.get(sym, 0)
            p['target'] = target_level
            p['status'] = status
            final_list.append(p)
        except Exception as e:
            print(f"Ошибка при анализе {sym}: {e}")

    text = "🎯 <b>ЦЕЛИ ДЛЯ ШОРТА (Пампы + Уровни)</b>\n\n"
    if final_list:
        for f in final_list:
            text += f"🪙 <b>{f['sym']}</b> | <code>+{f['change']:.1f}%</code>\n"
            text += f"├ Фандинг: <code>{f['funding']:.4f}%</code>\n"
            text += f"├ Текущая: <code>{f['current_price']}</code>\n"
            text += f"└ Цель (7д): <b><code>{f['target']}</code></b> {f['status']}\n\n"
    else:
        text += "Сейчас нет подходящих пампов."

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}).encode('utf-8')
    req = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(req) as response:
            print("Успех! Сигналы отправлены!")
    except Exception as e:
        print(f"ОШИБКА ТЕЛЕГРАМ: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
