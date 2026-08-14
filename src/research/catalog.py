"""데이터 카탈로그 — 어떤 계열이 무엇이고 어디서 오는지.

계열이 42개까지 늘면 목록 없이는 관리가 안 된다. 반 년 뒤에 이 프로젝트를
다시 열었을 때 `ecos.corp_bbb` 가 뭔지 알 방법이 있어야 한다.

이름은 config/series.yaml 에서 읽는다 — 코드에 두 번 적으면 갈라진다.
"""
from __future__ import annotations

import pandas as pd

from config import settings
from src import store

#: series.yaml 에 없는 파생/보조 계열의 설명
EXTRA_NAMES = {
    "_marcap": "시가총액",
    "_amount": "거래대금",
    "_per": "PER",
    "_pbr": "PBR",
    "_divyield": "배당수익률",
}

SOURCE_LABEL = {
    "fred": "FRED (미국 세인트루이스 연준)",
    "ecos": "ECOS (한국은행)",
    "krx": "KRX (pykrx / FinanceDataReader)",
    "YF": "yfinance",
    "KRX": "KRX 지수",
}


def _declared_names() -> dict[str, str]:
    """series.yaml 에 선언된 series_id → 이름."""
    out: dict[str, str] = {}
    for item in settings.series_for("fred"):
        out[f"fred.{item['id']}"] = item["name"]
    for item in settings.series_for("ecos"):
        out[f"ecos.{item['key']}"] = item["name"]
    for item in settings.series_for("krx_index"):
        out[f"KRX.{item['ticker']}"] = item["name"]
    # 규모·업종 지수. 빠뜨리면 카탈로그에 이름 없는 계열이 생긴다
    for item in settings.series_for("krx_sector"):
        grp = "규모" if item.get("group") == "size" else "업종"
        out[f"KRX.{item['ticker']}"] = f"{item['name']} ({grp})"
    for item in settings.series_for("yfinance"):
        out[f"YF.{item['ticker']}"] = item["name"]
    return out


def _label(series_id: str, declared: dict[str, str]) -> str:
    if series_id in declared:
        return declared[series_id]
    # krx.1001_per 처럼 코드+접미사로 만들어진 파생 계열
    for suffix, desc in EXTRA_NAMES.items():
        if series_id.endswith(suffix):
            code = series_id.split(".")[-1].replace(suffix, "")
            base = declared.get(f"KRX.{code}", code)
            return f"{base} {desc}"
    return "—"


def build() -> pd.DataFrame:
    """수집된 모든 계열의 목록 + 기간 + 관측 수."""
    declared = _declared_names()
    rows = []

    for table, col in [("macro", "series_id"), ("prices", "ticker")]:
        df = store.read(table)
        if df.empty:
            continue
        g = df.groupby(col)["date"].agg(["min", "max", "size"])
        for sid, r in g.iterrows():
            source = str(sid).split(".")[0]
            rows.append({
                "계열": sid,
                "이름": _label(str(sid), declared),
                "출처": SOURCE_LABEL.get(source, source),
                "저장소": table,
                "시작": r["min"].date(),
                "최신": r["max"].date(),
                "관측": int(r["size"]),
            })

    flows = store.read("flows")
    if not flows.empty:
        g = flows.groupby(["market", "investor"])["date"].agg(["min", "max", "size"])
        for (mk, inv), r in g.iterrows():
            rows.append({
                "계열": f"flows.{mk}.{inv}", "이름": f"{mk} {inv} 순매수",
                "출처": "KRX (pykrx)", "저장소": "flows",
                "시작": r["min"].date(), "최신": r["max"].date(),
                "관측": int(r["size"]),
            })

    if not rows:
        return pd.DataFrame()
    return (pd.DataFrame(rows)
            .sort_values(["저장소", "계열"])
            .reset_index(drop=True))


def staleness(catalog: pd.DataFrame, today: pd.Timestamp | None = None,
              warn_days: int = 7) -> pd.DataFrame:
    """오래 갱신되지 않은 계열. 월간 계열은 원래 느리므로 참고용이다."""
    if catalog.empty:
        return catalog
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = catalog.copy()
    out["지연(일)"] = [(today - pd.Timestamp(d)).days for d in out["최신"]]
    return out[out["지연(일)"] > warn_days].sort_values("지연(일)", ascending=False)
