"""경로 / 상수 / env 로더. 다른 모듈은 여기만 보고 경로를 결정한다."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

# --- 경로 ---
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
SERIES_YAML = CONFIG_DIR / "series.yaml"
META_JSON = DATA_DIR / "_meta.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- env ---
load_dotenv(ROOT / ".env")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
ECOS_API_KEY = os.getenv("ECOS_API_KEY", "")

# --- 수집 공통 상수 ---
DEFAULT_START = "2005-01-01"   # 히스토리 시작점
LOOKBACK_DAYS = 30             # 사후 정정(revision) 재수집 구간 (§5.2)
KRX_SLEEP = 0.3                # pykrx 호출 간 대기 (§7.5)
TZ = "Asia/Seoul"


@lru_cache(maxsize=1)
def series_config() -> dict:
    """config/series.yaml 파싱 결과. 프로세스당 1회만 읽는다."""
    with open(SERIES_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def series_for(source: str) -> list[dict]:
    """소스별 시리즈 목록. 없으면 빈 리스트."""
    return series_config().get(source, []) or []
