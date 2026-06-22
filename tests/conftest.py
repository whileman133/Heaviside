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
    """Drain in-flight async label renders at the end of every test (hygiene).

    The actual cross-thread render crash is fixed in ``app/preview/mathrender``:
    workers now produce only the Qt-free SVG and the UI thread builds the
    ``QPainterPath``, so no worker ever constructs Qt objects (verified — that
    alone took the rare aarch64 flake from ~1/15 to 0/30 with this fixture
    disabled). This fixture is **belt-and-suspenders**: it stops a worker's queued
    result from being delivered across a test boundary during pytest-qt teardown,
    keeping the suite deterministic and avoiding "QThread destroyed while running"
    noise. It mirrors the app's own ``aboutToQuit`` pool drain and is a no-op when
    the pool was never used. Runs as a fixture finalizer, before pytest-qt's
    ``_process_events`` teardown hook.
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


@pytest.fixture(autouse=True)
def _shutdown_preview_worker_threads():
    """Stop every ``PreviewWorker``'s background ``QThread`` at end of test.

    Constructing a ``MainWindow`` starts a ``PreviewWorker`` compile thread
    (``app/preview/worker.py``). The app stops it on ``closeEvent`` / ``aboutToQuit``,
    but most ``MainWindow`` tests neither close the window nor quit the app, so the
    thread is still running when Python later GCs the window — Qt then aborts with
    "QThread: Destroyed while thread is still running", an **intermittent CI
    segfault/abort**. (Not an off-main *construction* bug — the worker only produces
    Qt-free PDF bytes; this is teardown lifetime.)

    Patch ``PreviewWorker.__init__`` to record each worker **and its parent window**
    built during the test. Holding strong refs keeps both the worker and its owning
    window alive past the test's last local reference, so GC can't destroy a
    still-running thread before the finalizer calls the idempotent ``shutdown()``.
    Only patches when ``app.preview.worker`` is already imported (every GUI test
    imports it via ``MainWindow``), so non-GUI tests pay nothing. Independent of the
    ``_quiesce_render_pool`` finalizer above (a different thread pool)."""
    import sys

    mod = sys.modules.get("app.preview.worker")
    if mod is None:
        yield  # the worker was never imported; this test creates no compile thread
        return

    PreviewWorker = mod.PreviewWorker
    created: list = []
    orig_init = PreviewWorker.__init__

    def _tracked_init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        orig_init(self, *args, **kwargs)
        parent = args[0] if args else kwargs.get("parent")
        created.append((self, parent))   # hold both so neither GCs before shutdown

    PreviewWorker.__init__ = _tracked_init
    try:
        yield
    finally:
        PreviewWorker.__init__ = orig_init
        for worker, _parent in created:
            try:
                worker.shutdown()
            except (RuntimeError, AttributeError):
                pass  # already-deleted C++ object / partially constructed
