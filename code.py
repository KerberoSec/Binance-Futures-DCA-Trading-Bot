"""
====================================================================================================
Binance USDT-M Futures - Futures DCA Trading Bot
====================================================================================================

An institutional-grade, fully autonomous algorithmic Dollar-Cost Averaging (DCA) trading system
specifically engineered for high-frequency execution on Binance USDT-M Perpetual Futures (SOLUSDT).

Author: Arun Kumar
LinkedIn: https://github.com/KerberoSec/
GitHub: https://github.com/KerberoSec/
Instagram: https://www.instagram.com/so_far_from_your_heart/
X / Twitter: https://x.com/ArunKumar310706

====================================================================================================
1. MATHEMATICAL EXECUTION & CAPITAL BUDGETING MODEL
====================================================================================================

A. Initial Base Order Entry:
   - Evaluated at the start of each round as a percentage of total allocated wallet balance:
         Base_Margin_Target = Wallet_Equity * (BASE_ORDER_PCT_OF_ALLOCATION / 100)
   - Bounded by exchange MIN_NOTIONAL rules (enforcing minimum order value >= 6.0 USDT):
         Min_Required_Margin = (MIN_NOTIONAL / LEVERAGE)
         Base_Margin = max(Base_Margin_Target, Min_Required_Margin)

B. Martingale DCA Averaging Ladder (18 Rungs):
   - Price Step Spacing (Arithmetic Grid):
         For LONG:   Price_i = Entry_Price_0 * (1 - (PRICE_STEP_PCT / 100) * i)
         For SHORT:  Price_i = Entry_Price_0 * (1 + (PRICE_STEP_PCT / 100) * i)
   - Volume Scaling (Geometric Progression with 1.1x Multiplier):
         Margin_i = Base_Margin * (ORDER_SIZE_MULTIPLIER ** i)
         Position_Notional_i = Margin_i * LEVERAGE
         Quantity_i = quantize(Position_Notional_i / Price_i, step_size, ROUND_DOWN)

C. Mathematical Solvency Guarantee (Budget Cap Pre-Check):
   - To guarantee that all 18 DCA rungs can be placed without running out of margin or
     exceeding Binance leverage bracket limits, the total ladder multiplier sum is solved:
         Multiplier_Sum = Sum_{i=0}^{18} (1.10 ** i) ≈ 51.16
         Max_Allowed_Base_Margin = (Allocated_Equity * 0.95) / Multiplier_Sum
   - The bot dynamically constrains Base_Margin <= Max_Allowed_Base_Margin before placing order 0.

D. Volume-Weighted Average Entry Price (VWAP Recalculation):
   - Updated atomically on every partial or full execution fill:
         VWAP = Sum(Fill_Quantity_k * Fill_Price_k) / Sum(Fill_Quantity_k)
   - Total Position Size:
         Position_Qty = Sum(Fill_Quantity_k)

E. Fixed Limit Take-Profit Target (+1.0% Mean-Reversion Target):
   - Placed as a resting limit order relative to the dynamically changing VWAP:
         For LONG:   TP_Price = VWAP * (1 + (TAKE_PROFIT_PCT / 100))
         For SHORT:  TP_Price = VWAP * (1 - (TAKE_PROFIT_PCT / 100))
   - As price drops and lower DCA rungs execute, VWAP shifts downward, lowering the TP target
     and allowing the bot to exit profitably on minor market pullbacks.

====================================================================================================
2. DYNAMIC LIQUIDATION-ANCHORED STOP LOSS ENGINE
====================================================================================================

A. Flaws of Static Percentage Stop Losses:
   - Static percentage SLs (e.g. fixed 40%) fail to adapt when account equity, unrealized PnL, or
     cross-margin collateral changes, leading to either premature stop-outs or unexpected liquidation.

B. Real-Time Liquidation Floating Formula:
   - Queries the real-time exchange liquidation price from Binance's `futures_position_information()`.
   - Anchors the Stop-Loss trigger price strictly on the safe side of liquidation:
         • For LONG positions:  SL_Price = Liquidation_Price * (1 + (LIQUIDATION_BUFFER_PCT / 100))
         • For SHORT positions: SL_Price = Liquidation_Price * (1 - (LIQUIDATION_BUFFER_PCT / 100))
   - Default buffer: LIQUIDATION_BUFFER_PCT = 1.5% (SL sits exactly 1.5% above liquidation for LONG).
   - If exchange `liquidationPrice` is unpopulated (e.g. fresh cross-margin), calculates theoretical
     liquidation using maintenance margin rate (MMR = 0.5%):
         Buffer_Per_Unit = max(0, Total_Equity - (Position_Qty * VWAP * MMR)) / Position_Qty
         Est_Liq_Price = VWAP - Buffer_Per_Unit (for LONG)

C. Continuous Replacement & Debounced Coalescing:
   - Every DCA fill shifts the liquidation price. The bot cancels the old Algo Stop-Loss and
     places a new one at the updated liquidation anchor after a 1.5s debounce delay
     (TP_SL_REFRESH_DEBOUNCE_SECONDS = 1.5) to avoid rate limit spamming during rapid fill bursts.

====================================================================================================
3. DIRECTIONAL CONTROLS & TRADING MODES
====================================================================================================

- TRADING_MODE = "LONG_ONLY" (Active Default):
    Strictly buys dips and sells tops in the LONG direction. Completely eliminates short-squeeze
    risks during macro cryptocurrency bull runs. If Stop-Loss is hit, the next round resumes LONG.
- TRADING_MODE = "SHORT_ONLY":
    Strictly opens SHORT positions. Ideal for confirmed bear market regimes.
- TRADING_MODE = "BOTH":
    Permits bi-directional trading. If ENABLE_AUTO_FLIP = True, reverses direction (LONG <-> SHORT)
    upon hitting Stop-Loss. If USE_TREND_MA_FILTER = True, uses 20-SMA on 5m candles to choose bias.

====================================================================================================
4. MULTI-THREADED ARCHITECTURE & RESILIENCE MODEL
====================================================================================================

- Thread Isolation & Concurrency Safety:
    • Each thread uses its own dedicated `binance.client.Client` instance via `threading.local()`.
    • All REST operations route through a thread-safe sliding-window rate limiter (`RateLimiter`).
    • Re-entrant locks (`threading.RLock`) protect in-memory state (`SymbolState`) from race conditions.
    • Network calls (order creation/cancellation) execute outside state locks to prevent deadlocks.

- Event-Driven WebSocket Pipeline (Primary Path):
    • ThreadedWebsocketManager ingests live account pushes (`ORDER_TRADE_UPDATE`, `ALGO_UPDATE`).
    • Events are routed into per-symbol dedicated FIFO queues (`queue.Queue`) processed sequentially
      by worker threads (`ws-worker-SOLUSDT`) to guarantee chronological event handling.
    • 120-Second Silence Watchdog (`ws_heartbeat_watchdog`) monitors a 1s ticker stream and initiates
      automatic exponential backoff reconnects if the socket connection drops silently.

- Background REST Reconciliation (Safety Net Path):
    • Runs continuously every 30s (`poll_symbol_orders`) as a failsafe to detect manual interventions,
      exchange maintenance disconnects, or dropped WebSocket frames.

====================================================================================================
5. MULTI-TIER RISK MANAGEMENT & EMERGENCY CONTROLS
====================================================================================================

1. Priority Peak Floating Drawdown Guard (MAX_DRAWDOWN_FROM_PEAK_PCT = 40.0%):
   - Continuously evaluates total margin balance against rolling peak equity in real-time.
   - If floating loss exceeds 40% from peak, triggers immediate MARKET exit (bypassing resting SL
     to evade whale stop-runs) and restarts a clean round in the same direction after cooldown.

2. Hard Cumulative Account Kill Switch (HARD_KILL_SWITCH_DRAWDOWN_PCT = 50.0%):
   - Permanent emergency stop. If account equity drops 50% from all-time peak, flattens all open
     positions via multi-attempt market loops, cancels all resting orders, halts trading, and
     dispatches an urgent out-of-band alert via Telegram/Discord webhooks.

3. Anti-Whipsaw Guard (ENABLE_ANTI_WHIPSAW_GUARD = True):
   - Detects choppy sideways whipsaws. If 2 consecutive Stop-Losses occur (MAX_CONSECUTIVE_SL = 2),
     pauses trading for 5 minutes (WHIPSAW_COOLDOWN_SECONDS = 300) before resuming.

4. Flash-Dump Trend Pause (TREND_PAUSE_SECONDS = 60):
   - Enforces a 60-second cooling pause after any Stop-Loss or Drawdown exit to prevent buying
     falling knives during panic liquidation cascades.

====================================================================================================
6. PERSISTENCE, OBSERVABILITY & ANALYTICS
====================================================================================================

- State File (`bot_state.json`):
    Persists round numbers, active directions, and peak equity baselines. Restores state on restart.
- Trade History Ledger (`trade_history.csv`):
    Appends full execution metrics upon each round completion: Timestamp, Round #, Symbol, Direction,
    Entry Price, Exit Price, Quantity, Realized PnL in USDT, Duration (seconds), and Exit Reason.
- Rotating File Logs (`bot.log`):
    Outputs structured timestamps, order IDs, fill prices, and protection status with 20MB log rotation.

Requirements: pip install --upgrade "python-binance>=1.0.37"
====================================================================================================
"""

import os
import re
import time
import json
import csv
import logging
import threading
import queue
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from enum import Enum
from logging.handlers import RotatingFileHandler
from typing import Optional, Union, List, Dict, Set, Tuple
import requests
from binance import ThreadedWebsocketManager
from binance.client import Client
from binance.enums import (
    SIDE_BUY,
    SIDE_SELL,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    TIME_IN_FORCE_GTC,
)

# Compatibility check for Binance Algo Order Stop-Market constants
try:
    from binance.enums import FUTURE_ORDER_TYPE_STOP_MARKET as ORDER_TYPE_STOP_MARKET
except ImportError:
    ORDER_TYPE_STOP_MARKET = "STOP_MARKET"
from binance.exceptions import BinanceAPIException, BinanceOrderException

# ===========================================================================
# 1. CONFIGURATION & TRADING PARAMETERS
# ===========================================================================

# --- AUTO LOAD .ENV FILE IF PRESENT ---
def _load_env_file(filepath: str = ".env"):
    """Loads key-value pairs from a local .env file into os.environ if present."""
    if not os.path.exists(filepath):
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

_load_env_file()

# --- API CREDENTIALS & NETWORK SETTINGS ---
# Note: For production use, set these via OS environment variables (os.environ["BINANCE_API_KEY"]) or a .env file.
API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
TESTNET = True  # True: Connects to Binance Futures Testnet (fapi.binance.com testnet); False: Live Mainnet

# --- SYMBOLS & MARGIN LEVERAGE SETTINGS ---
SYMBOLS = ["SOLUSDT"]  # List of target USDT-M perpetual contracts to trade
LEVERAGE = 2           # Position leverage multiplier (2x for maximum safety and buffer)
MARGIN_TYPE = "CROSSED" # "CROSSED": shares entire wallet balance across positions; "ISOLATED": position isolated margin

# Percentage of total account wallet balance allocated per symbol (1.0 = 100% allocation for single-symbol trading)
ACCOUNT_ALLOCATION_FRACTION_PER_SYMBOL = 1

# --- POSITION SIZING & COMPOUNDING CONTROLS ---
# If USE_DYNAMIC_SIZING = True, the bot queries the live account wallet balance at the start of each round
# and dynamically sizes the base order as a percentage of available equity to compound profits.
USE_DYNAMIC_SIZING = True
BASE_ORDER_PCT_OF_ALLOCATION = 2   # Percentage of allocated wallet balance for the initial base entry order (2.0%)
BASE_ORDER_USDT = 6                # Fallback fixed base order margin in USDT if dynamic sizing is disabled

# --- MARTINGALE DCA LADDER SPECIFICATIONS ---
PRICE_STEP_PCT = 1.0            # Distance between each successive DCA averaging rung in percent (1.0% spacing)
TAKE_PROFIT_PCT = 1.0           # Profit target above/below volume-weighted average entry price (1.0%)
ORDER_SIZE_MULTIPLIER = 1.1     # Martingale volume scale factor (each rung's margin = previous rung * 1.1x)
MIN_DCA_ORDERS = 18             # Total number of resting DCA limit averaging orders placed on exchange (18 rungs)

# ===========================================================================
# DIRECTION & TRADING MODE CONTROLS
# ===========================================================================
# TRADING_MODE options:
#   "LONG_ONLY"  : Strictly opens LONG rounds (avoids infinite short-squeeze risk during crypto bull runs)
#   "SHORT_ONLY" : Strictly opens SHORT rounds
#   "BOTH"       : Can alternate between LONG and SHORT based on auto-flip or macro trend filters
TRADING_MODE = "LONG_ONLY"

# When TRADING_MODE = "BOTH":
#   ENABLE_AUTO_FLIP = True  -> Automatically reverses trading direction (LONG <-> SHORT) on Stop-Loss hit
#   ENABLE_AUTO_FLIP = False -> Restarts the next round in the SAME direction even after a Stop-Loss hit
ENABLE_AUTO_FLIP = False

# Default starting direction on fresh startup when TRADING_MODE = "BOTH"
INITIAL_DIRECTION = "LONG"

# ===========================================================================
# DYNAMIC LIQUIDATION-ANCHORED STOP LOSS & RISK CONTROLS
# ===========================================================================
# When USE_DYNAMIC_LIQUIDATION_SL = True:
# The bot queries Binance's live calculated liquidation price from `futures_position_information()`
# and dynamically places the Stop-Loss order at a safe buffer distance before liquidation.
USE_DYNAMIC_LIQUIDATION_SL = True
LIQUIDATION_BUFFER_PCT = 1.5         # Safety margin before liquidation (e.g. 1.5% above liq for LONG, 1.5% below for SHORT)
FALLBACK_STOP_LOSS_PCT = 40.0        # Fallback static SL % if position is too small or liquidation price is undefined
STOP_LOSS_PCT = 40                   # Baseline reference Stop-Loss percentage
PROTECTIVE_ORDER_WORKING_TYPE = "CONTRACT_PRICE"  # Trigger evaluation price: "CONTRACT_PRICE" (Last Price) or "MARK_PRICE"

# --- EMERGENCY DRAWDOWN & KILL SWITCH THRESHOLDS ---
MAX_DRAWDOWN_FROM_PEAK_PCT = 40.0   # 40% peak floating equity drawdown triggers immediate market reset and cooldown
HARD_KILL_SWITCH_DRAWDOWN_PCT = 50.0 # 50% cumulative loss triggers permanent shutdown, position flattening, and emergency alert
DRAWDOWN_BASIS = "EQUITY"           # "EQUITY": totalMarginBalance (includes unrealized PnL); "WALLET": totalWalletBalance
TREND_PAUSE_SECONDS = 60           # 60-second cooldown pause after Stop-Loss or Drawdown exit to avoid buying falling knives

# --- TIME-BASED ROUND RESET CONTROLS ---
ENABLE_TIME_BASED_ROUND_RESET = False  # Set True to enable automatic round reset if a trade remains open past max duration

# --- ANTI-WHIPSAW CHOPPY MARKET PROTECTION ---
ENABLE_ANTI_WHIPSAW_GUARD = True    # Activates extended cooldown if consecutive Stop-Losses occur in choppy markets
MAX_CONSECUTIVE_SL = 2              # Number of back-to-back SL hits that trigger the anti-whipsaw cooldown
WHIPSAW_COOLDOWN_SECONDS = 300      # 5-minute cooldown period during whipsaw market conditions
USE_TREND_MA_FILTER = False         # If True and TRADING_MODE="BOTH", uses a 20-period 5m MA to confirm macro trend direction

# --- WEBSOCKET, REST POLLING & RATE LIMIT CADENCE ---
POLL_INTERVAL_SECONDS = 3                # REST polling interval when WebSocket stream is disconnected
RECONCILE_POLL_INTERVAL_SECONDS = 30     # Background REST safety-net reconciliation cadence while WebSocket is healthy
WATCHDOG_INTERVAL_SECONDS = 20           # Main-loop health check, idle detector, and state persistence interval

MAX_ROUND_OPEN_RETRIES = 5               # Max consecutive failed round-open attempts before halting a symbol
STATE_FILE = "bot_state.json"            # Local JSON file persisting round count, active direction, and peak equity
TRADE_HISTORY_FILE = "trade_history.csv" # Local CSV ledger logging all closed round metrics and realized PnL

# --- RATE LIMITING & THROTTLING ---
RATE_LIMIT_MAX_CALLS = 2000              # Maximum REST calls allowed per period (Binance IP weight limit is 2400/min)
RATE_LIMIT_PERIOD_SECONDS = 60.0         # Sliding window duration in seconds for rate limiter
MAX_ALGO_ORDERS_PER_MINUTE = 60          # Maximum algo order creation attempts per symbol in 60s window before throttling
WS_HEARTBEAT_TIMEOUT_SECONDS = 120       # Silence limit in seconds for WebSocket stream before forcing automatic reconnect
TP_SL_REFRESH_DEBOUNCE_SECONDS = 1.5     # Debounce delay to coalesce rapid consecutive micro-fills into a single TP/SL update


def send_alert(message: str):
    """Dispatches out-of-band notifications to Telegram, Discord, or custom HTTP webhooks.
    Executes asynchronously in a background daemon thread to ensure trading execution is never blocked."""
    log.critical(f"[OUT-OF-BAND ALERT] {message}")

    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not (webhook_url or (telegram_token and telegram_chat_id)):
        return

    def _post():
        try:
            import urllib.request
            import json

            # 1. Telegram Notification Dispatch
            if telegram_token and telegram_chat_id:
                tg_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                payload = json.dumps({"chat_id": telegram_chat_id, "text": f"[BOT ALERT] {message}"}).encode("utf-8")
                req = urllib.request.Request(tg_url, data=payload, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)

            # 2. Discord / Generic Webhook Dispatch
            if webhook_url:
                if "discord.com" in webhook_url or "discordapp.com" in webhook_url:
                    payload = json.dumps({"content": f"**[BOT ALERT]**: {message}"}).encode("utf-8")
                else:
                    payload = json.dumps({"text": f"[BOT ALERT] {message}"}).encode("utf-8")
                req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            log.error(f"Failed to dispatch out-of-band alert: {e}")

    threading.Thread(target=_post, daemon=True, name="alert-thread").start()


