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


@pytest.fixture(autouse=True)
def _quiesce_render_pool():
    """Drain in-flight async label renders at the end of every test.

    ``render_async`` (``app/preview/mathrender``) typesets labels on a
    ``QThreadPool``. If a worker is still rendering (heavy Python object churn
    inside ziamath) when pytest-qt's teardown runs ``_process_events`` — which
    delivers a queued render result, repositions a canvas item, and can trigger a
    **garbage collection** on the UI thread — the GC walks the object graph while a
    worker concurrently mutates it, and the process segfaults. (Reliably masked
    until the deterministic palette refcount crash was fixed by requiring 3.12;
    this is the rare ~1/15 flake underneath it.)

    Waiting for the pool to finish and flushing the queued results here closes the
    window: the dispatch/GC happens while no worker is active. Runs as a fixture
    finalizer, i.e. before pytest-qt's ``_process_events`` teardown hook. Mirrors
    the app's own ``aboutToQuit`` pool drain. No-op when the pool was never used.
    """
    yield
    try:
        from app.preview import mathrender
    except Exception:  # noqa: BLE001 - nothing to drain if it didn't import
        return
    if not mathrender._pool.cache_info().currsize:
        return  # the render pool was never instantiated by this test
    mathrender._pool().waitForDone(5000)
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()  # deliver the now-queued render results
    except Exception:  # noqa: BLE001 - best-effort flush
        pass
