# Copyright 2026 Amazon.com, Inc. or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import logging
import sys
import os
import boto3
import uuid
import json
import pandas as pd
from urllib import parse
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, Optional, List, Tuple, TypeVar, cast
from botocore.config import Config
from retry_utils import retry_call

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("loader")

# S3 / FDR resilience defaults
S3_RETRY_CONFIG = Config(
    retries={"max_attempts": 5, "mode": "standard"},
)
FDR_MAX_ATTEMPTS = 3
FDR_RETRY_BASE_DELAY_SECONDS = 1.0

T = TypeVar("T")


def _retry_call(
    operation: str,
    fn: Callable[[], T],
    *,
    max_attempts: int = FDR_MAX_ATTEMPTS,
    base_delay: float = FDR_RETRY_BASE_DELAY_SECONDS,
) -> T:
    """Retry an idempotent read with exponential backoff."""
    return retry_call(
        operation,
        fn,
        max_attempts=max_attempts,
        base_delay=base_delay,
        log=logger,
    )

from utils import load_config

config = load_config()

region = config.get("region", "ap-northeast-2")
projectName = config.get("projectName", "es")

s3_prefix = "docs"
s3_image_prefix = "images"
model_name = "Claude 4.0 Sonnet"


def _s3_bucket() -> str:
    return (load_config().get("s3_bucket") or "").strip()


def _sharing_url() -> str:
    """Resolve at call time — import-time snapshots miss APP_CONFIG_JSON updates."""
    return (load_config().get("sharing_url") or "").strip().rstrip("/")


# Simple mapping: subject (company name) -> KRX ticker (yfinance format)
# Add more companies here if needed.
SUBJECT_TO_TICKER: Dict[str, str] = {
    "SK텔레콤": "017670.KS",  # SK텔레콤 Corp
    "CJ CGV": "079160.KS",  # CJ CGV Corp
    "CGV": "079160.KS",  # CGV Corp
    "네이버": "035420.KS",  # NAVER Corp
    "NAVER": "035420.KS",  # NAVER Corp    
    "카카오": "035720.KS",  # Kakao Corp
    "KT": "030200.KS",  # KT Corp   
    "대한항공": "003490.KS",  # 대한항공 Corp
    "아시아나항공": "020560.KS",  # 아시아나항공 Corp
    "호텔신라": "008770.KS",  # 호텔신라 Corp
    "현대차": "005380.KS",  # 현대차 Corp
    "현대모비스": "012330.KS",  # 현대모비스 Corp
    "현대오토에버": "307950.KS",  # 현대오토에버 Corp
    "SK이노베이션": "096770.KS",  # SK이노베이션 Corp
    "SK하이닉스": "000660.KS",  # SK하이닉스 Corp
    "SK Hynix": "000660.KS",  # SK Hynix Corp
    "LG전자": "066570.KS",  # LG 전자 Corp
    "LG Electronics": "066570.KS",  # LG Electronics Corp    
    "LG이노텍": "011070.KS",  # LG 이노텍 Corp
    "LG Innotek": "011070.KS",  # LG Innotek Corp
    "LG에너지솔루션": "373220.KS",  # LG 에너지솔루션 Corp
    "LG디스플레이": "034220.KS",  # LG 디스플레이 Corp
    "HD현대일렉트릭": "267260.KS",  # HD 현대일렉트릭 Corp
    "두산": "000150.KS",  # 두산 Corp
    "GS": "078930.KS",  # GS Corp
    "S-Oil": "010950.KS",  # S-Oil Corp
    "한국전력": "015760.KS",  # 한국전력 Corp
    "삼성전자": "005930.KS",  # 삼성전자 Corp
    "삼성SDI": "006400.KS",  # 삼성SDI Corp,
    "효성중공업": "298040.KS",  # 효성중공업 Corp
    "한화오션": "042660.KS",  # 한화오션 Corp
    "한화시스템": "272210.KS",  # 한화시스템 Corp
    "농심": "004370.KS",  # 농심 Corp
    "동원": "009150.KS",  # 동원 Corp
    "SK": "034730.KS",  # SK Corp
}

stocks = {}

# yfinance KRX ticker: 6-digit code + '.' + market suffix (KS=KOSPI, KQ=KOSDAQ), e.g. 035420.KS
TICKER_CODE_LEN = 6
TICKER_MIN_LEN = 9  # 6 digits + '.' + 2-char suffix
TICKER_DOT_INDEX = 6
TICKER_MARKET_SUFFIXES = frozenset({"KS", "KQ"})
# Extra calendar days beyond the requested period to cover weekends/holidays.
NON_TRADING_DAY_BUFFER = 5