def _get_next_log_filename(base_name: str = "bot.log") -> str:
    """If base_name exists, auto-increment to bot1.log, bot2.log, bot3.log, etc."""
    if not os.path.exists(base_name):
        return base_name
    name, ext = os.path.splitext(base_name)
    counter = 1
    while True:
        candidate = f"{name}{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1

LOG_FILE = _get_next_log_filename("bot.log")

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=20*1024*1024, backupCount=10, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
log = logging.getLogger("dca_bot")
log.info(f"Logging initialized | Console output & File output active -> {LOG_FILE}")


# ===========================================================================
# 2. ENUMS & IN-MEMORY DATA MODELS
# ===========================================================================

class Direction(Enum):
    """Trading direction for the perpetual futures position."""
    LONG = "LONG"    # Buy to open, sell to close
    SHORT = "SHORT"  # Sell to open, buy to close


@dataclass
class Fill:
    """Represents an individual filled order execution within the current round."""
    price: Decimal    # Execution fill price
    qty: Decimal      # Executed base asset quantity
    order_id: int     # Exchange order identifier (-1 for historical/resynced entries)


@dataclass
class SymbolState:
    """Complete in-memory state tracking container for an individual trading symbol.
    Protected by an internal re-entrant lock (`lock`) to ensure thread-safe concurrency."""
    symbol: str                                             # Trading pair symbol (e.g. "SOLUSDT")
    direction: Direction = Direction.LONG                   # Current active round direction (LONG or SHORT)
    entry_orders: list = field(default_factory=list)        # Chronological list of Fill objects for average price calculation
    average_price: Decimal = Decimal("0")                   # Volume-weighted average entry price across all filled rungs
    position_qty: Decimal = Decimal("0")                    # Total current accumulated position quantity
    resting_dca_orders: list = field(default_factory=list)  # List of resting limit order IDs currently open on exchange
    rung_last_qty: dict = field(default_factory=dict)       # Maps order_id -> cumulative executedQty already counted (partial fills)
    rung_last_cost: dict = field(default_factory=dict)      # Maps order_id -> cumulative executed quote cost (qty * price)
    take_profit_algo_id: Optional[int] = None               # Active Binance Take-Profit Algo Order ID
    stop_loss_algo_id: Optional[int] = None                 # Active Binance Stop-Loss Algo Order ID
    tp_sl_placed_time: float = 0.0                          # Timestamp (monotonic) when TP/SL orders were last submitted
    round_start_time: float = 0.0                           # Epoch timestamp when current round was initiated
    max_notional_cap: Decimal = Decimal("25000")            # Cached maximum position notional from Binance leverage bracket
    round_number: int = 1                                   # Incremental round counter for this symbol
    round_open_retry_count: int = 0                         # Consecutive failed round-opening attempt counter
    tick_size: Decimal = Decimal("0.0001")                  # Minimum price increment (from PRICE_FILTER)
    step_size: Decimal = Decimal("1")                       # Minimum order quantity increment (from LOT_SIZE)
    min_notional: Decimal = Decimal("5")                    # Minimum order value in USDT (from MIN_NOTIONAL filter)
    price_multiplier_up: Decimal = Decimal("5.0")           # PERCENT_PRICE upper limit multiplier
    price_multiplier_down: Decimal = Decimal("0.1")         # PERCENT_PRICE lower limit multiplier
    bid_multiplier_up: Decimal = Decimal("5.0")             # PERCENT_PRICE_BY_SIDE bid upper limit multiplier
    bid_multiplier_down: Decimal = Decimal("0.1")           # PERCENT_PRICE_BY_SIDE bid lower limit multiplier
    ask_multiplier_up: Decimal = Decimal("5.0")             # PERCENT_PRICE_BY_SIDE ask upper limit multiplier
    ask_multiplier_down: Decimal = Decimal("0.1")           # PERCENT_PRICE_BY_SIDE ask lower limit multiplier
    allocation_usdt: Decimal = Decimal("0")                 # Wallet balance allocated to this symbol
    consecutive_sl_count: int = 0                           # Back-to-back Stop-Loss counter for anti-whipsaw protection
    cooldown_until: float = 0.0                             # Monotonic timestamp until which new round opening is paused
    halted: bool = False                                    # Emergency safety halt flag (set True by kill switch or failure)
    refreshing_tp_sl: bool = False                          # Concurrency lock flag preventing duplicate TP/SL placement tasks
    tp_sl_dirty: bool = False                               # Dirty flag indicating a new fill arrived while placing TP/SL
    opening_round: bool = False                             # Concurrency lock flag preventing duplicate round-open races
    closing_round: bool = False                             # Concurrency lock flag preventing racing duplicate round closes
    protection_gap_events: int = 0                          # Cumulative count of unexpected TP/SL dropouts while position open
    protection_gap_timestamps: list = field(default_factory=list) # Timestamps of protection gap events in rolling 10m window
    algo_order_timestamps: list = field(default_factory=list)     # Timestamps of algo order submissions in rolling 60s window
    expected_canceled_algo_ids: set = field(default_factory=set)  # Set of algo IDs intentionally cancelled by the bot
    tp_sl_refresh_timer: Optional[threading.Timer] = None         # Debounced timer handle for coalescing rapid micro-fills
    protection_retry_count: int = 0                               # Retry counter for securing missing TP/SL protection
    lock: threading.RLock = field(default_factory=threading.RLock)# Re-entrant lock for thread-safe state mutations


# ===========================================================================
# 3. PRECISION ARITHMETIC, RETRY & RATE LIMITING HELPERS
# ===========================================================================

_RETRYABLE_ORDER_ERROR_CODES = {-1001, -1021, -1008}


def round_step(value: Decimal, step: Decimal, rounding=ROUND_DOWN) -> Decimal:
    """Rounds `value` down (or up) to the nearest exact multiple of exchange `step`.
    
    Args:
        value: The raw Decimal price or quantity to be rounded.
        step: The minimum increment (tick_size for price, step_size for quantity).
        rounding: Rounding mode (ROUND_DOWN for bids/quantities, ROUND_UP for asks).
    Returns:
        Exact quantized Decimal value complying with Binance step precision.
    """
    if step <= 0:
        return value
    return (value / step).quantize(Decimal("1"), rounding=rounding) * step


def _bool_param(value: bool) -> str:
    """Converts a Python boolean to exact lowercase string ('true' / 'false').
    Binance API query endpoints strictly require lowercase strings for flags like
    `reduceOnly`, `closePosition`, and `priceProtect`."""
    return "true" if value else "false"


class RateLimiter:
    """Thread-safe sliding-window rate limiter to ensure API calls remain strictly within Binance IP rate limits."""

    def __init__(self, max_calls: int, period: float):
        """
        Args:
            max_calls: Maximum number of REST calls permitted within the rolling window.
            period: Window duration in seconds (e.g. 60.0s).
        """
        self.max_calls = max_calls
        self.period = period
        self.lock = threading.Lock()
        self.calls = []

    def acquire(self):
        """Blocks and sleeps if current call frequency exceeds `max_calls` per `period`."""
        while True:
            sleep_time = 0.0
            with self.lock:
                now = time.monotonic()
                self.calls = [t for t in self.calls if now - t < self.period]
                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return
                sleep_time = self.period - (now - self.calls[0])

            if sleep_time > 0:
                time.sleep(sleep_time)


def _clean_err_msg(e) -> str:
    """Sanitizes raw HTML or bulky JSON API error payloads into clean, compact single-line log strings."""
    msg = str(e)
    if "502 Bad Gateway" in msg or "<html>" in msg:
        return "Binance Server 502 Bad Gateway (temporary Testnet server outage)"
    elif "503 Service Unavailable" in msg:
        return "Binance Server 503 Service Unavailable"
    elif "504 Gateway Timeout" in msg:
        return "Binance Server 504 Gateway Timeout"
    cleaned = msg.replace("\n", " ").replace("\r", " ")
    return cleaned[:150] + ("..." if len(cleaned) > 150 else "")


