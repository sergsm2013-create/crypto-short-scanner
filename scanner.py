
import os
import time
import uuid
import html
import logging
import requests
import numpy as np
import pandas as pd
import yfinance as yf
import mplfinance as mpf

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =========================================================
# 1. CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SIGNALS_FILE = os.path.join(BASE_DIR, "signals.csv")
LOG_FILE = os.path.join(BASE_DIR, "scanner.log")

MIN_VOLUME_USDT = 5_000_000
MAX_COINS = 80
MAX_SCAN_SECONDS = 180

MIN_RR = 2.0
ATR_PERIOD = 14
VOLUME_MULTIPLIER = 1.10

MAX_SIGNAL_AGE = 96
REQUEST_TIMEOUT = 10

# Включаем обе стратегии
ENABLE_REVERSAL = True
ENABLE_TREND = True

# =========================================================
# 2. LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("TRADING_SCANNER")


# =========================================================
# 3. HTTP SESSION
# =========================================================

session = requests.Session()

retry = Retry(
    total=2,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)

session.headers.update({
    "User-Agent": "Mozilla/5.0"
})


# =========================================================
# 4. SIGNAL DATABASE
# =========================================================

COLUMNS = [
    "id", "market", "exchange", "symbol",
    "strategy", "direction", "entry_time",
    "entry", "sl", "tp", "risk",
    "status", "exit_time", "exit_price",
    "result_r", "bars"
]


def load_signals():

    if not os.path.exists(SIGNALS_FILE):
        return pd.DataFrame(columns=COLUMNS)

    try:
        df = pd.read_csv(SIGNALS_FILE)

        for col in COLUMNS:
            if col not in df.columns:
                df[col] = np.nan

        return df[COLUMNS]

    except Exception as e:
        log.error(f"Database error: {e}")
        return pd.DataFrame(columns=COLUMNS)


def save_signals(df):

    temp = SIGNALS_FILE + ".tmp"

    df.to_csv(temp, index=False)

    os.replace(temp, SIGNALS_FILE)


# =========================================================
# 5. TELEGRAM
# =========================================================

def telegram_text(message):

    if not BOT_TOKEN or not CHAT_ID:
        return False

    try:

        response = session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json().get("ok", False)

    except Exception as e:

        log.error(f"Telegram text error: {e}")
        return False


def telegram_photo(path, caption):

    if not BOT_TOKEN or not CHAT_ID:
        return False

    try:

        with open(path, "rb") as photo:

            response = session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id": CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={"photo": photo},
                timeout=REQUEST_TIMEOUT
            )

        response.raise_for_status()

        return response.json().get("ok", False)

    except Exception as e:

        log.error(f"Telegram photo error: {e}")
        return False


# =========================================================
# 6. DATA NORMALIZATION
# =========================================================

def normalize_df(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    df.columns = [str(c).lower() for c in df.columns]

    required = ["open", "high", "low", "close"]

    if not all(c in df.columns for c in required):
        return None

    for col in required:
        df[col] = pd.to_numeric(
            df[col], errors="coerce"
        )

    if "volume" not in df.columns:
        df["volume"] = 0.0

    df["volume"] = pd.to_numeric(
        df["volume"], errors="coerce"
    ).fillna(0)

    df = df.dropna(subset=required)

    df = df[
        (df["high"] >= df["low"])
        & (df["high"] >= df["open"])
        & (df["high"] >= df["close"])
        & (df["low"] <= df["open"])
        & (df["low"] <= df["close"])
    ]

    df = df[~df.index.duplicated(keep="last")]

    return df.sort_index()


def remove_open_candle(df):

    if df is None or len(df) < 3:
        return None

    # Консервативно исключаем последнюю свечу,
    # которая может быть еще незакрытой.

    return df.iloc[:-1].copy()


# =========================================================
# 7. INDICATORS
# =========================================================

def calculate_ema(series, period):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def calculate_atr(df, period=14):

    previous_close = df["close"].shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def calculate_rsi(df, period=14):

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0), 100
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0), 0
    )

    return rsi


