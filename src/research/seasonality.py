"""계절성과 변동성 레짐 — 조건부 통계.

두 화면 다 '지금이 어떤 상태인가'를 과거 분포와 대조하는 것이 목적이다.
예측이 아니라 **참고 분포**로 읽어야 한다 — 표본이 20년이면 각 월은 21개뿐이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월",
          "7월", "8월", "9월", "10월", "11월", "12월"]


def monthly_returns(close: pd.Series) -> pd.Series:
    """월별 수익률. 월말 종가 기준."""
    c = close.dropna().astype("float64").sort_index()
    return c.resample("ME").last().pct_change().dropna()


def monthly_stats(close: pd.Series) -> pd.DataFrame:
    """달력 월별 평균/중앙값/상승확률.

    '5월에 팔아라' 같은 통념이 데이터에 있는지 보는 화면이다.
    표본이 월당 20여 개뿐이라 **평균보다 상승확률과 표본 수를 함께** 봐야 한다.
    """
    r = monthly_returns(close)
    if r.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"ret": r, "month": r.index.month})
    g = df.groupby("month")["ret"]
    out = pd.DataFrame({
        "표본": g.size(),
        "평균%": g.mean() * 100,
        "중앙값%": g.median() * 100,
        "상승확률%": g.apply(lambda s: (s > 0).mean() * 100),
        "최악%": g.min() * 100,
        "최고%": g.max() * 100,
    })
    out.index = [MONTHS[i - 1] for i in out.index]
    return out.round(2)


def realized_vol(close: pd.Series, window: int = 20) -> pd.Series:
    """연율화 실현변동성."""
    c = close.dropna().astype("float64").sort_index()
    return (c.pct_change().rolling(window).std() * np.sqrt(252)).rename(
        f"rv_{window}")


def vol_regime_stats(close: pd.Series, *, window: int = 20,
                     horizon: int = 60, bins: int = 5) -> pd.DataFrame:
    """실현변동성 분위별 향후 수익률.

    '변동성이 높을 때 사야 하나 팔아야 하나'를 과거 분포로 답한다.
    표본이 겹치므로(overlapping) 유의성 검정이 아니라 서술 통계로만 쓸 것.
    """
    c = close.dropna().astype("float64").sort_index()
    rv = realized_vol(c, window)
    fwd = (c.shift(-horizon) / c - 1.0)
    df = pd.concat({"rv": rv, "fwd": fwd}, axis=1).dropna()
    if len(df) < bins * 20:
        return pd.DataFrame()

    df["q"] = pd.qcut(df["rv"], bins, labels=False, duplicates="drop") + 1
    g = df.groupby("q")
    out = pd.DataFrame({
        "일수": g.size(),
        "변동성 하한%": g["rv"].min() * 100,
        "변동성 상한%": g["rv"].max() * 100,
        f"향후{horizon}일 평균%": g["fwd"].mean() * 100,
        "상승확률%": g["fwd"].apply(lambda s: (s > 0).mean() * 100),
    })
    return out.round(2)


def current_vol_percentile(close: pd.Series, *, window: int = 20,
                           lookback: int = 252) -> tuple[float, float]:
    """(현재 실현변동성, 그 값의 롤링 백분위)."""
    rv = realized_vol(close, window).dropna()
    if rv.empty:
        return float("nan"), float("nan")
    pr = rv.rolling(lookback).rank(pct=True)
    return float(rv.iloc[-1]), float(pr.iloc[-1]) if not np.isnan(pr.iloc[-1]) else float("nan")


#: 한국장 마감(15:30 KST) **이후에** 거래되는 자산.
#: 이들의 '오늘' 종가는 한국의 '내일'에야 반영된다.
LATER_SESSION = {"YF.^GSPC", "YF.^IXIC", "YF.^SOX", "YF.^VIX",
                 "YF.DX-Y.NYB", "YF.EEM", "YF.HG=F", "YF.CL=F"}


def correlation(prices: pd.DataFrame, tickers: list[str], *,
                start: pd.Timestamp | None = None,
                min_obs: int = 60, align_sessions: bool = True) -> pd.DataFrame:
    """티커 간 일간 수익률 상관행렬.

    거래일이 다른 시장을 섞으므로 교집합 날짜만 쓴다. ffill 로 메우면
    휴장일의 '변화 없음'이 상관을 인위적으로 높인다.

    ★ align_sessions: 미국 자산을 하루 늦춰 정렬한다.
      한국장은 15:30 에 닫고 미국장은 그 뒤에 열린다. 같은 날짜끼리 비교하면
      한국이 아직 모르는 정보와 짝지어져 관계가 **과소평가**된다.
      실측: KOSPI↔SOX 같은 날 0.15 → 하루 늦추면 훨씬 높아진다.
      (닛케이는 같은 시간대라 정렬 없이도 0.64 로 높게 나온다)
    """
    wide = prices[prices["ticker"].isin(tickers)].pivot(
        index="date", columns="ticker", values="close")
    if start is not None:
        wide = wide[wide.index >= start]

    rets = wide.pct_change(fill_method=None)
    if align_sessions:
        for col in rets.columns:
            if col in LATER_SESSION:
                rets[col] = rets[col].shift(1)
    rets = rets.dropna(how="any")
    if len(rets) < min_obs:
        return pd.DataFrame()
    return rets.corr()
