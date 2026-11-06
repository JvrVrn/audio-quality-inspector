from typing import Optional
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import numpy as np

from core.models import AnalysisResult, SpectrogramData


class SpectrogramWidget(QWidget):
    """
    Renders an interactive high-resolution audio spectrogram with frequency reference guides.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(8, 3.2), facecolor="#181a20")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setStyleSheet("background-color: #181a20;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

        self.current_result: Optional[AnalysisResult] = None
        self.show_placeholder()

    def show_placeholder(self, message: str = "Select a track from the table to view its frequency spectrogram"):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("#181a20")
        ax.text(
            0.5, 0.5, message,
            color="#8c909a", fontsize=12,
            ha="center", va="center", transform=ax.transAxes
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def display_result(self, result: AnalysisResult):
        self.current_result = result
        spec_data = result.spectrogram

        if not spec_data or spec_data.spec_db is None or len(spec_data.frequencies) == 0:
            self.show_placeholder(f"Spectrogram not available for {result.track_info.file_name}")
            return

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("#121316")

        # Convert frequency to kHz
        freqs_khz = spec_data.frequencies / 1000.0
        cutoff_khz = spec_data.cutoff_hz / 1000.0
        nyquist_khz = (spec_data.sample_rate / 2.0) / 1000.0

        times = spec_data.times
        extent = [times[0], times[-1], freqs_khz[0], freqs_khz[-1]]

        # Render spectrogram heatmap
        # Dynamic range from -90 dB to 0 dB
        im = ax.imshow(
            spec_data.spec_db,
            extent=extent,
            origin="lower",
            aspect="auto",
            cmap="inferno",
            vmin=-85,
            vmax=0
        )

        # Reference horizontal threshold markers
        ax.axhline(15.0, color="#ff4757", linestyle=":", linewidth=1.2, alpha=0.8, label="15 kHz (128k MP3)")
        ax.axhline(19.0, color="#ffa502", linestyle=":", linewidth=1.2, alpha=0.8, label="19 kHz (192k MP3)")
        ax.axhline(20.5, color="#eccc68", linestyle=":", linewidth=1.2, alpha=0.8, label="20.5 kHz (320k MP3)")
        if nyquist_khz >= 22.0:
            ax.axhline(22.05, color="#2ed573", linestyle="--", linewidth=1.2, alpha=0.8, label="22.05 kHz (CD Nyquist)")

        # Detected Cutoff marker
        ax.axhline(
            cutoff_khz,
            color="#00d2d3",
            linestyle="-",
            linewidth=2.0,
            alpha=0.9,
            label=f"Detected Cutoff ({cutoff_khz:.1f} kHz)"
        )

        ax.set_ylim(0, min(24.0, nyquist_khz * 1.02))
        ax.set_xlabel("Time (seconds)", color="#c8ccd4", fontsize=10)
        ax.set_ylabel("Frequency (kHz)", color="#c8ccd4", fontsize=10)
        ax.tick_params(colors="#9aa0a6", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#30343e")

        ax.set_title(
            f"{result.track_info.file_name}  —  {result.verdict_text}  (Cutoff: {cutoff_khz:.1f} kHz)",
            color="#ffffff", fontsize=11, fontweight="bold", pad=8
        )

        # Legend and colorbar
        leg = ax.legend(loc="upper right", facecolor="#1e2028", edgecolor="#30343e", fontsize=8, labelcolor="#e1e3e6")
        cbar = self.figure.colorbar(im, ax=ax, pad=0.015, fraction=0.035)
        cbar.set_label("Magnitude (dB)", color="#9aa0a6", fontsize=9)
        cbar.ax.tick_params(colors="#9aa0a6", labelsize=8)
        cbar.outline.set_edgecolor("#30343e")

        self.figure.tight_layout()
        self.canvas.draw_idle()
