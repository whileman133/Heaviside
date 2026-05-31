"""
Heaviside entry point (spec §11).

Creates the QApplication, shows the MainWindow, and starts the Qt event loop.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.ui.mainwindow import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Heaviside")
    app.setOrganizationName("Heaviside")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
