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

def get_htf_walls(sym, exchange, current_price):
    """Ищем ВСЕ Недельные и Месячные EMA, которые находятся ВЫШЕ текущей цены"""
    walls = []
    timeframes = {
        "MEXC": [("Week1", "1W"), ("Month1", "1M")], 
        "Bitget": [("1week", "1W"), ("1M", "1M")]
    }
    
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

            for span in [7, 14, 28, 50]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    
                    # Берем ТОЛЬКО те EMA, которые ВЫШЕ текущей цены
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        walls.append({
                            'name': f"{tf_name} EMA {span}",
                            'val': val,
                            'diff': diff_pct
                        })
            time.sleep(0.1)
        except: pass
    
    # Сортируем стены от ближайшей к самой дальней
    walls.sort(key=lambda x: x['diff'])
    return walls

def draw_chart(sym, df_15m, walls):
    try:
        filename = f"{sym}_pump.png"
        df = df_15m.copy()
        df.set_index('time', inplace=True)
        
        # Рисуем линии для всех найденных стен
        lines = [w['val'] for w in walls]
        colors = ['red', 'orange', 'purple', 'blue'] # Разные цвета для разных EMA
        widths = [2.0, 1.5, 1.5, 1.0]
        
        hlines = dict(hlines=lines, colors=colors[:len(lines)], linestyle='-', linewidths=widths[:len(lines)])
        
        mpf.plot(df.tail(40), type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m | SNIPER TARGETS (1W/1M)", tight_layout=True, hlines=hlines)
        return filename
    except Exception as e: 
        print(f"Chart error: {e}")
        return None

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
        
        df_15m = get_15m_data(sym, ex)
        if df_15m is None or len(df_15m) < 30: continue
        
        # 🔥 ЛОВИМ ПАМП: Рост от 4% за 15 минут (параболическое движение)
        open_p = df_15m['open'].iloc[-1]
        change_pct = ((current_price - open_p) / open_p) * 100
        
        if change_pct < 4.0: continue # Если нет сильного пампа, пропускаем

        # Ищем Недельные и Месячные стены над ценой
        walls = get_htf_walls(sym, ex, current_price)
        if not walls: continue # Если цена выше всех EMA (космос), пропускаем

        # Берем первую (самую близкую) и вторую стену
        wall1 = walls[0]
        wall2 = walls[1] if len(walls) > 1 else None

        chart_file = draw_chart(sym, df_15m, walls)
        
        if chart_file:
            base_coin = sym.split('_')[0].replace('USDT', '')
            
            text = f"🚀 <b>ПАМП +{change_pct:.2f}% (Ловим Пик!)</b>\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{base_coin}</b>\n"
            text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
            
            text += f"🧱 <b>Твои цели на ШОРТ (1W/1M):</b>\n"
            text += f"├ Ближайшая: <b>{wall1['name']}</b> на <code>{wall1['val']:.4f}</code> (+{wall1['diff']:.1f}%)\n"
            if wall2:
                text += f"└ Следующая: <b>{wall2['name']}</b> на <code>{wall2['val']:.4f}</code> (+{wall2['diff']:.1f}%)\n\n"
            
            text += f"🛑 <b>Стоп-лосс:</b> ставь на 2-3% выше выбранной EMA.\n"
            text += f"⚡️ <i>Ставь лимитку ровно на уровень стены. Когда цена долетит и ударится — она рухнет.</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

if __name__ == "__main__":
    main()
