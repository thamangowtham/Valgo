"""Canonical data models shared across all Valgo services.

Provider-agnostic: ticks from Kite, Fyers, etc. all normalize to `Tick`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


# ============================================================================
# Market data
# ============================================================================
class TickMode(str, Enum):
    LTP = "LTP"
    QUOTE = "QUOTE"
    FULL = "FULL"


class DepthLevel(BaseModel):
    price: Decimal
    quantity: int
    orders: int = 0


class Tick(BaseModel):
    """Provider-agnostic market tick. All sources normalize to this."""
    instrument_token: int
    tradingsymbol: str
    last_price: Decimal
    last_traded_quantity: int = 0
    timestamp: datetime
    mode: TickMode

    # QUOTE / FULL only
    ohlc_open: Decimal | None = None
    ohlc_high: Decimal | None = None
    ohlc_low: Decimal | None = None
    ohlc_close: Decimal | None = None
    volume: int | None = None
    oi: int | None = None
    average_price: Decimal | None = None

    # FULL only
    depth_buy: list[DepthLevel] = Field(default_factory=list)
    depth_sell: list[DepthLevel] = Field(default_factory=list)

    # Provenance — which source produced this tick
    source: str = "unknown"


# ============================================================================
# Orders
# ============================================================================
class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"          # converted to MPP under SEBI 2026 rules
    LIMIT = "LIMIT"
    SL = "SL"                  # stop-loss
    SL_M = "SL-M"              # stop-loss market


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OrderRequest(BaseModel):
    """Decision engine → execution router."""
    strategy_id: str
    account_id: str
    tradingsymbol: str
    exchange: str = "NFO"
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    price: Decimal | None = None
    trigger_price: Decimal | None = None
    product: str = "MIS"           # MIS=intraday, NRML=overnight
    tag: str | None = None
    idempotency_key: str           # required — protects against duplicates


class Order(BaseModel):
    """Persistent order record — what's stored in DynamoDB."""
    order_id: str                  # internal UUID
    broker_order_id: str | None = None
    strategy_id: str
    account_id: str
    tradingsymbol: str
    side: OrderSide
    quantity: int
    filled_quantity: int = 0
    order_type: OrderType
    price: Decimal | None = None
    average_price: Decimal | None = None
    status: OrderStatus
    rejection_reason: str | None = None
    placed_at: datetime
    updated_at: datetime
    idempotency_key: str


# ============================================================================
# Strategies
# ============================================================================
class StrategyType(str, Enum):
    CE = "CE"
    PE = "PE"
    CE_PE = "CE+PE"


class Strategy(BaseModel):
    id: str
    name: str
    instrument: str                # NIFTY, BANKNIFTY, FINNIFTY
    type: StrategyType
    strike_logic: str              # human-readable: ATM, OTM 200pts
    entry_condition: str
    target_pct: Decimal
    stop_loss_pct: Decimal
    quantity: int
    account_id: str
    instruments: list[str] = Field(default_factory=list)  # symbols this strategy needs ticks for
    active: bool = True
    last_fired: datetime | None = None


# ============================================================================
# Risk
# ============================================================================
class RiskLimits(BaseModel):
    max_orders_per_sec: int = 10           # SEBI cap unless exchange-approved
    max_daily_loss: Decimal = Decimal("50000")
    max_position_value: Decimal = Decimal("1000000")
    max_open_positions: int = 5
    kill_switch: bool = False


class RiskCheckResult(BaseModel):
    allowed: bool
    reason: str | None = None              # populated only when denied


# ============================================================================
# Data sources
# ============================================================================
class SourcePriority(str, Enum):
    PRIMARY = "primary"
    BACKUP_1 = "backup-1"
    BACKUP_2 = "backup-2"
    DISABLED = "disabled"


class DataSource(BaseModel):
    id: str
    name: str
    provider: str                  # "Zerodha Kite", "Fyers", etc
    connection_type: str = "WebSocket"
    endpoint: str
    api_key: str
    api_secret: str | None = None
    subscription_mode: TickMode = TickMode.FULL
    priority: SourcePriority = SourcePriority.BACKUP_1
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 5
    active: bool = True


# ============================================================================
# Audit
# ============================================================================
class AuditEvent(BaseModel):
    event_id: str
    timestamp: datetime
    event_type: str                # "order_placed", "order_filled", "kill_switch_engaged", etc
    actor: str                     # "system", strategy_id, "admin"
    payload: dict = Field(default_factory=dict)