def retry(max_attempts=5, base_delay=1.0):
    """Decorator providing automatic retry with exponential backoff and rate limiting for network/API calls."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    if args and hasattr(args[0], "rate_limiter"):
                        args[0].rate_limiter.acquire()
                    return fn(*args, **kwargs)
                except BinanceAPIException as e:
                    attempt += 1
                    if getattr(e, "code", None) == -1021 and attempt < max_attempts:
                        if args and hasattr(args[0], "_sync_time_offset"):
                            log.warning(f"[{fn.__name__}] timestamp outside recvWindow (-1021); resyncing server time offset...")
                            try:
                                args[0]._sync_time_offset()
                            except Exception:
                                pass
                        time.sleep(0.5)
                        continue
                    retryable = getattr(e, "status_code", None) in (429, 418, 500, 502, 503, 504)
                    if not retryable or attempt >= max_attempts:
                        log.error(f"API error in {fn.__name__} (attempt {attempt}): {_clean_err_msg(e)}")
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(f"Retryable error in {fn.__name__}, backing off {delay:.1f}s: {_clean_err_msg(e)}")
                    time.sleep(delay)
                except BinanceOrderException as e:
                    attempt += 1
                    code = getattr(e, "code", None)
                    if code not in _RETRYABLE_ORDER_ERROR_CODES or attempt >= max_attempts:
                        log.error(f"Non-retryable order error in {fn.__name__} (code={code}): {_clean_err_msg(e)}")
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(f"Retryable order error in {fn.__name__} (code={code}), backing off {delay:.1f}s: {_clean_err_msg(e)}")
                    time.sleep(delay)
                except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        log.error(f"Transient error in {fn.__name__} (attempt {attempt}): {_clean_err_msg(e)}")
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(f"Transient error in {fn.__name__}, backing off {delay:.1f}s: {_clean_err_msg(e)}")
                    time.sleep(delay)
        return wrapper
    return decorator



# ===========================================================================
# 4. BOT
# ===========================================================================

class DCABot:
    def __init__(self, api_key: str, api_secret: str, symbols: list):
        self._api_key = api_key
        self._api_secret = api_secret
        self.symbols = symbols
        self.states: dict[str, SymbolState] = {}
        self.peak_equity: Decimal = Decimal("0")
        self.all_time_peak_equity: Decimal = Decimal("0")
        self.kill_switch_tripped = False
        self._stop_event = threading.Event()
        self._last_ws_message_time: float = 0.0



        # Thread isolation: each thread maintains its own Client instance
        # (python-binance's Client / requests.Session is not documented as thread-safe).
        self._thread_local = threading.local()
        self._all_clients: list[Client] = []
        self._clients_lock = threading.Lock()

        # Global rate limiter: enforces shared rate limits across all REST calls and threads.
        self.rate_limiter = RateLimiter(RATE_LIMIT_MAX_CALLS, RATE_LIMIT_PERIOD_SECONDS)

        # Per-symbol FIFO queues and worker threads to prevent WS blocking AND enforce strict in-order message processing
        self.symbol_queues: dict[str, queue.Queue] = {}
        self.symbol_ws_threads: list[threading.Thread] = []

        self._equity_lock = threading.Lock()
        self._ws_reconnect_attempts: int = 0

        self.twm: Optional[ThreadedWebsocketManager] = None
        self._ws_connected = False
        self._user_conn_key: Optional[str] = None
        self._ticker_conn_key: Optional[str] = None

    def _sync_time_offset(self):
        try:
            cl = self.client
            res = cl.get_server_time()
            cl.TIME_OFFSET = res["serverTime"] - int(time.time() * 1000)
            log.info(f"Server time offset resynchronized: TIME_OFFSET={cl.TIME_OFFSET}ms")
        except Exception as e:
            log.warning(f"Failed to resync server time offset: {e}")

    @property
    def client(self) -> Client:
        if not hasattr(self._thread_local, "client"):
            cl = Client(self._api_key, self._api_secret)
            if TESTNET:
                cl.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
            try:
                res = cl.futures_time()
                cl.TIME_OFFSET = res["serverTime"] - int(time.time() * 1000)
            except Exception:
                pass
            self._thread_local.client = cl
            with self._clients_lock:
                self._all_clients.append(cl)
        return self._thread_local.client

    def _close_all_clients(self):
        with self._clients_lock:
            clients = list(self._all_clients)
            self._all_clients.clear()
        for cl in clients:
            try:
                cl.close_session()
            except Exception:
                pass


    @retry()
    def _get_position_info(self, symbol: str):
        return self.client.futures_position_information(symbol=symbol)

    @retry()
    def _get_open_orders(self, symbol: str):
        return self.client.futures_get_open_orders(symbol=symbol)

    @retry()
    def _get_order_info(self, symbol: str, order_id: int):
        return self.client.futures_get_order(symbol=symbol, orderId=order_id)

    @retry()
    def _get_account_trades(self, symbol: str, limit: int = 10):
        return self.client.futures_account_trades(symbol=symbol, limit=limit)

    @retry()
    def _get_open_algo_orders(self, symbol: str):
        return self.client.futures_get_open_algo_orders(symbol=symbol)

    @retry()
    def _cancel_all_open_orders(self, symbol: str):
        return self.client.futures_cancel_all_open_orders(symbol=symbol)

    @retry()
    def _change_leverage(self, symbol: str, leverage: int):
        return self.client.futures_change_leverage(symbol=symbol, leverage=leverage)

    @retry()
    def _change_margin_type(self, symbol: str, margin_type: str):
        try:
            return self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        except BinanceAPIException as e:
            if e.code in (-4046, -4067):
                log.info(f"[{symbol}] margin type setting notice ({e.message})")
                return None
            raise

    @retry()
    def _get_leverage_bracket(self, symbol: str):
        return self.client.futures_leverage_bracket(symbol=symbol)

    # =======================================================================
    # SECTION 5: EXCHANGE FILTERS, LEVERAGE & STARTUP CONFIGURATION
    # =======================================================================

    @retry()
    def _get_symbol_filters(self, symbol: str):
        """Fetches symbol trading rules from exchange info, including price/lot filters,
        min_notional requirements, and PERCENT_PRICE bands."""
        try:
            info = self.client.futures_exchange_info()
            for s in info.get("symbols", []):
                if s["symbol"] == symbol:
                    tick_size = Decimal("1")
                    step_size = Decimal("1")
                    min_notional = Decimal("5")
                    bid_up = Decimal("5.0")
                    bid_down = Decimal("0.1")
                    ask_up = Decimal("5.0")
                    ask_down = Decimal("0.1")
                    found_price_filter = False
                    found_lot_size_filter = False
                    found_min_notional_filter = False
                    for f in s["filters"]:
                        if f["filterType"] == "PRICE_FILTER":
                            found_price_filter = True
                            tick_size = Decimal(f["tickSize"])
                        elif f["filterType"] == "LOT_SIZE":
                            found_lot_size_filter = True
                            step_size = Decimal(f["stepSize"])
                        elif f["filterType"] == "MIN_NOTIONAL":
                            found_min_notional_filter = True
                            if "notional" in f:
                                min_notional = Decimal(f["notional"])
                            elif "minNotional" in f:
                                min_notional = Decimal(f["minNotional"])
                            else:
                                log.warning(
                                    f"[{symbol}] MIN_NOTIONAL filter present but key unrecognized, "
                                    f"using fallback default {min_notional}"
                                )
                        elif f["filterType"] == "PERCENT_PRICE":
                            bid_up = ask_up = Decimal(str(f.get("multiplierUp", "5.0")))
                            bid_down = ask_down = Decimal(str(f.get("multiplierDown", "0.1")))
                        elif f["filterType"] == "PERCENT_PRICE_BY_SIDE":
                            bid_up = Decimal(str(f.get("bidMultiplierUp", "5.0")))
                            bid_down = Decimal(str(f.get("bidMultiplierDown", "0.1")))
                            ask_up = Decimal(str(f.get("askMultiplierUp", "5.0")))
                            ask_down = Decimal(str(f.get("askMultiplierDown", "0.1")))
                    if not found_price_filter:
                        log.warning(
                            f"[{symbol}] no PRICE_FILTER found in exchange info, "
                            f"using fallback default tick_size={tick_size}"
                        )
                    if not found_lot_size_filter:
                        log.warning(
                            f"[{symbol}] no LOT_SIZE filter found in exchange info, "
                            f"using fallback default step_size={step_size}"
                        )
                    if not found_min_notional_filter:
                        log.warning(
                            f"[{symbol}] no MIN_NOTIONAL filter found in exchange info, "
                            f"using fallback default {min_notional}"
                        )
                    return tick_size, step_size, min_notional, bid_up, bid_down, ask_up, ask_down
        except Exception as e:
            log.warning(f"[{symbol}] exchange info query failed ({_clean_err_msg(e)}), attempting fallback ticker filters...")
            try:
                ticker = self.client.futures_symbol_ticker(symbol=symbol)
                if ticker and "price" in ticker:
                    p_str = str(ticker["price"])
                    dec_places = len(p_str.split(".")[1]) if "." in p_str else 2
                    tick = Decimal("1") / (Decimal("10") ** dec_places)
                    log.info(f"[{symbol}] derived fallback tick_size={tick} from live ticker price={p_str}")
                    return tick, Decimal("0.1"), Decimal("5"), Decimal("1.05"), Decimal("0.95"), Decimal("1.05"), Decimal("0.95")
            except Exception as ex:
                log.debug(f"[{symbol}] fallback ticker query failed: {ex}")
            raise
        raise ValueError(f"Symbol {symbol} not found in exchange info")

    @retry()
    def _get_account_balance_usdt(self) -> Decimal:
        balances = self.client.futures_account_balance()
        for b in balances:
            if b["asset"] == "USDT":
                bal = Decimal(b.get("crossWalletBalance") or b.get("balance") or "0")
                if bal > 0:
                    return bal
                return Decimal(b.get("balance", "0"))
        return Decimal("0")

    def setup_symbol(self, symbol: str):
        tick_size, step_size, min_notional, bid_up, bid_down, ask_up, ask_down = self._get_symbol_filters(symbol)

        try:
            self._change_leverage(symbol, LEVERAGE)
        except BinanceAPIException as e:
            log.error(f"[{symbol}] could not confirm leverage={LEVERAGE} is set: {e}")
            raise

        try:
            self._change_margin_type(symbol, MARGIN_TYPE)
        except BinanceAPIException as e:
            log.info(f"[{symbol}] margin type: {e}")

        # Pre-fetch and cache leverage bracket max_notional_cap at setup time
        max_notional_cap = Decimal("25000") if LEVERAGE <= 20 else Decimal("5000")
        try:
            brackets = self._get_leverage_bracket(symbol)
            b_list = []
            if isinstance(brackets, list) and len(brackets) > 0:
                b_list = brackets[0].get("brackets", [])
            elif isinstance(brackets, dict):
                b_list = brackets.get("brackets", [])

            if b_list:
                sorted_b = sorted(b_list, key=lambda x: x.get("initialLeverage", 0))
                for b in sorted_b:
                    if b.get("initialLeverage", 0) >= LEVERAGE:
                        max_notional_cap = Decimal(str(b.get("notionalCap", 25000)))
                        break
        except Exception as e:
            log.warning(f"[{symbol}] leverage bracket fetch error, using fallback {max_notional_cap}: {e}")


        balance = self._get_account_balance_usdt()
        num_symbols = max(1, len(self.symbols))
        allocation = (balance * Decimal(str(ACCOUNT_ALLOCATION_FRACTION_PER_SYMBOL))) / Decimal(str(num_symbols))

        init_dir = (
            Direction.LONG if TRADING_MODE == "LONG_ONLY"
            else (Direction.SHORT if TRADING_MODE == "SHORT_ONLY"
                  else (Direction.SHORT if str(INITIAL_DIRECTION).upper() == "SHORT" else Direction.LONG))
        )

        state = SymbolState(
            symbol=symbol,
            direction=init_dir,
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
            price_multiplier_up=bid_up,
            price_multiplier_down=bid_down,
            bid_multiplier_up=bid_up,
            bid_multiplier_down=bid_down,
            ask_multiplier_up=ask_up,
            ask_multiplier_down=ask_down,
            allocation_usdt=allocation,
            max_notional_cap=max_notional_cap,
        )
        self.states[symbol] = state
        log.info(
            f"[{symbol}] setup complete | dir={init_dir.value} (mode={TRADING_MODE}) tick={tick_size} step={step_size} "
            f"min_notional={min_notional} bid_bands=[{bid_down}x, {bid_up}x] ask_bands=[{ask_down}x, {ask_up}x] "
            f"allocation={allocation} USDT bracket_cap={max_notional_cap} USDT"
        )

    def _clamp_price_to_percent_filter(self, state: SymbolState, price: Decimal, mark_price: Decimal, is_buy: bool = True) -> Decimal:
        """Clamp order price to respect Binance PERCENT_PRICE and PERCENT_PRICE_BY_SIDE price band filter bounds."""
        if mark_price <= 0:
            return price
        mult_up = state.bid_multiplier_up if is_buy else state.ask_multiplier_up
        mult_down = state.bid_multiplier_down if is_buy else state.ask_multiplier_down
        max_p = round_step(mark_price * mult_up, state.tick_size, ROUND_DOWN)
        min_p = round_step(mark_price * mult_down, state.tick_size, ROUND_UP)
        if price > max_p:
            log.warning(f"[{state.symbol}] Price {price} exceeds PERCENT_PRICE max {max_p} (is_buy={is_buy}), clamping to {max_p}")
            return max_p
        elif price < min_p:
            log.warning(f"[{state.symbol}] Price {price} below PERCENT_PRICE min {min_p} (is_buy={is_buy}), clamping to {min_p}")
            return min_p
        return price

    def setup_all(self):
        for symbol in self.symbols:
            for attempt in range(1, 4):
                try:
                    self.setup_symbol(symbol)
                    break
                except Exception as e:
                    if attempt >= 3:
                        log.error(f"[{symbol}] fatal error during setup_symbol after {attempt} attempts: {e}")
                        raise
                    delay = 1.0 * (2 ** (attempt - 1))
                    log.warning(f"[{symbol}] setup_symbol attempt {attempt} failed, backing off {delay:.1f}s: {e}")
                    time.sleep(delay)
        self._load_state()
        current_equity = self._get_total_equity()
        with self._equity_lock:
            if self.peak_equity == Decimal("0"):
                self.peak_equity = current_equity
            else:
                self.peak_equity = max(self.peak_equity, current_equity)

            if self.all_time_peak_equity == Decimal("0"):
                self.all_time_peak_equity = current_equity
            else:
                self.all_time_peak_equity = max(self.all_time_peak_equity, current_equity)
            log.info(f"Startup equity ({DRAWDOWN_BASIS}) recorded | current={current_equity} | peak_equity={self.peak_equity} | all_time_peak={self.all_time_peak_equity}")



    def _log_trade_history(
        self,
        symbol: str,
        round_number: int,
        direction: str,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        exit_reason: str,
        start_time: float = 0.0,
    ):
        """Append completed round performance, metrics, and Realized PnL to trade_history.csv."""
        try:
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            duration_sec = round(time.time() - start_time, 1) if start_time > 0 else 0.0

            # Calculate Realized PnL in USDT
            dir_str = direction.value if hasattr(direction, "value") else str(direction)
            if dir_str.upper() == "LONG":
                pnl_usdt = (exit_price - entry_price) * quantity
            else:
                pnl_usdt = (entry_price - exit_price) * quantity
            pnl_usdt = round(pnl_usdt, 4)

            # Cumulative total equity
            try:
                equity = self._get_total_equity()
            except Exception:
                equity = Decimal("0")

            file_exists = os.path.exists(TRADE_HISTORY_FILE)
            with open(TRADE_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        "Timestamp",
                        "Round",
                        "Symbol",
                        "Direction",
                        "Entry_Price",
                        "Exit_Price",
                        "Quantity",
                        "Realized_PnL_USDT",
                        "Duration_Seconds",
                        "Exit_Reason",
                        "Total_Equity_USDT",
                    ])
                writer.writerow([
                    now_str,
                    round_number,
                    symbol,
                    dir_str,
                    f"{entry_price:.6f}",
                    f"{exit_price:.6f}",
                    f"{quantity:.4f}",
                    f"{pnl_usdt:+.4f}",
                    f"{duration_sec:.1f}",
                    exit_reason,
                    f"{equity:.4f}",
                ])
            log.info(
                f"[{symbol}] Trade History Logged -> Round {round_number} | {dir_str} | "
                f"Entry: {entry_price} | Exit: {exit_price} | PnL: {pnl_usdt:+.4f} USDT | "
                f"Duration: {duration_sec}s | Reason: {exit_reason}"
            )
        except Exception as e:
            log.warning(f"[{symbol}] Failed to write trade history to CSV: {e}")

    def _save_state(self):
        try:
            with self._equity_lock:
                pk_eq = str(self.peak_equity)
                at_pk_eq = str(self.all_time_peak_equity)
            data = {
                "symbols": {},
                "peak_equity": pk_eq,
                "all_time_peak_equity": at_pk_eq,
            }
            for symbol, state in self.states.items():
                with state.lock:
                    data["symbols"][symbol] = {
                        "round_number": state.round_number,
                        "direction": state.direction.value,
                    }
            with open(STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.warning(f"failed to persist state: {e}")

    def _load_state(self):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)

            with self._equity_lock:
                if "peak_equity" in data:
                    try:
                        self.peak_equity = Decimal(str(data["peak_equity"]))
                        log.info(f"loaded persisted peak_equity = {self.peak_equity}")
                    except Exception as e:
                        log.warning(f"failed to parse persisted peak_equity: {e}")

                if "all_time_peak_equity" in data:
                    try:
                        self.all_time_peak_equity = Decimal(str(data["all_time_peak_equity"]))
                        log.info(f"loaded persisted all_time_peak_equity = {self.all_time_peak_equity}")
                    except Exception as e:
                        log.warning(f"failed to parse persisted all_time_peak_equity: {e}")


            symbols_data = data.get("symbols", data)
            for symbol, saved in symbols_data.items():
                if symbol in self.states and isinstance(saved, dict):
                    state = self.states[symbol]
                    state.round_number = saved.get("round_number", state.round_number)
                    if TRADING_MODE == "LONG_ONLY":
                        state.direction = Direction.LONG
                    elif TRADING_MODE == "SHORT_ONLY":
                        state.direction = Direction.SHORT
                    else:
                        state.direction = Direction(saved.get("direction", state.direction.value))
            log.info(f"loaded persisted state from {STATE_FILE} | active mode={TRADING_MODE}")
        except FileNotFoundError:
            log.info("no persisted state file found, starting fresh")
        except Exception as e:
            log.warning(f"failed to load persisted state: {e}")

    # -- Startup State Resynchronization: recover existing position and orders from exchange --

    def resync_symbol(self, symbol: str) -> bool:
        """Returns True if an existing position/orders were recovered (or the
        resync itself failed and the symbol was halted out of caution) --
        i.e. True means "do not call open_round for this symbol right now".
        Returns False only when the resync confirms the symbol is genuinely
        flat with nothing resting, meaning it's safe to open a fresh round.
        """
        state = self.states[symbol]
        try:
            positions = self._get_position_info(symbol)
            open_orders = self._get_open_orders(symbol)
            # Stop-loss and take-profit conditional orders are tracked via Binance Algo service
            # and queried via the dedicated algo listing endpoint.
            open_algo_orders = self._get_open_algo_orders(symbol)

            if isinstance(open_algo_orders, dict):
                open_algo_orders = (
                    open_algo_orders.get("orders")
                    or open_algo_orders.get("algoOrders")
                    or []
                )
        except Exception as e:
            log.error(
                f"[{symbol}] resync failed ({e}); halting this symbol rather than risk opening "
                f"a duplicate position on top of an unknown existing one. Restart once you've "
                f"manually verified the account state."
            )
            with state.lock:
                state.halted = True
            send_alert(f"[{symbol}] Resync failed ({e}). Symbol HALTED for safety.")
            return True

        pos_amt = Decimal("0")
        entry_price = Decimal("0")
        for p in positions:
            amt = Decimal(p["positionAmt"])
            if amt != 0:
                pos_amt = amt
                entry_price = Decimal(p["entryPrice"])
                break

        if pos_amt == 0:
            with state.lock:
                state.resting_dca_orders.clear()
                state.rung_last_qty.clear()
                state.take_profit_algo_id = None
                state.stop_loss_algo_id = None
                state.position_qty = Decimal("0")
                state.entry_orders = []
            if open_orders:
                try:
                    self._cancel_all_open_orders(symbol)
                    log.info(f"[{symbol}] resync: position is flat; canceled {len(open_orders)} leftover open orders")
                except Exception as e:
                    log.warning(f"[{symbol}] resync cancel_all_open_orders error: {e}")

            if open_algo_orders:
                try:
                    for ao in open_algo_orders:
                        aid = ao.get("algoId") or ao.get("orderId")
                        if aid:
                            self._cancel_algo_order(symbol, int(aid))
                    log.info(f"[{symbol}] resync: position is flat; canceled {len(open_algo_orders)} leftover algo orders")
                except Exception as e:
                    log.warning(f"[{symbol}] resync cancel algo orders error: {e}")
            log.info(f"[{symbol}] resync: position is flat with no open position, opening fresh round")
            return False

        with state.lock:
            if pos_amt != 0:
                state.direction = Direction.LONG if pos_amt > 0 else Direction.SHORT
                state.position_qty = abs(pos_amt)
                state.average_price = entry_price if entry_price > 0 else state.average_price
                state.entry_orders = [Fill(price=state.average_price, qty=state.position_qty, order_id=-1)]

            state.resting_dca_orders = []
            state.rung_last_qty = {}
            state.rung_last_cost = {}
            state.take_profit_algo_id = None
            state.stop_loss_algo_id = None

            for o in open_orders:
                if o["type"] == "LIMIT" and not o.get("reduceOnly"):
                    oid = o["orderId"]
                    exec_qty = Decimal(o["executedQty"])
                    exec_price = Decimal(o.get("avgPrice") or o.get("price") or "0")
                    cum_cost = Decimal(o.get("cumQuote") or str(exec_qty * exec_price))
                    state.resting_dca_orders.append(oid)
                    state.rung_last_qty[oid] = exec_qty
                    state.rung_last_cost[oid] = cum_cost

            for ao in open_algo_orders:
                order_type = str(ao.get("orderType") or ao.get("type") or ao.get("algoType") or "").upper()
                aid = ao.get("algoId") or ao.get("orderId")
                if order_type in ("STOP_MARKET", "STOP"):
                    state.stop_loss_algo_id = int(aid) if aid else None
                elif order_type in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
                    state.take_profit_algo_id = int(aid) if aid else None

            log.warning(
                f"[{symbol}] resync: recovered existing state | dir={state.direction.value} "
                f"qty={state.position_qty} avg={state.average_price} "
                f"rungs={len(state.resting_dca_orders)} tp_algo={state.take_profit_algo_id} "
                f"sl_algo={state.stop_loss_algo_id}"
            )

            needs_protection = state.position_qty > 0 and (state.take_profit_algo_id is None or state.stop_loss_algo_id is None)
            direction = state.direction

        if needs_protection:
            log.error(
                f"[{symbol}] resync found an open position missing its TP or SL order; "
                f"placing fresh protective orders now at the recovered average price."
            )
            exit_side = SIDE_SELL if direction == Direction.LONG else SIDE_BUY
            self._place_tp_and_sl(state, exit_side)

        self._save_state()
        return True

    # =======================================================================
    # SECTION 6: PRICING, SIZING & DYNAMIC LIQUIDATION SL CALCULATION
    # =======================================================================

    @retry()
    def _mark_price(self, symbol: str) -> Decimal:
        """Fetches the latest live mark price ticker from Binance Futures."""
        ticker = self.client.futures_symbol_ticker(symbol=symbol)
        return Decimal(ticker["price"])

    def _qty_for_notional(self, state: SymbolState, notional_usdt: Decimal, price: Decimal) -> Decimal:
        """Calculates base asset order quantity for a given margin notional in USDT,
        taking leverage into account and bumping to satisfy Binance MIN_NOTIONAL limits."""
        if price <= 0:
            return Decimal("0")
        # notional_usdt is margin; actual position notional = margin * leverage
        position_notional = notional_usdt * Decimal(LEVERAGE)
        qty = position_notional / price
        qty = round_step(qty, state.step_size, ROUND_DOWN)

        target_min_notional = max(state.min_notional, Decimal("6"))
        if qty * price < target_min_notional:
            min_qty = round_step(target_min_notional / price, state.step_size, ROUND_UP)
            if min_qty > qty:
                log.warning(
                    f"[{state.symbol}] bumping order qty from {qty} to {min_qty} "
                    f"to satisfy min_notional={target_min_notional}"
                )
                qty = min_qty
        return qty

    def _compute_sl_price(
        self,
        state: SymbolState,
        mark_price: Optional[Decimal] = None,
        liq_price: Optional[Decimal] = None,
    ) -> Decimal:
        """Calculates the Stop-Loss trigger price.
        When USE_DYNAMIC_LIQUIDATION_SL is True, dynamically anchors SL to the live
        exchange liquidation price with a safety buffer (LIQUIDATION_BUFFER_PCT = 1.5%).
        Otherwise, falls back to static percentage from average entry."""
        with state.lock:
            avg_price = state.average_price
            direction = state.direction
            tick_size = state.tick_size
            symbol = state.symbol
            pos_qty = state.position_qty

        live_mark = mark_price if (mark_price and mark_price > 0) else self._mark_price(symbol)
        ref_price = avg_price if avg_price > 0 else live_mark

        # Dynamic Liquidation-Anchored SL Calculation
        if USE_DYNAMIC_LIQUIDATION_SL and pos_qty > 0:
            active_liq = liq_price
            if active_liq is None or active_liq <= 0:
                try:
                    positions = self._get_position_info(symbol)
                    for p in positions:
                        if p.get("symbol") == symbol:
                            lp = Decimal(str(p.get("liquidationPrice") or "0"))
                            if lp > 0:
                                active_liq = lp
                                break
                except Exception as e:
                    log.debug(f"[{symbol}] error querying exchange liquidationPrice: {e}")

            # If exchange liquidation price is not yet populated, compute estimated liquidation price
            if active_liq is None or active_liq <= 0:
                mmr = Decimal("0.005")
                lev = Decimal(str(LEVERAGE))
                if MARGIN_TYPE == "CROSSED":
                    try:
                        tot_equity = self._get_total_equity()
                        maint_margin = (pos_qty * ref_price) * mmr
                        buffer_per_unit = max(Decimal("0"), tot_equity - maint_margin) / pos_qty if pos_qty > 0 else Decimal("0")
                        if direction == Direction.LONG:
                            active_liq = max(Decimal("0"), ref_price - buffer_per_unit)
                        else:
                            active_liq = ref_price + buffer_per_unit
                    except Exception:
                        active_liq = ref_price * (Decimal("1") - (Decimal("1") / lev) + mmr) if direction == Direction.LONG else ref_price * (Decimal("1") + (Decimal("1") / lev) - mmr)
                else:
                    active_liq = ref_price * (Decimal("1") - (Decimal("1") / lev) + mmr) if direction == Direction.LONG else ref_price * (Decimal("1") + (Decimal("1") / lev) - mmr)

            # Anchor Stop Loss to Liquidation Price with configured safety buffer
            if active_liq and active_liq > 0:
                buf = Decimal(str(LIQUIDATION_BUFFER_PCT)) / Decimal("100")
                if direction == Direction.LONG:
                    # SL must sit ABOVE liquidation price for LONG
                    sl_price = active_liq * (Decimal("1") + buf)
                    if avg_price > 0 and sl_price >= avg_price:
                        sl_price = avg_price * (Decimal("1") - Decimal("0.02"))
                    rounding = ROUND_UP
                else:
                    # SL must sit BELOW liquidation price for SHORT
                    sl_price = active_liq * (Decimal("1") - buf)
                    if avg_price > 0 and sl_price <= avg_price:
                        sl_price = avg_price * (Decimal("1") + Decimal("0.02"))
                    rounding = ROUND_DOWN

                raw_sl = round_step(sl_price, tick_size, rounding)
                log.info(
                    f"[{symbol}] Dynamic Liq Stop-Loss | Dir={direction.value} LiqPrice={active_liq:.6f} "
                    f"Buffer={LIQUIDATION_BUFFER_PCT}% -> SL Trigger={raw_sl}"
                )
                return raw_sl

        # Fallback to static percentage from average entry
        fallback_pct = Decimal(str(FALLBACK_STOP_LOSS_PCT))
        if direction == Direction.LONG:
            sl_price = ref_price * (Decimal("1") - fallback_pct / 100)
            rounding = ROUND_DOWN
        else:
            sl_price = ref_price * (Decimal("1") + fallback_pct / 100)
            rounding = ROUND_UP
        raw_sl = round_step(sl_price, tick_size, rounding)
        return raw_sl

    def _compute_tp_price(self, state: SymbolState, mark_price: Optional[Decimal] = None) -> Decimal:
        """Calculates the Take-Profit limit price target (TAKE_PROFIT_PCT = 1.0%)
        relative to the volume-weighted average entry price."""
        with state.lock:
            avg_price = state.average_price
            direction = state.direction
            tick_size = state.tick_size
            symbol = state.symbol
        live_mark = mark_price if (mark_price and mark_price > 0) else self._mark_price(symbol)
        ref_price = avg_price if avg_price > 0 else live_mark
        if direction == Direction.LONG:
            tp_price = ref_price * (Decimal("1") + Decimal(str(TAKE_PROFIT_PCT)) / 100)
            rounding = ROUND_DOWN
        else:
            tp_price = ref_price * (Decimal("1") - Decimal(str(TAKE_PROFIT_PCT)) / 100)
            rounding = ROUND_UP
        raw_tp = round_step(tp_price, tick_size, rounding)
        return raw_tp

    # =======================================================================
    # SECTION 7: ORDER EXECUTION & MARTINGALE DCA LADDER DEPLOYMENT
    # =======================================================================

    @retry()
    def _place_market_order(self, symbol: str, side: str, qty: Decimal):
        return self.client.futures_create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=str(qty),
        )

    @retry()
    def _place_market_close_order(self, symbol: str, side: str, qty: Decimal):
        """Place Market Order explicitly flagged with reduceOnly=True to guarantee position close without accidental reversal."""
        return self.client.futures_create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=str(qty),
            reduceOnly=_bool_param(True),
        )

    def _emergency_flatten(self, symbol: str, exit_side: str, max_attempts: int = 5) -> bool:
        """Repeatedly market close whatever quantity is actually still open on the
        exchange, re reading the live position each time, until flat or attempts
        are exhausted. Returns True if the position is confirmed flat."""
        send_alert(f"[{symbol}] EMERGENCY FLATTEN STARTED for exit_side={exit_side}. Attempting market close of live position.")
        is_flat = False
        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    positions = self._get_position_info(symbol)
                    pos_amt = next(
                        (abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol),
                        Decimal("0"),
                    )
                except Exception as e:
                    log.error(f"[{symbol}] emergency flatten position check failed (attempt {attempt}): {e}")
                    time.sleep(0.5)
                    continue

                if pos_amt == 0:
                    log.info(f"[{symbol}] emergency flatten confirmed position is flat (attempt {attempt})")
                    is_flat = True
                    break

                log.error(f"[{symbol}] emergency flatten attempt {attempt}/{max_attempts}: live positionAmt={pos_amt}, closing")
                try:
                    self._place_market_close_order(symbol, exit_side, pos_amt)
                except Exception as e:
                    log.error(f"[{symbol}] emergency flatten market close failed (attempt {attempt}): {e}")

                time.sleep(0.5)

            if not is_flat:
                try:
                    positions = self._get_position_info(symbol)
                    pos_amt = next(
                        (abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol),
                        Decimal("0"),
                    )
                    is_flat = (pos_amt == 0)
                except Exception:
                    is_flat = False
        finally:
            if is_flat:
                send_alert(f"[{symbol}] EMERGENCY FLATTEN SUCCESSFUL! Position confirmed flat.")
            else:
                send_alert(f"[{symbol}] EMERGENCY FLATTEN FAILED AFTER {max_attempts} RETRIES! Position may still be open.")

        return is_flat

    def _emergency_market_exit_until_flat(self, symbol: str, exit_side: str) -> bool:
        """Backward compatibility helper that delegates to _emergency_flatten."""
        return self._emergency_flatten(symbol, exit_side)


    @retry()
    def _place_limit_order(self, symbol: str, side: str, qty: Decimal, price: Decimal, reduce_only=False, mark_price: Optional[Decimal] = None):
        state = self.states.get(symbol)
        is_buy = (side == SIDE_BUY)
        if state:
            mark_p = mark_price if (mark_price and mark_price > 0) else self._mark_price(symbol)
            price = self._clamp_price_to_percent_filter(state, price, mark_p, is_buy=is_buy)

        try:
            return self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=str(qty),
                price=str(price),
                reduceOnly=_bool_param(reduce_only),
            )
        except BinanceAPIException as e:
            if getattr(e, "code", None) == -4024:
                log.warning(f"[{symbol}] Limit order price {price} rejected by PERCENT_PRICE filter (-4024): {e}")
                match = re.search(r"(?:lower|higher) than ([\d.]+)", str(e))
                if match and state:
                    bound_str = match.group(1)
                    rounding = ROUND_UP if "lower" in str(e) else ROUND_DOWN
                    bound_price = round_step(Decimal(bound_str), state.tick_size, rounding)
                    log.info(f"[{symbol}] Adjusting limit order price from {price} to filter boundary {bound_price} and retrying...")
                    return self.client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type=ORDER_TYPE_LIMIT,
                        timeInForce=TIME_IN_FORCE_GTC,
                        quantity=str(qty),
                        price=str(bound_price),
                        reduceOnly=_bool_param(reduce_only),
                    )
                elif state:
                    fresh_mark = self._mark_price(symbol)
                    clamped = self._clamp_price_to_percent_filter(state, price, fresh_mark, is_buy=is_buy)
                    if clamped != price:
                        log.info(f"[{symbol}] Adjusting limit order price from {price} to clamped {clamped} and retrying...")
                        return self.client.futures_create_order(
                            symbol=symbol,
                            side=side,
                            type=ORDER_TYPE_LIMIT,
                            timeInForce=TIME_IN_FORCE_GTC,
                            quantity=str(qty),
                            price=str(clamped),
                            reduceOnly=_bool_param(reduce_only),
                        )
            raise

    def _check_algo_placement_rate(self, symbol: str) -> bool:
        """Track algo order creation frequency per symbol over a 60s rolling window.
        Throttles placement if rate threshold is reached without halting the symbol."""
        state = self.states.get(symbol)
        if not state:
            return True

        now = time.monotonic()
        with state.lock:
            if state.halted:
                return False
            state.algo_order_timestamps = [t for t in state.algo_order_timestamps if now - t <= 60.0]
            if len(state.algo_order_timestamps) >= MAX_ALGO_ORDERS_PER_MINUTE:
                count = len(state.algo_order_timestamps)
                log.warning(
                    f"[{symbol}] ALGO ORDER RATE LIMIT THROTTLE ({count} algo orders in last 60s, limit={MAX_ALGO_ORDERS_PER_MINUTE}/min). "
                    f"Deferring TP/SL replacement for this cycle to preserve existing resting protection."
                )
                return False
            state.algo_order_timestamps.append(now)
            return True

    @retry()
    def _place_take_profit_algo(self, symbol: str, side: str, trigger_price: Decimal, cancel_algo_id: Optional[Union[int, str]] = None):
        """Place Take Profit as an Algo Order (TAKE_PROFIT_MARKET) directly attached to the position."""
        if not self._check_algo_placement_rate(symbol):
            log.warning(f"[{symbol}] TP algo placement throttled due to rate limit ({MAX_ALGO_ORDERS_PER_MINUTE}/min); keeping resting fallback TP.")
            return {}
        if cancel_algo_id:
            self._cancel_algo_order(symbol, cancel_algo_id)
            time.sleep(0.02)
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": str(trigger_price),
            "timeInForce": "GTE_GTC",
            "workingType": PROTECTIVE_ORDER_WORKING_TYPE,
            "priceProtect": _bool_param(True),
            "closePosition": _bool_param(True),
        }
        for attempt in range(1, 7):
            try:
                res = self.client.futures_create_algo_order(**params)
                log.debug(f"[{symbol}] raw futures_create_algo_order (TP) response: {res}")
                return res
            except BinanceAPIException as e:
                if e.code == -4130 and attempt < 6:
                    backoff = 0.25 * attempt
                    log.warning(
                        f"[{symbol}] TP algo creation returned code=-4130 (in-flight order cancellation pending), "
                        f"cancelling lingering algo orders and retrying in {backoff:.2f}s (attempt {attempt}/6)..."
                    )
                    time.sleep(backoff)
                    try:
                        open_algos = self.client.futures_get_open_algo_orders(symbol=symbol)
                        if isinstance(open_algos, list):
                            for a in open_algos:
                                a_id = a.get("algoId")
                                if a_id and str(a.get("side")) == str(side):
                                    self._cancel_algo_order(symbol, a_id)
                    except Exception:
                        pass
                    continue
                raise

    @retry()
    def _place_stop_loss_algo(self, symbol: str, side: str, trigger_price: Decimal, cancel_algo_id: Optional[Union[int, str]] = None):
        """Place Stop Loss as an Algo Order (STOP_MARKET) directly attached to the position."""
        if not self._check_algo_placement_rate(symbol):
            log.warning(f"[{symbol}] SL algo placement throttled due to rate limit ({MAX_ALGO_ORDERS_PER_MINUTE}/min); keeping resting fallback SL.")
            return {}
        if cancel_algo_id:
            self._cancel_algo_order(symbol, cancel_algo_id)
            time.sleep(0.02)
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": ORDER_TYPE_STOP_MARKET,
            "triggerPrice": str(trigger_price),
            "timeInForce": "GTE_GTC",
            "workingType": PROTECTIVE_ORDER_WORKING_TYPE,
            "priceProtect": _bool_param(True),
            "closePosition": _bool_param(True),
        }
        for attempt in range(1, 7):
            try:
                res = self.client.futures_create_algo_order(**params)
                log.debug(f"[{symbol}] raw futures_create_algo_order (SL) response: {res}")
                return res
            except BinanceAPIException as e:
                if e.code == -4130 and attempt < 6:
                    backoff = 0.25 * attempt
                    log.warning(
                        f"[{symbol}] SL algo creation returned code=-4130 (in-flight order cancellation pending), "
                        f"cancelling lingering algo orders and retrying in {backoff:.2f}s (attempt {attempt}/6)..."
                    )
                    time.sleep(backoff)
                    try:
                        open_algos = self.client.futures_get_open_algo_orders(symbol=symbol)
                        if isinstance(open_algos, list):
                            for a in open_algos:
                                a_id = a.get("algoId")
                                if a_id and str(a.get("side")) == str(side):
                                    self._cancel_algo_order(symbol, a_id)
                    except Exception:
                        pass
                    continue
                raise

    @retry()
    def _cancel_order(self, symbol: str, order_id: int):
        try:
            self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except BinanceAPIException as e:
            # Order may already be filled/cancelled -- log and move on.
            log.info(f"[{symbol}] cancel_order {order_id}: {e}")

    @retry()
    def _cancel_algo_order(self, symbol: str, algo_id: int):
        state = self.states.get(symbol)
        if state and algo_id:
            with state.lock:
                state.expected_canceled_algo_ids.add(str(algo_id))
        try:
            self.client.futures_cancel_algo_order(symbol=symbol, algoId=algo_id)
        except BinanceAPIException as e:
            # Algo order may already have triggered/expired/been cancelled.
            log.info(f"[{symbol}] cancel_algo_order {algo_id}: {e}")

    def _cancel_resting_orders(self, state: SymbolState):
        with state.lock:
            if state.tp_sl_refresh_timer is not None:
                try:
                    state.tp_sl_refresh_timer.cancel()
                except Exception:
                    pass
                state.tp_sl_refresh_timer = None
            dca_orders = list(state.resting_dca_orders)
            tp_algo = state.take_profit_algo_id
            sl_algo = state.stop_loss_algo_id

        canceled_dca = []
        for oid in dca_orders:
            try:
                self._cancel_order(state.symbol, oid)
                canceled_dca.append(oid)
            except Exception as e:
                log.error(f"[{state.symbol}] persistent error canceling DCA order {oid}: {e}")

        tp_canceled = False
        if tp_algo:
            try:
                self._cancel_algo_order(state.symbol, tp_algo)
                tp_canceled = True
            except Exception as e:
                log.error(f"[{state.symbol}] persistent error canceling TP algo order {tp_algo}: {e}")

        sl_canceled = False
        if sl_algo:
            try:
                self._cancel_algo_order(state.symbol, sl_algo)
                sl_canceled = True
            except Exception as e:
                log.error(f"[{state.symbol}] persistent error canceling SL algo order {sl_algo}: {e}")

        with state.lock:
            for oid in canceled_dca:
                if oid in state.resting_dca_orders:
                    state.resting_dca_orders.remove(oid)
                state.rung_last_qty.pop(oid, None)
                state.rung_last_cost.pop(oid, None)

            if tp_canceled and state.take_profit_algo_id == tp_algo:
                state.take_profit_algo_id = None
            if sl_canceled and state.stop_loss_algo_id == sl_algo:
                state.stop_loss_algo_id = None

    @retry()
    def _get_algo_order(self, symbol: str, algo_id: int):
        try:
            res = self.client.futures_get_algo_order(symbol=symbol, algoId=algo_id)
            log.debug(f"[{symbol}] raw futures_get_algo_order response for algoId={algo_id}: {res}")
            return res
        except BinanceAPIException as e:
            log.debug(f"[{symbol}] futures_get_algo_order exception for algoId={algo_id}: {e}")
            if e.code == -2013:
                return {"algoStatus": "UNKNOWN_OR_NOT_FOUND", "code": -2013}
            raise

    def open_round(self, symbol: str, ref_price: Optional[Decimal] = None):
        """Initiates a new DCA round with bounded retry tracking and safe per-symbol halt handling."""
        state = self.states[symbol]
        with state.lock:
            if time.monotonic() < state.cooldown_until:
                remaining = int(state.cooldown_until - time.monotonic())
                log.info(f"[{symbol}] In anti-whipsaw cooldown ({remaining}s remaining); skipping open_round")
                return
            if state.halted or state.opening_round or state.closing_round or state.position_qty > 0 or len(state.resting_dca_orders) > 0:
                return
            state.opening_round = True
        try:
            self._open_round_inner(symbol, ref_price=ref_price)
            with state.lock:
                state.round_open_retry_count = 0
        except Exception as e:
            with state.lock:
                state.round_open_retry_count += 1
                count = state.round_open_retry_count
            log.error(f"[{symbol}] failed to open round (attempt {count}/{MAX_ROUND_OPEN_RETRIES}): {e}")
            if count >= MAX_ROUND_OPEN_RETRIES:
                with state.lock:
                    state.halted = True
                msg = f"[{symbol}] Exceeded max round-open retries ({count}/{MAX_ROUND_OPEN_RETRIES}). Symbol HALTED! Manual intervention required."
                log.error(msg)
                send_alert(msg)
        finally:
            with state.lock:
                state.opening_round = False

    def _open_round_inner(self, symbol: str, ref_price: Optional[Decimal] = None):
        state = self.states[symbol]

        if TRADING_MODE == "LONG_ONLY":
            with state.lock:
                state.direction = Direction.LONG
                direction = Direction.LONG
        elif TRADING_MODE == "SHORT_ONLY":
            with state.lock:
                state.direction = Direction.SHORT
                direction = Direction.SHORT
        elif USE_TREND_MA_FILTER:
            trend_dir = self._get_trend_bias(symbol)
            with state.lock:
                state.direction = trend_dir
                direction = trend_dir
                log.info(f"[{symbol}] Trend MA Filter set round direction -> {direction.value}")
        else:
            with state.lock:
                direction = state.direction

        with state.lock:
            allocation_usdt = state.allocation_usdt
            max_notional_cap = state.max_notional_cap
            state.resting_dca_orders.clear()
            state.rung_last_qty.clear()
            min_notional = state.min_notional
            step_size = state.step_size
            tick_size = state.tick_size

        entry_side = SIDE_BUY if direction == Direction.LONG else SIDE_SELL
        exit_side = SIDE_SELL if direction == Direction.LONG else SIDE_BUY
        is_buy_side = (entry_side == SIDE_BUY)

        # Use configured PRICE_STEP_PCT for true DCA ladder spacing
        effective_step_pct = Decimal(str(PRICE_STEP_PCT))
        if effective_step_pct < Decimal("0.05"):
            effective_step_pct = Decimal("0.05")

        num_symbols = max(1, len(self.symbols))

        if USE_DYNAMIC_SIZING:
            try:
                live_bal = self._get_account_balance_usdt()
                if live_bal > 0:
                    allocation_usdt = (live_bal * Decimal(str(ACCOUNT_ALLOCATION_FRACTION_PER_SYMBOL))) / Decimal(str(num_symbols))
                    with state.lock:
                        state.allocation_usdt = allocation_usdt
            except Exception as e:
                log.warning(f"[{symbol}] dynamic balance update failed, using cached {allocation_usdt}: {e}")

        # Calculate base order margin to guarantee all 18 rungs (MIN_DCA_ORDERS) fit allocation and leverage bracket cap
        ladder_mult_sum = sum(Decimal(str(ORDER_SIZE_MULTIPLIER)) ** i for i in range(0, MIN_DCA_ORDERS + 1))
        max_base_for_alloc = (allocation_usdt * Decimal("0.95")) / ladder_mult_sum
        max_base_for_bracket = (max_notional_cap * Decimal("0.90")) / (ladder_mult_sum * Decimal(str(LEVERAGE)))
        max_allowed_base_margin = min(max_base_for_alloc, max_base_for_bracket)

        target_min_notional = max(min_notional, Decimal("6"))
        min_base_margin = round_step(target_min_notional / Decimal(LEVERAGE), Decimal("0.01"), ROUND_UP)

        if USE_DYNAMIC_SIZING:
            base_order_usdt = allocation_usdt * (Decimal(str(BASE_ORDER_PCT_OF_ALLOCATION)) / Decimal("100"))
            base_order_usdt = min(base_order_usdt, max_allowed_base_margin)
            base_order_usdt = max(base_order_usdt, min_base_margin)
            if base_order_usdt > allocation_usdt:
                base_order_usdt = allocation_usdt
        else:
            base_order_usdt = min(Decimal(str(BASE_ORDER_USDT)), max_allowed_base_margin)
            base_order_usdt = max(base_order_usdt, min_base_margin)

        # Fast mark price: use ref_price directly from memory if available, else fetch ticker
        mark_price = ref_price if (ref_price and ref_price > 0) else self._mark_price(symbol)

        qty0 = self._qty_for_notional(state, base_order_usdt, mark_price)
        if qty0 <= 0:
            raise RuntimeError("computed base order qty <= 0 (check min_notional / allocation)")

        order0 = self._place_market_order(symbol, entry_side, qty0)
        fill_price0 = Decimal(str(order0.get("avgPrice") or "0"))

        if fill_price0 <= 0:
            order_id0 = order0.get("orderId")
            if order_id0:
                for _ in range(5):
                    time.sleep(0.2)
                    try:
                        refreshed = self._get_order_info(symbol, order_id0)
                        refreshed_avg = Decimal(str(refreshed.get("avgPrice") or "0"))
                        if refreshed_avg > 0:
                            fill_price0 = refreshed_avg
                            break
                    except Exception:
                        continue

        if fill_price0 <= 0:
            fill_price0 = mark_price

        # Sanity check fill_price0 anchor against live mark price
        live_mark = self._mark_price(symbol)
        if live_mark > 0 and fill_price0 > 0:
            deviation = abs(fill_price0 - live_mark) / live_mark
            if deviation > Decimal("0.05"):
                log.critical(
                    f"[{symbol}] fill_price0={fill_price0} deviates {deviation:.1%} from "
                    f"live mark {live_mark}; treating fill_price0 as unreliable and using "
                    f"live mark price ({live_mark}) for ladder anchor/clamping instead."
                )
                fill_price0 = live_mark
        elif live_mark > 0:
            fill_price0 = live_mark

        with state.lock:
            state.entry_orders = [Fill(price=fill_price0, qty=qty0, order_id=order0["orderId"])]
            state.position_qty = qty0
            state.average_price = fill_price0
            state.rung_last_qty = {}
            state.rung_last_cost = {}
            state.round_start_time = time.time()
            round_num = state.round_number

        log.info(
            f"[{symbol}] ROUND {round_num} OPENED | dir={direction.value} "
            f"entry_price={fill_price0} qty={qty0} base_margin={base_order_usdt:.4f} USDT "
            f"(allocation={allocation_usdt:.2f} USDT, step_pct={effective_step_pct:.3f}%)"
        )

        remaining_budget = allocation_usdt - base_order_usdt
        usable_rungs = MIN_DCA_ORDERS
        cum_margin = Decimal("0")
        cum_notional = qty0 * fill_price0

        for i in range(1, MIN_DCA_ORDERS + 1):
            if direction == Direction.LONG:
                p_est = fill_price0 * (Decimal("1") - effective_step_pct / 100 * i)
                p_est = round_step(p_est, tick_size, ROUND_DOWN)
            else:
                p_est = fill_price0 * (Decimal("1") + effective_step_pct / 100 * i)
                p_est = round_step(p_est, tick_size, ROUND_UP)

            rung_notional = base_order_usdt * (Decimal(str(ORDER_SIZE_MULTIPLIER)) ** i)
            q_est = self._qty_for_notional(state, rung_notional, p_est)
            actual_notional_i = q_est * p_est
            actual_margin_i = actual_notional_i / Decimal(str(LEVERAGE))

            if (cum_margin + actual_margin_i > remaining_budget) or (cum_notional + actual_notional_i >= max_notional_cap * Decimal("0.95")):
                usable_rungs = i - 1
                break
            cum_margin += actual_margin_i
            cum_notional += actual_notional_i

        if usable_rungs < MIN_DCA_ORDERS:
            log.warning(
                f"[{symbol}] allocation/bracket cap supports {usable_rungs} DCA rungs (wanted {MIN_DCA_ORDERS}); "
                f"ladder safely capped (max_notional_cap={max_notional_cap} USDT)."
            )

        placed_dca_count = 0
        live_mark_fresh = self._mark_price(symbol)
        stop_outer_ladder = False
        for i in range(1, usable_rungs + 1):
            if stop_outer_ladder:
                break
            with state.lock:
                if state.halted:
                    log.warning(f"[{symbol}] state halted during DCA placement loop; stopping placement of remaining rungs.")
                    break

            if direction == Direction.LONG:
                rung_price = fill_price0 * (Decimal("1") - effective_step_pct / 100 * i)
                rung_price = round_step(rung_price, tick_size, ROUND_DOWN)
            else:
                rung_price = fill_price0 * (Decimal("1") + effective_step_pct / 100 * i)
                rung_price = round_step(rung_price, tick_size, ROUND_UP)

            raw_rung_price = rung_price
            rung_price = self._clamp_price_to_percent_filter(state, raw_rung_price, live_mark_fresh, is_buy=is_buy_side)
            is_boundary_clamped = (rung_price != raw_rung_price)

            rung_notional = base_order_usdt * (Decimal(str(ORDER_SIZE_MULTIPLIER)) ** i)
            rung_qty = self._qty_for_notional(state, rung_notional, rung_price)
            if rung_qty <= 0:
                log.warning(f"[{symbol}] rung {i} qty rounds to 0, skipping rung")
                continue

            actual_margin_i = (rung_qty * rung_price) / Decimal(str(LEVERAGE))
            if actual_margin_i > remaining_budget:
                clamped_notional = remaining_budget * Decimal(str(LEVERAGE))
                if clamped_notional >= target_min_notional:
                    rung_qty = self._qty_for_notional(state, clamped_notional, rung_price)
                    actual_margin_i = (rung_qty * rung_price) / Decimal(str(LEVERAGE))
                else:
                    log.warning(
                        f"[{symbol}] rung {i} required margin ({actual_margin_i:.2f} USDT) exceeds remaining "
                        f"allocation budget ({remaining_budget:.2f} USDT); stopping DCA ladder placement."
                    )
                    break
            remaining_budget -= actual_margin_i

            # Placement loop with 3 retries per rung to guarantee presence on exchange
            for attempt in range(1, 4):
                try:
                    order = self._place_limit_order(symbol, entry_side, rung_qty, rung_price, reduce_only=False, mark_price=live_mark_fresh)
                    order_id = order["orderId"]
                    placed_dca_count += 1
                    with state.lock:
                        state.resting_dca_orders.append(order_id)
                        state.rung_last_qty[order_id] = Decimal("0")
                        state.rung_last_cost[order_id] = Decimal("0")
                    if is_boundary_clamped:
                        log.info(f"[{symbol}] rung {i} reached PERCENT_PRICE filter boundary ({rung_price}); stopping placement of further out-of-bounds rungs.")
                        stop_outer_ladder = True
                    break
                except (BinanceAPIException, BinanceOrderException) as e:
                    if attempt < 3:
                        log.warning(f"[{symbol}] failed to place DCA rung {i} at {rung_price} (attempt {attempt}/3): {e}; retrying in 0.2s...")
                        time.sleep(0.2)
                    else:
                        log.error(f"[{symbol}] failed to place DCA rung {i} at {rung_price} after 3 attempts: {e}")

        if 0 < placed_dca_count < usable_rungs:
            log.error(
                f"[{symbol}] Only placed {placed_dca_count}/{usable_rungs} intended DCA rungs "
                f"this round — ladder is incomplete!"
            )
            send_alert(f"[{symbol}] DCA ladder incomplete: {placed_dca_count}/{usable_rungs} rungs placed.")

        if usable_rungs > 0 and placed_dca_count == 0:
            log.error(f"[{symbol}] All DCA limit order placements failed! Executing emergency market exit for base order...")
            with state.lock:
                state.halted = True
            send_alert(f"[{symbol}] All DCA limit order placements failed! Symbol HALTED.")
            flattened = self._emergency_flatten(symbol, exit_side)
            self._cancel_resting_orders(state)
            with state.lock:
                if flattened:
                    state.entry_orders = []
                    state.position_qty = Decimal("0")
            self._save_state()
            raise RuntimeError("Zero DCA limit orders placed successfully")

        self._place_tp_and_sl(state, exit_side)
        self._save_state()

    def _schedule_tp_sl_refresh(self, state: SymbolState, exit_side: str, delay: Optional[float] = None):
        """Debounce rapid DCA fill bursts so multiple fills coalesce into a single TP/SL algo replacement."""
        wait_time = delay if delay is not None else TP_SL_REFRESH_DEBOUNCE_SECONDS
        with state.lock:
            if state.halted:
                return
            if state.tp_sl_refresh_timer is not None:
                try:
                    state.tp_sl_refresh_timer.cancel()
                except Exception:
                    pass
            timer = threading.Timer(
                wait_time, self._place_tp_and_sl, args=(state, exit_side)
            )
            timer.daemon = True
            state.tp_sl_refresh_timer = timer
            timer.start()

    def _place_tp_and_sl(self, state: SymbolState, exit_side: str):
        symbol = state.symbol
        with state.lock:
            if state.tp_sl_refresh_timer is not None:
                try:
                    state.tp_sl_refresh_timer.cancel()
                except Exception:
                    pass
                state.tp_sl_refresh_timer = None
            if state.halted:
                log.warning(f"[{symbol}] Symbol is halted; skipping TP/SL refresh.")
                return
            if state.refreshing_tp_sl:
                state.tp_sl_dirty = True
                log.info(f"[{symbol}] TP/SL placement in progress, marked dirty to re-run for latest fill")
                return
            state.refreshing_tp_sl = True

        pending_close_reason = None

        try:
            while True:
                with state.lock:
                    if state.halted:
                        log.warning(f"[{symbol}] halted mid TP/SL refresh; aborting loop.")
                        return
                    state.tp_sl_dirty = False
                    pos_qty = state.position_qty
                    avg_price = state.average_price
                    prev_tp = state.take_profit_algo_id
                    prev_sl = state.stop_loss_algo_id

                if pos_qty <= 0:
                    if prev_tp:
                        self._cancel_algo_order(symbol, prev_tp)
                    if prev_sl:
                        self._cancel_algo_order(symbol, prev_sl)
                    self._cancel_resting_orders(state)
                    try:
                        self._cancel_all_open_orders(symbol)
                    except Exception:
                        pass
                    return

                # Pre-verify exchange position truth before submitting algo orders
                try:
                    positions = self._get_position_info(symbol)
                    ex_pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
                    if ex_pos_amt == 0:
                        log.info(f"[{symbol}] Exchange position is flat (pos_amt=0); canceling all open resting orders.")
                        with state.lock:
                            state.position_qty = Decimal("0")
                            state.entry_orders = []
                            state.take_profit_algo_id = None
                            state.stop_loss_algo_id = None
                        self._cancel_resting_orders(state)
                        try:
                            self._cancel_all_open_orders(symbol)
                        except Exception:
                            pass
                        self._save_state()
                    live_liq_price = next(
                        (Decimal(str(p["liquidationPrice"])) for p in positions if p.get("symbol") == symbol and Decimal(str(p.get("liquidationPrice") or "0")) > 0),
                        None,
                    )
                    pos_qty = ex_pos_amt
                    with state.lock:
                        if state.position_qty != ex_pos_amt:
                            log.info(f"[{symbol}] Manual position size change detected on exchange ({state.position_qty} -> {ex_pos_amt}); updating state.")
                            state.position_qty = ex_pos_amt
                except Exception as e:
                    live_liq_price = None
                    log.debug(f"[{symbol}] exchange position pre-check exception in _place_tp_and_sl: {e}")

                live_mark = self._mark_price(symbol)
                tp_price = self._compute_tp_price(state, mark_price=live_mark)
                sl_price = self._compute_sl_price(state, mark_price=live_mark, liq_price=live_liq_price)

                # Estimate maintenance margin rate (approx 0.5% for <=20x) and liquidation price for safety monitoring
                mmr = Decimal("0.005")
                lev = Decimal(str(LEVERAGE))
                if MARGIN_TYPE == "CROSSED":
                    try:
                        tot_equity = self._get_total_equity()
                        maint_margin = (pos_qty * avg_price) * mmr
                        buffer_per_unit = max(Decimal("0"), tot_equity - maint_margin) / pos_qty if pos_qty > 0 else Decimal("0")
                        if state.direction == Direction.LONG:
                            est_liq_price = max(Decimal("0"), avg_price - buffer_per_unit)
                        else:
                            est_liq_price = avg_price + buffer_per_unit
                    except Exception:
                        est_liq_price = avg_price * (Decimal("1") - (Decimal("1") / lev) + mmr) if state.direction == Direction.LONG else avg_price * (Decimal("1") + (Decimal("1") / lev) - mmr)
                else:
                    if state.direction == Direction.LONG:
                        est_liq_price = avg_price * (Decimal("1") - (Decimal("1") / lev) + mmr)
                    else:
                        est_liq_price = avg_price * (Decimal("1") + (Decimal("1") / lev) - mmr)

                est_liq_price = round_step(est_liq_price, state.tick_size, ROUND_DOWN if state.direction == Direction.LONG else ROUND_UP)
                if state.direction == Direction.LONG and sl_price <= est_liq_price:
                    log.critical(
                        f"[{symbol}] RISK WARNING: Stop loss trigger ({sl_price}) is BELOW or EQUAL to "
                        f"estimated liquidation price ({est_liq_price})! Liquidation may occur before SL triggers!"
                    )
                elif state.direction == Direction.SHORT and sl_price >= est_liq_price:
                    log.critical(
                        f"[{symbol}] RISK WARNING: Stop loss trigger ({sl_price}) is ABOVE or EQUAL to "
                        f"estimated liquidation price ({est_liq_price})! Liquidation may occur before SL triggers!"
                    )

                # Check live mark price against protective targets before attempting placement
                try:
                    mark_p = live_mark
                    with state.lock:
                        dir_val = state.direction
                    
                    tp_triggered = (dir_val == Direction.LONG and mark_p >= tp_price) or (dir_val == Direction.SHORT and mark_p <= tp_price)
                    sl_triggered = (dir_val == Direction.LONG and mark_p <= sl_price) or (dir_val == Direction.SHORT and mark_p >= sl_price)

                    if tp_triggered:
                        log.warning(f"[{symbol}] Live mark price ({mark_p}) reached/exceeded TP target ({tp_price}); executing immediate MARKET take-profit exit!")
                        if self._emergency_market_exit_until_flat(symbol, exit_side):
                            pending_close_reason = "TP"
                        else:
                            with state.lock:
                                state.halted = True
                            msg = f"[{symbol}] TP mark price exit failed to flatten position; halting symbol for manual review."
                            log.critical(msg)
                            send_alert(msg)
                        return
                    elif sl_triggered:
                        log.warning(f"[{symbol}] Live mark price ({mark_p}) crossed SL target ({sl_price}); executing immediate MARKET stop-loss exit!")
                        if self._emergency_market_exit_until_flat(symbol, exit_side):
                            pending_close_reason = "SL"
                        else:
                            with state.lock:
                                state.halted = True
                            msg = f"[{symbol}] SL mark price exit failed to flatten position; halting symbol for manual review."
                            log.critical(msg)
                            send_alert(msg)
                        return
                except Exception as e:
                    log.debug(f"[{symbol}] mark price pre-check exception in _place_tp_and_sl: {e}")

                new_tp_algo_id = None
                new_sl_algo_id = None

                # 1. PLACE NEW Take-Profit (cancels prev_tp after rate limit check passes)
                try:
                    tp_algo = self._place_take_profit_algo(symbol, exit_side, tp_price, cancel_algo_id=prev_tp)
                    new_tp_algo_id = tp_algo.get("algoId")
                except BinanceAPIException as e:
                    if e.code == -2021:
                        log.warning(f"[{symbol}] TP price {tp_price} would immediately trigger (code=-2021); executing immediate MARKET take-profit exit!")
                        if self._emergency_market_exit_until_flat(symbol, exit_side):
                            pending_close_reason = "TP"
                        else:
                            with state.lock:
                                state.halted = True
                            msg = f"[{symbol}] TP market exit (-2021) failed to flatten position; halting symbol for manual review."
                            log.critical(msg)
                            send_alert(msg)
                        return
                    elif e.code == -4509:
                        log.warning(f"[{symbol}] TP placement returned code=-4509 (no open position on exchange). Canceling all resting orders...")
                        positions = self._get_position_info(symbol)
                        pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
                        if pos_amt == 0:
                            log.info(f"[{symbol}] Exchange confirmed position flat after code=-4509.")
                            with state.lock:
                                state.position_qty = Decimal("0")
                                state.entry_orders = []
                                state.take_profit_algo_id = None
                                state.stop_loss_algo_id = None
                            self._cancel_resting_orders(state)
                            try:
                                self._cancel_all_open_orders(symbol)
                            except Exception:
                                pass
                            self._save_state()
                            return
                    log.warning(f"[{symbol}] TP algo placement failed: {e}")
                except Exception as e:
                    log.warning(f"[{symbol}] TP algo placement failed: {e}")

                # 2. PLACE NEW Stop-Loss (cancels prev_sl after rate limit check passes)
                try:
                    sl_algo = self._place_stop_loss_algo(symbol, exit_side, sl_price, cancel_algo_id=prev_sl)
                    new_sl_algo_id = sl_algo.get("algoId")
                except BinanceAPIException as e:
                    if e.code == -2021:
                        log.warning(f"[{symbol}] SL price {sl_price} would immediately trigger (code=-2021); executing immediate MARKET stop-loss exit!")
                        if self._emergency_market_exit_until_flat(symbol, exit_side):
                            pending_close_reason = "SL"
                        else:
                            with state.lock:
                                state.halted = True
                            msg = f"[{symbol}] SL market exit (-2021) failed to flatten position; halting symbol for manual review."
                            log.critical(msg)
                            send_alert(msg)
                        return
                    elif e.code == -4509:
                        log.warning(f"[{symbol}] SL placement returned code=-4509 (no open position on exchange).")
                        positions = self._get_position_info(symbol)
                        pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
                        if pos_amt == 0:
                            log.info(f"[{symbol}] Exchange confirmed position flat after code=-4509.")
                            with state.lock:
                                state.position_qty = Decimal("0")
                                state.entry_orders = []
                                state.take_profit_algo_id = None
                                state.stop_loss_algo_id = None
                            self._save_state()
                            return
                    log.warning(f"[{symbol}] SL algo placement failed: {e}")
                except Exception as e:
                    log.warning(f"[{symbol}] SL algo placement failed: {e}")

                # If replacement succeeded, use new algo ID; otherwise keep previous resting order as fallback
                active_tp = new_tp_algo_id or prev_tp
                active_sl = new_sl_algo_id or prev_sl

                with state.lock:
                    state.take_profit_algo_id = active_tp
                    state.stop_loss_algo_id = active_sl
                    state.tp_sl_placed_time = time.monotonic()


                # Accurate status logging
                tp_str = f"set at {tp_price} (algoId={new_tp_algo_id})" if new_tp_algo_id else (f"FALLBACK (algoId={prev_tp})" if prev_tp else "FAILED")
                sl_str = f"set at {sl_price} (algoId={new_sl_algo_id})" if new_sl_algo_id else (f"FALLBACK (algoId={prev_sl})" if prev_sl else "FAILED")

                log.info(f"[{symbol}] Protection status | TP {tp_str} | SL {sl_str} | avg_price={avg_price}")

                # Verify full protection coverage by checking live open algo orders if in-flight check is None
                if not active_tp or not active_sl:
                    try:
                        open_algos = self.client.futures_get_open_algo_orders(symbol=symbol)
                        if isinstance(open_algos, list):
                            for a in open_algos:
                                a_id = a.get("algoId")
                                a_type = str(a.get("type") or a.get("algoType", ""))
                                if a_id:
                                    if "TAKE_PROFIT" in a_type and not active_tp:
                                        active_tp = a_id
                                    elif "STOP" in a_type and not active_sl:
                                        active_sl = a_id
                    except Exception:
                        pass

                    with state.lock:
                        state.take_profit_algo_id = active_tp
                        state.stop_loss_algo_id = active_sl

                tp_covered = bool(active_tp)
                sl_covered = bool(active_sl)

                if tp_covered and sl_covered:
                    state.protection_retry_count = 0

                # CRITICAL SAFETY GUARD: If Stop-Loss or Take-Profit placement failed completely with no fallback, retry debounced refresh before emergency exit
                if not tp_covered or not sl_covered:
                    current_retries = getattr(state, "protection_retry_count", 0)
                    if current_retries < 3:
                        state.protection_retry_count = current_retries + 1
                        missing_type = "Stop-Loss" if not sl_covered else "Take-Profit"
                        log.warning(
                            f"[{symbol}] Protection placement transient delay for {missing_type} (attempt {state.protection_retry_count}/3); "
                            f"scheduling 1.5s debounced retry before emergency exit..."
                        )
                        self._schedule_tp_sl_refresh(state, exit_side, delay=1.5)
                        return

                    state.protection_retry_count = 0
                    missing_type = "Stop-Loss" if not sl_covered else "Take-Profit"
                    log.critical(
                        f"[{symbol}] UNPROTECTED LEVERAGED POSITION DETECTED! Failed to secure {missing_type} protection after 3 retries. "
                        f"Executing robust emergency market exit loop until flat..."
                    )
                    with state.lock:
                        state.halted = True
                    send_alert(f"[{symbol}] UNPROTECTED LEVERAGED POSITION DETECTED! Failed to secure {missing_type} protection. Symbol HALTED.")

                    flattened = self._emergency_flatten(symbol, exit_side)
                    self._cancel_resting_orders(state)

                    with state.lock:
                        if flattened:
                            state.entry_orders = []
                            state.position_qty = Decimal("0")
                        else:
                            log.critical(
                                f"[{symbol}] EMERGENCY FLATTEN FAILED AFTER RETRIES! Manual intervention required immediately, "
                                f"account may still hold an unprotected position on {symbol}."
                            )
                    self._save_state()

                    raise RuntimeError(
                        f"[{symbol}] Failed to secure {missing_type} order for open position; "
                        f"emergency flatten {'succeeded' if flattened else 'FAILED, position may still be open'}."
                    )

                with state.lock:
                    if state.halted:
                        log.warning(f"[{symbol}] Symbol halted during TP/SL refresh; aborting loop.")
                        break
                    if state.tp_sl_dirty:
                        log.info(f"[{symbol}] Newer fill arrived during placement; scheduling debounced TP/SL refresh ({TP_SL_REFRESH_DEBOUNCE_SECONDS}s)...")
                        self._schedule_tp_sl_refresh(state, exit_side, delay=TP_SL_REFRESH_DEBOUNCE_SECONDS)
                    break

        finally:
            with state.lock:
                state.refreshing_tp_sl = False

        if pending_close_reason == "TP":
            self.on_take_profit_filled(symbol)
        elif pending_close_reason == "SL":
            self.on_stop_loss_filled(symbol)


    # =======================================================================
    # SECTION 8: REAL-TIME FILL DETECTION & PARTIAL FILL ACCUMULATION
    # =======================================================================

    def _handle_order_update(self, symbol: str, order_id: int, status: str, executed_qty: Decimal, price: Decimal, event_round: Optional[int] = None, price_is_total_avg: bool = False):
        """Unified entry point for both WebSocket callbacks and REST reconciliation poller.
        Tracks partial and full fills via executedQty deltas, recalculates the volume-weighted
        average entry price, updates position size, and schedules a debounced TP/SL refresh."""
        state = self.states.get(symbol)
        if not state:
            return

        needs_tp_sl_refresh = False
        is_terminal = False

        with state.lock:
            if event_round is not None and state.round_number != event_round:
                return

            # ATOMIC TOCTOU Membership & Fill Check (Single Lock Acquisition)
            if order_id not in state.resting_dca_orders and order_id not in state.rung_last_qty:
                return

            last_seen_qty = state.rung_last_qty.get(order_id, Decimal("0"))
            last_seen_cost = state.rung_last_cost.get(order_id, Decimal("0"))
            delta_qty = executed_qty - last_seen_qty
            if delta_qty < 0:
                delta_qty = Decimal("0")

            if delta_qty > 0:
                if price_is_total_avg:
                    if price <= 0:
                        log.warning(f"[{symbol}] order update for order {order_id} has invalid avgPrice={price}, skipping cost recalculation until valid price received")
                        return
                    current_total_cost = executed_qty * price
                    delta_cost = current_total_cost - last_seen_cost
                    if delta_cost < 0:
                        delta_cost = Decimal("0")
                    fill_price = delta_cost / delta_qty if delta_qty > 0 else price
                else:
                    if price > 0:
                        fill_price = price
                    elif state.average_price > 0:
                        fill_price = state.average_price
                    else:
                        fill_price = self._mark_price(symbol)
                    current_total_cost = last_seen_cost + (fill_price * delta_qty)


                state.entry_orders.append(Fill(price=fill_price, qty=delta_qty, order_id=order_id))
                total_cost = sum(f.price * f.qty for f in state.entry_orders)
                total_qty = sum(f.qty for f in state.entry_orders)
                state.average_price = total_cost / total_qty
                state.position_qty = total_qty
                state.rung_last_qty[order_id] = executed_qty
                state.rung_last_cost[order_id] = current_total_cost
                needs_tp_sl_refresh = True
                log.info(
                    f"[{symbol}] DCA RUNG FILL | order_id={order_id} +{delta_qty} @ {fill_price} "
                    f"| new_average={state.average_price} new_qty={state.position_qty}"
                )

            is_terminal = status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED")
            if is_terminal:
                if order_id in state.resting_dca_orders:
                    state.resting_dca_orders.remove(order_id)
                state.rung_last_qty.pop(order_id, None)
                state.rung_last_cost.pop(order_id, None)

            if needs_tp_sl_refresh:
                exit_side = SIDE_SELL if state.direction == Direction.LONG else SIDE_BUY

        if needs_tp_sl_refresh:
            self._schedule_tp_sl_refresh(state, exit_side)

        if needs_tp_sl_refresh or is_terminal:
            self._save_state()

    @retry()
    def _get_trend_bias(self, symbol: str) -> Direction:
        """Calculates 20-period 5-minute Simple Moving Average (SMA) baseline to confirm macro trend direction."""
        state = self.states.get(symbol)
        current_dir = state.direction if state else Direction.LONG
        flipped_fallback = Direction.SHORT if current_dir == Direction.LONG else Direction.LONG
        try:
            klines = self.client.futures_klines(symbol=symbol, interval="5m", limit=20)
            if not klines:
                raise ValueError("empty klines response")
            closes = [Decimal(str(k[4])) for k in klines]
            sma_20 = sum(closes) / Decimal(len(closes))
            mark_price = self._mark_price(symbol)
            bias = Direction.LONG if mark_price >= sma_20 else Direction.SHORT
            log.info(f"[{symbol}] Trend MA Filter | Mark={mark_price} 20-SMA={sma_20:.7f} -> Bias={bias.value}")
            return bias
        except Exception as e:
            log.warning(f"[{symbol}] failed to fetch trend MA ({e}), falling back to flipped direction ({flipped_fallback.value})")
            return flipped_fallback

    # =======================================================================
    # SECTION 9: ROUND COMPLETION & DIRECTION CONTROL (TP, SL, EXTERNAL)
    # =======================================================================

    def on_take_profit_filled(self, symbol: str, event_round: Optional[int] = None, expected_tp_algo_id: Optional[int] = None):
        """Triggered when the Take-Profit algo order fills in profit.
        Cancels leftover DCA orders, appends trade metrics to CSV, and restarts in the same direction."""
        state = self.states[symbol]
        with state.lock:
            if event_round is not None and state.round_number != event_round:
                log.info(f"[{symbol}] ignoring TP filled event for past round {event_round} (current round is {state.round_number})")
                return
            if expected_tp_algo_id is not None and str(state.take_profit_algo_id) != str(expected_tp_algo_id):
                log.info(f"[{symbol}] ignoring stale TP-filled call for algo {expected_tp_algo_id} (current TP is {state.take_profit_algo_id})")
                return
            # Idempotency guard
            if state.take_profit_algo_id is None and state.position_qty == 0:
                return
            last_price = state.average_price
            closed_qty = state.position_qty
            closed_round = state.round_number
            closed_dir = state.direction.value
            r_start = state.round_start_time

            log.info(
                f"[{symbol}] ROUND {state.round_number} CLOSED IN PROFIT | avg_entry={last_price}"
            )
            state.consecutive_sl_count = 0  # Reset consecutive SL streak on profit
            state.take_profit_algo_id = None  # this is the order that just filled -- don't re-cancel it
            state.entry_orders = []
            state.position_qty = Decimal("0")
            state.round_number += 1

        # Cancel resting DCA orders outside lock to prevent lock contention
        self._cancel_resting_orders(state)
        self._save_state()

        # Log completed round performance to CSV
        try:
            live_m = self._mark_price(symbol)
            exit_p = live_m if live_m > 0 else last_price
            if closed_qty > 0 and last_price > 0:
                self._log_trade_history(
                    symbol=symbol,
                    round_number=closed_round,
                    direction=closed_dir,
                    entry_price=last_price,
                    exit_price=exit_p,
                    quantity=closed_qty,
                    exit_reason="TAKE_PROFIT",
                    start_time=r_start,
                )
        except Exception as e:
            log.debug(f"[{symbol}] Error dispatching TP trade history: {e}")

        if not state.halted:
            self.open_round(symbol, ref_price=last_price)

    def on_stop_loss_filled(self, symbol: str, event_round: Optional[int] = None, expected_sl_algo_id: Optional[int] = None):
        """Triggered when the Stop-Loss algo order fills.
        Cancels resting DCA orders, logs metrics to CSV, applies anti-whipsaw / cooldown pause,
        enforces TRADING_MODE (LONG_ONLY), and initiates the next round."""
        state = self.states[symbol]
        with state.lock:
            if event_round is not None and state.round_number != event_round:
                log.info(f"[{symbol}] ignoring SL filled event for past round {event_round} (current round is {state.round_number})")
                return
            if expected_sl_algo_id is not None and str(state.stop_loss_algo_id) != str(expected_sl_algo_id):
                log.info(f"[{symbol}] ignoring stale SL-filled call for algo {expected_sl_algo_id} (current SL is {state.stop_loss_algo_id})")
                return
            # Idempotency guard
            if state.stop_loss_algo_id is None and state.position_qty == 0:
                return
            last_price = state.average_price
            closed_qty = state.position_qty
            closed_round = state.round_number
            closed_dir = state.direction.value
            r_start = state.round_start_time

            state.consecutive_sl_count += 1
            sl_count = state.consecutive_sl_count
            log.info(
                f"[{symbol}] ROUND {state.round_number} CLOSED AT LOSS (dir={state.direction.value}) | "
                f"avg_entry={last_price} | consecutive_sl_streak={sl_count}"
            )
            state.stop_loss_algo_id = None  # this is the order that just triggered -- don't re-cancel it
            state.entry_orders = []
            state.position_qty = Decimal("0")
            state.round_number += 1

        # Cancel resting DCA orders outside lock to prevent lock contention
        self._cancel_resting_orders(state)
        self._save_state()

        # Log completed round performance to CSV
        try:
            live_m = self._mark_price(symbol)
            exit_p = live_m if live_m > 0 else last_price
            if closed_qty > 0 and last_price > 0:
                self._log_trade_history(
                    symbol=symbol,
                    round_number=closed_round,
                    direction=closed_dir,
                    entry_price=last_price,
                    exit_price=exit_p,
                    quantity=closed_qty,
                    exit_reason="STOP_LOSS",
                    start_time=r_start,
                )
        except Exception as e:
            log.debug(f"[{symbol}] Error dispatching SL trade history: {e}")

        if ENABLE_ANTI_WHIPSAW_GUARD and sl_count >= MAX_CONSECUTIVE_SL:
            log.warning(
                f"[{symbol}] ANTI-WHIPSAW GUARD TRIGGERED ({sl_count} consecutive SL hits)! "
                f"Market is in a choppy/whipsaw regime. Scheduling non-blocking {WHIPSAW_COOLDOWN_SECONDS}s (5m) cooldown..."
            )
            with state.lock:
                state.cooldown_until = time.monotonic() + WHIPSAW_COOLDOWN_SECONDS
                state.consecutive_sl_count = 0
        elif TREND_PAUSE_SECONDS > 0:
            log.info(f"[{symbol}] scheduling non-blocking {TREND_PAUSE_SECONDS}s trend pause before next round")
            with state.lock:
                state.cooldown_until = time.monotonic() + TREND_PAUSE_SECONDS


        # Determine direction for next round based on TRADING_MODE, ENABLE_AUTO_FLIP, and MA filter
        with state.lock:
            if TRADING_MODE == "LONG_ONLY":
                state.direction = Direction.LONG
                log.info(f"[{symbol}] TRADING_MODE is LONG_ONLY; maintaining direction -> LONG")
            elif TRADING_MODE == "SHORT_ONLY":
                state.direction = Direction.SHORT
                log.info(f"[{symbol}] TRADING_MODE is SHORT_ONLY; maintaining direction -> SHORT")
            elif ENABLE_AUTO_FLIP and not USE_TREND_MA_FILTER:
                state.direction = Direction.SHORT if state.direction == Direction.LONG else Direction.LONG
                log.info(f"[{symbol}] auto-flip direction -> {state.direction.value}")
            else:
                log.info(f"[{symbol}] maintaining current direction -> {state.direction.value}")
        self._save_state()

        if not state.halted:
            self.open_round(symbol, ref_price=last_price)

    # =======================================================================
    # SECTION 11: REAL-TIME WEBSOCKET USER DATA STREAM & HEARTBEAT WATCHDOG
    # =======================================================================

    def _on_ticker_heartbeat(self, msg: dict):
        """Callback for symbol ticker WebSocket events; updates heartbeat timestamp."""
        self._last_ws_message_time = time.monotonic()
        self._ws_connected = True
        self._ws_reconnect_attempts = 0

    def start_user_stream(self):
        """Initializes the Binance Futures User Data WebSocket stream with per-symbol FIFO worker queues."""
        try:
            for symbol in self.symbols:
                if symbol not in self.symbol_queues:
                    q = queue.Queue()
                    self.symbol_queues[symbol] = q
                    t = threading.Thread(
                        target=self._symbol_ws_worker,
                        args=(symbol,),
                        daemon=True,
                        name=f"ws-worker-{symbol}",
                    )
                    t.start()
                    self.symbol_ws_threads.append(t)

            if getattr(self, "twm", None) is None:
                try:
                    self.twm = ThreadedWebsocketManager(api_key=self._api_key, api_secret=self._api_secret, testnet=TESTNET)
                    self.twm.start()
                except Exception as e:
                    log.warning(f"WebSocket manager startup warning ({_clean_err_msg(e)}); falling back to REST reconciliation loop.")
                    self.twm = None

            if self.twm is None:
                log.warning("WebSocket stream offline; trading safely via REST polling reconciliation.")
                return

            user_conn = getattr(self, "_user_conn_key", None)
            if user_conn and self.twm:
                try:
                    self.twm.stop_socket(user_conn)
                except Exception:
                    pass
                self._user_conn_key = None

            ticker_conn = getattr(self, "_ticker_conn_key", None)
            if ticker_conn and self.twm:
                try:
                    self.twm.stop_socket(ticker_conn)
                except Exception:
                    pass
                self._ticker_conn_key = None

            self._user_conn_key = self.twm.start_futures_user_socket(callback=self._on_user_data_message)
            if self.symbols:
                self._ticker_conn_key = self.twm.start_symbol_ticker_socket(symbol=self.symbols[0], callback=self._on_ticker_heartbeat)

            self._last_ws_message_time = time.monotonic()
            self._ws_connected = True
            log.info("user data websocket stream started with per-symbol FIFO worker queues and 1s ticker heartbeat")
        except Exception as e:
            log.error(f"failed to start user data websocket stream: {e}")
            self._ws_connected = False

    def _symbol_ws_worker(self, symbol: str):
        q = self.symbol_queues[symbol]
        while not self._stop_event.is_set():
            try:
                item = q.get(timeout=1.0)
                try:
                    self._process_user_data_message(item)
                finally:
                    q.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                log.exception(f"[{symbol}] error in symbol ws worker loop: {e}")

    def ws_heartbeat_watchdog(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(15):
                break
            if self._last_ws_message_time == 0.0:
                continue
            silent_for = time.monotonic() - self._last_ws_message_time
            if silent_for > WS_HEARTBEAT_TIMEOUT_SECONDS:
                self._ws_reconnect_attempts += 1
                backoff = min(60, 5 * (2 ** (self._ws_reconnect_attempts - 1)))
                log.error(
                    f"No websocket message received in {silent_for:.0f}s (limit {WS_HEARTBEAT_TIMEOUT_SECONDS}s); "
                    f"attempt {self._ws_reconnect_attempts}, backing off {backoff}s before reconnect..."
                )
                self._ws_connected = False
                if self._stop_event.wait(backoff):
                    break
                try:
                    self.start_user_stream()
                except Exception as e:
                    log.error(f"Websocket reconnect attempt failed: {e}")
            else:
                self._ws_connected = True



    def _on_user_data_message(self, msg: dict):
        self._last_ws_message_time = time.monotonic()
        self._ws_connected = True
        self._ws_reconnect_attempts = 0
        try:
            etype = msg.get("e")

            if etype == "ORDER_TRADE_UPDATE":
                o = msg.get("o", {})
                symbol = o.get("s")
                if symbol in self.states and symbol in self.symbol_queues:
                    state = self.states[symbol]
                    with state.lock:
                        current_round = state.round_number
                    self.symbol_queues[symbol].put((msg, current_round))
            elif etype == "ALGO_UPDATE":
                o = msg.get("o") or msg
                symbol = o.get("s")
                algo_id = o.get("aid") or o.get("algoId") or o.get("ai")
                if symbol and symbol in self.symbol_queues and symbol in self.states:
                    state = self.states[symbol]
                    with state.lock:
                        current_round = state.round_number
                    self.symbol_queues[symbol].put((msg, current_round))
                elif algo_id:
                    matched_sym = None
                    for sym, state in self.states.items():
                        with state.lock:
                            if (state.take_profit_algo_id and str(state.take_profit_algo_id) == str(algo_id)) or \
                               (state.stop_loss_algo_id and str(state.stop_loss_algo_id) == str(algo_id)):
                                matched_sym = sym
                                current_round = state.round_number
                                break
                    if matched_sym and matched_sym in self.symbol_queues:
                        self.symbol_queues[matched_sym].put((msg, current_round))
                    else:
                        log.debug(f"Received ALGO_UPDATE for untracked algoId={algo_id}, symbol={symbol}")

        except Exception as e:
            log.exception(f"error dispatching user data websocket message: {e}")

    def _process_user_data_message(self, item: tuple):
        try:
            msg, event_round = item
            etype = msg.get("e")
            if etype == "ORDER_TRADE_UPDATE":
                o = msg["o"]
                symbol = o["s"]
                state = self.states.get(symbol)
                if not state:
                    return

                with state.lock:
                    if state.round_number != event_round:
                        log.info(
                            f"[{symbol}] ignoring order update from past round {event_round} "
                            f"(current round is {state.round_number})"
                        )
                        return

                order_id = o["i"]
                status = o["X"]
                executed_qty = Decimal(str(o["z"]))  # cumulative filled quantity
                last_price = Decimal(str(o.get("L", "0")))
                if last_price == 0:
                    last_price = Decimal(str(o.get("ap", "0")))
                self._handle_order_update(symbol, order_id, status, executed_qty, last_price, event_round)

                # Algo Order Fast Path: when our stop-loss or take-profit algo order triggers,
                # Binance's Algo Service spawns a market order to close the position.
                # That spawned order carries strategy ID ("si") equal to the algoId that triggered it.
                # Catching it here enables near-instant reaction without waiting for the slower ALGO_UPDATE event.
                strategy_id = o.get("si")
                if strategy_id and status == "FILLED":
                    with state.lock:
                        tp_match = (state.take_profit_algo_id is not None) and (str(state.take_profit_algo_id) == str(strategy_id))
                        sl_match = (state.stop_loss_algo_id is not None) and (str(state.stop_loss_algo_id) == str(strategy_id))
                    if tp_match:
                        self.on_take_profit_filled(symbol, event_round, expected_tp_algo_id=strategy_id)
                    elif sl_match:
                        self.on_stop_loss_filled(symbol, event_round, expected_sl_algo_id=strategy_id)


            elif etype == "ALGO_UPDATE":
                o = msg.get("o") or msg
                target_symbol = o.get("s")
                algo_id = o.get("aid") or o.get("algoId") or o.get("ai")
                status = o.get("as") or o.get("algoStatus") or o.get("X")
                if target_symbol and target_symbol in self.states:
                    self._handle_algo_update_event(target_symbol, algo_id, status, event_round)
                elif algo_id:
                    for sym, state in self.states.items():
                        with state.lock:
                            is_match = (state.take_profit_algo_id and str(state.take_profit_algo_id) == str(algo_id)) or \
                                       (state.stop_loss_algo_id and str(state.stop_loss_algo_id) == str(algo_id))
                        if is_match:
                            self._handle_algo_update_event(sym, algo_id, status, event_round)
                            break


        except Exception as e:
            log.exception(f"error processing user data websocket message: {e}")

    def _handle_algo_update_event(self, symbol: str, algo_id: Optional[int], status: Optional[str], event_round: Optional[int]):
        state = self.states.get(symbol)
        if not state or state.halted:
            return

        with state.lock:
            if event_round is not None and state.round_number != event_round:
                return
            tp_algo_id = state.take_profit_algo_id
            sl_algo_id = state.stop_loss_algo_id

        if not status:
            self._reconcile_algo_orders(symbol)
            return

        status_upper = status.upper()

        # Active status ("NEW", "WORKING") -- order is resting peacefully on exchange
        if status_upper in ("NEW", "WORKING"):
            return

        # Triggered / Filled status -- order triggered and closed position
        if status_upper in ("FINISHED", "FILLED", "TRIGGERED", "EXECUTED"):
            if algo_id and tp_algo_id and str(algo_id) == str(tp_algo_id):
                try:
                    positions = self._get_position_info(symbol)
                    pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
                except Exception as e:
                    log.warning(f"[{symbol}] position check during TP ALGO_UPDATE failed ({e}), delegating to reconcile")
                    pos_amt = None

                if pos_amt == 0:
                    self.on_take_profit_filled(symbol, event_round, expected_tp_algo_id=algo_id)
                else:
                    self._reconcile_algo_orders(symbol)
            elif algo_id and sl_algo_id and str(algo_id) == str(sl_algo_id):
                try:
                    positions = self._get_position_info(symbol)
                    pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
                except Exception as e:
                    log.warning(f"[{symbol}] position check during SL ALGO_UPDATE failed ({e}), delegating to reconcile")
                    pos_amt = None

                if pos_amt == 0:
                    self.on_stop_loss_filled(symbol, event_round, expected_sl_algo_id=algo_id)
                else:
                    self._reconcile_algo_orders(symbol)
            else:
                self._reconcile_algo_orders(symbol)
            return


        # Canceled / Expired / Rejected status
        if status_upper in ("CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
            with state.lock:
                if algo_id and str(algo_id) in state.expected_canceled_algo_ids:
                    state.expected_canceled_algo_ids.remove(str(algo_id))
                    log.debug(f"[{symbol}] ignoring expected intentional cancellation for algo_id={algo_id}")
                    return

                tp_is_current = algo_id and tp_algo_id and str(algo_id) == str(tp_algo_id)
                sl_is_current = algo_id and sl_algo_id and str(algo_id) == str(sl_algo_id)

            # Strict guard: if algo_id doesn't match the currently tracked TP or SL ID, ignore stale event
            if algo_id and not (tp_is_current or sl_is_current):
                log.debug(f"[{symbol}] ignoring stale/superseded algo update event (algo_id={algo_id}, status={status})")
                return

            try:
                positions = self._get_position_info(symbol)
                pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
            except Exception:
                pos_amt = state.position_qty

            if pos_amt == 0:
                time.sleep(0.05)
                try:
                    positions = self._get_position_info(symbol)
                    pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
                except Exception:
                    pass

            if pos_amt == 0:
                hint = "TP" if tp_is_current else ("SL" if sl_is_current else None)
                reason = self._determine_position_close_reason(symbol, algo_type_hint=hint)
                if reason == "TP":
                    self.on_take_profit_filled(symbol, event_round, expected_tp_algo_id=tp_algo_id)
                elif reason == "SL":
                    self.on_stop_loss_filled(symbol, event_round, expected_sl_algo_id=sl_algo_id)
                else:
                    self._handle_external_close(symbol, f"algo order {algo_id} ended (status={status}) & position flat", expected_round=event_round)
            else:
                # Position is still open, and the currently tracked protective order really ended -- replace it
                with state.lock:
                    if tp_is_current:
                        state.take_profit_algo_id = None
                    elif sl_is_current:
                        state.stop_loss_algo_id = None
                    direction = state.direction
                    last_placed = state.tp_sl_placed_time
                    now = time.monotonic()
                    state.protection_gap_events += 1
                    state.protection_gap_timestamps.append(now)
                    state.protection_gap_timestamps = [t for t in state.protection_gap_timestamps if now - t <= 600.0]
                    recent_gaps = len(state.protection_gap_timestamps)
                    total_gaps = state.protection_gap_events

                order_type_str = "TP" if tp_is_current else ("SL" if sl_is_current else "algo order")
                log.warning(
                    f"[{symbol}] UNEXPECTED PROTECTION GAP DETECTED! {order_type_str} (algo_id={algo_id}) ended with status={status} "
                    f"while position is open ({pos_amt}). Total gap events: {total_gaps}, recent (10m): {recent_gaps}"
                )
                if recent_gaps >= 3:
                    log.warning(
                        f"[{symbol}] HIGH PROTECTION GAP FREQUENCY! {recent_gaps} protection gap events detected in last 10 minutes "
                        f"(total={total_gaps}). Check exchange algo order behavior or market volatility."
                    )

                # Debounce replacement requests to prevent tight infinite loop
                if time.monotonic() - last_placed < 2.0:
                    log.debug(f"[{symbol}] TP/SL replacement requested too quickly ({time.monotonic() - last_placed:.2f}s since last placement), debouncing")
                    return

                if not state.halted:
                    exit_side = SIDE_SELL if direction == Direction.LONG else SIDE_BUY
                    self._place_tp_and_sl(state, exit_side)
                    self._save_state()


    def _handle_external_close(self, symbol: str, reason: str, expected_round: Optional[int] = None):
        state = self.states[symbol]
        with state.lock:
            if expected_round is not None and state.round_number != expected_round:
                log.info(f"[{symbol}] ignoring external close for past round {expected_round} (current round is {state.round_number})")
                return
            log.warning(f"[{symbol}] Manual/External trade close detected ({reason}). Cleaning up open orders...")
            last_price = state.average_price
            closed_qty = state.position_qty
            closed_round = state.round_number
            closed_dir = state.direction.value
            r_start = state.round_start_time

            state.entry_orders = []
            state.position_qty = Decimal("0")
            state.round_number += 1

        # Cancel resting orders outside lock to prevent lock contention
        self._cancel_resting_orders(state)
        self._save_state()

        # Log to CSV
        try:
            live_m = self._mark_price(symbol)
            exit_p = live_m if live_m > 0 else last_price
            if closed_qty > 0 and last_price > 0:
                self._log_trade_history(
                    symbol=symbol,
                    round_number=closed_round,
                    direction=closed_dir,
                    entry_price=last_price,
                    exit_price=exit_p,
                    quantity=closed_qty,
                    exit_reason=f"EXTERNAL ({reason})",
                    start_time=r_start,
                )
        except Exception as e:
            log.debug(f"[{symbol}] Error dispatching external trade history: {e}")

        if TREND_PAUSE_SECONDS > 0:
            log.info(f"[{symbol}] Scheduling non-blocking {TREND_PAUSE_SECONDS}s pause before automatically resuming fresh trading round...")
            with state.lock:
                state.cooldown_until = time.monotonic() + TREND_PAUSE_SECONDS

        if not state.halted and not self.kill_switch_tripped:
            log.info(f"[{symbol}] Resuming fresh trading round now...")
            self.open_round(symbol)

    # =======================================================================
    # SECTION 12: ALGO ORDER RECONCILIATION & REST POLLING SAFETY NET
    # =======================================================================

    @retry()
    def _determine_position_close_reason(self, symbol: str, algo_type_hint: Optional[str] = None) -> str:
        """Determines whether a position closed due to Take-Profit, Stop-Loss, or External manual intervention."""
        state = self.states.get(symbol)
        if not state:
            return "EXTERNAL"

        with state.lock:
            start_ms = int(state.round_start_time * 1000) - 5000 if state.round_start_time > 0 else 0

        try:
            trades = self._get_account_trades(symbol=symbol, limit=15)
            if trades and isinstance(trades, list):
                # Filter exit trades that occurred during or after the current round started
                exit_trades = [
                    t for t in trades
                    if Decimal(str(t.get("realizedPnl", "0"))) != Decimal("0") and int(t.get("time", 0)) >= start_ms
                ]
                if exit_trades:
                    latest = max(exit_trades, key=lambda t: int(t.get("time", 0)))
                    pnl = Decimal(str(latest.get("realizedPnl", "0")))
                    trade_price = Decimal(str(latest.get("price", "0")))

                    with state.lock:
                        avg_price = state.average_price
                        direction = state.direction
                        pos_qty = state.position_qty

                    # If authoritative exchange algo_type_hint is present, prioritize it
                    if algo_type_hint in ("TP", "SL"):
                        return algo_type_hint

                    position_notional = pos_qty * avg_price
                    dead_zone = max(Decimal("0.10"), position_notional * Decimal("0.0005"))

                    # Cross-check price direction relative to entry price if available
                    price_diff = None
                    if avg_price > 0 and trade_price > 0:
                        if direction == Direction.LONG:
                            price_diff = trade_price - avg_price
                        elif direction == Direction.SHORT:
                            price_diff = avg_price - trade_price

                    if price_diff is not None and abs(price_diff) > Decimal("0.0001"):
                        if price_diff > 0:
                            return "TP"
                        elif price_diff < 0:
                            return "SL"

                    if pnl > dead_zone:
                        return "TP"
                    elif pnl < -dead_zone:
                        return "SL"
        except Exception as e:
            log.warning(f"[{symbol}] failed to fetch account trades for close classification: {e}")

        if algo_type_hint in ("TP", "SL"):
            return algo_type_hint

        return "EXTERNAL"

    # -- Protective Algo Order Reconciliation (TP / SL Synchronization) ---------

    def _reconcile_algo_orders(self, symbol: str, pre_fetched_pos_amt: Optional[Decimal] = None):
        """Check tracked TP and SL algo orders against REST API and react if triggered."""
        state = self.states.get(symbol)
        if not state or state.halted:
            return

        with state.lock:
            if state.refreshing_tp_sl or state.opening_round or state.closing_round:
                return
            tp_algo_id = state.take_profit_algo_id
            sl_algo_id = state.stop_loss_algo_id
            pos_qty = state.position_qty

        # Check live position on exchange (use pre-fetched value if available to save API rate limits)
        if pre_fetched_pos_amt is not None:
            pos_amt = pre_fetched_pos_amt
        else:
            try:
                positions = self._get_position_info(symbol)
                pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
            except Exception:
                pos_amt = pos_qty


        # If position is flat on exchange, classify and handle the position close immediately
        if pos_amt == 0 and pos_qty > 0:
            reason = self._determine_position_close_reason(symbol)
            if reason == "TP":
                self.on_take_profit_filled(symbol, event_round=None, expected_tp_algo_id=tp_algo_id)
            elif reason == "SL":
                self.on_stop_loss_filled(symbol, event_round=None, expected_sl_algo_id=sl_algo_id)
            else:
                self._handle_external_close(symbol, "position flat on exchange during algo reconciliation")
            return


        # Automatic recovery path: if position is open but TP or SL algo order is missing, re-place protection immediately
        if pos_amt > 0 and (tp_algo_id is None or sl_algo_id is None):
            log.warning(
                f"[{symbol}] Missing protective algo order detected (TP={tp_algo_id}, SL={sl_algo_id}) "
                f"while position open (pos_amt={pos_amt})! Re-placing protection now..."
            )
            with state.lock:
                direction = state.direction
            exit_side = SIDE_SELL if direction == Direction.LONG else SIDE_BUY
            self._schedule_tp_sl_refresh(state, exit_side)
            self._save_state()
            return

        if tp_algo_id and pos_amt > 0:
            try:
                order = self._get_algo_order(symbol, tp_algo_id)
                status = order.get("algoStatus") or order.get("status")
                with state.lock:
                    still_tracked = state.take_profit_algo_id == tp_algo_id
                if still_tracked and status in ("CANCELED", "EXPIRED", "REJECTED"):
                    log.warning(f"[{symbol}] take-profit algo order {tp_algo_id} ended (status={status}) while position open")
                    with state.lock:
                        if state.take_profit_algo_id == tp_algo_id:
                            state.take_profit_algo_id = None
                        still_has_position = state.position_qty > 0
                        direction = state.direction
                        last_placed = state.tp_sl_placed_time

                    if still_has_position and not state.halted and (time.monotonic() - last_placed >= 2.0):
                        log.info(f"[{symbol}] position open on exchange; replacing TP/SL algo orders now")
                        exit_side = SIDE_SELL if direction == Direction.LONG else SIDE_BUY
                        self._schedule_tp_sl_refresh(state, exit_side)
                        self._save_state()
            except Exception as e:
                log.debug(f"[{symbol}] TP algo reconcile notice: {e}")

        if sl_algo_id and pos_amt > 0:
            try:
                order = self._get_algo_order(symbol, sl_algo_id)
                status = order.get("algoStatus") or order.get("status")
                with state.lock:
                    still_tracked = state.stop_loss_algo_id == sl_algo_id
                if still_tracked and status in ("CANCELED", "EXPIRED", "REJECTED"):
                    log.warning(f"[{symbol}] stop-loss algo order {sl_algo_id} ended (status={status}) while position open")
                    with state.lock:
                        if state.stop_loss_algo_id == sl_algo_id:
                            state.stop_loss_algo_id = None
                        still_has_position = state.position_qty > 0
                        direction = state.direction
                        last_placed = state.tp_sl_placed_time

                    if still_has_position and not state.halted and (time.monotonic() - last_placed >= 2.0):
                        log.info(f"[{symbol}] position open on exchange; replacing TP/SL algo orders now")
                        exit_side = SIDE_SELL if direction == Direction.LONG else SIDE_BUY
                        self._schedule_tp_sl_refresh(state, exit_side)
                        self._save_state()

            except Exception as e:
                log.debug(f"[{symbol}] SL algo reconcile notice: {e}")

    # -- Background REST State Reconciliation (Partial-Fill & Fallback Detection) --

    def poll_symbol_orders(self, symbol: str):
        state = self.states[symbol]
        while not self._stop_event.is_set():
            try:
                if state.halted:
                    if self._stop_event.wait(RECONCILE_POLL_INTERVAL_SECONDS):
                        break
                    continue

                # Check live exchange position to detect manual close by user
                live_pos_amt = Decimal("0")
                try:
                    positions = self._get_position_info(symbol)
                    live_pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in positions if p["symbol"] == symbol)
                    if state.position_qty > 0 and live_pos_amt == 0:
                        self._reconcile_algo_orders(symbol, pre_fetched_pos_amt=live_pos_amt)
                        with state.lock:
                            still_open_locally = state.position_qty > 0
                            expected_tp = state.take_profit_algo_id
                            expected_sl = state.stop_loss_algo_id
                            current_round = state.round_number
                        if still_open_locally:
                            reason = self._determine_position_close_reason(symbol)
                            if reason == "TP":
                                self.on_take_profit_filled(symbol, event_round=current_round, expected_tp_algo_id=expected_tp)
                            elif reason == "SL":
                                self.on_stop_loss_filled(symbol, event_round=current_round, expected_sl_algo_id=expected_sl)
                            else:
                                self._handle_external_close(symbol, "manual position close detected via exchange polling", expected_round=current_round)
                        continue
                except Exception as e:
                    log.debug(f"[{symbol}] exchange position check error: {e}")


                open_orders = self._get_open_orders(symbol)
                open_map = {o["orderId"]: o for o in open_orders}

                with state.lock:
                    tracked_ids = list(state.resting_dca_orders)

                for oid in tracked_ids:
                    if oid in open_map:
                        o = open_map[oid]
                        executed_qty = Decimal(o["executedQty"])
                        avg_price = Decimal(o.get("avgPrice", "0"))
                        price = avg_price if avg_price > 0 else Decimal(o["price"])
                        self._handle_order_update(symbol, oid, o["status"], executed_qty, price, price_is_total_avg=True)
                    else:
                        try:
                            order = self._get_order_info(symbol, oid)
                        except BinanceAPIException as e:
                            log.warning(f"[{symbol}] could not fetch final status for order {oid}: {e}")
                            continue
                        executed_qty = Decimal(order["executedQty"])
                        avg_price = Decimal(order.get("avgPrice", "0"))
                        price = avg_price if avg_price > 0 else Decimal(order.get("price", "0"))
                        self._handle_order_update(symbol, oid, order["status"], executed_qty, price, price_is_total_avg=True)


                # Protective Order Backstop: reconcile TP & SL algo orders, reusing pre-fetched position info
                self._reconcile_algo_orders(symbol, pre_fetched_pos_amt=live_pos_amt)

            except BinanceAPIException as e:
                log.error(f"[{symbol}] polling error: {e}")
            except Exception as e:
                log.exception(f"[{symbol}] unexpected polling error: {e}")

            poll_interval = RECONCILE_POLL_INTERVAL_SECONDS if self._ws_connected else POLL_INTERVAL_SECONDS
            if self._stop_event.wait(poll_interval):
                break




    # =======================================================================
    # SECTION 13: RISK MANAGEMENT, DRAWDOWN RESET & EMERGENCY KILL SWITCH
    # =======================================================================

    @retry()
    def _get_total_equity(self) -> Decimal:
        """Queries account equity (totalMarginBalance or totalWalletBalance) for drawdown tracking."""
        account = self.client.futures_account()
        field_name = "totalWalletBalance" if DRAWDOWN_BASIS == "WALLET" else "totalMarginBalance"
        val = account.get(field_name, "0") if isinstance(account, dict) else "0"
        return Decimal(str(val))

    def kill_switch_loop(self):
        """Continuous background thread evaluating peak floating equity drawdowns (40% reset)
        and cumulative all-time drawdowns (50% permanent kill switch)."""
        while not self._stop_event.is_set():
            try:
                equity = self._get_total_equity()
                if equity <= Decimal("0"):
                    log.warning(f"Received invalid/zero equity ({equity}) from exchange API; skipping drawdown check.")
                    if self._stop_event.wait(POLL_INTERVAL_SECONDS):
                        break
                    continue
                with self._equity_lock:
                    if self.peak_equity == Decimal("0"):
                        self.peak_equity = equity
                    else:
                        self.peak_equity = max(self.peak_equity, equity)

                    if self.all_time_peak_equity == Decimal("0"):
                        self.all_time_peak_equity = equity
                    else:
                        self.all_time_peak_equity = max(self.all_time_peak_equity, equity)

                    pk_eq = self.peak_equity
                    at_pk_eq = self.all_time_peak_equity

                # 1. PRIORITY PEAK EQUITY DRAWDOWN GUARD (40%): Anti-whale market exit & SAME-DIRECTION re-entry
                drawdown_floor = pk_eq * (Decimal("1") - Decimal(str(MAX_DRAWDOWN_FROM_PEAK_PCT)) / 100)
                if equity <= drawdown_floor and not self.kill_switch_tripped:
                    has_open_position = any(state.position_qty > 0 for state in self.states.values())
                    if has_open_position:
                        log.warning(
                            f"PEAK DRAWDOWN GUARD TRIGGERED ({MAX_DRAWDOWN_FROM_PEAK_PCT}%) | equity={equity:.2f} "
                            f"peak={pk_eq:.2f} floor={drawdown_floor:.2f} | Executing priority market exit & same-direction re-entry..."
                        )
                        self._handle_drawdown_market_exit_and_reentry()
                    else:
                        with self._equity_lock:
                            self.peak_equity = equity

                # 2. HARD EMERGENCY KILL SWITCH (50% cumulative loss from ALL-TIME PEAK): Permanent shutdown
                hard_floor = at_pk_eq * (Decimal("1") - Decimal(str(HARD_KILL_SWITCH_DRAWDOWN_PCT)) / 100)
                if equity <= hard_floor and not self.kill_switch_tripped:
                    log.error(
                        f"HARD KILL SWITCH TRIGGERED ({HARD_KILL_SWITCH_DRAWDOWN_PCT}%) | equity={equity:.2f} "
                        f"all_time_peak={at_pk_eq:.2f} floor={hard_floor:.2f} | Permanent emergency halt!"
                    )
                    self._trip_kill_switch()


            except BinanceAPIException as e:
                log.error(f"equity check failed: {e}")
            except Exception as e:
                log.exception(f"unexpected error in equity loop: {e}")

            if self._stop_event.wait(POLL_INTERVAL_SECONDS):
                break


    def _handle_drawdown_market_exit_and_reentry(self):
        """When peak equity drops by MAX_DRAWDOWN_FROM_PEAK_PCT (40.0%) during an open trade:
        1. Immediately close position at MARKET price (prioritized over SL to avoid whale sweeps).
        2. Clean up resting DCA & TP/SL orders.
        3. Reset peak equity to current balance.
        4. Re-open a fresh round in the SAME direction.
        """
        for symbol, state in self.states.items():
            with state.lock:
                if state.position_qty == 0 or state.closing_round:
                    continue
                state.closing_round = True
                pos_qty = state.position_qty
                direction = state.direction
                log.warning(f"[{symbol}] Closing position at MARKET price due to {MAX_DRAWDOWN_FROM_PEAK_PCT}% peak drawdown (dir={direction.value})...")
                exit_side = SIDE_SELL if direction == Direction.LONG else SIDE_BUY

            try:
                # Cancel resting DCA & TP/SL orders outside lock
                self._cancel_resting_orders(state)

                if pos_qty > 0:
                    is_flat = self._emergency_market_exit_until_flat(symbol, exit_side)
                else:
                    is_flat = True

                if not is_flat:
                    msg = f"[{symbol}] Drawdown market exit failed to flatten position; halting symbol for manual review."
                    log.critical(msg)
                    send_alert(msg)
                    with state.lock:
                        state.halted = True
                    self._save_state()
                    continue

                with state.lock:
                    closed_round = state.round_number
                    closed_dir = state.direction.value
                    avg_p = state.average_price
                    r_start = state.round_start_time

                    state.entry_orders = []
                    state.position_qty = Decimal("0")
                    state.round_number += 1
                self._save_state()

                # Log to CSV
                try:
                    live_m = self._mark_price(symbol)
                    exit_p = live_m if live_m > 0 else avg_p
                    if pos_qty > 0 and avg_p > 0:
                        self._log_trade_history(
                            symbol=symbol,
                            round_number=closed_round,
                            direction=closed_dir,
                            entry_price=avg_p,
                            exit_price=exit_p,
                            quantity=pos_qty,
                            exit_reason=f"DRAWDOWN_RESET ({MAX_DRAWDOWN_FROM_PEAK_PCT}%)",
                            start_time=r_start,
                        )
                except Exception as e:
                    log.debug(f"[{symbol}] Error dispatching drawdown trade history: {e}")

                # Update peak equity baseline to current total margin balance (EQUITY basis)
                try:
                    eq = self._get_total_equity()
                    with self._equity_lock:
                        self.peak_equity = eq
                except Exception:
                    pass

                if TREND_PAUSE_SECONDS > 0:
                    log.info(f"[{symbol}] Scheduling non-blocking {TREND_PAUSE_SECONDS}s pause after drawdown exit before opening next round...")
                    with state.lock:
                        state.cooldown_until = time.monotonic() + TREND_PAUSE_SECONDS

                if not state.halted and not self.kill_switch_tripped:
                    log.info(f"[{symbol}] Re-opening fresh round in direction ({state.direction.value}) after drawdown exit...")
                    self.open_round(symbol)
            finally:
                with state.lock:
                    state.closing_round = False

    def _trip_kill_switch(self):
        self.kill_switch_tripped = True
        msg = "HARD KILL SWITCH TRIGGERED: Executing emergency market exit & permanent halt for all symbols!"
        log.error(msg)
        send_alert(msg)
        for symbol, state in self.states.items():
            with state.lock:
                state.halted = True

            # Network REST calls execute OUTSIDE state.lock
            self._cancel_resting_orders(state)

            try:
                position_info = self._get_position_info(symbol)
                for pos in position_info:
                    amt = Decimal(pos["positionAmt"])
                    if amt == 0:
                        continue
                    close_side = SIDE_SELL if amt > 0 else SIDE_BUY
                    self._emergency_market_exit_until_flat(symbol, close_side)
            except Exception as e:
                log.error(f"[{symbol}] kill switch position query error: {e}")

            # Verify exchange truth before zeroing local state
            try:
                position_info = self._get_position_info(symbol)
                pos_amt = sum(abs(Decimal(p["positionAmt"])) for p in position_info if p["symbol"] == symbol)
            except Exception:
                pos_amt = state.position_qty

            with state.lock:
                if pos_amt == 0:
                    state.entry_orders = []
                    state.position_qty = Decimal("0")
                    state.rung_last_qty = {}
                    state.rung_last_cost = {}
                else:
                    log.critical(f"[{symbol}] kill switch warning: exchange still reports open position amt={pos_amt}!")


        self._save_state()
        log.critical("KILL SWITCH EXECUTED PERMANENTLY: Trading halted across all symbols. Manual intervention required to restart.")

    # =======================================================================
    # SECTION 14: BOT EXECUTION LOOP & THREAD ORCHESTRATION
    # =======================================================================

    def run(self):
        """Primary engine entry point: executes startup setup, connects WebSockets,
        spawns worker and monitoring threads, and runs the health watchdog loop."""
        # Startup Safety Guard: guard initial account setup so transient errors log cleanly
        try:
            self.setup_all()
        except Exception as e:
            log.error(f"fatal error during setup, aborting startup: {e}")
            return

        self.start_user_stream()

        threads = []
        kill_thread = threading.Thread(target=self.kill_switch_loop, daemon=True, name="kill-switch")
        kill_thread.start()
        threads.append(kill_thread)

        ws_watchdog_thread = threading.Thread(target=self.ws_heartbeat_watchdog, daemon=True, name="ws-heartbeat")
        ws_watchdog_thread.start()
        threads.append(ws_watchdog_thread)


        for symbol in self.symbols:
            # Resync against real exchange state on startup before opening a fresh round
            # to prevent duplicate position entries across bot restarts.
            try:
                resumed = self.resync_symbol(symbol)
                if not resumed:
                    self.open_round(symbol)
            except Exception as e:
                log.error(f"[{symbol}] error during resync/startup ({e}); symbol halted for safety.")
                if symbol in self.states:
                    with self.states[symbol].lock:
                        self.states[symbol].halted = True
                send_alert(f"[{symbol}] Startup / resync error ({e}). Symbol HALTED for safety.")

            t = threading.Thread(target=self.poll_symbol_orders, args=(symbol,), daemon=True, name=f"poll-{symbol}")
            t.start()
            threads.append(t)

        try:
            while not self._stop_event.is_set():
                if self._stop_event.wait(WATCHDOG_INTERVAL_SECONDS):
                    break
                self._save_state()

                # Watchdog Safety Net: if a symbol is completely flat with no resting orders
                # and cooldown has elapsed, automatically initiate a fresh round.
                for symbol, state in self.states.items():
                    if state.halted or self.kill_switch_tripped:
                        continue
                    with state.lock:
                        flat = (
                            state.position_qty == 0
                            and not state.resting_dca_orders
                            and state.take_profit_algo_id is None
                            and state.stop_loss_algo_id is None
                            and time.monotonic() >= state.cooldown_until
                        )
                    if flat:
                        log.warning(f"[{symbol}] watchdog: flat with no open orders, reopening round")
                        self.open_round(symbol)

        except KeyboardInterrupt:
            log.info("Shutdown requested, stopping threads...")
            self._stop_event.set()
            if self.twm:
                try:
                    self.twm.stop()
                except Exception:
                    pass
            for t in threads + getattr(self, "symbol_ws_threads", []):
                try:
                    t.join(timeout=5)
                except Exception:
                    pass
            self._close_all_clients()
            log.info("Clean shutdown completed successfully.")




# ===========================================================================
if __name__ == "__main__":
    if not API_KEY or not API_SECRET:
        log.critical("Missing Binance API credentials! Please set BINANCE_API_KEY and BINANCE_API_SECRET in your .env file or environment variables.")
        print("\n[ERROR] Missing Binance API Credentials!")
        print("Please copy .env.example to .env and configure your BINANCE_API_KEY and BINANCE_API_SECRET.\n")
        exit(1)
    bot = DCABot(API_KEY, API_SECRET, SYMBOLS)
    bot.run()