def get_contents_type(file_name):
    if file_name.lower().endswith((".jpg", ".jpeg")):
        content_type = "image/jpeg"
    elif file_name.lower().endswith((".pdf")):
        content_type = "application/pdf"
    elif file_name.lower().endswith((".txt")):
        content_type = "text/plain"
    elif file_name.lower().endswith((".csv")):
        content_type = "text/csv"
    elif file_name.lower().endswith((".ppt", ".pptx")):
        content_type = "application/vnd.ms-powerpoint"
    elif file_name.lower().endswith((".doc", ".docx")):
        content_type = "application/msword"
    elif file_name.lower().endswith((".xls")):
        content_type = "application/vnd.ms-excel"
    elif file_name.lower().endswith((".py")):
        content_type = "text/x-python"
    elif file_name.lower().endswith((".js")):
        content_type = "application/javascript"
    elif file_name.lower().endswith((".md")):
        content_type = "text/markdown"
    elif file_name.lower().endswith((".png")):
        content_type = "image/png"
    else:
        content_type = "no info"    
    return content_type

def upload_to_s3(file_bytes, file_name):
    """
    Upload a file to S3 and return the URL
    """
    try:
        bucket = _s3_bucket()
        sharing = _sharing_url()
        if not bucket or not sharing:
            logger.error(
                "S3 upload skipped: s3_bucket=%r sharing_url=%r",
                bucket,
                sharing,
            )
            return None

        s3_client = boto3.client(
            service_name='s3',
            region_name=load_config().get("region", region),
            config=S3_RETRY_CONFIG,
        )

        content_type = get_contents_type(file_name)       
        logger.info(f"content_type: {content_type}") 

        if content_type == "image/jpeg" or content_type == "image/png":
            s3_key = f"{s3_image_prefix}/{file_name}"
        else:
            s3_key = f"{s3_prefix}/{file_name}"
        
        user_meta = {  # user-defined metadata
            "content_type": content_type,
            "model_name": model_name
        }
        
        response = s3_client.put_object(
            Bucket=bucket, 
            Key=s3_key, 
            ContentType=content_type,
            Metadata = user_meta,
            Body=file_bytes            
        )
        logger.info(f"upload response: {response}")

        url = f"{sharing}/{s3_image_prefix}/{parse.quote(file_name)}"
        return url
    
    except Exception as exc:
        logger.error(
            "Error uploading to S3: %s", type(exc).__name__, exc_info=True
        )
        return None

def resolve_ticker(subject: str) -> str:
    """Resolve input into a yfinance-style ticker.

    Order of resolution:
    1) Exact company name match in SUBJECT_TO_TICKER (with/without spaces)
    2) Already a yfinance-style ticker (e.g., 035420.KS / 000660.KQ)
    3) Fallback: search via search_ticker_candidates and use the first match
    """
    # 1) Company name -> ticker mapping (exact match)
    if subject in SUBJECT_TO_TICKER:
        return SUBJECT_TO_TICKER[subject]
    
    # 1-2) Try matching without spaces (e.g., "LG 에너지솔루션" -> "LG에너지솔루션")
    subject_no_space = subject.replace(" ", "")
    if subject_no_space in SUBJECT_TO_TICKER:
        return SUBJECT_TO_TICKER[subject_no_space]
    
    # 1-3) Try matching with normalized keys (remove spaces from both)
    for key, ticker in SUBJECT_TO_TICKER.items():
        if key.replace(" ", "") == subject_no_space:
            return ticker

    # 2) If it's already a yfinance-style ticker, accept as-is
    ticker_text = (subject or "").strip().upper()
    if (
        len(ticker_text) >= TICKER_MIN_LEN
        and ticker_text[:TICKER_CODE_LEN].isdigit()
        and ticker_text[TICKER_DOT_INDEX] == "."
        and ticker_text[TICKER_DOT_INDEX + 1 :] in TICKER_MARKET_SUFFIXES
    ):
        return ticker_text

    # 3) Fallback: try searching candidates
    try:
        candidates = search_ticker_candidates(subject, limit=1)
    except Exception as exc:
        logger.error(
            "Failed to resolve ticker for input %r (%s)",
            subject,
            type(exc).__name__,
            exc_info=True,
        )
        raise ValueError("Failed to resolve ticker for the provided input") from exc

    if candidates:
        return candidates[0].get("ticker", "") or (
            f"{candidates[0].get('itemcode', '')}"  # very defensive fallback
        )

    raise ValueError(
        f"Unknown subject: {subject!r}. Provide a known company name or a valid ticker."
    )

def _ticker_to_itemcode(ticker: str) -> str:
    # Example: 035420.KS -> 035420
    return ticker.split(".")[0]

def generate_short_uuid(length: int = 8) -> str:
    """Generate a short UUID string."""
    full_uuid = uuid.uuid4().hex
    return full_uuid[:length]


