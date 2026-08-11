"""유동성 Δ × 모멘텀 2D 레짐 (CLAUDE.md §6 Phase 6).

두 축을 각각 +/- 로 잘라 4분면을 만든다.

              모멘텀 +          모멘텀 -
  유동성 Δ +   확장(Expansion)   회복 시도(Recovery)
  유동성 Δ -   후퇴(Fade)        수축(Contraction)

레벨이 아니라 **변화량**으로 자르는 이유: 유동성 레벨은 추세적으로 커지기만 해서
구간 비교가 안 된다. 임계값을 상수로 박지 않고 롤링 백분위로 잡는 것도 같은 이유다.
"""
from __future__ import annotations

import pandas as pd

from src.indicators.technical import pct_rank

LABELS = {
    (True, True): "확장",
    (True, False): "회복 시도",
    (False, True): "후퇴",
    (False, False): "수축",
}

ORDER = ["확장", "회복 시도", "후퇴", "수축"]


def classify(liquidity: pd.Series, price: pd.Series, *,
             liq_periods: int = 4, mom_periods: int = 60,
             window: int = 252, threshold: float = 0.5) -> pd.DataFrame:
    """레짐 분류 결과를 반환한다.

    liquidity : 유동성 레벨 (예: 미국 순유동성, 주간)
    price     : 지수 종가 (일간)
    liq_periods : 유동성 변화량을 잴 기간 (주간 계열이면 4 ≈ 한 달)
    mom_periods : 모멘텀 기간 (영업일)
    threshold : 롤링 백분위 기준선. 0.5 = 중앙값

    임계값을 상수(예: "유동성 +100B")로 박으면 레짐이 바뀔 때마다 무의미해진다.
    백분위로 자르면 '최근 1년 대비 지금이 어느 위치인가'로 해석이 고정된다.
    """
    liq = liquidity.dropna().sort_index()
    px = price.dropna().sort_index().astype("float64")

    liq_chg = liq.diff(liq_periods).rename("liq_chg")
    mom = (px / px.shift(mom_periods) - 1.0).rename("momentum")

    # 유동성(주간)을 가격(일간) index 로 옮긴다. 보간 금지 — ffill 만.
    liq_daily = liq_chg.reindex(px.index.union(liq_chg.index)).ffill().reindex(px.index)

    df = pd.concat({"liq_chg": liq_daily, "momentum": mom}, axis=1).dropna()
    if df.empty:
        return pd.DataFrame(columns=["liq_chg", "momentum", "liq_pr",
                                     "mom_pr", "regime"])

    df["liq_pr"] = pct_rank(df["liq_chg"].rename("liq"), window)
    df["mom_pr"] = pct_rank(df["momentum"].rename("mom"), window)
    df = df.dropna(subset=["liq_pr", "mom_pr"])

    df["regime"] = [
        LABELS[(lp > threshold, mp > threshold)]
        for lp, mp in zip(df["liq_pr"], df["mom_pr"])
    ]
    return df


def summarize(regimes: pd.DataFrame, price: pd.Series,
              horizon: int = 20) -> pd.DataFrame:
    """레짐별 향후 수익률 통계.

    ★ 이건 서술 통계지 전략 성과가 아니다. 표본이 겹치고(overlapping),
    레짐 판정에 이미 모멘텀이 들어가 있어 자기상관이 크다. 방향 참고용으로만 쓸 것.
    """
    from src.research.ic import forward_return

    fwd = forward_return(price, horizon)
    df = regimes.join(fwd.rename("fwd")).dropna(subset=["fwd"])
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("regime")["fwd"]
    out = pd.DataFrame({
        "일수": g.size(),
        "비중%": g.size() / len(df) * 100.0,
        "평균%": g.mean() * 100.0,
        "중앙값%": g.median() * 100.0,
        "상승확률%": g.apply(lambda s: (s > 0).mean() * 100.0),
        "표준편차%": g.std() * 100.0,
    })
    return out.reindex([r for r in ORDER if r in out.index]).round(2)


def current(regimes: pd.DataFrame) -> dict:
    """가장 최근 레짐과 지속 일수."""
    if regimes.empty:
        return {}
    last = regimes.iloc[-1]
    run = 1
    for i in range(len(regimes) - 2, -1, -1):
        if regimes["regime"].iloc[i] != last["regime"]:
            break
        run += 1
    return {
        "date": regimes.index[-1],
        "regime": last["regime"],
        "streak_days": run,
        "liq_pr": float(last["liq_pr"]),
        "mom_pr": float(last["mom_pr"]),
    }
