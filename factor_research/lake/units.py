"""Canonical physical units for A-share daily price data."""

from dataclasses import dataclass
from typing import ClassVar

import pandas as pd


@dataclass(frozen=True)
class PriceUnitContract:
    """Data-lake units shared by every board and price-data source."""

    volume: ClassVar[str] = "share"
    amount: ClassVar[str] = "CNY"
    raw_close: ClassVar[str] = "CNY_per_share"


def implied_amount(volume: pd.DataFrame, raw_close: pd.DataFrame) -> pd.DataFrame:
    """Return traded amount in CNY from shares and unadjusted CNY/share prices."""

    aligned_raw = raw_close.reindex(index=volume.index, columns=volume.columns)
    return volume * aligned_raw


def amount_ratio(
    volume: pd.DataFrame,
    raw_close: pd.DataFrame,
    amount: pd.DataFrame,
) -> pd.DataFrame:
    """Return stored amount divided by the amount implied by canonical units."""

    implied = implied_amount(volume, raw_close).replace(0.0, float("nan"))
    return amount.reindex(index=implied.index, columns=implied.columns) / implied
