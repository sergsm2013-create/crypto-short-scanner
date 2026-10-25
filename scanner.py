import os
import requests
import urllib.request
import urllib.parse
import sys

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def get_bybit():
    try:
        res = requests.get("https://api.bybit.com/v5/market/tickers?category=linear", timeout=10).json()
        pumps = []
        for item in res.get('result', {}).get('list', []):
            sym = item['symbol']
            if sym.endswith("USDT"):
                change = float(item['price24hPcnt']) * 100
                vol = float(item['turnover24h'])
                if change > 10 and vol > 5000000:
                    pumps.append({"ex": "Bybit", "sym": sym, "change": change, "price": float(item['lastPrice']), "funding": float(item['fundingRate'])*100})
        return pumps
    except Exception as e:
        print(f"Bybit error: {e}")
        return []

def get_bitunix():
    try:
        res = requests.get("https://fapi.bitunix.com/api/v1/futures/market/tickers", timeout=10).json()
        pumps = []
        for item in res.get('data', []):
            sym = item.get('symbol', '')
            if sym.endswith("USDT"):
                change = float(item.get('change', 0))
                if 0 < change < 1: change *= 100 # Приводим к процентам
                vol = float(item.get('quoteVol', 0) or item.get('quoteVolume', 0))
                if change > 10 and vol > 5000000:
                    pumps.append({"ex": "Bitunix", "sym": sym, "change": change, "price": float(item.get('lastPrice',0)), "funding": float(item.get('fundingRate',0))*100})
        return pumps
    except Exception as e:
        print(f"Bitunix error: {e}")
        return []

def get_mexc():
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=10).json()
        pumps = []
        for item in res.get('data', []):
            sym = item.get('symbol', '')
            if sym.endswith("_USDT"):
                change = float(item.get('riseFallRate', 0)) * 100
                vol = float(item.get('amount24', 0))
                if change > 10 and vol > 5000000:
                    pumps.append({"ex": "MEXC", "sym": sym, "change": change, "price": float(item.get('lastPrice',0)), "funding": float(item.get('fundingRate',0))*100})
        return pumps
    except Exception as e:
        print(f"MEXC error: {e}")
        return []

def get_7d_high(ex, sym):
    try:
        if ex == "Bybit":
            url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=60&limit=168"
            r = requests.get(url, timeout=5).json()
            return max([float(k[2]) for k in r['result']['list'][1:]])
        elif ex == "Bitunix":
            url = f"https://fapi.bitunix.com/api/v1/futures/market/kline?symbol={sym}&interval=1h&limit=168"
            r = requests.get(url, timeout=5).json()
            return max([float(k['high']) for k in r['data'][:-1]])
        elif ex == "MEXC":
            url = f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min60"
            r = requests.get(url, timeout=5).json()
            return max(r['data']['high'])
    except Exception as e:
        print(f"Kline error {ex} {sym}: {e}")
        return None

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ОШИБКА: Нет токенов!")
        sys.exit(1)

    print("Сканируем Bybit, Bitunix, MEXC...")
    
    # Собираем пампы со всех трех бирж
    all_pumps = get_bybit() + get_bitunix() + get_mexc()
    
    # Сортируем по росту и берем ТОП-7
    all_pumps.sort(key=lambda x: x['change'], reverse=True)
    top_pumps = all_pumps[:7]

    text = "🎯 <b>ЦЕЛИ ДЛЯ ШОРТА (Мульти-биржа)</b>\n\n"
    found = False

    for p in top_pumps:
        high_7d = get_7d_high(p['ex'], p['sym'])
        if high_7d:
            found = True
            current_price = p['price']
            if current_price >= high_7d:
                target_level = high_7d
                status = "🚀 ПРОБОЙ"
            else:
                target_level = high_7d
                status = "📈 ЛЕТИМ К МАКС"
                
            text += f"🏦 <b>{p['ex']}</b> | 🪙 <b>{p['sym']}</b>\n"
            text += f"├ Рост: <code>+{p['change']:.1f}%</code>\n"
            text += f"├ Фандинг: <code>{p['funding']:.4f}%</code>\n"
            text += f"├ Цена: <code>{current_price}</code>\n"
            text += f"└ Цель (7д): <b><code>{target_level}</code></b> {status}\n\n"
        else:
            # Если не удалось получить свечи, показываем хотя бы просто памп
            found = True
            text += f"🏦 <b>{p['ex']}</b> | 🪙 <b>{p['sym']}</b>\n"
            text += f"├ Рост: <code>+{p['change']:.1f}%</code>\n"
            text += f"├ Фандинг: <code>{p['funding']:.4f}%</code>\n"
            text += f"└ Цель: <i>нет данных</i>\n\n"

    if not found:
        text += "Сейчас нет пампов (>10%) на выбранных биржах."

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}).encode('utf-8')
    req = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(req) as response:
            print("Успех! Сигналы отправлены!")
    except Exception as e:
        print(f"ОШИБКА ТЕЛЕГРАМ: {e}")

if __name__ == "__main__":
    main()
