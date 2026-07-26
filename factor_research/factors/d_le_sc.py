"""directed Likelihood Estimation Spectral Clustering (d-LE-SC) Factor.

Decomposes daily returns into overnight and daytime components,
builds a directed lead-lag correlation network, and clusters stocks
into Leaders and Laggers using Hermitian Spectral Clustering.
"""

import logging

import numpy as np
import pandas as pd
import scipy.linalg
import scipy.sparse.linalg
import torch
from pandas.core.algorithms import rank as pandas_rank
from sklearn.cluster import KMeans

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core Correlation Utilities (Optimized)
# ---------------------------------------------------------------------------

def compute_pearson_matrix(lead_returns: np.ndarray, lag_returns: np.ndarray) -> np.ndarray:
    """Computes the cross-correlation matrix between lead and lag returns (Pearson)."""
    T, N = lead_returns.shape
    if T <= 1:
        return np.zeros((N, N))

    lead_mean = np.mean(lead_returns, axis=0, keepdims=True)
    lead_std = np.std(lead_returns, axis=0, ddof=1, keepdims=True) + 1e-8
    lead_normalized = (lead_returns - lead_mean) / lead_std

    lag_mean = np.mean(lag_returns, axis=0, keepdims=True)
    lag_std = np.std(lag_returns, axis=0, ddof=1, keepdims=True) + 1e-8
    lag_normalized = (lag_returns - lag_mean) / lag_std

    M = (lead_normalized.T @ lag_normalized) / (T - 1)
    return np.clip(M, -1.0, 1.0)


def compute_spearman_matrix(lead_returns: np.ndarray, lag_returns: np.ndarray) -> np.ndarray:
    """Computes the cross-correlation matrix between lead and lag returns (Spearman)."""
    try:
        lead_ranks = pandas_rank(
            lead_returns, axis=0, method="average", na_option="keep", ascending=True, pct=False
        )
        lag_ranks = pandas_rank(
            lag_returns, axis=0, method="average", na_option="keep", ascending=True, pct=False
        )
    except Exception:
        from scipy.stats import rankdata
        lead_ranks = np.apply_along_axis(rankdata, 0, lead_returns)
        lag_ranks = np.apply_along_axis(rankdata, 0, lag_returns)

    return compute_pearson_matrix(lead_ranks, lag_ranks)


# ---------------------------------------------------------------------------
# d-LE-SC Clustering Engine (SciPy/CPU & PyTorch/CUDA Hybrid)
# ---------------------------------------------------------------------------

