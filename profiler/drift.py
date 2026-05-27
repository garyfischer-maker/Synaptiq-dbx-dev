"""Distribution-drift metrics (milestone 4).

PSI and KS for numeric columns; PSI + Chi-square + JS divergence for
categorical columns. Binning happens in Spark; only the binned histograms
are collected to the driver.

Honest stubs in milestone 1.
"""

from __future__ import annotations


def psi(*args, **kwargs):
    raise NotImplementedError("psi — milestone 4.")


def ks_test(*args, **kwargs):
    raise NotImplementedError("ks_test — milestone 4.")


def chi_square(*args, **kwargs):
    raise NotImplementedError("chi_square — milestone 4.")


def js_divergence(*args, **kwargs):
    raise NotImplementedError("js_divergence — milestone 4.")
