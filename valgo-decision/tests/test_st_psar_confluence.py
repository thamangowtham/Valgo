"""Unit + brute-force tests for st_psar_confluence strategy.

Coverage:
    - check_buy_entry  : each of the 4 conditions independently + all-pass
    - check_sell_entry : each of the 4 conditions independently + all-pass
    - check_buy_exit   : each of the 4 exit conditions independently
    - check_sell_exit  : each of the 4 exit conditions independently
    - Bar aggregation  : same-bar update, bar-close detection, deque overflow
    - _decide          : NaN guard, correct action per side, no re-entry
    - State machine    : side transitions None->B->None, None->S->None
    - emit_order call  : correct OrderSide for each action
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decision.strategies.st_psar_confluence import (
    BARS,
    MIN_BARS,
    _SymbolState,
    _bar_start,
    check_buy_entry,
    check_buy_exit,
    check_sell_entry,
    check_sell_exit,
    STPSARConfluenceStrategy,
)
from valgo_common.models import OrderSide, Tick, TickMode


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _state(
    st=100.0, sgl=95.0, psar=98.0, atr=2.0,
    prev_mid=99.0, side=None,
) -> _SymbolState:
    s = _SymbolState("TEST")
    s.st, s.sgl, s.psar, s.atr, s.prev_mid, s.side = st, sgl, psar, atr, prev_mid, side
    return s


def _tick(symbol: str, price: float, ts: datetime | None = None) -> Tick:
    return Tick(
        instrument_token=1,
        tradingsymbol=symbol,
        last_price=Decimal(str(price)),
        timestamp=ts or datetime.now(timezone.utc),
        mode=TickMode.LTP,
        source="test",
    )


def _make_strategy(instruments: list[str] | None = None) -> STPSARConfluenceStrategy:
    cfg = MagicMock()
    cfg.id = "test-strat"
    cfg.account_id = "ACC1"
    cfg.quantity = 1
    cfg.instruments = instruments or ["TEST"]
    return STPSARConfluenceStrategy(cfg)


def _ts(minute: int, second: int = 0) -> datetime:
    """Fixed date, varying minute — all IST-naive for bar bucketing."""
    return datetime(2024, 1, 15, 9, minute, second)


# ─────────────────────────────────────────────────────────────────────────────
# check_buy_entry
# ─────────────────────────────────────────────────────────────────────────────

class TestBuyEntry:
    # baseline: all four conditions pass
    # st=100, atr=2 → st + atr*0.2 = 100.4; ltp=101 satisfies c1
    # ltp - st = 1 < atr=2 satisfies c2
    # ltp=101 > psar=98 satisfies c3
    # ltp=101 > sgl=95 satisfies c4

    def test_all_pass(self):
        assert check_buy_entry(ltp=101.0, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    # ── c1: ltp >= st + atr * 0.2 ────────────────────────────────────────────
    def test_c1_exact_boundary_passes(self):
        assert check_buy_entry(ltp=100.4, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    def test_c1_just_below_boundary_fails(self):
        assert not check_buy_entry(ltp=100.39, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    def test_c1_fails_below_supertrend(self):
        assert not check_buy_entry(ltp=99.0, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    @pytest.mark.parametrize("ltp", [100.4, 100.5, 101.0, 101.5, 101.99])
    def test_c1_passes_above_threshold(self, ltp):
        # 102.0 would fail c2 (ltp-st=2 = atr → not <), so cap at 101.99
        assert check_buy_entry(ltp=ltp, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    # ── c2: ltp - st < atr ───────────────────────────────────────────────────
    def test_c2_exact_boundary_fails(self):
        # ltp - st == atr is NOT < atr
        assert not check_buy_entry(ltp=102.0, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    def test_c2_just_below_boundary_passes(self):
        assert check_buy_entry(ltp=101.99, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    def test_c2_overextended_fails(self):
        assert not check_buy_entry(ltp=103.0, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    # ── c3: ltp > psar ───────────────────────────────────────────────────────
    def test_c3_equal_to_psar_fails(self):
        assert not check_buy_entry(ltp=101.0, st=100.0, sgl=95.0, psar=101.0, atr=2.0)

    def test_c3_below_psar_fails(self):
        assert not check_buy_entry(ltp=101.0, st=100.0, sgl=95.0, psar=105.0, atr=2.0)

    # ── c4: ltp > sgl OR (sgl - ltp) > 5*atr ────────────────────────────────
    def test_c4_above_sgl_passes(self):
        assert check_buy_entry(ltp=101.0, st=100.0, sgl=95.0, psar=98.0, atr=2.0)

    def test_c4_below_sgl_but_far_enough_passes(self):
        # sgl - ltp = 120 - 101 = 19 > 5*2 = 10 → c4 True via OR
        assert check_buy_entry(ltp=101.0, st=100.0, sgl=120.0, psar=98.0, atr=2.0)

    def test_c4_below_sgl_not_far_enough_fails(self):
        # sgl - ltp = 108 - 101 = 7, 5*atr = 10 → c4 False
        assert not check_buy_entry(ltp=101.0, st=100.0, sgl=108.0, psar=98.0, atr=2.0)

    def test_c4_exact_five_atr_distance_fails(self):
        # sgl - ltp = 10 = 5*atr=10  → NOT > 10
        assert not check_buy_entry(ltp=101.0, st=100.0, sgl=111.0, psar=98.0, atr=2.0)

    def test_c4_one_above_five_atr_passes(self):
        # sgl - ltp = 11 > 10
        assert check_buy_entry(ltp=101.0, st=100.0, sgl=112.0, psar=98.0, atr=2.0)


# ─────────────────────────────────────────────────────────────────────────────
# check_sell_entry
# ─────────────────────────────────────────────────────────────────────────────

class TestSellEntry:
    # baseline: st=100, atr=2, ltp=98.5 (below st-atr*0.2=99.6)
    # c1: 98.5 <= 99.6 ✓  c2: 100-98.5=1.5 < 2 ✓  c3: 98.5 < 105 ✓  c4: 98.5 < 102 ✓

    def test_all_pass(self):
        assert check_sell_entry(ltp=98.5, st=100.0, sgl=102.0, psar=105.0, atr=2.0)

    def test_c1_exact_boundary_passes(self):
        # st - atr*0.2 = 99.6; ltp=99.6 satisfies <=
        assert check_sell_entry(ltp=99.6, st=100.0, sgl=102.0, psar=105.0, atr=2.0)

    def test_c1_just_above_boundary_fails(self):
        assert not check_sell_entry(ltp=99.61, st=100.0, sgl=102.0, psar=105.0, atr=2.0)

    def test_c1_above_supertrend_fails(self):
        assert not check_sell_entry(ltp=101.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0)

    def test_c2_exact_boundary_fails(self):
        # st - ltp = 2 = atr; NOT < atr
        assert not check_sell_entry(ltp=98.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0)

    def test_c2_just_above_boundary_passes(self):
        assert check_sell_entry(ltp=98.01, st=100.0, sgl=102.0, psar=105.0, atr=2.0)

    def test_c3_equal_to_psar_fails(self):
        assert not check_sell_entry(ltp=98.5, st=100.0, sgl=102.0, psar=98.5, atr=2.0)

    def test_c3_above_psar_fails(self):
        assert not check_sell_entry(ltp=98.5, st=100.0, sgl=102.0, psar=90.0, atr=2.0)

    def test_c4_below_sgl_passes(self):
        assert check_sell_entry(ltp=98.5, st=100.0, sgl=102.0, psar=105.0, atr=2.0)

    def test_c4_above_sgl_but_far_enough_passes(self):
        # ltp - sgl = 98.5 - 70 = 28.5 > 5*2=10 → c4 True
        assert check_sell_entry(ltp=98.5, st=100.0, sgl=70.0, psar=105.0, atr=2.0)

    def test_c4_above_sgl_not_far_enough_fails(self):
        # ltp - sgl = 98.5 - 95 = 3.5 < 10
        assert not check_sell_entry(ltp=98.5, st=100.0, sgl=95.0, psar=105.0, atr=2.0)

    @pytest.mark.parametrize("ltp", [99.6, 99.0, 98.5, 98.01])
    def test_c1_parametric_passes(self, ltp):
        assert check_sell_entry(ltp=ltp, st=100.0, sgl=102.0, psar=105.0, atr=2.0)


# ─────────────────────────────────────────────────────────────────────────────
# check_buy_exit
# ─────────────────────────────────────────────────────────────────────────────

class TestBuyExit:
    # baseline safe (no exit): ltp=101, st=100, atr=2, psar=98, sgl=95, prev_mid=99

    def _no_exit(self, **kw):
        defaults = dict(ltp=101.0, st=100.0, sgl=95.0, psar=98.0, atr=2.0, prev_mid=99.0)
        defaults.update(kw)
        return not check_buy_exit(**defaults)

    def test_no_exit_when_all_safe(self):
        assert self._no_exit()

    # ── e1: ltp < st - atr ───────────────────────────────────────────────────
    def test_e1_triggers(self):
        # st - atr = 98; ltp=97 < 98
        assert check_buy_exit(ltp=97.0, st=100.0, sgl=95.0, psar=98.0, atr=2.0, prev_mid=99.0)

    def test_e1_exact_boundary_does_not_trigger(self):
        # ltp = st - atr = 98; e1: NOT < 98
        # Disable e2 by setting prev_mid >= st; disable e3 by psar < ltp; disable e4 by sgl < ltp
        assert not check_buy_exit(ltp=98.0, st=100.0, sgl=95.0, psar=97.0, atr=2.0, prev_mid=101.0)

    @pytest.mark.parametrize("ltp", [97.9, 97.0, 95.0, 80.0])
    def test_e1_parametric(self, ltp):
        assert check_buy_exit(ltp=ltp, st=100.0, sgl=95.0, psar=96.0, atr=2.0, prev_mid=99.0)

    # ── e2: ltp < st AND prev_mid < st ───────────────────────────────────────
    def test_e2_both_conditions_trigger(self):
        # ltp=99 < st=100, prev_mid=98 < st=100
        assert check_buy_exit(ltp=99.0, st=100.0, sgl=95.0, psar=96.0, atr=2.0, prev_mid=98.0)

    def test_e2_only_ltp_below_st_no_trigger(self):
        # ltp < st but prev_mid >= st → e2 False, check others
        # With safe psar/sgl, only e2 could trigger
        assert not check_buy_exit(ltp=99.0, st=100.0, sgl=95.0, psar=96.0, atr=2.0, prev_mid=101.0)

    def test_e2_only_prev_mid_below_st_no_trigger(self):
        # prev_mid < st but ltp >= st → e2 False
        assert not check_buy_exit(ltp=101.0, st=100.0, sgl=95.0, psar=96.0, atr=2.0, prev_mid=98.0)

    # ── e3: ltp < psar ───────────────────────────────────────────────────────
    def test_e3_triggers(self):
        assert check_buy_exit(ltp=101.0, st=100.0, sgl=95.0, psar=102.0, atr=2.0, prev_mid=101.0)

    def test_e3_equal_no_trigger(self):
        assert not check_buy_exit(ltp=101.0, st=100.0, sgl=95.0, psar=101.0, atr=2.0, prev_mid=101.0)

    # ── e4: ltp < sgl OR (sgl - ltp) > 5*atr ────────────────────────────────
    def test_e4_ltp_below_sgl_triggers(self):
        # ltp=94 < sgl=95
        assert check_buy_exit(ltp=94.0, st=100.0, sgl=95.0, psar=92.0, atr=2.0, prev_mid=101.0)

    def test_e4_sgl_far_above_triggers(self):
        # sgl - ltp = 115 - 101 = 14 > 10
        assert check_buy_exit(ltp=101.0, st=100.0, sgl=115.0, psar=96.0, atr=2.0, prev_mid=101.0)

    def test_e4_does_not_trigger_when_ltp_above_sgl(self):
        # e4: ltp < sgl → False when ltp=101 > sgl=100; arm2: sgl-ltp negative → False
        # disable e1/e2/e3 so only e4 is tested
        assert not check_buy_exit(ltp=101.0, st=100.0, sgl=100.0, psar=96.0, atr=2.0, prev_mid=101.0)


# ─────────────────────────────────────────────────────────────────────────────
# check_sell_exit
# ─────────────────────────────────────────────────────────────────────────────

class TestSellExit:
    # baseline safe: ltp=99, st=100, atr=2, psar=105, sgl=102, prev_mid=101

    def _no_exit(self, **kw):
        defaults = dict(ltp=99.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0, prev_mid=101.0)
        defaults.update(kw)
        return not check_sell_exit(**defaults)

    def test_no_exit_when_all_safe(self):
        assert self._no_exit()

    # ── e1: ltp > st + atr ───────────────────────────────────────────────────
    def test_e1_triggers(self):
        assert check_sell_exit(ltp=103.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0, prev_mid=101.0)

    def test_e1_exact_boundary_does_not_trigger(self):
        # e1: ltp=102 > st+atr=102 → NOT > (strict); disable e2 by prev_mid<=st; e3/e4 safe
        assert not check_sell_exit(ltp=102.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0, prev_mid=99.0)

    @pytest.mark.parametrize("ltp", [102.1, 103.0, 110.0])
    def test_e1_parametric(self, ltp):
        assert check_sell_exit(ltp=ltp, st=100.0, sgl=102.0, psar=105.0, atr=2.0, prev_mid=101.0)

    # ── e2: ltp > st AND prev_mid > st ───────────────────────────────────────
    def test_e2_both_trigger(self):
        assert check_sell_exit(ltp=101.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0, prev_mid=101.5)

    def test_e2_only_ltp_above_st_no_trigger(self):
        assert not check_sell_exit(ltp=101.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0, prev_mid=99.0)

    def test_e2_only_prev_mid_above_no_trigger(self):
        assert not check_sell_exit(ltp=99.0, st=100.0, sgl=102.0, psar=105.0, atr=2.0, prev_mid=101.5)

    # ── e3: ltp > psar ───────────────────────────────────────────────────────
    def test_e3_triggers(self):
        assert check_sell_exit(ltp=99.0, st=100.0, sgl=102.0, psar=98.0, atr=2.0, prev_mid=99.0)

    def test_e3_equal_no_trigger(self):
        assert not check_sell_exit(ltp=99.0, st=100.0, sgl=102.0, psar=99.0, atr=2.0, prev_mid=99.0)

    # ── e4: ltp > sgl OR (ltp - sgl) > 5*atr ────────────────────────────────
    def test_e4_ltp_above_sgl_triggers(self):
        assert check_sell_exit(ltp=99.0, st=100.0, sgl=98.0, psar=105.0, atr=2.0, prev_mid=99.0)

    def test_e4_ltp_far_below_sgl_triggers(self):
        # ltp - sgl = 99 - 85 = 14 > 10
        assert check_sell_exit(ltp=99.0, st=100.0, sgl=85.0, psar=105.0, atr=2.0, prev_mid=99.0)

    def test_e4_does_not_trigger_when_ltp_below_sgl(self):
        # e4: ltp > sgl → False when ltp=99 < sgl=100; arm2: ltp-sgl negative → False
        # disable e1/e2/e3 so only e4 is tested
        assert not check_sell_exit(ltp=99.0, st=100.0, sgl=100.0, psar=105.0, atr=2.0, prev_mid=99.0)


# ─────────────────────────────────────────────────────────────────────────────
# Bar aggregation
# ─────────────────────────────────────────────────────────────────────────────

class TestBarAggregation:
    def setup_method(self):
        self.strat = _make_strategy()
        self.state = self.strat._states["TEST"]

    def test_first_tick_initialises_live_bar(self):
        self.strat._update_bar(self.state, _ts(15, 30), 100.0)
        assert self.state.live is not None
        assert self.state.live["open"] == 100.0
        assert self.state.live["close"] == 100.0

    def test_same_bar_updates_high_low_close(self):
        self.strat._update_bar(self.state, _ts(15, 0),  100.0)
        self.strat._update_bar(self.state, _ts(15, 30), 102.0)
        self.strat._update_bar(self.state, _ts(15, 59), 98.0)
        bar = self.state.live
        assert bar["open"]  == 100.0
        assert bar["high"]  == 102.0
        assert bar["low"]   == 98.0
        assert bar["close"] == 98.0

    def test_new_5min_boundary_closes_bar(self):
        self.strat._update_bar(self.state, _ts(15, 0),  100.0)
        closed = self.strat._update_bar(self.state, _ts(20, 0), 105.0)
        assert closed is True
        assert len(self.state.bars) == 1
        assert self.state.bars[0]["close"] == 100.0

    def test_new_bar_open_equals_first_price(self):
        self.strat._update_bar(self.state, _ts(15, 0), 100.0)
        self.strat._update_bar(self.state, _ts(20, 0), 105.0)
        assert self.state.live["open"] == 105.0

    def test_no_bar_close_within_same_minute(self):
        self.strat._update_bar(self.state, _ts(15, 0), 100.0)
        closed = self.strat._update_bar(self.state, _ts(15, 45), 101.0)
        assert closed is False
        assert len(self.state.bars) == 0

    def test_multiple_bar_closes_accumulate(self):
        # Use 5-min aligned timestamps: 0, 5, 10, 15, 20, 25, 30, 35, 40
        # Each new bucket closes the previous → 8 closed bars when 9th arrives
        for i in range(9):
            self.strat._update_bar(self.state, _ts(i * 5, 0), float(i * 5))
        assert len(self.state.bars) == 8

    def test_deque_overflow_capped_at_bars_plus_50(self):
        for i in range(BARS + 100):
            self.strat._update_bar(self.state, _ts(0, 0) + timedelta(minutes=i * 5), float(i))
            self.strat._update_bar(self.state, _ts(0, 0) + timedelta(minutes=i * 5 + 1), float(i))
        assert len(self.state.bars) <= BARS + 50

    def test_first_tick_returns_false(self):
        closed = self.strat._update_bar(self.state, _ts(15, 0), 100.0)
        assert closed is False


# ─────────────────────────────────────────────────────────────────────────────
# _bar_start helper
# ─────────────────────────────────────────────────────────────────────────────

class TestBarStart:
    @pytest.mark.parametrize("minute,expected", [
        (0,  0),
        (4,  0),
        (5,  5),
        (9,  5),
        (10, 10),
        (14, 10),
        (15, 15),
        (59, 55),
    ])
    def test_buckets(self, minute, expected):
        ts = datetime(2024, 1, 15, 9, minute, 30)
        result = _bar_start(ts)
        assert result.minute == expected
        assert result.second == 0

    def test_strips_timezone(self):
        ts = datetime(2024, 1, 15, 9, 7, 0, tzinfo=timezone.utc)
        result = _bar_start(ts)
        assert result.tzinfo is None


# ─────────────────────────────────────────────────────────────────────────────
# _decide: NaN guard + side routing
# ─────────────────────────────────────────────────────────────────────────────

class TestDecide:
    def setup_method(self):
        self.strat = _make_strategy()

    def _decide(self, ltp, **kw):
        s = _state(**kw)
        self.strat._states["TEST"] = s
        return self.strat._decide(s, ltp)

    def test_nan_st_returns_none(self):
        s = _state(st=float("nan"))
        assert self.strat._decide(s, 101.0) is None

    def test_nan_sgl_returns_none(self):
        s = _state(sgl=float("nan"))
        assert self.strat._decide(s, 101.0) is None

    def test_nan_psar_returns_none(self):
        s = _state(psar=float("nan"))
        assert self.strat._decide(s, 101.0) is None

    def test_nan_atr_returns_none(self):
        s = _state(atr=float("nan"))
        assert self.strat._decide(s, 101.0) is None

    def test_buy_signal_when_side_none(self):
        # ltp=101 passes all buy conditions with st=100,atr=2,psar=98,sgl=95
        result = self._decide(101.0, st=100.0, atr=2.0, psar=98.0, sgl=95.0, side=None)
        assert result == "BUY"

    def test_sell_signal_when_side_none(self):
        # ltp=98.5 passes all sell conditions with st=100,atr=2,psar=105,sgl=102
        result = self._decide(98.5, st=100.0, atr=2.0, psar=105.0, sgl=102.0, side=None)
        assert result == "SELL"

    def test_no_signal_when_conditions_not_met(self):
        # ltp=100.0 — below st+atr*0.2=100.4 → no buy; above st-atr*0.2=99.6 → no sell
        result = self._decide(100.0, st=100.0, atr=2.0, psar=98.0, sgl=95.0, side=None)
        assert result is None

    def test_buy_exit_when_side_B(self):
        # ltp < psar triggers e3
        result = self._decide(97.0, st=100.0, atr=2.0, psar=99.0, sgl=95.0, side="B", prev_mid=99.0)
        assert result == "BUY_EXIT"

    def test_no_buy_entry_when_side_B(self):
        # side="B" — should not trigger new BUY even if conditions pass
        result = self._decide(101.0, st=100.0, atr=2.0, psar=98.0, sgl=95.0, side="B", prev_mid=99.0)
        # 101 is safe (not exiting), so should return None
        assert result is None

    def test_sell_exit_when_side_S(self):
        # ltp > psar triggers e3
        result = self._decide(106.0, st=100.0, atr=2.0, psar=105.0, sgl=102.0, side="S", prev_mid=101.0)
        assert result == "SEL_EXIT"

    def test_no_sell_entry_when_side_S(self):
        result = self._decide(98.5, st=100.0, atr=2.0, psar=105.0, sgl=102.0, side="S", prev_mid=101.0)
        # 98.5 would trigger SELL entry but side="S", so no new SELL
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# State machine: side transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestStateMachine:
    def setup_method(self):
        self.strat = _make_strategy()

    @pytest.mark.asyncio
    async def test_buy_sets_side_B(self):
        s = self.strat._states["TEST"]
        with patch.object(self.strat, "emit_order", new_callable=AsyncMock):
            await self.strat._emit(s, "BUY", 101.0)
        assert s.side == "B"

    @pytest.mark.asyncio
    async def test_sell_sets_side_S(self):
        s = self.strat._states["TEST"]
        with patch.object(self.strat, "emit_order", new_callable=AsyncMock):
            await self.strat._emit(s, "SELL", 98.0)
        assert s.side == "S"

    @pytest.mark.asyncio
    async def test_buy_exit_resets_side(self):
        s = self.strat._states["TEST"]
        s.side = "B"
        with patch.object(self.strat, "emit_order", new_callable=AsyncMock):
            await self.strat._emit(s, "BUY_EXIT", 97.0)
        assert s.side is None

    @pytest.mark.asyncio
    async def test_sel_exit_resets_side(self):
        s = self.strat._states["TEST"]
        s.side = "S"
        with patch.object(self.strat, "emit_order", new_callable=AsyncMock):
            await self.strat._emit(s, "SEL_EXIT", 103.0)
        assert s.side is None

    @pytest.mark.asyncio
    async def test_invalid_transition_ignored(self):
        s = self.strat._states["TEST"]
        s.side = "B"
        mock = AsyncMock()
        with patch.object(self.strat, "emit_order", mock):
            await self.strat._emit(s, "SEL_EXIT", 103.0)  # wrong action for side=B
        assert s.side == "B"
        mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_reentry_while_in_position(self):
        s = self.strat._states["TEST"]
        s.side = "B"
        mock = AsyncMock()
        with patch.object(self.strat, "emit_order", mock):
            await self.strat._emit(s, "BUY", 101.0)  # re-entry attempt
        assert s.side == "B"
        mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# emit_order side mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderSideMapping:
    def setup_method(self):
        self.strat = _make_strategy()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action,expected_side", [
        ("BUY",      OrderSide.BUY),
        ("SEL_EXIT", OrderSide.BUY),   # closing a short = buy
        ("SELL",     OrderSide.SELL),
        ("BUY_EXIT", OrderSide.SELL),  # closing a long = sell
    ])
    async def test_order_side(self, action, expected_side):
        s = self.strat._states["TEST"]
        if action == "BUY_EXIT":
            s.side = "B"
        elif action == "SEL_EXIT":
            s.side = "S"

        mock = AsyncMock(return_value="order-123")
        with patch.object(self.strat, "emit_order", mock):
            await self.strat._emit(s, action, 100.0)

        mock.assert_called_once()
        _, call_kwargs = mock.call_args
        # emit_order(tradingsymbol, side, quantity, price=...)
        call_args = mock.call_args[0]
        assert call_args[1] == expected_side


# ─────────────────────────────────────────────────────────────────────────────
# Brute-force parametric: boundary sweep on buy entry c1+c2 simultaneously
# ─────────────────────────────────────────────────────────────────────────────

class TestBruteForce:
    """Sweep ltp across the valid buy/sell window to verify boundary precision."""

    @pytest.mark.parametrize("ltp,expect_buy", [
        (100.39, False),  # below c1 threshold
        (100.40, True),   # exact c1 threshold, within c2 (ltp-st=0.4 < 2)
        (100.50, True),
        (101.00, True),
        (101.99, True),   # just below c2 boundary
        (102.00, False),  # ltp - st = 2 = atr, fails c2
        (102.50, False),  # overextended
    ])
    def test_buy_entry_ltp_sweep(self, ltp, expect_buy):
        result = check_buy_entry(ltp=ltp, st=100.0, sgl=95.0, psar=98.0, atr=2.0)
        assert result == expect_buy

    @pytest.mark.parametrize("ltp,expect_sell", [
        (99.61, False),   # above c1 boundary (99.6)
        (99.60, True),    # exact c1
        (99.00, True),
        (98.50, True),
        (98.01, True),    # just above c2 boundary (st-ltp=1.99 < 2)
        (98.00, False),   # st - ltp = 2 = atr, fails c2
        (97.50, False),   # too far below
    ])
    def test_sell_entry_ltp_sweep(self, ltp, expect_sell):
        result = check_sell_entry(ltp=ltp, st=100.0, sgl=102.0, psar=105.0, atr=2.0)
        assert result == expect_sell

    @pytest.mark.parametrize("sgl_offset,expect_c4_buy", [
        (-6.0, True),    # ltp > sgl (sgl=95, ltp=101)
        (-1.0, True),    # ltp > sgl
        (0.0,  False),   # ltp == sgl → not > sgl; sgl-ltp=0 not > 10
        (1.0,  False),   # ltp < sgl but gap=1 not > 10
        (9.0,  False),   # gap=9 not > 10
        (10.0, False),   # gap=10 NOT > 10 (strict)
        (11.0, True),    # gap=11 > 10
        (20.0, True),    # gap=20 > 10
    ])
    def test_buy_entry_c4_sgl_sweep(self, sgl_offset, expect_c4_buy):
        ltp = 101.0
        sgl = ltp + sgl_offset
        # Set other conditions to always pass
        result = check_buy_entry(ltp=ltp, st=100.0, sgl=sgl, psar=98.0, atr=2.0)
        assert result == expect_c4_buy

    @pytest.mark.parametrize("atr,expect_buy", [
        (0.5,  False),  # st+atr*0.2=100.1; ltp=101 > 100.1 but ltp-st=1 >= atr=0.5 → c2 fails
        (1.0,  False),  # ltp-st=1 = atr=1 → c2 fails (not <)
        (1.01, True),   # ltp-st=1 < 1.01 → c2 passes; st+0.2*1.01=100.202 < 101 → c1 passes
        (2.0,  True),
        (5.0,  True),   # ltp-st=1 < 5 → c2 passes; st+0.2*5=101 ≤ ltp=101 → c1 passes
        (10.0, False),  # st+atr*0.2=102 > ltp=101 → c1 fails
    ])
    def test_buy_entry_atr_sweep(self, atr, expect_buy):
        result = check_buy_entry(ltp=101.0, st=100.0, sgl=95.0, psar=98.0, atr=atr)
        assert result == expect_buy
