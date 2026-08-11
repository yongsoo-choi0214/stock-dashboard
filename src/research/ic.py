"""지표별 Information Coefficient (CLAUDE.md §6 Phase 6).

IC = 시점 t의 신호와 t 이후 수익률의 순위상관. 지표가 실제로 앞날을 설명하는지
재는 도구다. 값이 아니라 **부호의 안정성**을 봐야 한다.

이 모듈의 존재 이유 절반은 §7.2(look-ahead bias) 방지다.
매크로 계열은 관측일이 아니라 **공표일** 기준으로 써야 한다. 발표 시차를 무시하면
백테스트에 미래 정보가 새어 들어가 IC가 실제보다 훨씬 좋아 보인다.
"""
from __future__ import annotations

import pandas as pd

from config import settings


def apply_publication_lag(s: pd.Series, lag_days: int) -> pd.Series:
    """관측일 index → 공표일 index. (§7.2)

    ECOS M2는 약 2개월 지연 공표된다. 2026-06-30 관측치를 그 날짜에 두고 쓰면
    실제로는 8월 말에야 알 수 있었던 값을 6월에 알았다고 가정하는 셈이다.
    """
    if lag_days <= 0:
        return s
    out = s.copy()
    out.index = out.index + pd.Timedelta(days=lag_days)
    return out


def lag_for(series_id: str) -> int:
    """series.yaml 에 선언된 발표 시차(일). 없으면 0."""
    source, _, key = series_id.partition(".")
    for item in settings.series_for(source):
        if str(item.get("id", item.get("key", ""))) == key:
            return int(item.get("publication_lag_days", 0) or 0)
    return 0


def forward_return(close: pd.Series, horizon: int = 20) -> pd.Series:
    """t 시점부터 horizon 영업일 뒤까지의 수익률. 마지막 horizon개는 NaN."""
    c = close.astype("float64").sort_index()
    return (c.shift(-horizon) / c - 1.0).rename(f"fwd_{horizon}")


def align_signal(signal: pd.Series, close: pd.Series, *,
                 lag_days: int = 0) -> pd.Series:
    """신호를 가격 index 에 맞춘다.

    ffill 로 붙이는 이유: 월간 신호를 일간 가격에 쓰려면 '가장 최근에 알려진 값'을
    들고 가야 한다. 보간(interpolate)은 미래 값을 섞으므로 절대 쓰지 않는다.
    """
    s = apply_publication_lag(signal.dropna().sort_index(), lag_days)
    return s.reindex(close.index.union(s.index)).ffill().reindex(close.index)


def _corr(a: pd.Series, b: pd.Series, method: str) -> float:
    """상관계수. spearman 은 '순위 변환 후 피어슨'으로 계산한다.

    pandas 의 method="spearman" 은 scipy 를 요구하는데, 순위상관의 정의가
    곧 순위에 대한 피어슨이므로 결과가 같다. pandas .rank() 는 동점을
    평균순위로 처리한다 — 기준금리처럼 값이 오래 평평한 계열에서 이게 중요하다.
    """
    if len(a) < 3 or a.nunique() < 2 or b.nunique() < 2:
        return float("nan")
    if method == "spearman":
        a, b = a.rank(), b.rank()
    elif method != "pearson":
        raise ValueError(f"지원하지 않는 method: {method}")
    return float(a.corr(b))


def ic(signal: pd.Series, close: pd.Series, horizon: int = 20, *,
       lag_days: int = 0, method: str = "spearman") -> float:
    """전체 구간 IC 하나. 순위상관이 기본 — 이상치에 덜 흔들린다."""
    sig = align_signal(signal, close, lag_days=lag_days)
    fwd = forward_return(close, horizon)
    df = pd.concat({"s": sig, "f": fwd}, axis=1).dropna()
    return _corr(df["s"], df["f"], method)


def rolling_ic(signal: pd.Series, close: pd.Series, horizon: int = 20,
               window: int = 252, *, lag_days: int = 0,
               method: str = "spearman") -> pd.Series:
    """롤링 IC 시계열. 레짐이 바뀌면 지표가 죽는지 여기서 드러난다.

    순위는 **창 안에서** 다시 매긴다. 전체 구간 순위를 미리 매겨두고 롤링하면
    창 밖의 정보가 순위에 섞여 들어가므로 같은 값이 아니다.
    """
    sig = align_signal(signal, close, lag_days=lag_days)
    fwd = forward_return(close, horizon)
    df = pd.concat({"s": sig, "f": fwd}, axis=1).dropna()
    name = f"ic_{horizon}"
    if df.empty or len(df) < window:
        return pd.Series(dtype="float64", name=name)

    if method == "pearson":
        return df["s"].rolling(window).corr(df["f"]).rename(name)

    s, f = df["s"], df["f"]
    vals = [float("nan")] * (window - 1)
    for i in range(window - 1, len(df)):
        sl = slice(i - window + 1, i + 1)
        vals.append(_corr(s.iloc[sl], f.iloc[sl], "spearman"))
    return pd.Series(vals, index=df.index, name=name)


def ic_table(signals: dict[str, pd.Series], close: pd.Series,
             horizons=(5, 20, 60), *, lags: dict[str, int] | None = None,
             method: str = "spearman") -> pd.DataFrame:
    """신호 × 기간 IC 표.

    hit_ratio = 부호가 맞은 비율. IC 크기보다 이쪽이 실전 감각에 가깝다.
    """
    lags = lags or {}
    rows = []
    for name, sig in signals.items():
        lag = lags.get(name, 0)
        row: dict[str, object] = {"signal": name, "lag_days": lag}
        for h in horizons:
            row[f"IC_{h}d"] = ic(sig, close, h, lag_days=lag, method=method)
            row[f"hit_{h}d"] = hit_ratio(sig, close, h, lag_days=lag)
        row["n"] = int(pd.concat(
            {"s": align_signal(sig, close, lag_days=lag),
             "f": forward_return(close, max(horizons))}, axis=1).dropna().shape[0])
        rows.append(row)
    return pd.DataFrame(rows).set_index("signal")


def hit_ratio(signal: pd.Series, close: pd.Series, horizon: int = 20, *,
              lag_days: int = 0) -> float:
    """신호를 중앙값 기준 상/하로 나눴을 때, 상위 구간의 상승 확률."""
    sig = align_signal(signal, close, lag_days=lag_days)
    fwd = forward_return(close, horizon)
    df = pd.concat({"s": sig, "f": fwd}, axis=1).dropna()
    if df.empty:
        return float("nan")
    upper = df[df["s"] > df["s"].median()]
    if upper.empty:
        return float("nan")
    return float((upper["f"] > 0).mean())
