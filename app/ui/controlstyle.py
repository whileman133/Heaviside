"""Application style tweak: give the short native form controls room to breathe.

On the macOS native style a ``QComboBox`` is ~32 px tall, but ``QLineEdit`` /
``QSpinBox`` / ``QDoubleSpinBox`` render at ~21 px (the compact macOS text-field
height) — so text fields and spin boxes look *squished*, especially next to the
taller combos. This :class:`QProxyStyle` wraps the platform style and floors just
those controls at a comfortable height. Only the size hint changes; the controls
keep their fully native rendering (unlike a stylesheet, which would de-nature
them). Applied process-wide, so every panel and dialog is consistent.
"""

from __future__ import annotations

from PySide6.QtWidgets import QProxyStyle, QStyle

#: Minimum height (px) for text fields, spin boxes, and combos. The native macOS
#: combo already exceeds this, so flooring it is a no-op there.
CONTROL_MIN_HEIGHT = 28

_BUMPED = (
    QStyle.ContentsType.CT_LineEdit,
    QStyle.ContentsType.CT_SpinBox,
    QStyle.ContentsType.CT_ComboBox,
)


class RoomyControlStyle(QProxyStyle):
    """Proxy style that floors the height of text fields / spin boxes / combos so
    the native macOS "small" controls don't render squished."""

    def sizeFromContents(self, ct, opt, size, widget):  # noqa: ANN001, N802
        s = super().sizeFromContents(ct, opt, size, widget)
        if ct in _BUMPED and s.height() < CONTROL_MIN_HEIGHT:
            s.setHeight(CONTROL_MIN_HEIGHT)
        return s


def install(app) -> None:  # noqa: ANN001
    """Wrap *app*'s current style so its form controls aren't squished. Call once,
    after the QApplication is created and before building the UI."""
    app.setStyle(RoomyControlStyle(app.style()))
