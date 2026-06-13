"""The form-control height fix (app/ui/controlstyle)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QStyleFactory,
)


@pytest.fixture
def _app():
    return QApplication.instance() or QApplication([])


def test_roomy_style_floors_short_form_controls(_app):
    """The proxy style raises the squished native text fields / spin boxes to
    CONTROL_MIN_HEIGHT, while leaving taller controls (combos) alone."""
    from app.ui.controlstyle import RoomyControlStyle, CONTROL_MIN_HEIGHT, install

    base_name = _app.style().objectName()
    try:
        install(_app)
        for cls in (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox):
            assert cls().sizeHint().height() >= CONTROL_MIN_HEIGHT, cls.__name__
        # The proxy wraps (does not replace) the platform style.
        assert isinstance(_app.style(), RoomyControlStyle)
    finally:
        _app.setStyle(QStyleFactory.create(base_name or "Fusion"))


def test_install_is_idempotent_enough(_app):
    """Installing wraps the current style; a second install just re-wraps. The
    floor still holds and nothing raises."""
    from app.ui.controlstyle import install, CONTROL_MIN_HEIGHT

    base_name = _app.style().objectName()
    try:
        install(_app)
        install(_app)
        assert QLineEdit().sizeHint().height() >= CONTROL_MIN_HEIGHT
    finally:
        _app.setStyle(QStyleFactory.create(base_name or "Fusion"))
