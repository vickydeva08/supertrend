import datetime
import time
import pandas as pd
import pandas_ta as ta
from SharekhanApi.sharekhanConnect import SharekhanConnect

# ==============================================================================
# 1. SHAREKHAN API CREDENTIALS & SESSION CONFIGURATION
# ==============================================================================
API_KEY = "YOUR_SHAREKHAN_API_KEY"
SECRET_KEY = "YOUR_SHAREKHAN_SECRET_KEY"
CUSTOMER_ID = "YOUR_CUSTOMER_ID"          # Also used as channelUser (LoginId)

# Leave ACCESS_TOKEN blank ("") if generating a fresh one via REQUEST_TOKEN below
ACCESS_TOKEN = "YOUR_DAILY_ACCESS_TOKEN"
REQUEST_TOKEN = ""                        # Paste decrypted request_token if generating token
VENDOR_KEY = ""                           # Optional: vendor key if applicable, else ""
VERSION_ID = ""                           # Optional: "1005" or "1006", else ""
STATE = 12345

# ==============================================================================
# 2. EXCHANGE & CONTRACT CONFIGURATION
# ==============================================================================
EXCHANGE_INDEX = "NC"                     # NSE Cash / Index
EXCHANGE_FNO = "NF"                       # NSE Futures & Options

NIFTY_SCRIP_CODE = 26000                  # Nifty 50 Index scripCode (verify via master("NC"))

# Option Contract Details (Verify from sharekhan.master("NF"))
CE_SCRIP_CODE = 12345                     # Your ITM Call scripCode (int)
CE_TRADING_SYMBOL = "NIFTY"               # Symbol from Sharekhan script master
CE_STRIKE_PRICE = "24000"                 # Strike price as string

PE_SCRIP_CODE = 12346                     # Your ITM Put scripCode (int)
PE_TRADING_SYMBOL = "NIFTY"               # Symbol from Sharekhan script master
PE_STRIKE_PRICE = "24200"                 # Strike price as string

EXPIRY_DATE = "26/11/2026"                # Contract expiry in DD/MM/YYYY format
LOT_SIZE = 75                             # NSE Nifty lot size (update if revised by NSE)

SQUARE_OFF_TIME = datetime.time(15, 15)
MARKET_OPEN_TIME = datetime.time(9, 15)


# ==============================================================================
# 3. SHAREKHAN LOGIN & CLIENT INITIALIZATION
# ==============================================================================
def init_sharekhan_client() -> SharekhanConnect:
    """Initializes SharekhanConnect using existing ACCESS_TOKEN or generates one."""
    global ACCESS_TOKEN

    if ACCESS_TOKEN and ACCESS_TOKEN != "YOUR_DAILY_ACCESS_TOKEN":
        return SharekhanConnect(API_KEY, ACCESS_TOKEN)

    login = SharekhanConnect(API_KEY)
    if not REQUEST_TOKEN:
        url = login.login_url(vendor_key=VENDOR_KEY, version_id=VERSION_ID)
        print("1. Open this URL in your browser and log in to Sharekhan:")
        print(url)
        raise ValueError(
            "Paste the decrypted request_token into REQUEST_TOKEN (or set ACCESS_TOKEN) and re-run."
        )

    if VERSION_ID:
        session = login.generate_session(REQUEST_TOKEN, SECRET_KEY)
        ACCESS_TOKEN = login.get_access_token(
            API_KEY, session, STATE, versionId=VERSION_ID
        )
    else:
        session = login.generate_session_without_versionId(REQUEST_TOKEN, SECRET_KEY)
        ACCESS_TOKEN = login.get_access_token(API_KEY, session, STATE)

    print(f"Generated Daily Access Token: {ACCESS_TOKEN}")
    return SharekhanConnect(API_KEY, ACCESS_TOKEN)


sharekhan = init_sharekhan_client()


# ==============================================================================
# 4. HELPER: SCRIP MASTER LOOKUP (OPTIONAL UTILITY)
# ==============================================================================
def print_nifty_option_master(expiry: str = EXPIRY_DATE):
    """Fetches NF script master so you can verify exact scripCode & tradingSymbol."""
    master_res = sharekhan.master(EXCHANGE_FNO)
    records = master_res.get("data", master_res) if isinstance(master_res, dict) else master_res
    if not records:
        print("Could not fetch NF script master:", master_res)
        return
    df_master = pd.DataFrame(records)
    nifty_opts = df_master[
        df_master.astype(str).apply(lambda r: "NIFTY" in " ".join(r.values), axis=1)
    ]
    print(nifty_opts.head(15))


# ==============================================================================
# 5. TIMESTAMP PARSING & INTRADAY CANDLES (SUPERTREND 7, 3)
# ==============================================================================
def parse_timestamp(series: pd.Series) -> pd.Series:
    """Converts numeric epoch (s or ms) or datetime strings to Asia/Kolkata."""
    if pd.api.types.is_numeric_dtype(series):
        max_val = series.dropna().max()
        unit = "ms" if max_val and max_val > 1e12 else "s"
        return pd.to_datetime(series, unit=unit, utc=True).dt.tz_convert("Asia/Kolkata")

    dt = pd.to_datetime(series)
    if dt.dt.tz is None:
        return dt.dt.tz_localize("Asia/Kolkata")
    return dt.dt.tz_convert("Asia/Kolkata")


