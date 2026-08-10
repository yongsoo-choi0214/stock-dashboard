"""유동성 지표. CLAUDE.md §5.7 의 검증 완료 코드. 리팩터링 금지."""
from __future__ import annotations
import pandas as pd


def us_net_liquidity(walcl: pd.Series, tga: pd.Series,
                     onrrp: pd.Series) -> pd.Series:
    """미국 순유동성 = 연준총자산 - TGA - ON RRP.

    입력 3개는 모두 **십억 USD로 사전 정규화**되어 있어야 한다.
    주기가 다르므로 수요일 기준 주간으로 정렬 후 계산한다.
    """
    df = pd.concat(
        {"walcl": walcl, "tga": tga, "onrrp": onrrp}, axis=1, sort=True
    ).sort_index()
    wk = df.resample("W-WED").last().ffill()
    out = (wk["walcl"] - wk["tga"] - wk["onrrp"]).rename("net_liquidity")
    # ★ 후행 stale 구간 절단 (§7.3)
    valid_until = min(walcl.index.max(), tga.index.max(), onrrp.index.max())
    return out[out.index <= valid_until]


def deposit_ratio(deposit: pd.Series, market_cap: pd.Series) -> pd.Series:
    """예탁금 / 시가총액 (%). 단위를 맞춘 뒤 호출할 것."""
    d, m = deposit.align(market_cap, join="inner")
    return (d / m * 100.0).rename("deposit_to_mcap")


def deposit_turnover(trading_value: pd.Series, deposit: pd.Series) -> pd.Series:
    """예탁금 회전율 = 일평균 거래대금 / 예탁금."""
    t, d = trading_value.align(deposit, join="inner")
    return (t / d).rename("deposit_turnover")


def to_change(s: pd.Series, periods: int = 1) -> pd.Series:
    """레벨보다 변화량이 신호력이 높은 계열용."""
    return s.diff(periods).rename(f"{s.name}_d{periods}")