def search_ticker_candidates(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """Search ticker candidates by partial company name or 6-digit item code.

    Return format: [{ company_name, itemcode, market, ticker }]
    ticker follows yfinance format (e.g., 035420.KS or 000660.KQ).
    """
    try:
        import FinanceDataReader as fdr  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "FinanceDataReader is not installed. Install with: pip install finance-datareader"
        ) from exc

    try:
        df = _retry_call(
            "FDR StockListing(KRX)",
            lambda: fdr.StockListing("KRX"),
        )
    except Exception as exc:
        logger.error(
            "FDR StockListing(KRX) failed (%s)",
            type(exc).__name__,
            exc_info=True,
        )
        raise RuntimeError("Failed to retrieve stock listing data") from exc

    if df is None or getattr(df, "empty", True):
        return []

    # Defensive access to columns
    name_col = "Name"
    symbol_col = "Symbol"
    market_col = "Market"

    query_text = (query or "").strip()
    if not query_text:
        return []

    try:
        name_mask = df[name_col].astype(str).str.contains(query_text, case=False, na=False)
    except Exception:
        name_mask = False
    try:
        symbol_mask = df[symbol_col].astype(str).str.contains(query_text, na=False)
    except Exception:
        symbol_mask = False

    try:
        sub = df[name_mask | symbol_mask].copy()
    except Exception:
        return []

    def market_to_suffix(market: str) -> str:
        market_name = (market or "").upper()
        if "KOSDAQ" in market_name:
            return ".KQ"
        # Default: treat as KOSPI
        return ".KS"

    results: List[Dict[str, str]] = []
    for _, row in sub.iterrows():
        try:
            name_v = cast(str, row.get(name_col, ""))
            code_v = str(row.get(symbol_col, "")).zfill(6)
            market_v = cast(str, row.get(market_col, ""))
            ticker_v = f"{code_v}{market_to_suffix(market_v)}"
        except Exception:
            continue
        results.append(
            {
                "company_name": name_v,
                "itemcode": code_v,
                "market": market_v,
                "ticker": ticker_v,
            }
        )

    return results[: max(0, limit)]

def _fetch_fdr(itemcode: str, period: int = 30) -> List[Dict[str, object]]:
    """Fetch daily candles for the last ~period days using FinanceDataReader."""
    try:
        import FinanceDataReader as fdr  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "FinanceDataReader is not installed. Install with: pip install finance-datareader"
        ) from exc

    end_dt = datetime.now(timezone.utc).date()
    # Query period days (add buffer to account for non-trading days)
    start_dt = end_dt - timedelta(days=period + NON_TRADING_DAY_BUFFER)

    try:
        df = _retry_call(
            f"FDR DataReader({itemcode})",
            lambda: fdr.DataReader(
                itemcode, start_dt.isoformat(), end_dt.isoformat()
            ),
        )
    except Exception as exc:
        logger.error(
            "FDR DataReader failed for code=%s (%s)",
            itemcode,
            type(exc).__name__,
            exc_info=True,
        )
        raise RuntimeError("Failed to retrieve stock data for the specified ticker") from exc

    if df is None or df.empty:
        logger.info(
            f"FDR DataFrame empty: code={itemcode}, start={start_dt}, end={end_dt}"
        )
        return []

    logger.info(
        f"FDR DataFrame shape={getattr(df, 'shape', None)}, columns={list(getattr(df, 'columns', []))}"
    )

    series: List[Dict[str, object]] = []
    for idx, row in df.iterrows():
        try:
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        except Exception:
            continue
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        time_iso = ts.astimezone(timezone.utc).isoformat()

        def _get_float(name: str):
            raw_value = row.get(name)
            try:
                return float(raw_value) if raw_value is not None else None
            except Exception:
                return None

        def _get_int(name: str):
            raw_value = row.get(name)
            try:
                return int(raw_value) if raw_value is not None else None
            except Exception:
                return None

        series.append(
            {
                "time": time_iso,
                "open": _get_float("Open"),
                "high": _get_float("High"),
                "low": _get_float("Low"),
                "close": _get_float("Close"),
                "volume": _get_int("Volume"),
            }
        )

    # Keep only the last period calendar days
    cutoff = datetime.now(timezone.utc) - timedelta(days=period)
    before_len = len(series)
    series = [p for p in series if p["time"] and datetime.fromisoformat(p["time"]) >= cutoff]
    logger.info(f"FDR filtered last {period} days: {before_len} -> {len(series)} rows")
    return series

