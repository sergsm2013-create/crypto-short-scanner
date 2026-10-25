import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time
from collections import defaultdict
import re

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

# --- ФУНКЦИИ ДЛЯ ОПЦИОНОВ DERIBIT ---
def fetch_deribit_options(currency="BTC"):
    """Получает данные по всем опционам для валюты (BTC/ETH)"""
    url = f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        return res.get('result', [])
    except Exception as e:
        print(f"Deribit API error: {e}")
        return []

def parse_instrument_name(name):
    """Парсит имя инструмента, например BTC-29NOV24-100000-C"""
    # Формат: BTC-29NOV24-100000-C
    m = re.match(r"^([A-Z]+)-(\d+[A-Z]+\d+)-(\d+)-([CP])$", name)
    if not m: return None
    return {
        'currency': m.group(1),
        'expiry': m.group(2),
        'strike': float(m.group(3)),
        'type': m.group(4) # C или P
    }

def calculate_options_levels(options_data):
    """Считает Max Pain, Call Wall, Put Wall"""
    if not options_data: return None

    # Группируем открытый интерес (Open Interest) по страйкам
    strike_oi = defaultdict(lambda: {'call_oi': 0.0, 'put_oi': 0.0})
    
    for opt in options_data:
        # Используем только опционы с экспирацией в ближайший месяц или два
        # (можно убрать фильтр, если хотите учитывать все)
        parsed = parse_instrument_name(opt.get('instrument_name', ''))
        if not parsed: continue
        
        oi = float(opt.get('open_interest', 0))
        if parsed['type'] == 'C':
            strike_oi[parsed['strike']]['call_oi'] += oi
        else:
            strike_oi[parsed['strike']]['put_oi'] += oi

    if not strike_oi: return None

    # 1. Считаем Max Pain
    strikes = sorted(strike_oi.keys())
    min_pain = float('inf')
    max_pain_strike = strikes[0]
    
    for candidate in strikes:
        total_pain = 0.0
        for strike, oi_data in strike_oi.items():
            # Intrinsic value для коллов: max(0, candidate - strike)
            call_intrinsic = max(0, candidate - strike)
            total_pain += call_intrinsic * oi_data['call_oi']
            
            # Intrinsic value для путов: max(0, strike - candidate)
            put_intrinsic = max(0, strike - candidate)
            total_pain += put_intrinsic * oi_data['put_oi']
        
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = candidate

    # 2. Ищем Call Wall и Put Wall (топ-3 страйка по OI)
    call_walls = sorted(strike_oi.items(), key=lambda x: x[1]['call_oi'], reverse=True)[:3]
    put_walls = sorted(strike_oi.items(), key=lambda x: x[1]['put_oi'], reverse=True)[:3]

    return {
        'max_pain': max_pain_strike,
        'call_walls': [{'strike': s, 'oi': d['call_oi']} for s, d in call_walls if d['call_oi'] > 0],
        'put_walls': [{'strike': s, 'oi': d['put_oi']} for s, d in put_walls if d['put_oi'] > 0]
    }

# --- ОСТАЛЬНЫЕ ФУНКЦИИ БОТА (без изменений) ---
def send_telegram_text(text):
    # ... (ваш код) ...

def send_telegram_photo(photo_path, caption):
    # ... (ваш код) ...

def get_weekly_emas(sym, exchange):
    # ... (ваш код) ...

def draw_chart(sym, exchange, target_ema_val):
    # ... (ваш код) ...

def get_all_coins():
    # ... (ваш код) ...

def check_spike(sym, exchange):
    # ... (ваш код) ...

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    print("Сканируем...")
    
    # --- ЗАГРУЖАЕМ ОПЦИОННЫЕ УРОВНИ ЗАРАНЕЕ ---
    print("Загружаем опционные данные Deribit...")
    btc_options = calculate_options_levels(fetch_deribit_options("BTC"))
    eth_options = calculate_options_levels(fetch_deribit_options("ETH"))

    # ... (ваш цикл сканирования монет) ...

        if best_ema_name and 0.5 <= min_diff_pct <= 10.0:
            chart_file = draw_chart(sym, ex, best_ema_val)
            if chart_file:
                # ... (формирование текста) ...
                text = f"🚀 <b>АНОМАЛИЯ (ПАМП +{spike_change:.2f}%)</b>\n"
                text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
                text += f"💧 Ликвидность: <b>{liq_badge} LIQ</b>\n"
                text += f"🔥 Волатильность (24ч): <b>{volat:.1f}%</b>\n"
                text += f"💰 Цена: <code>{current_price}</code>\n\n"
                text += f"🎯 <b>Летит прямо в EMA {best_ema_name} (1W)!</b>\n"
                text += f"├ Уровень (Лимитка): <code>{best_ema_val:.4f}</code>\n"
                text += f"└ Осталось: <b><code>{min_diff_pct:.2f}%</code></b>\n\n"

                # --- ДОБАВЛЯЕМ БЛОК ОПЦИОНОВ ---
                base_coin = sym.split('_')[0].replace('USDT', '')
                opt_levels = None
                if base_coin == 'BTC': opt_levels = btc_options
                elif base_coin == 'ETH': opt_levels = eth_options

                if opt_levels:
                    text += f"🏦 <b>ОПЦИОННЫЕ УРОВНИ (Deribit):</b>\n"
                    text += f"├ Max Pain: <code>{opt_levels['max_pain']}</code>\n"
                    # Берем ближайшую стену сверху для сопротивления
                    if opt_levels['call_walls']:
                        call_wall = min([w for w in opt_levels['call_walls'] if w['strike'] > current_price], key=lambda x: x['strike'], default=None)
                        if call_wall:
                            text += f"├ Call Wall (Сопр.): <code>{call_wall['strike']}</code>\n"
                    # Берем ближайшую стену снизу для поддержки
                    if opt_levels['put_walls']:
                        put_wall = max([w for w in opt_levels['put_walls'] if w['strike'] < current_price], key=lambda x: x['strike'], default=None)
                        if put_wall:
                            text += f"└ Put Wall (Поддержка): <code>{put_wall['strike']}</code>\n"
                    text += "\n"

                text += f"⚡️ <i>Срочно ставь лимитку на шорт!</i>"
                send_telegram_photo(chart_file, text)
                # ... (остальной код) ...

if __name__ == "__main__":
    main()
