"""수급 누적 — 일간 순매수는 잡음이지만 누적하면 구조가 보인다.

외국인이 몇 년째 순매도인지, 개인이 어디서 받아냈는지는 누적선에서만
드러난다. 일간 막대로는 절대 안 보인다.
"""
from __future__ import annotations

import pandas as pd


def cumulative(flows: pd.DataFrame, market: str = "KOSPI", *,
               start: pd.Timestamp | None = None,
               unit: float = 1e12) -> pd.DataFrame:
    """투자자별 누적 순매수 (기본 조원).

    start 를 주면 그 시점부터 0 에서 다시 쌓는다 — 누적선은 시작점에 따라
    모양이 완전히 달라지므로, 어디서 시작했는지가 항상 명시돼야 한다.
    """
    sub = flows[flows["market"] == market]
    if sub.empty:
        return pd.DataFrame()
    wide = sub.pivot(index="date", columns="investor",
                     values="net_value").sort_index()
    if start is not None:
        wide = wide[wide.index >= start]
    return (wide.fillna(0.0).cumsum() / unit)


def deposit_vs_margin(deposit: pd.Series, margin: pd.Series) -> pd.DataFrame:
    """예탁금과 신용융자를 함께 본다.

    예탁금은 '대기 자금', 신용융자는 '빌려서 이미 산 돈'이다. 둘의 비율이
    올라가면 시장이 현금보다 빚으로 굴러간다는 뜻이고, 그만큼 조정 때
    반대매매로 증폭될 여지가 커진다.
    """
    d, m = deposit.dropna(), margin.dropna()
    df = pd.concat({"예탁금": d, "신용융자": m}, axis=1).dropna()
    if df.empty:
        return df
    df["신용/예탁금%"] = df["신용융자"] / df["예탁금"] * 100
    return df