def get_stock_trend(company_name: str = "NAVER", period: int = 30) -> Dict[str, object]:
    """
    Return last ~period days price trend as a dict. Uses FinanceDataReader only.
    company_name: the company name to get stock trend
    period: the period to get stock trend
    return: the price trend of the given company as a dict
    """
    ticker = resolve_ticker(company_name)
    itemcode = _ticker_to_itemcode(ticker)

    logger.info(f"Fetching trend for {period} days via FDR: subject={company_name}, itemcode={itemcode}")
    series_fdr = _fetch_fdr(itemcode, period)

    points: List[Dict[str, Optional[object]]] = []
    if series_fdr:
        prev_close: Optional[float] = None
        for r in series_fdr:
            close_v = r.get("close")
            change_v: Optional[float] = None
            change_pct_v: Optional[float] = None
            if isinstance(close_v, (int, float)) and prev_close is not None:
                change_v = float(close_v) - float(prev_close)
                if prev_close != 0:
                    change_pct_v = (change_v / float(prev_close)) * 100.0
            points.append({**r, "change": change_v, "change_percent": change_pct_v})
            if isinstance(close_v, (int, float)):
                prev_close = float(close_v)

    if not points:
        logger.info("FDR returned no rows for last month trend.")

    result: Dict[str, object] = {
        "company_name": company_name,
        "ticker": ticker,
        "currency": "KRW",
        "range": "1mo",
        "interval": "1d",
        "points": points,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    stocks[f"{company_name}_{period}"] = result

    return result

def get_expected_high_low(company_name: str = "NAVER", period: int = 30) -> Tuple[str, str]:
    """
    Return last ~period days price trend with expected high and low as a dict. Uses FinanceDataReader only.
    company_name: the company name to get stock trend
    period: the period to get stock trend
    return: the price trend of the given company as a dict
    """

    trend_dict = stocks.get(f"{company_name}_{period}")
    if trend_dict is None:
        trend_dict = get_stock_trend(company_name, period)
        stocks[f"{company_name}_{period}"] = trend_dict
    
    points = trend_dict.get("points", [])
    if not points:
        raise ValueError("trend does not contain points data.")

    # Prepare data similar to draw_stock_trend
    df = pd.DataFrame(points)
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)  # Sort by time
    
    # Filter out None values for close price
    df_clean = df[df['close'].notna()].copy()
    
    if len(df_clean) == 0:
        raise ValueError("No valid close price data in points.")
    
    # Get closing prices as array
    close_prices = df_clean['close'].values
    
    # Find and highlight maximum and minimum closing prices (highest and lowest)
    max_idx = pd.Series(close_prices).idxmax()
    min_idx = pd.Series(close_prices).idxmin()
    
    max_close = close_prices[max_idx]
    min_close = close_prices[min_idx]
    
    # Get current (last) closing price for percentage calculation
    current_close = close_prices[-1]
    
    # Calculate percentage from current closing price
    max_percent = ((max_close - current_close) / current_close) * 100 if current_close != 0 else 0
    min_percent = ((min_close - current_close) / current_close) * 100 if current_close != 0 else 0

    expected_high = f"{max_percent:+.2f}%"
    expected_low = f"{min_percent:+.2f}%"

    return expected_high, expected_low

def is_lower_than_ma20(company_name: str = "NAVER", period: int = 30) -> bool:
    """
    Return True if the current closing price is lower than the 20-day moving average, False otherwise. Uses FinanceDataReader only.
    company_name: the company name to get stock trend
    period: the period to get stock trend
    return: True if the current closing price is lower than the 20-day moving average, False otherwise
    """

    trend_dict = stocks.get(f"{company_name}_{period}")
    if trend_dict is None:
        trend_dict = get_stock_trend(company_name, period)
        stocks[f"{company_name}_{period}"] = trend_dict
    
    points = trend_dict.get("points", [])
    if not points:
        raise ValueError("trend does not contain points data.")

    # Prepare data similar to draw_stock_trend
    df = pd.DataFrame(points)
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)  # Sort by time

    df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()  # 20-day moving average
    
    # Filter out None values for close price
    df_clean = df[df['close'].notna()].copy()
    
    if len(df_clean) == 0:
        raise ValueError("No valid close price data in points.")
    
    # Get closing prices as array
    close_prices = df_clean['close'].values
    
    # Get current (last) closing price for percentage calculation
    current_close = close_prices[-1]

    return True if current_close < df['ma20'].values[-1] else False


def draw_stock_trend(trend: Dict[str, object]) -> Dict[str, List[str]]:
    """Draw graphs of the given trend (delegates rendering to trade_charts)."""
    from trade_charts import draw_stock_trend as _draw_stock_trend

    return _draw_stock_trend(
        trend,
        upload_to_s3=upload_to_s3,
        generate_short_uuid=generate_short_uuid,
        sharing_url=_sharing_url(),
        s3_bucket=_s3_bucket(),
    )