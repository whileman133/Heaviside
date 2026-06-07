"""
Shared pytest configuration.

Defines the ``slow`` marker and a ``--run-slow`` opt-in. Tests marked ``slow``
(e.g. the full component-store reproducibility test, which re-renders every
symbol through ``latex``/``dvisvgm`` and dominates wall-clock) are **skipped by
default** so the local suite stays fast, and run only with::

    pytest --run-slow

CI passes ``--run-slow`` so the slow guarantees are still enforced there.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser) -> None:  # noqa: ANN001
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="run tests marked @pytest.mark.slow (skipped by default)",
    )


def pytest_collection_modifyitems(config, items) -> None:  # noqa: ANN001
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="slow; run with --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
