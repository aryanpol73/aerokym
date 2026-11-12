"""M0 companion: how much turbulence signal survives a given sampling interval?

A synthetic experiment, not a validation. It answers one narrow question: if
we only see an aircraft's ADS-B vertical rate every `dt` seconds, can
RMSVA-style detection still tell bumpy air from smooth air?

Model (assumptions, deliberately simple):
  * Vertical acceleration in turbulence = Gaussian noise band-limited to
    0.05-1 Hz (roughly how an airliner responds to gusts), scaled to an RMS
    in g: smooth 0.01, light 0.05, moderate 0.15, severe 0.30.
  * Vertical rate = integral of that acceleration; the autopilot holding
    altitude removes the slow drift (band lower edge).
  * ADS-B reports vertical rate in 64 ft/min steps.
  * We keep one sample every `dt` seconds and estimate
        accel = diff(vertical_rate) / dt,   RMSVA = rms(accel) over 60 s
    plus a coarser fallback: std(vertical_rate) over 5 min.

Output: recovered/true RMSVA ratio and AUC (smooth vs light, smooth vs
moderate). AUC 0.5 = coin flip, 1.0 = perfect separation.

Run:  python backend/spikes/m0_rate_sim.py
"""

from __future__ import annotations

import numpy as np

G = 9.80665
FPM = 0.00508            # 1 ft/min in m/s
QUANT = 64 * FPM         # ADS-B vertical-rate resolution
FS = 4                   # simulation rate, Hz
RMS_G = {"smooth": 0.01, "light": 0.05, "moderate": 0.15, "severe": 0.30}
rng = np.random.default_rng(7)


def vertical_rate(rms_g: float, seconds: int) -> np.ndarray:
    """Simulate quantised ADS-B vertical rate (m/s) at FS Hz."""
    n = seconds * FS
    freqs = np.fft.rfftfreq(n, 1 / FS)
    spec = np.fft.rfft(rng.standard_normal(n))
    spec[(freqs < 0.05) | (freqs > 1.0)] = 0          # band-limit the gusts
    accel = np.fft.irfft(spec, n)
    accel *= rms_g * G / accel.std()
    vr = np.cumsum(accel) / FS
    vr -= vr.mean()
    return np.round(vr / QUANT) * QUANT


def auc(neg: np.ndarray, pos: np.ndarray) -> float:
    """Probability a random bumpy window scores above a random smooth one."""
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def metrics(level: str, dt: float, trials: int = 300) -> tuple[np.ndarray, np.ndarray]:
    step = int(dt * FS)
    rmsva, vr_std = [], []
    for _ in range(trials):
        vr = vertical_rate(RMS_G[level], 300)[:: step]
        w = vr[: max(2, int(60 / dt) + 1)]             # 60 s window
        rmsva.append(np.sqrt(np.mean((np.diff(w) / dt) ** 2)) / G if len(w) > 1 else 0.0)
        vr_std.append(vr.std() / FPM)                  # 5 min window, ft/min
    return np.array(rmsva), np.array(vr_std)


def main() -> None:
    print(f"{'dt (s)':>7} {'samples/60s':>11} | {'RMSVA recovered (light)':>24} | "
          f"{'AUC RMSVA s/l':>13} {'s/m':>5} | {'AUC std(vr) s/l':>15} {'s/m':>5}")
    for dt in (1, 2, 5, 10, 15, 30, 60):
        s_a, s_v = metrics("smooth", dt)
        l_a, l_v = metrics("light", dt)
        m_a, m_v = metrics("moderate", dt)
        print(f"{dt:>7} {int(60 / dt) + 1:>11} | {100 * l_a.mean() / RMS_G['light']:>23.0f}% | "
              f"{auc(s_a, l_a):>13.2f} {auc(s_a, m_a):>5.2f} | {auc(s_v, l_v):>15.2f} {auc(s_v, m_v):>5.2f}")
    print("\nstd(vertical_rate) over 5 min, ft/min (median):",
          {k: round(float(np.median(metrics(k, 5)[1]))) for k in RMS_G})


if __name__ == "__main__":
    main()
