import sys
import logging
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from gui.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)


def main():
    # Set high DPI policy for modern 4K/high-res displays
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Fakin' The Funk - Fake Audio Inspector")
    app.setOrganizationName("HomeAudioTools")

    window = MainWindow()
    window.show()

    # If launched with CLI arguments (e.g. python app.py path/to/music)
    if len(sys.argv) > 1:
        initial_paths = [Path(arg).resolve() for arg in sys.argv[1:] if Path(arg).exists()]
        if initial_paths:
            window._start_scan(initial_paths)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
