#!/usr/bin/env python3
import sys
import os
import logging
from pathlib import Path
from typing import Optional


def _bootstrap_venv():
    """
    If running under system Python without required packages, automatically
    re-execute under the local virtual environment (.venv) if present.
    """
    try:
        import PySide6  # noqa: F401
        import av  # noqa: F401
    except ImportError:
        project_root = Path(__file__).resolve().parent
        for venv_dir in (".venv", "venv"):
            if sys.platform == "win32":
                venv_py = project_root / venv_dir / "Scripts" / "python.exe"
            else:
                venv_py = project_root / venv_dir / "bin" / "python"

            if venv_py.is_file() and os.access(venv_py, os.X_OK):
                if str(venv_py) != sys.executable:
                    print(f"Launching within virtual environment: {venv_py}")
                    os.execv(str(venv_py), [str(venv_py)] + sys.argv)


_bootstrap_venv()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent

from gui.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)


class AudioInspectorApp(QApplication):
    """
    Custom QApplication supporting macOS QFileOpenEvent
    (e.g. dropping files onto Dock icon, 'Open With...', etc.)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.main_window: Optional[MainWindow] = None

    def event(self, e):
        if e.type() == QEvent.Type.FileOpen:
            file_path = Path(e.file()).resolve()
            if self.main_window and file_path.exists():
                self.main_window._start_scan([file_path])
            return True
        return super().event(e)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: python3 app.py [OPTIONS] [AUDIO_FILE_OR_DIR...]")
        print("\nAudio Quality Inspector (macOS & Windows)")
        print("\nOptions:")
        print("  -h, --help       Show this help message and exit")
        print("  -v, --version    Show version information and exit")
        return 0

    if "--version" in sys.argv or "-v" in sys.argv:
        print("Audio Quality Inspector v1.0.0 (macOS & Windows)")
        return 0

    # Set high DPI policy for modern displays
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = AudioInspectorApp(sys.argv)
    # Using Fusion style guarantees consistent, dark-mode styling across macOS, Windows, and Linux
    app.setStyle("Fusion")
    app.setApplicationName("audio-quality-inspector")
    app.setApplicationDisplayName("Audio Quality Inspector")
    app.setOrganizationName("AudioQualityInspector")

    window = MainWindow()
    app.main_window = window
    window.show()

    # If launched with CLI arguments (e.g. python app.py path/to/music)
    if len(sys.argv) > 1:
        initial_paths = [Path(arg).resolve() for arg in sys.argv[1:] if Path(arg).exists()]
        if initial_paths:
            window._start_scan(initial_paths)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
