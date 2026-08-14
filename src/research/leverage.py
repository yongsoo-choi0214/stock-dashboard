"""신용(레버리지) 위험 — '언제 꺼야 하나'를 다루는 모듈.

★ 이건 **예측 문제가 아니라 파산 확률 문제**다. 그래서 취약성 지수와 접근이 다르다.

레버리지를 쓰면 두 가지가 바뀐다.
1. 손실이 배로 커진다 (자명하다)
2. **반대매매가 경로에서 발생한다** — 60일 뒤 지수가 제자리로 돌아와도,
   그 사이 한 번만 담보비율을 깨면 강제청산된 뒤다. 그래서 기간 수익률이
   아니라 **기간 중 최대낙폭**이 위험의 척도다.

두 번째가 핵심이다. "장기적으로는 오른다"는 말이 레버리지에서는 통하지 않는
이유가 이것이다 — 장기가 오기 전에 청산된다.

담보비율 = 총평가금액 / 융자금 × 100
레버리지 L = 총자산 / 자기자본 (2.0 = 자기자본만큼 빌림)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: 국내 증권사 담보유지비율. 보통 140%, 회사·종목별로 130~170%.
DEFAULT_MAINTENANCE = 1.40


def margin_call_drawdown(leverage: float,
                         maintenance: float = DEFAULT_MAINTENANCE) -> float:
    """반대매매를 부르는 하락률.

    자기자본 E, 융자 B, 총자산 P = E + B, 레버리지 L = P/E 이면 B = E(L-1).
    x 만큼 하락 후 담보비율 = P(1-x)/B = L(1-x)/(L-1).
    이것이 maintenance 와 같아지는 x 가 답이다.

        x = 1 - maintenance × (L-1) / L

    L=1 이면 융자가 없으므로 반대매매도 없다(1.0 반환).
    """
    if leverage <= 1.0:
        return 1.0
    x = 1.0 - maintenance * (leverage - 1.0) / leverage
    return float(max(x, 0.0))


def leverage_table(maintenance: float = DEFAULT_MAINTENANCE,
                   levels=(1.2, 1.5, 1.8, 2.0, 2.5, 3.0)) -> pd.DataFrame:
    """레버리지별 반대매매 임계 하락률."""
    rows = []
    for L in levels:
        x = margin_call_drawdown(L, maintenance)
        rows.append({
            "레버리지": f"{L:.1f}x",
            "융자 비중%": (1 - 1 / L) * 100,
            "반대매매 하락률%": x * 100,
            "자기자본 소멸 하락률%": (1 / L) * 100,
        })
    return pd.DataFrame(rows).round(1)


def historical_hit_rate(close: pd.Series, drop: float, *,
                        horizon: int = 60) -> dict:
    """과거에 horizon 안에서 drop 이상 하락한 빈도.

    '반대매매를 맞을 확률'의 무조건부 추정치다. 시작 시점을 특정하지 않으므로
    낙관도 비관도 아닌 기저율(base rate)로 읽어야 한다.
    """
    from src.research.vulnerability import forward_max_drawdown

    fwd = forward_max_drawdown(close, horizon).dropna()
    if fwd.empty:
        return {}
    hit = fwd <= -abs(drop)
    return {
        "표본일": int(len(fwd)),
        "적중일": int(hit.sum()),
        "확률%": float(hit.mean() * 100),
        "최악낙폭%": float(fwd.min() * 100),
    }


def survival_by_leverage(close: pd.Series, *, horizon: int = 60,
                         maintenance: float = DEFAULT_MAINTENANCE,
                         levels=(1.2, 1.5, 1.8, 2.0, 2.5, 3.0)) -> pd.DataFrame:
    """레버리지별 '60일 안에 반대매매 맞을 확률' (무조건부)."""
    rows = []
    for L in levels:
        x = margin_call_drawdown(L, maintenance)
        st = historical_hit_rate(close, x, horizon=horizon)
        if not st:
            continue
        rows.append({
            "레버리지": f"{L:.1f}x",
            "반대매매 하락률%": round(x * 100, 1),
            f"{horizon}일 내 확률%": round(st["확률%"], 1),
        })
    return pd.DataFrame(rows)


def conditional_risk(index: pd.Series, close: pd.Series, *,
                     leverage: float = 2.0, horizon: int = 60,
                     maintenance: float = DEFAULT_MAINTENANCE,
                     bins: int = 5,
                     condition: pd.Series | None = None) -> pd.DataFrame:
    """취약성 분위별 반대매매 확률.

    ★ 이 모듈의 목적이 여기 있다. 취약성 지수는 조정 시점을 짚지 못하지만,
      **확률을 기울이는 데는 쓸모가 있다**. 레버리지 사용자는 기대수익이
      아니라 파산 확률로 판단해야 하므로, 확률이 2~3배 달라지는 것만으로도
      의사결정에 충분하다.
    """
    from src.research.vulnerability import forward_max_drawdown

    x = margin_call_drawdown(leverage, maintenance)
    fwd = forward_max_drawdown(close, horizon)
    df = pd.concat({"v": index, "f": fwd}, axis=1).dropna()
    if condition is not None:
        df = df[condition.reindex(df.index).fillna(False).astype(bool)]
    if len(df) < bins * 20:
        return pd.DataFrame()

    df["q"] = pd.qcut(df["v"], bins, labels=False, duplicates="drop") + 1
    g = df.groupby("q")["f"]
    out = pd.DataFrame({
        "일수": g.size(),
        "평균낙폭%": g.mean() * 100,
        f"-{x * 100:.0f}% 이상 하락 확률%": g.apply(
            lambda s: (s <= -x).mean() * 100),
    })
    return out.round(1)


def margin_debt_signal(margin_debt: pd.Series, market_cap: pd.Series,
                       window: int = 60) -> pd.DataFrame:
    """시장 전체 신용융자의 위치. '남들이 얼마나 빌렸나'.

    개인의 레버리지 결정과 별개로, **시장 전체 레버리지가 극단일 때**
    조정이 증폭된다. 반대매매가 반대매매를 부르기 때문이다(반사성).
    """
    md, mc = margin_debt.dropna(), market_cap.dropna()
    ratio = (md / mc.reindex(mc.index.union(md.index)).ffill()
             .reindex(md.index)).dropna()
    if ratio.empty:
        return pd.DataFrame()
    pr = ratio.rolling(window, min_periods=max(12, window // 4)).rank(pct=True)
    return pd.DataFrame({"신용융자/시총%": ratio * 100,
                         "백분위": pr}).dropna()


def deleverage_checklist(*, drawdown: float, vulnerability: float | None,
                         applicable: bool, margin_pr: float | None,
                         leverage: float,
                         maintenance: float = DEFAULT_MAINTENANCE) -> list[tuple[str, str, str]]:
    """지금 신용을 줄여야 하는가 — 판단 재료를 모아준다.

    ★ 자동 매매 신호가 아니다. 각 항목이 '무엇을 뜻하는지'를 붙여
      스스로 판단하도록 돕는 것이 목적이다.
    """
    out: list[tuple[str, str, str]] = []
    x = margin_call_drawdown(leverage, maintenance)

    out.append(("info", "반대매매 임계",
                f"여기서 {x * 100:.0f}% 더 빠지면 강제청산 "
                f"(레버리지 {leverage:.1f}x, 담보유지 {maintenance * 100:.0f}%)"))

    if drawdown <= -0.15:
        out.append(("bad", "이미 조정 중",
                    f"고점 대비 {drawdown * 100:.1f}%. 반대매매는 경로에서 나므로 "
                    "여기서 더 빠지면 회복 전에 청산된다"))
    elif drawdown <= -0.05:
        out.append(("warn", "고점 이탈", f"고점 대비 {drawdown * 100:.1f}%"))
    else:
        out.append(("ok", "고점 근처", f"고점 대비 {drawdown * 100:.1f}%"))

    if vulnerability is None:
        out.append(("info", "취약성", "산출 불가"))
    elif not applicable:
        out.append(("info", "취약성",
                    "적용 밖 — 이미 조정 중이라 이 지표로는 판단할 수 없다"))
    else:
        lvl = "bad" if vulnerability >= 0.65 else (
            "warn" if vulnerability >= 0.55 else "ok")
        out.append((lvl, "취약성", f"{vulnerability:.2f}"))

    if margin_pr is not None:
        lvl = "bad" if margin_pr >= 0.8 else ("warn" if margin_pr >= 0.6 else "ok")
        out.append((lvl, "시장 신용융자",
                    f"백분위 {margin_pr:.0%} — 전체 레버리지가 높을수록 "
                    "반대매매가 연쇄한다"))

    return out


def local_drawdown(close: pd.Series, window: int = 250) -> pd.Series:
    """**1년 롤링 고점** 대비 낙폭.

    ★ 전고점(cummax) 기준만 쓰면 사각지대가 생긴다. 2021-07 고점을 2025-09
      에야 회복했기 때문에 2022~2025 의 조정이 전부 '하나의 긴 조정' 안으로
      묶여 버렸다. 실제로는 2022-09(-30.8%), 2023-10(-14.6%),
      2025-04(-20.7%) 세 번의 국면이 있었다.

      롤링 고점으로 보면 '최근 1년 안에서 얼마나 빠졌나'가 나온다.
      레버리지 판단에는 이쪽이 더 맞다 — 반대매매는 내 진입가 근처에서
      결정되지, 4년 전 전고점과는 무관하다.
    """
    c = close.dropna().astype("float64").sort_index()
    peak = c.rolling(window, min_periods=max(20, window // 5)).max()
    return (c / peak - 1.0).rename(f"local_dd_{window}")


def local_episodes(close: pd.Series, window: int = 250,
                   threshold: float = -0.10,
                   recover: float = -0.02) -> pd.DataFrame:
    """롤링 고점 기준 조정 국면. 전고점 기준이 놓치는 구간을 잡는다."""
    dd = local_drawdown(close, window)
    rows, inside, low = [], False, None
    for d, v in dd.dropna().items():
        if not inside and v <= threshold:
            inside, low = True, d
        elif inside:
            if v < dd[low]:
                low = d
            if v >= recover:
                rows.append((low, float(dd[low]), d))
                inside = False
    if inside and low is not None:
        rows.append((low, float(dd[low]), dd.index[-1]))
    return pd.DataFrame(rows, columns=["trough", "depth", "end"])