def fetch_intraday_candles(interval: str = "5minute") -> pd.DataFrame | None:
    """Fetches intraday candles for Nifty and computes Supertrend (7, 3)."""
    response = sharekhan.historicaldata(
        exchange=EXCHANGE_INDEX,
        scripcode=int(NIFTY_SCRIP_CODE),
        interval=interval,
    )
    if not isinstance(response, dict):
        return None

    candles = response.get("data") or response.get("candles")
    if not candles:
        return None

    df = pd.DataFrame(candles)
    time_col = next(
        (c for c in ["timestamp", "date", "time", "datetime"] if c in df.columns),
        None,
    )
    if time_col is None:
        return None

    df["timestamp"] = parse_timestamp(df[time_col])
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)

    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            return None
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["open", "high", "low", "close"], inplace=True)
    if len(df) < 10:
        return None

    st = ta.supertrend(df["high"], df["low"], df["close"], length=7, multiplier=3)
    return pd.concat([df, st], axis=1)


# ==============================================================================
# 6. OFFICIAL SHAREKHAN F&O ORDER EXECUTION
# ==============================================================================
def place_fno_order(
    scrip_code: int,
    trading_symbol: str,
    txn_type: str,        # "B" (Buy) or "S" (Sell)
    qty: int,
    option_type: str,     # "CE" or "PE"
    strike_price: str,
):
    """Places an F&O order using Sharekhan's official placeOrder schema."""
    orderparams = {
        "customerId": CUSTOMER_ID,
        "scripCode": int(scrip_code),
        "tradingSymbol": trading_symbol,
        "exchange": EXCHANGE_FNO,
        "transactionType": txn_type,          # "B" or "S"
        "quantity": int(qty),
        "disclosedQty": 0,
        "price": "0",                         # "0" for Market execution
        "triggerPrice": "0",
        "rmsCode": "ANY",
        "afterHour": "N",
        "orderType": "NORMAL",
        "channelUser": CUSTOMER_ID,           # LoginId as ChannelUser
        "validity": "GFD",                    # Good For Day
        "requestType": "NEW",
        "productType": "INVESTMENT",          # INVESTMENT / BIGTRADE / BIGTRADEPLUS
        # Required F&O Parameters:
        "instrumentType": "OI",               # Option Index
        "strikePrice": str(strike_price),
        "optionType": option_type,            # "CE" or "PE"
        "expiry": EXPIRY_DATE,                # "DD/MM/YYYY"
    }

    res = sharekhan.placeOrder(orderparams)
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now_str}] {txn_type} {option_type} ({scrip_code}) qty={qty} -> {res}")
    return res


# ==============================================================================
# 7. MAIN BOT LOOP
# ==============================================================================
def run_bot():
    current_position = None  # None | "CE" | "PE"
    last_processed_candle = None
    print(f"Bot initialized. Request Headers: {sharekhan.requestHeaders()}")

    while True:
        now_dt = datetime.datetime.now()
        now_t = now_dt.time()

        # Square off open option leg and exit at 3:15 PM IST
        if now_t >= SQUARE_OFF_TIME:
            if current_position == "CE":
                place_fno_order(
                    CE_SCRIP_CODE, CE_TRADING_SYMBOL, "S", LOT_SIZE, "CE", CE_STRIKE_PRICE
                )
                current_position = None
            elif current_position == "PE":
                place_fno_order(
                    PE_SCRIP_CODE, PE_TRADING_SYMBOL, "S", LOT_SIZE, "PE", PE_STRIKE_PRICE
                )
                current_position = None
            print("3:15 PM square-off complete. Stopping bot.")
            break

        if now_t < MARKET_OPEN_TIME:
            time.sleep(10)
            continue

        try:
            df = fetch_intraday_candles(interval="5minute")
            if df is not None and len(df) >= 2:
                closed_ts = df.index[-2]
                latest_closed = df.iloc[-2]
                direction = latest_closed.get("SUPERTd_7_3.0")

                # Act once per newly closed 5-minute candle
                if closed_ts != last_processed_candle and direction in (1, -1):
                    last_processed_candle = closed_ts
                    print(f"[{closed_ts}] Closed Candle | Supertrend Direction: {direction}")

                    if direction == 1 and current_position != "CE":
                        if current_position == "PE":
                            place_fno_order(
                                PE_SCRIP_CODE, PE_TRADING_SYMBOL, "S", LOT_SIZE, "PE", PE_STRIKE_PRICE
                            )
                        place_fno_order(
                            CE_SCRIP_CODE, CE_TRADING_SYMBOL, "B", LOT_SIZE, "CE", CE_STRIKE_PRICE
                        )
                        current_position = "CE"

                    elif direction == -1 and current_position != "PE":
                        if current_position == "CE":
                            place_fno_order(
                                CE_SCRIP_CODE, CE_TRADING_SYMBOL, "S", LOT_SIZE, "CE", CE_STRIKE_PRICE
                            )
                        place_fno_order(
                            PE_SCRIP_CODE, PE_TRADING_SYMBOL, "B", LOT_SIZE, "PE", PE_STRIKE_PRICE
                        )
                        current_position = "PE"

        except Exception as exc:
            print(f"[{now_dt.strftime('%H:%M:%S')}] Loop error: {exc}")

        time.sleep(15)


if __name__ == "__main__":
    # Uncomment the line below once to inspect NF scrip codes & symbols:
    # print_nifty_option_master()
    run_bot()