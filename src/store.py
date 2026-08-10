"""Parquet 읽기/쓰기/병합 — 프로젝트의 유일한 I/O 지점 (CLAUDE.md §5.1).

여기 밖에서는 어떤 모듈도 data/ 를 직접 열지 않는다.
스키마(§3)는 write() 에서 dtype까지 강제된다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from config.settings import DATA_DIR, META_JSON

# --- §3 스키마 계약 -------------------------------------------------------
SCHEMAS: dict[str, dict[str, str]] = {
    "macro": {
        "date": "datetime64[ns]",
        "series_id": "string",
        "value": "float64",
    },
    "prices": {
        "date": "datetime64[ns]",
        "ticker": "string",
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "volume": "float64",
    },
    "flows": {
        "date": "datetime64[ns]",
        "market": "string",
        "investor": "string",
        "net_value": "float64",
    },
}

KEYS: dict[str, list[str]] = {
    "macro": ["date", "series_id"],
    "prices": ["date", "ticker"],
    "flows": ["date", "market", "investor"],
}

KST = timezone(timedelta(hours=9))


def _path(name: str) -> Path:
    return DATA_DIR / f"{name}.parquet"


def empty(name: str) -> pd.DataFrame:
    """스키마만 맞는 빈 DataFrame. RangeIndex."""
    schema = SCHEMAS[name]
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in schema.items()})


def coerce(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """컬럼 순서·dtype 강제. 초과 컬럼은 버리고, 누락 컬럼은 에러."""
    schema = SCHEMAS[name]
    missing = set(schema) - set(df.columns)
    if missing:
        raise ValueError(f"{name}: 필수 컬럼 누락 {sorted(missing)}")

    out = df.loc[:, list(schema)].copy()
    for col, dtype in schema.items():
        if dtype == "datetime64[ns]":
            s = pd.to_datetime(out[col], errors="coerce")
            # tz-aware 로 들어온 경우 tz 제거 후 자정 정규화 (§3.1)
            if isinstance(s.dtype, pd.DatetimeTZDtype):
                s = s.dt.tz_localize(None)
            out[col] = s.dt.normalize()
        else:
            out[col] = out[col].astype(dtype)
    return out.reset_index(drop=True)


def read(name: str) -> pd.DataFrame:
    """data/{name}.parquet 로드. 없으면 스키마만 맞는 빈 DataFrame."""
    p = _path(name)
    if not p.exists():
        return empty(name)
    return coerce(name, pd.read_parquet(p))


def write(name: str, df: pd.DataFrame) -> None:
    """스키마 검증 후 저장. dtype 강제."""
    out = coerce(name, df)
    out = out.sort_values(KEYS[name]).reset_index(drop=True)
    _path(name).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(_path(name), index=False)


def upsert(name: str, new: pd.DataFrame, keys: list[str] | None = None) -> pd.DataFrame:
    """기존 + 신규 병합. keys 기준 중복 제거(신규 우선), date 정렬 후 저장.

    신규 우선인 이유: 매크로 계열은 사후 정정(revision)되므로 나중에 받은 값이 옳다.
    """
    keys = keys or KEYS[name]
    new = coerce(name, new)
    if new.empty:
        return read(name)

    merged = pd.concat([read(name), new], ignore_index=True)
    # keep="last" → 나중에 붙인 new 가 살아남는다
    merged = merged.drop_duplicates(subset=keys, keep="last")
    write(name, merged)
    return read(name)


def last_date(name: str, **filters) -> pd.Timestamp | None:
    """증분 수집용. 해당 조건의 마지막 관측일. 데이터 없으면 None."""
    df = read(name)
    for col, val in filters.items():
        df = df[df[col] == val]
    if df.empty:
        return None
    return pd.Timestamp(df["date"].max())


# --- _meta.json (§3.4) ----------------------------------------------------
def read_meta() -> dict:
    if not META_JSON.exists():
        return {}
    try:
        return json.loads(META_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def update_meta(source: str, status: str, **kw) -> None:
    """_meta.json 갱신. 대시보드 상단 '최종 갱신' 배지의 원천."""
    meta = read_meta()
    entry = {
        "last_run": datetime.now(KST).isoformat(timespec="seconds"),
        "status": status,
    }
    for k, v in kw.items():
        entry[k] = str(v) if isinstance(v, pd.Timestamp) else v
    meta[source] = entry
    META_JSON.parent.mkdir(parents=True, exist_ok=True)
    META_JSON.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
