"""Matplotlib chart rendering for trade_info stock trends."""

from __future__ import annotations

import io
import logging
import os
from typing import Dict, List, cast

import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

logger = logging.getLogger("loader")

script_dir = os.path.dirname(os.path.abspath(__file__))

CHART_Y_AXIS_LOWER_SCALE = 0.98
CHART_Y_AXIS_UPPER_SCALE = 1.02
CANDLESTICK_BODY_WIDTH = 0.6
CHART_Y_AXIS_PADDING_KRW = 200


def _configure_korean_font() -> None:
    """Register an installed Hangul TTF for stock charts.

    Prefer ``addfont`` on known Nanum paths (fonts-nanum package). Setting
    ``font.family = 'AppleGothic'`` never raises when missing on Linux.
    """
    plt.rcParams["axes.unicode_minus"] = False

    ttf_candidates = (
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/nanum/NanumGothic.ttf",
        os.path.join(script_dir, "assets", "NanumGothic-Regular.ttf"),
        "/Library/Fonts/NanumGothic.ttf",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    )
    for path in ttf_candidates:
        if not os.path.isfile(path):
            continue
        try:
            fm.fontManager.addfont(path)
            name = fm.FontProperties(fname=path).get_name()
            plt.rcParams["font.family"] = name
            plt.rcParams["font.sans-serif"] = [
                name,
                "NanumGothic",
                "Nanum Gothic",
                "AppleGothic",
                "DejaVu Sans",
                "sans-serif",
            ]
            logger.info("Korean font set to: %s (%s)", name, path)
            return
        except Exception as exc:
            logger.info("font add failed for %s: %s", path, exc)

    korean_fonts = [
        "NanumGothic",
        "Nanum Gothic",
        "NanumBarunGothic",
        "AppleGothic",
        "Apple SD Gothic Neo",
        "Malgun Gothic",
    ]
    for font_name in korean_fonts:
        if any(f.name == font_name for f in fm.fontManager.ttflist):
            plt.rcParams["font.family"] = font_name
            logger.info("Korean font set to: %s", font_name)
            return
    logger.warning("Could not set Korean font, using default font")


def _prepare_trend_series(points: List[object], *, with_moving_averages: bool = False) -> pd.DataFrame:
    df = pd.DataFrame(points)
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    if with_moving_averages:
        df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()
    return df


def _chart_title(base: str, company_name: str, ticker: str) -> str:
    title = f'{company_name} {base}'
    if ticker:
        title += f' ({ticker})'
    return title


def _render_candlestick_chart(df: pd.DataFrame, company_name: str, ticker: str):
    fig, ax = plt.subplots(figsize=(14, 7))
    width = CANDLESTICK_BODY_WIDTH

    for _idx, row in df.iterrows():
        date = mdates.date2num(row['time'])
        open_price = row['open']
        close_price = row['close']
        high_price = row['high']
        low_price = row['low']

        if any(v is None for v in [open_price, close_price, high_price, low_price]):
            continue

        color = 'red' if close_price >= open_price else 'blue'
        ax.plot([date, date], [low_price, high_price], color=color, linewidth=1)
        body_height = abs(close_price - open_price)
        body_bottom = min(open_price, close_price)
        rect = Rectangle(
            (date - width / 2, body_bottom),
            width,
            body_height,
            facecolor=color,
            edgecolor=color,
            linewidth=1,
        )
        ax.add_patch(rect)

    if len(df) > 0:
        dates = [mdates.date2num(t) for t in df['time']]
        ax.plot(dates, df['ma5'], color='orange', linewidth=2, label='MA5', linestyle='-', alpha=0.8)
        ax.plot(dates, df['ma20'], color='green', linewidth=2, label='MA20', linestyle='-', alpha=0.8)
        ax.set_xlim(mdates.date2num(df['time'].min()) - 1, mdates.date2num(df['time'].max()) + 1)
        ax.set_ylim(
            df['low'].min() - CHART_Y_AXIS_PADDING_KRW,
            df['high'].max() + CHART_Y_AXIS_PADDING_KRW,
        )

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.xticks(rotation=45, ha='right')

    ax.set_xlabel('Date', fontsize=12, fontweight='bold')
    ax.set_ylabel('Price (KRW)', fontsize=12, fontweight='bold')
    ax.set_title(
        _chart_title('Stock Trend - Candlestick Chart', company_name, ticker),
        fontsize=14,
        fontweight='bold',
    )
    ax.grid(True, alpha=0.3)
    ax.legend(
        handles=[
            Patch(facecolor='red', edgecolor='red', label='Up'),
            Patch(facecolor='blue', edgecolor='blue', label='Down'),
            Line2D([0], [0], color='orange', linewidth=2, label='MA5 (5-day)'),
            Line2D([0], [0], color='green', linewidth=2, label='MA20 (20-day)'),
        ],
        loc='upper left',
    )
    plt.tight_layout()
    return fig