def prepare_indicators(df):

    df = df.copy()

    df["ema20"] = calculate_ema(df["close"], 20)
    df["atr"] = calculate_atr(df, ATR_PERIOD)
    df["rsi"] = calculate_rsi(df)

    return df


# =========================================================
# 8. CLOSED CANDLE VALIDATION
# =========================================================

def get_market_bias(df):

    if df is None or len(df) < 105:
        return "NEUTRAL"

    df = remove_open_candle(df)

    if df is None or len(df) < 100:
        return "NEUTRAL"

    close = df["close"]

    ema50 = calculate_ema(close, 50)
    ema100 = calculate_ema(close, 100)

    price = close.iloc[-1]

    if (
        price > ema50.iloc[-1]
        and ema50.iloc[-1] > ema100.iloc[-1]
    ):
        return "BULLISH"

    if (
        price < ema50.iloc[-1]
        and ema50.iloc[-1] < ema100.iloc[-1]
    ):
        return "BEARISH"

    return "NEUTRAL"


# =========================================================
# 9. VOLUME FILTER
# =========================================================

def volume_confirmed(df):

    if len(df) < 25:
        return False

    current = df["volume"].iloc[-1]

    average = df["volume"].iloc[-21:-1].mean()

    if average <= 0:
        return True

    return current >= average * VOLUME_MULTIPLIER


# =========================================================
# 10. RISK MANAGEMENT
# =========================================================

def calculate_trade_levels(
    direction,
    entry,
    structural_stop,
    atr
):

    if not np.isfinite(atr) or atr <= 0:
        return None

    buffer = atr * 0.20

    if direction == "LONG":

        sl = structural_stop - buffer

        risk = entry - sl

        if risk <= 0:
            return None

        if risk > atr * 3:
            return None

        tp = entry + risk * MIN_RR

    else:

        sl = structural_stop + buffer

        risk = sl - entry

        if risk <= 0:
            return None

        if risk > atr * 3:
            return None

        tp = entry - risk * MIN_RR

    return {
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "risk": float(risk),
        "rr": MIN_RR
    }


# =========================================================
# 11. STRATEGY: REVERSAL
# LIQUIDITY SWEEP + CHOCH
# =========================================================

def find_reversal(df, bias):

    if len(df) < 30:
        return None

    current = df.iloc[-1]

    history = df.iloc[-21:-1]
    structure = df.iloc[-6:-1]

    atr = current["atr"]
    rsi = current["rsi"]

    if pd.isna(atr) or pd.isna(rsi) or atr <= 0:
        return None

    if not volume_confirmed(df):
        return None

    previous_high = history["high"].max()
    previous_low = history["low"].min()

    structure_high = structure["high"].max()
    structure_low = structure["low"].min()

    # SHORT: снятие верхней ликвидности

    swept_high = (
        current["high"] > previous_high
        and current["close"] < previous_high
    )

    bearish_choch = (
        current["close"] < structure_low
        and current["close"] < current["open"]
    )

    if swept_high and bearish_choch:

        if bias == "BULLISH":
            return None

        if rsi > 75:
            return None

        levels = calculate_trade_levels(
            "SHORT",
            current["close"],
            current["high"],
            atr
        )

        if levels:

            return {
                **levels,
                "direction": "SHORT",
                "strategy": "REVERSAL",
                "reason": "Liquidity Sweep + Bearish CHoCH"
            }

    # LONG: снятие нижней ликвидности

    swept_low = (
        current["low"] < previous_low
        and current["close"] > previous_low
    )

    bullish_choch = (
        current["close"] > structure_high
        and current["close"] > current["open"]
    )

    if swept_low and bullish_choch:

        if bias == "BEARISH":
            return None

        if rsi < 25:
            return None

        levels = calculate_trade_levels(
            "LONG",
            current["close"],
            current["low"],
            atr
        )

        if levels:

            return {
                **levels,
                "direction": "LONG",
                "strategy": "REVERSAL",
                "reason": "Liquidity Sweep + Bullish CHoCH"
            }

    return None


