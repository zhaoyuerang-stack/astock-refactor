"""Factor computation utilities."""


def safe_zscore(df):
    """Row-wise z-score with epsilon guard."""
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1) + 1e-8, axis=0)


def mad_clip(df, n=5):
    """Row-wise MAD outlier clip."""
    med = df.median(axis=1)
    mad = df.sub(med, axis=0).abs().median(axis=1)
    return df.clip(lower=med - n * mad, upper=med + n * mad, axis=0)