def _render_change_percent_chart(df: pd.DataFrame, company_name: str, ticker: str):
    fig, ax = plt.subplots(figsize=(14, 7))

    if len(df) > 0 and 'change_percent' in df.columns:
        change_percent = df['change_percent'].fillna(0).values
        date_nums = [mdates.date2num(d) for d in df['time']]
        colors = ['red' if x >= 0 else 'blue' for x in change_percent]
        bars = ax.bar(date_nums, change_percent, color=colors, alpha=0.7, width=CANDLESTICK_BODY_WIDTH)

        for bar, val in zip(bars, change_percent):
            if not pd.isna(val) and val != 0:
                label_text = f'{val:.2f}%'
                if val >= 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        val,
                        label_text,
                        ha='center',
                        va='bottom',
                        fontsize=12,
                        fontweight='bold',
                    )
                else:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        val,
                        label_text,
                        ha='center',
                        va='top',
                        fontsize=12,
                        fontweight='bold',
                    )

        ax.set_xlim(mdates.date2num(df['time'].min()) - 1, mdates.date2num(df['time'].max()) + 1)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax.set_xlabel('Date', fontsize=12, fontweight='bold')
        ax.set_ylabel('Change Percentage (%)', fontsize=12, fontweight='bold')
        ax.set_title(
            _chart_title('Daily Price Change Percentage', company_name, ticker),
            fontsize=14,
            fontweight='bold',
        )
        ax.grid(True, alpha=0.3, axis='y')
        ax.legend(
            handles=[
                Patch(facecolor='red', edgecolor='red', label='Increase'),
                Patch(facecolor='blue', edgecolor='blue', label='Decrease'),
            ],
            loc='upper right',
        )

    plt.tight_layout()
    return fig