# =========================================================
# 12. STRATEGY: TREND CONTINUATION
# HTF + EMA20 + IMPULSE
# =========================================================

def find_trend_entry(df, bias):

    if len(df) < 30:
        return None

    current = df.iloc[-1]

    history = df.iloc[-9:-1]

    atr = current["atr"]
    ema = current["ema20"]
    rsi = current["rsi"]

    if (
        pd.isna(atr)
        or pd.isna(ema)
        or pd.isna(rsi)
        or atr <= 0
    ):
        return None

    if not volume_confirmed(df):
        return None

    # LONG

    if bias == "BULLISH":

        pullback = (
            current["low"] <= ema + atr * 0.25
            and current["close"] > ema
        )

        impulse = (
            current["close"] > history["high"].max()
            and current["close"] > current["open"]
        )

        rsi_ok = 50 <= rsi <= 75

        if pullback and impulse and rsi_ok:

            stop = history["low"].min()

            levels = calculate_trade_levels(
                "LONG",
                current["close"],
                stop,
                atr
            )

            if levels:

                return {
                    **levels,
                    "direction": "LONG",
                    "strategy": "TREND",
                    "reason": "4H Bullish + EMA20 Pullback + Impulse"
                }

    # SHORT

    if bias == "BEARISH":

        pullback = (
            current["high"] >= ema - atr * 0.25
            and current["close"] < ema
        )

        impulse = (
            current["close"] < history["low"].min()
            and current["close"] < current["open"]
        )

        rsi_ok = 25 <= rsi <= 50

        if pullback and impulse and rsi_ok:

            stop = history["high"].max()

            levels = calculate_trade_levels(
                "SHORT",
                current["close"],
                stop,
                atr
            )

            if levels:

                return {
                    **levels,
                    "direction": "SHORT",
                    "strategy": "TREND",
                    "reason": "4H Bearish + EMA20 Pullback + Impulse"
                }

    return None


# =========================================================
# 13. SIGNAL ANALYSIS
# =========================================================

def analyze_market(
    df15,
    dfhtf,
    market,
    exchange,
    symbol
):

    if df15 is None or dfhtf is None:
        return None

    df15 = remove_open_candle(df15)
    dfhtf = remove_open_candle(dfhtf)

    if df15 is None or dfhtf is None:
        return None

    if len(df15) < 40 or len(dfhtf) < 100:
        return None

    bias = get_market_bias(dfhtf)

    df = prepare_indicators(df15)

    candidates = []

    if ENABLE_REVERSAL:

        signal = find_reversal(df, bias)

        if signal:
            candidates.append(signal)

    if ENABLE_TREND:

        signal = find_trend_entry(df, bias)

        if signal:
            candidates.append(signal)

    # Противоположные сигналы одновременно:
    # пропускаем рынок.

    directions = {
        x["direction"] for x in candidates
    }

    if len(directions) > 1:
        return None

    if not candidates:
        return None

    # При совпадении стратегий приоритет REVERSAL.

    signal = next(
        (
            x for x in candidates
            if x["strategy"] == "REVERSAL"
        ),
        candidates[0]
    )

    signal.update({
        "market": market,
        "exchange": exchange,
        "symbol": symbol,
        "bias": bias,
        "entry_time": df.index[-1].isoformat(),
        "rsi": float(df["rsi"].iloc[-1]),
        "atr": float(df["atr"].iloc[-1])
    })

    return signal


# =========================================================
# 14. MEXC DATA
# =========================================================