class DLESCClustering:
    """d-LE-SC (directed Likelihood Estimation Spectral Clustering) implementation."""

    def __init__(
        self,
        n_iterations: int = 10,
        random_state: int = 42,
        tol: float = 1e-6,
        device: str | None = None,
    ):
        self.n_iterations = n_iterations
        self.random_state = random_state
        self.tol = tol

        # Determine backend: use 'torch' only for CUDA GPU to avoid MPS complex number issues
        if device is None:
            if torch.cuda.is_available():
                self.device = "cuda"
                self.backend = "torch"
            else:
                self.device = "cpu"
                self.backend = "scipy"
        else:
            if "cuda" in device:
                self.device = device
                self.backend = "torch"
            else:
                self.device = "cpu"
                self.backend = "scipy"

        logger.debug(f"DLESCClustering initialized with backend: {self.backend} on device: {self.device}")

        self.eta = None
        self.lead_cluster = None
        self.lag_cluster = None

        if self.backend == "torch":
            self.torch_device = torch.device(self.device)
            torch.manual_seed(self.random_state)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(self.random_state)
            self.kmeans_seed = self.random_state + 1000
        else:
            self.kmeans_seed = self.random_state

    # ---- PyTorch Backend Functions ----
    def _compute_hermitian_matrix_torch(self, A: torch.Tensor) -> torch.Tensor:
        eta_tensor = torch.tensor(self.eta, device=self.torch_device, dtype=torch.float32)
        eta_clipped = torch.clamp(eta_tensor, min=1e-10, max=0.5 - 1e-10)
        w_directional = torch.log((1 - eta_clipped) / eta_clipped)
        w_symmetric = torch.log(1 / (4 * eta_clipped * (1 - eta_clipped)))

        A_diff = A - A.T
        A_sum = A + A.T
        H_real = w_symmetric * A_sum
        H_imag = w_directional * A_diff
        return torch.complex(H_real, H_imag)

    def _compute_top_eigenvector_torch(self, H: torch.Tensor) -> torch.Tensor:
        n = H.shape[0]
        I = torch.eye(n, dtype=H.dtype, device=self.torch_device)
        try:
            H_reg = H + 1e-7 * I
            eigenvalues, eigenvectors = torch.linalg.eigh(H_reg)
            top_idx = torch.argmax(eigenvalues.real)
            return eigenvectors[:, top_idx]
        except RuntimeError:
            U, S, Vh = torch.linalg.svd(H)
            return U[:, 0]

    def _kmeans_pytorch(self, X: torch.Tensor) -> torch.Tensor:
        n_samples, n_features = X.shape
        torch.manual_seed(self.kmeans_seed)
        indices = torch.randint(0, n_samples, (1,), device=self.torch_device)
        centroids = X[indices]

        for _ in range(1, 2):
            distances = torch.cdist(X, centroids).min(dim=1)[0]
            probabilities = distances**2 + 1e-10
            probabilities = probabilities / probabilities.sum()
            next_idx = torch.multinomial(probabilities, 1)
            centroids = torch.cat([centroids, X[next_idx]], dim=0)

        for _ in range(50):
            distances = torch.cdist(X, centroids)
            labels = torch.argmin(distances, dim=1)
            new_centroids = torch.stack(
                [
                    X[labels == k].mean(dim=0) if (labels == k).any() else centroids[k]
                    for k in range(2)
                ]
            )
            if torch.allclose(centroids, new_centroids, atol=1e-6):
                break
            centroids = new_centroids
        return labels

    # ---- SciPy Backend Functions ----
    def _compute_hermitian_matrix_scipy(self, A: np.ndarray) -> np.ndarray:
        eta_clipped = np.clip(self.eta, 1e-10, 0.5 - 1e-10)
        w_directional = np.log((1 - eta_clipped) / eta_clipped)
        w_symmetric = np.log(1.0 / (4.0 * eta_clipped * (1.0 - eta_clipped)))

        A_diff = A - A.T
        A_sum = A + A.T
        H_real = w_symmetric * A_sum
        H_imag = w_directional * A_diff
        return H_real + 1j * H_imag

    def _compute_top_eigenvector_scipy(self, H: np.ndarray) -> np.ndarray:
        try:
            eigenvalues, eigenvectors = scipy.sparse.linalg.eigsh(H, k=1, which="LR", maxiter=200)
            return eigenvectors[:, 0]
        except Exception:
            try:
                eigenvalues, eigenvectors = scipy.linalg.eigh(H)
                top_idx = np.argmax(eigenvalues)
                return eigenvectors[:, top_idx]
            except Exception:
                U, S, Vh = scipy.linalg.svd(H)
                return U[:, 0]

    # ---- Main Fit Flow ----
    def fit_single(self, A: np.ndarray) -> dict[str, np.ndarray]:
        """Runs the complete d-LE-SC algorithm on a single directed adjacency matrix."""
        self.eta = 0.25

        if self.backend == "torch":
            A_tensor = torch.from_numpy(A).float().to(self.torch_device)
            for _ in range(self.n_iterations):
                old_eta = self.eta
                H = self._compute_hermitian_matrix_torch(A_tensor)
                v1 = self._compute_top_eigenvector_torch(H)
                embedding = torch.stack([v1.real, v1.imag], dim=1)
                labels = self._kmeans_pytorch(embedding)
                
                self.lead_cluster = torch.where(labels == 0)[0]
                self.lag_cluster = torch.where(labels == 1)[0]

                # Determine direction
                if len(self.lead_cluster) > 0 and len(self.lag_cluster) > 0:
                    i_idx = self.lead_cluster.unsqueeze(1).expand(-1, len(self.lag_cluster))
                    j_idx = self.lag_cluster.unsqueeze(0).expand(len(self.lead_cluster), -1)
                    net_flow = torch.sum(A_tensor[i_idx, j_idx]) - torch.sum(A_tensor[j_idx, i_idx])
                    if net_flow < 0:
                        self.lead_cluster, self.lag_cluster = self.lag_cluster, self.lead_cluster

                    # Update eta
                    total_flow = torch.sum(A_tensor[i_idx, j_idx]) + torch.sum(A_tensor[j_idx, i_idx])
                    if total_flow > 0:
                        flow_lead_to_lag = torch.sum(A_tensor[self.lead_cluster.unsqueeze(1).expand(-1, len(self.lag_cluster)), self.lag_cluster.unsqueeze(0).expand(len(self.lead_cluster), -1)])
                        flow_lag_to_lead = torch.sum(A_tensor[self.lag_cluster.unsqueeze(1).expand(-1, len(self.lead_cluster)), self.lead_cluster.unsqueeze(0).expand(len(self.lead_cluster), -1)])
                        new_eta = torch.min(flow_lead_to_lag / total_flow, flow_lag_to_lead / total_flow)
                        self.eta = float(torch.clamp(new_eta, min=1e-10, max=0.5 - 1e-10).cpu())

                if abs(self.eta - old_eta) < self.tol:
                    break

            return {
                "lead_cluster": self.lead_cluster.cpu().numpy(),
                "lag_cluster": self.lag_cluster.cpu().numpy(),
                "eta": self.eta,
            }

        else:
            # SciPy/CPU Backend
            for _ in range(self.n_iterations):
                old_eta = self.eta
                H = self._compute_hermitian_matrix_scipy(A)
                v1 = self._compute_top_eigenvector_scipy(H)
                embedding = np.column_stack([v1.real, v1.imag])

                # Use sklearn KMeans
                kmeans = KMeans(n_clusters=2, random_state=self.kmeans_seed, n_init=1).fit(embedding)
                labels = kmeans.labels_

                self.lead_cluster = np.where(labels == 0)[0]
                self.lag_cluster = np.where(labels == 1)[0]

                # Determine direction
                if len(self.lead_cluster) > 0 and len(self.lag_cluster) > 0:
                    flow_lead_to_lag = np.sum(A[np.ix_(self.lead_cluster, self.lag_cluster)])
                    flow_lag_to_lead = np.sum(A[np.ix_(self.lag_cluster, self.lead_cluster)])
                    net_flow = flow_lead_to_lag - flow_lag_to_lead
                    if net_flow < 0:
                        self.lead_cluster, self.lag_cluster = self.lag_cluster, self.lead_cluster
                        flow_lead_to_lag, flow_lag_to_lead = flow_lag_to_lead, flow_lead_to_lag

                    # Update eta
                    total_flow = flow_lead_to_lag + flow_lag_to_lead
                    if total_flow > 0:
                        new_eta = min(flow_lead_to_lag / total_flow, flow_lag_to_lead / total_flow)
                        self.eta = np.clip(new_eta, 1e-10, 0.5 - 1e-10)

                if abs(self.eta - old_eta) < self.tol:
                    break

            return {
                "lead_cluster": self.lead_cluster,
                "lag_cluster": self.lag_cluster,
                "eta": self.eta,
            }