def _render_closing_price_chart(df: pd.DataFrame, company_name: str, ticker: str):
    fig, ax = plt.subplots(figsize=(14, 7))
    df_clean = df[df['close'].notna()].copy()

    if len(df_clean) > 0:
        dates = [mdates.date2num(t) for t in df_clean['time']]
        close_prices = df_clean['close'].values
        high_prices = df_clean['high'].fillna(df_clean['close']).values
        low_prices = df_clean['low'].fillna(df_clean['close']).values

        ax.fill_between(
            dates,
            low_prices,
            high_prices,
            color='lightgray',
            alpha=0.3,
            label='High-Low Range',
            zorder=1,
        )
        ax.plot(
            dates,
            close_prices,
            color='blue',
            linewidth=2,
            label='Closing Price',
            marker='o',
            markersize=4,
            zorder=3,
        )

        max_idx = pd.Series(close_prices).idxmax()
        min_idx = pd.Series(close_prices).idxmin()
        max_close = close_prices[max_idx]
        min_close = close_prices[min_idx]
        max_date = dates[max_idx]
        min_date = dates[min_idx]
        current_close = close_prices[-1]
        max_percent = ((max_close - current_close) / current_close) * 100 if current_close != 0 else 0
        min_percent = ((min_close - current_close) / current_close) * 100 if current_close != 0 else 0
        logger.info(f"max_percent: {max_percent}")
        logger.info(f"min_percent: {min_percent}")

        ax.plot(
            max_date,
            max_close,
            marker='^',
            markersize=12,
            color='darkred',
            markeredgecolor='white',
            markeredgewidth=2,
            zorder=5,
            label='Max Price',
        )
        ax.annotate(
            f'Max: {max_percent:+.2f}%',
            xy=(max_date, max_close),
            xytext=(10, 10),
            textcoords='offset points',
            fontsize=15,
            fontweight='bold',
            color='darkred',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', alpha=0.7),
            arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='darkred'),
        )
        ax.plot(
            min_date,
            min_close,
            marker='v',
            markersize=12,
            color='darkblue',
            markeredgecolor='white',
            markeredgewidth=2,
            zorder=5,
            label='Min Price',
        )
        ax.annotate(
            f'Min: {min_percent:+.2f}%',
            xy=(min_date, min_close),
            xytext=(10, -20),
            textcoords='offset points',
            fontsize=15,
            fontweight='bold',
            color='darkblue',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='lightblue', alpha=0.7),
            arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='darkblue'),
        )

        all_values = list(close_prices) + list(high_prices) + list(low_prices)
        if len(all_values) > 0:
            min_val_plot = min(all_values)
            max_val_plot = max(all_values)
            ax.set_ylim(min_val_plot * CHART_Y_AXIS_LOWER_SCALE, max_val_plot * CHART_Y_AXIS_UPPER_SCALE)

        ax.set_xlim(min(dates) - 1, max(dates) + 1)
        ax.set_ylabel('Price (KRW)', fontsize=12, fontweight='bold', color='blue')
        ax.tick_params(axis='y', labelcolor='blue')

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.xticks(rotation=45, ha='right')
    ax.set_xlabel('Date', fontsize=12, fontweight='bold')
    ax.set_title(
        _chart_title('Stock Trend - Closing Price & Daily Change', company_name, ticker),
        fontsize=14,
        fontweight='bold',
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left')
    plt.tight_layout()
    return fig


def _save_trend_chart_image(
    fig,
    image_url: List[str],
    *,
    upload_to_s3,
    generate_short_uuid,
    sharing_url: str,
    s3_bucket: str,
    log_s3_failure: bool = False,
) -> None:
    image_name = generate_short_uuid() + '.png'

    if sharing_url and s3_bucket:
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)

        url = upload_to_s3(buf.getvalue(), image_name)
        if url:
            image_url.append(url)
            logger.info(f"image_url: {image_url}")
        elif log_s3_failure:
            logger.error(f"Failed to upload image to S3: {image_name}")
    else:
        os.makedirs('contents', exist_ok=True)
        file_path = os.path.join('contents', image_name)
        plt.savefig(file_path, format='png', dpi=100, bbox_inches='tight')
        plt.close(fig)
        image_url.append(os.path.abspath(file_path))
        logger.info(
            "image_url (local fallback, sharing_url unset): %s", image_url
        )


def draw_stock_trend(
    trend: Dict[str, object],
    *,
    upload_to_s3,
    generate_short_uuid,
    sharing_url: str,
    s3_bucket: str,
) -> Dict[str, List[str]]:
    """Draw graphs of the given trend and return image paths/URLs."""
    logger.info(f"draw_stock_trend --> trend: {trend}")

    image_url: List[str] = []
    _configure_korean_font()

    points = trend.get("points", [])
    if not points:
        raise ValueError("trend does not contain points data.")

    company_name = cast(str, trend.get("company_name", "Stock"))
    ticker = cast(str, trend.get("ticker", ""))

    candle_df = _prepare_trend_series(cast(List[object], points), with_moving_averages=True)
    fig = _render_candlestick_chart(candle_df, company_name, ticker)
    _save_trend_chart_image(
        fig,
        image_url,
        upload_to_s3=upload_to_s3,
        generate_short_uuid=generate_short_uuid,
        sharing_url=sharing_url,
        s3_bucket=s3_bucket,
    )

    change_df = _prepare_trend_series(cast(List[object], points))
    fig2 = _render_change_percent_chart(change_df, company_name, ticker)
    _save_trend_chart_image(
        fig2,
        image_url,
        upload_to_s3=upload_to_s3,
        generate_short_uuid=generate_short_uuid,
        sharing_url=sharing_url,
        s3_bucket=s3_bucket,
    )

    close_df = _prepare_trend_series(cast(List[object], points))
    fig3 = _render_closing_price_chart(close_df, company_name, ticker)
    _save_trend_chart_image(
        fig3,
        image_url,
        upload_to_s3=upload_to_s3,
        generate_short_uuid=generate_short_uuid,
        sharing_url=sharing_url,
        s3_bucket=s3_bucket,
        log_s3_failure=True,
    )

    return {"path": image_url}