def get_mexc_candles(symbol, interval="Min15"):

    try:

        url = (
            "https://contract.mexc.com/api/v1/contract/kline/"
            f"{symbol}?interval={interval}"
        )

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("success"):
            return None

        data = result["data"]

        df = pd.DataFrame({
            "time": pd.to_datetime(
                data["time"],
                unit="s",
                utc=True
            ),
            "open": data["open"],
            "high": data["high"],
            "low": data["low"],
            "close": data["close"],
            "volume": data.get(
                "vol",
                np.zeros(len(data["time"]))
            )
        })

        df.set_index("time", inplace=True)

        return normalize_df(df)

    except Exception as e:

        log.debug(f"MEXC {symbol}: {e}")
        return None


# =========================================================
# 15. BITGET DATA
# =========================================================

def get_bitget_candles(symbol, granularity="15m"):

    try:

        response = session.get(
            "https://api.bitget.com/api/v2/mix/market/candles",
            params={
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "granularity": granularity,
                "limit": "200"
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        result = response.json()

        if result.get("code") != "00000":
            return None

        data = result.get("data", [])

        if not data:
            return None

        df = pd.DataFrame(
            data,
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "turnover"
            ]
        )

        df["time"] = pd.to_datetime(
            pd.to_numeric(df["time"]),
            unit="ms",
            utc=True
        )

        df.set_index("time", inplace=True)

        return normalize_df(df)

    except Exception as e:

        log.debug(f"Bitget {symbol}: {e}")
        return None


# =========================================================
# 16. GOLD DATA
# =========================================================

def get_gold_data():

    try:

        ticker = yf.Ticker("GC=F")

        df = ticker.history(
            period="5d",
            interval="15m",
            auto_adjust=False
        )

        if df.empty:
            return None

        df.columns = [c.lower() for c in df.columns]

        if df.index.tz is not None:
            df.index = (
                df.index.tz_convert("UTC").tz_localize(None)
            )

        return normalize_df(df)

    except Exception as e:

        log.error(f"Gold 15M error: {e}")
        return None


def get_gold_4h():

    try:

        ticker = yf.Ticker("GC=F")

        df = ticker.history(
            period="730d",
            interval="1h",
            auto_adjust=False
        )

        if df.empty:
            return None

        df.columns = [c.lower() for c in df.columns]

        if df.index.tz is not None:
            df.index = (
                df.index.tz_convert("UTC").tz_localize(None)
            )

        df = normalize_df(df)

        if df is None:
            return None

        # Собираем 4H из часовых свечей.

        result = df.resample("4h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        })

        return result.dropna()

    except Exception as e:

        log.error(f"Gold 4H error: {e}")
        return None


# =========================================================
# 17. MARKET DATA ROUTER
# =========================================================

def get_market_data(exchange, symbol, timeframe):

    if exchange == "MEXC":

        interval = (
            "Min15" if timeframe == "15m"
            else "Hour4"
        )

        return get_mexc_candles(
            symbol,
            interval
        )

    if exchange == "Bitget":

        granularity = (
            "15m" if timeframe == "15m"
            else "4H"
        )

        return get_bitget_candles(
            symbol,
            granularity
        )

    return None


# =========================================================
# 18. MARKET DISCOVERY
# =========================================================

def get_all_coins():

    coins = []

    # MEXC

    try:

        response = session.get(
            "https://contract.mexc.com/api/v1/contract/ticker",
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        result = response.json()

        if result.get("success"):

            for item in result["data"]:

                symbol = item.get("symbol", "")

                if not symbol.endswith("_USDT"):
                    continue

                volume = float(
                    item.get("amount24", 0) or 0
                )

                price = float(
                    item.get("lastPrice", 0) or 0
                )

                if (
                    volume >= MIN_VOLUME_USDT
                    and price > 0
                ):

                    coins.append({
                        "market": "CRYPTO",
                        "exchange": "MEXC",
                        "symbol": symbol,
                        "volume": volume
                    })

    except Exception as e:

        log.error(f"MEXC ticker error: {e}")

    # BITGET

    try:

        response = session.get(
            "https://api.bitget.com/api/v2/mix/market/tickers",
            params={
                "productType": "USDT-FUTURES"
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        result = response.json()

        if result.get("code") == "00000":

            for item in result["data"]:

                symbol = item.get("symbol", "")

                volume = float(
                    item.get("usdtVolume", 0) or 0
                )

                price = float(
                    item.get("lastPr", 0) or 0
                )

                if (
                    symbol.endswith("USDT")
                    and volume >= MIN_VOLUME_USDT
                    and price > 0
                ):

                    coins.append({
                        "market": "CRYPTO",
                        "exchange": "Bitget",
                        "symbol": symbol,
                        "volume": volume
                    })

    except Exception as e:

        log.error(f"Bitget ticker error: {e}")

    # Не объединяем монеты с разных бирж.

    coins.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return coins[:MAX_COINS]


# =========================================================
# 19. DUPLICATE PROTECTION
# =========================================================

def has_active_signal(
    db,
    market,
    exchange,
    symbol
):

    if db.empty:
        return False

    active = db[
        (db["market"] == market)
        & (db["exchange"] == exchange)
        & (db["symbol"] == symbol)
        & (db["status"] == "OPEN")
    ]

    return not active.empty


def signal_already_registered(db, signal):

    if db.empty:
        return False

    existing = db[
        (db["market"] == signal["market"])
        & (db["exchange"] == signal["exchange"])
        & (db["symbol"] == signal["symbol"])
        & (db["entry_time"] == signal["entry_time"])
    ]

    return not existing.empty


# =========================================================
# 20. REGISTER SIGNAL
# =========================================================

def register_signal(signal, db):

    record = {
        "id": str(uuid.uuid4())[:8],
        "market": signal["market"],
        "exchange": signal["exchange"],
        "symbol": signal["symbol"],
        "strategy": signal["strategy"],
        "direction": signal["direction"],
        "entry_time": signal["entry_time"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "risk": signal["risk"],
        "status": "OPEN",
        "exit_time": "",
        "exit_price": np.nan,
        "result_r": np.nan,
        "bars": 0
    }

    db = pd.concat(
        [db, pd.DataFrame([record])],
        ignore_index=True
    )

    save_signals(db)

    return db


# =========================================================
# 21. PRICE FORMATTING
# =========================================================

def price_format(value):

    value = float(value)

    if abs(value) >= 1000:
        return f"{value:.2f}"

    if abs(value) >= 1:
        return f"{value:.4f}"

    if abs(value) >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# =========================================================
# 22. SIGNAL MESSAGE
# =========================================================

def format_signal(signal):

    direction = signal["direction"]

    emoji = (
        "🟢" if direction == "LONG"
        else "🔴"
    )

    symbol = html.escape(signal["symbol"])

    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]

    reward = abs(tp - entry)
    risk = abs(entry - sl)

    exchange = html.escape(signal["exchange"])

    text = (
        f"{emoji} <b>TRADING SCANNER 2.0</b>\n\n"
        f"<b>{direction} | {signal['strategy']}</b>\n"
        f"🏦 {exchange}\n"
        f"🪙 <b>{symbol}</b>\n"
        f"📊 Таймфрейм: 15M\n"
        f"📈 Тренд HTF: {signal['bias']}\n"
        f"📉 RSI: {signal['rsi']:.1f}\n\n"
        f"🔎 <b>Подтверждение:</b>\n"
        f"{html.escape(signal['reason'])}\n\n"
        f"💰 Вход: <code>{price_format(entry)}</code>\n"
        f"🛑 SL: <code>{price_format(sl)}</code>\n"
        f"🎯 TP: <code>{price_format(tp)}</code>\n\n"
        f"⚖️ Risk/Reward: "
        f"<b>1:{reward/risk:.2f}</b>\n"
        f"📏 Риск: <code>{price_format(risk)}</code>\n\n"
        f"⚠️ Сигнал сформирован по закрытой свече.\n"
        f"Проверь цену исполнения, комиссию и проскальзывание."
    )

    return text


# =========================================================
# 23. SIGNAL CHART
# =========================================================

def create_chart(df, signal):

    filename = os.path.join(
        BASE_DIR,
        f"chart_{uuid.uuid4().hex[:8]}.png"
    )

    plot = df.tail(60).copy()

    plot.index = pd.to_datetime(plot.index)

    plot = plot.rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume"
    })

    hlines = dict(
        hlines=[
            signal["entry"],
            signal["sl"],
            signal["tp"]
        ],
        colors=[
            "blue",
            "red",
            "green"
        ],
        linestyle="--",
        linewidths=1.3
    )

    mpf.plot(
        plot,
        type="candle",
        style="yahoo",
        volume=True,
        hlines=hlines,
        title=(
            f'{signal["symbol"]} | '
            f'{signal["direction"]} | 15M'
        ),
        tight_layout=True,
        savefig=dict(
            fname=filename,
            dpi=120
        )
    )

    return filename


# =========================================================
# 24. UPDATE SIGNAL PERFORMANCE
# =========================================================

def update_performance(db, market_data):

    if db.empty:
        return db

    changed = False

    for idx, signal in db.iterrows():

        if signal["status"] != "OPEN":
            continue

        key = (
            signal["market"],
            signal["exchange"],
            signal["symbol"]
        )

        df = market_data.get(key)

        if df is None:
            continue

        df = remove_open_candle(df)

        if df is None:
            continue

        entry_time = pd.to_datetime(
            signal["entry_time"],
            utc=True
        )

        times = pd.to_datetime(
            df.index,
            utc=True
        )

        future = df.loc[times > entry_time]

        if future.empty:
            continue

        direction = signal["direction"]

        sl = float(signal["sl"])
        tp = float(signal["tp"])
        entry = float(signal["entry"])
        risk = float(signal["risk"])

        if risk <= 0:
            continue

        for bar_time, candle in future.iterrows():

            hit_sl = (
                candle["low"] <= sl
                if direction == "LONG"
                else candle["high"] >= sl
            )

            hit_tp = (
                candle["high"] >= tp
                if direction == "LONG"
                else candle["low"] <= tp
            )

            # Если SL и TP достигнуты внутри одной свечи,
            # консервативно считаем первым SL.

            if hit_sl:

                db.at[idx, "status"] = "SL"
                db.at[idx, "exit_price"] = sl
                db.at[idx, "result_r"] = -1.0
                db.at[idx, "exit_time"] = str(bar_time)

                changed = True
                break

            if hit_tp:

                db.at[idx, "status"] = "TP"
                db.at[idx, "exit_price"] = tp
                db.at[idx, "result_r"] = (
                    abs(tp - entry) / risk
                )
                db.at[idx, "exit_time"] = str(bar_time)

                changed = True
                break

        if db.at[idx, "status"] != "OPEN":
            continue

        bars = len(future)

        db.at[idx, "bars"] = bars

        if bars >= MAX_SIGNAL_AGE:

            db.at[idx, "status"] = "EXPIRED"

            last_price = float(
                future["close"].iloc[-1]
            )

            if direction == "LONG":

                result = (
                    last_price - entry
                ) / risk

            else:

                result = (
                    entry - last_price
                ) / risk

            db.at[idx, "exit_price"] = last_price
            db.at[idx, "result_r"] = result
            db.at[idx, "exit_time"] = str(
                future.index[-1]
            )

            changed = True

    if changed:
        save_signals(db)

    return db


# =========================================================
# 25. REFRESH ALL OPEN SIGNALS
# =========================================================

def refresh_open_signals(db, market_data):

    if db.empty:
        return db

    active = db[
        db["status"] == "OPEN"
    ]

    unique_keys = active[
        ["market", "exchange", "symbol"]
    ].drop_duplicates()

    for _, signal in unique_keys.iterrows():

        key = (
            signal["market"],
            signal["exchange"],
            signal["symbol"]
        )

        if key in market_data:
            continue

        if signal["market"] == "GOLD":

            market_data[key] = get_gold_data()

        else:

            market_data[key] = get_market_data(
                signal["exchange"],
                signal["symbol"],
                "15m"
            )

        time.sleep(0.2)

    return update_performance(
        db,
        market_data
    )


# =========================================================
# 26. STATISTICS
# =========================================================

def get_statistics(db):

    if db.empty:
        return "Статистика пока отсутствует."

    closed = db[
        db["status"].isin([
            "TP",
            "SL",
            "EXPIRED"
        ])
    ].copy()

    total = len(closed)

    opened = len(
        db[db["status"] == "OPEN"]
    )

    if total == 0:

        return (
            "📊 <b>TRADING SCANNER STATISTICS</b>\n\n"
            f"Всего сигналов: <b>{len(db)}</b>\n"
            f"Открытых: <b>{opened}</b>\n"
            "Закрытых сделок пока нет."
        )

    wins = len(
        closed[closed["status"] == "TP"]
    )

    losses = len(
        closed[closed["status"] == "SL"]
    )

    expired = len(
        closed[closed["status"] == "EXPIRED"]
    )

    decisive = wins + losses

    winrate = (
        wins / decisive * 100
        if decisive > 0
        else 0
    )

    results = pd.to_numeric(
        closed["result_r"],
        errors="coerce"
    ).dropna()

    total_r = results.sum()

    avg_r = (
        results.mean()
        if len(results)
        else 0
    )

    return (
        "📊 <b>TRADING SCANNER STATISTICS</b>\n\n"
        f"Всего сигналов: <b>{len(db)}</b>\n"
        f"Открытых: <b>{opened}</b>\n\n"
        f"🎯 TP: <b>{wins}</b>\n"
        f"🛑 SL: <b>{losses}</b>\n"
        f"⌛ EXPIRED: <b>{expired}</b>\n\n"
        f"Win rate (TP / TP+SL): "
        f"<b>{winrate:.1f}%</b>\n"
        f"Суммарный результат: "
        f"<b>{total_r:+.2f}R</b>\n"
        f"Средний результат: "
        f"<b>{avg_r:+.2f}R</b>"
    )


# =========================================================
# 27. PROCESS ONE CRYPTO MARKET
# =========================================================

def process_market(
    market,
    exchange,
    symbol,
    db,
    market_data
):

    key = (
        market,
        exchange,
        symbol
    )

    # 15M

    df15 = market_data.get(key)

    if df15 is None:

        df15 = get_market_data(
            exchange,
            symbol,
            "15m"
        )

        market_data[key] = df15

    if df15 is None:
        return db

    # 4H

    htf_key = (
        "HTF",
        exchange,
        symbol
    )

    df4h = market_data.get(htf_key)

    if df4h is None:

        df4h = get_market_data(
            exchange,
            symbol,
            "4h"
        )

        market_data[htf_key] = df4h

    if df4h is None:
        return db

    # Обновляем старые сделки.

    db = update_performance(
        db,
        {key: df15}
    )

    if has_active_signal(
        db,
        market,
        exchange,
        symbol
    ):
        return db

    # Анализ рынка.

    signal = analyze_market(
        df15,
        df4h,
        market,
        exchange,
        symbol
    )

    if not signal:
        return db

    if signal_already_registered(db, signal):
        return db

    # Сохраняем сигнал.

    db = register_signal(
        signal,
        db
    )

    message = format_signal(signal)

    chart = None

    try:

        closed_df = remove_open_candle(df15)

        chart = create_chart(
            prepare_indicators(closed_df),
            signal
        )

        if not telegram_photo(chart, message):
            telegram_text(message)

    except Exception as e:

        log.error(
            f"Chart error {exchange} {symbol}: {e}"
        )

        telegram_text(message)

    finally:

        if chart and os.path.exists(chart):
            os.remove(chart)

    log.info(
        f"SIGNAL {exchange} {symbol} "
        f"{signal['direction']} {signal['strategy']}"
    )

    return db


# =========================================================
# 28. PROCESS GOLD
# =========================================================

def process_gold(db, market_data):

    market = "GOLD"
    exchange = "Yahoo Finance"
    symbol = "GC=F"

    key = (
        market,
        exchange,
        symbol
    )

    try:

        df15 = market_data.get(key)

        if df15 is None:

            df15 = get_gold_data()

            market_data[key] = df15

        df4h = get_gold_4h()

        if df15 is None or df4h is None:
            return db

        db = update_performance(
            db,
            {key: df15}
        )

        if has_active_signal(
            db,
            market,
            exchange,
            symbol
        ):
            return db

        signal = analyze_market(
            df15,
            df4h,
            market,
            exchange,
            symbol
        )

        if not signal:
            return db

        if signal_already_registered(db, signal):
            return db

        db = register_signal(
            signal,
            db
        )

        message = format_signal(signal)

        chart = None

        try:

            chart = create_chart(
                prepare_indicators(
                    remove_open_candle(df15)
                ),
                signal
            )

            if not telegram_photo(chart, message):
                telegram_text(message)

        finally:

            if chart and os.path.exists(chart):
                os.remove(chart)

        log.info(
            f"GOLD SIGNAL {signal['direction']} "
            f"{signal['strategy']}"
        )

    except Exception as e:

        log.error(
            f"Gold processing error: {e}"
        )

    return db


# =========================================================
# 29. MAIN
# =========================================================

def main():

    if not BOT_TOKEN or not CHAT_ID:

        raise RuntimeError(
            "Set BOT_TOKEN and CHAT_ID environment variables."
        )

    log.info("=" * 55)
    log.info("TRADING SCANNER 2.0 STARTED")
    log.info("=" * 55)

    db = load_signals()

    market_data = {}

    # Сначала обновляем все предыдущие сигналы,
    # включая монеты, которые выпали из топ-80.

    log.info("Refreshing open signals...")

    db = refresh_open_signals(
        db,
        market_data
    )

    # GOLD

    log.info("Checking GOLD GC=F...")

    db = process_gold(
        db,
        market_data
    )

    # CRYPTO

    log.info("Loading crypto markets...")

    coins = get_all_coins()

    log.info(
        f"Markets selected: {len(coins)}"
    )

    start_time = time.time()

    for number, coin in enumerate(coins, 1):

        if (
            time.time() - start_time
            > MAX_SCAN_SECONDS
        ):

            log.warning(
                "Crypto scan time limit reached."
            )

            break

        try:

            db = process_market(
                coin["market"],
                coin["exchange"],
                coin["symbol"],
                db,
                market_data
            )

        except Exception as e:

            log.error(
                f'{coin["exchange"]} '
                f'{coin["symbol"]}: {e}'
            )

        if number % 10 == 0:

            log.info(
                f"Scanned {number}/{len(coins)}"
            )

        time.sleep(0.15)

    # Обновляем результаты по всем рынкам,
    # данные которых были получены в этом запуске.

    db = update_performance(
        db,
        market_data
    )

    save_signals(db)

    # Статистика

    statistics = get_statistics(db)

    log.info("SCAN COMPLETED")

    log.info(
        statistics.replace("<b>", "")
                  .replace("</b>", "")
    )

    telegram_text(statistics)


# =========================================================
# 30. START
# =========================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        log.exception(
            f"FATAL ERROR: {e}"
        )