# ---------------------------------------------------------------------------
# Factor Calculation Logic
# ---------------------------------------------------------------------------

def build_d_le_sc_factor(
    panels: dict,
    universe_size: int = 800,
    lookback: int = 60,
    network_type: str = "overnight_lead_daytime",
    correlation_method: str = "pearson",
    n_iterations: int = 10,
    lead_percentile: float = 0.5,
    random_state: int = 42,
    rebalance_days: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Computes the d-LE-SC factor values.

    Parameters:
        panels: Dictionary containing pivoted price tables. Must contain 'open' and 'close'.
        universe_size: Number of stocks to filter in the universe by average trading amount.
        lookback: Sliding window size for rolling correlation network.
        network_type: 'overnight_lead_daytime', 'daytime_lead_overnight', or 'preclose_lead_close'.
        correlation_method: 'pearson' or 'spearman'.
        n_iterations: Number of d-LE-SC iterations.
        lead_percentile: Percentile of top leaders to construct overnight signal.
        rebalance_days: Stepping interval to compute factors (huge speedup!).

    Returns:
        factor: DataFrame of shape (date, code) containing expected return of Laggers.
        univ: Boolean DataFrame of shape (date, code) representing the universe.
    """
    open_px = panels["open"]
    close = panels["close"]
    amount = panels["amount"]
    raw_close = panels.get("raw_close", close)

    # 1. Decompose Returns based on network_type
    close_prev = close.shift(1)
    daily_return = close / close_prev - 1.0
    
    if network_type == "overnight_lead_daytime":
        lead_returns = open_px / close_prev - 1.0
        lag_returns = close / open_px - 1.0
    elif network_type == "daytime_lead_overnight":
        lead_returns = (close / open_px - 1.0).shift(1)
        lag_returns = open_px / close_prev - 1.0
    elif network_type == "preclose_lead_close":
        lead_returns = daily_return.shift(1)
        lag_returns = daily_return
    else:
        raise ValueError(f"Unknown network_type: {network_type}")

    # Fill NaNs with 0
    lead_clean = lead_returns.fillna(0.0).values
    lag_clean = lag_returns.fillna(0.0).values

    # 2. Build Universe (Top N by average amount)
    avg_amount = amount.rolling(20).mean() * raw_close
    univ = avg_amount.rank(axis=1, ascending=False, pct=False) <= universe_size

    trade_dates = close.index
    stock_codes = close.columns
    N = len(stock_codes)
    T = len(trade_dates)

    # Output factor DataFrame (initialized to NaN)
    factor_values = np.full((T, N), np.nan)

    # Initialize clustering model
    clustering_model = DLESCClustering(n_iterations=n_iterations, random_state=random_state)
    corr_func = compute_pearson_matrix if correlation_method == "pearson" else compute_spearman_matrix

    logger.info(f"Computing d-LE-SC factor over {T} dates with window {lookback} on device {clustering_model.device}...")
    logger.info(f"Using network_type={network_type}, correlation_method={correlation_method}, rebalance_days={rebalance_days}")

    # Iterate over sliding windows on a rebalance grid
    for t in range(lookback, T):
        # Skip calculation on non-rebalance days to speed up
        if (t - lookback) % rebalance_days != 0 and t != T - 1:
            continue

        # Slice rolling returns for active universe on day t
        active_mask = univ.iloc[t].values
        active_indices = np.where(active_mask)[0]

        if len(active_indices) < 10:
            continue

        lead_slice = lead_clean[t - lookback + 1 : t + 1, active_indices]
        lag_slice = lag_clean[t - lookback + 1 : t + 1, active_indices]

        # Calculate lead-lag correlation matrix M
        M = corr_func(lead_slice, lag_slice)
        A = np.abs(M)

        # Run d-LE-SC clustering
        try:
            res = clustering_model.fit_single(A)
        except Exception as e:
            logger.warning(f"Clustering failed on index {t} / date {trade_dates[t]}: {e}")
            continue

        lead_idx = res["lead_cluster"]
        lag_idx = res["lag_cluster"]

        if len(lead_idx) == 0 or len(lag_idx) == 0:
            continue

        # Compute Leader Scores (absolute sum of lead correlations)
        lead_scores = np.sum(A[lead_idx], axis=1)
        # Sort and select top leaders
        n_lead_top = max(1, int(len(lead_idx) * lead_percentile))
        top_lead_sub_idx = np.argsort(lead_scores)[::-1][:n_lead_top]
        top_lead_indices = lead_idx[top_lead_sub_idx]

        # Compute Signal: mean lead return on day t
        current_lead_ret = lead_slice[-1, top_lead_indices]
        signal = np.mean(current_lead_ret)

        # Compute Lagger Scores (signed sum of lead-lag correlations from leaders)
        lag_sub = M[np.ix_(lead_idx, lag_idx)]
        lag_scores = np.sum(lag_sub, axis=0)

        # Expected daytime return on day t for each lagger: Sign(signal) * lagger_score
        pred_returns = np.sign(signal) * lag_scores

        # Fill into output factor
        global_lag_indices = active_indices[lag_idx]
        factor_values[t, global_lag_indices] = pred_returns

        if (t + 1) % 100 == 0:
            logger.info(f"Processed {t + 1} / {T} dates")

    factor_df = pd.DataFrame(factor_values, index=trade_dates, columns=stock_codes)
    return factor_df, univ
