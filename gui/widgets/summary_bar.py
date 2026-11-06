from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QProgressBar, QFrame
)
from PySide6.QtCore import Qt


class StatCard(QFrame):
    def __init__(self, title: str, initial_value: str = "0", value_color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: #1e2026;
                border: 1px solid #2e323b;
                border-radius: 6px;
                padding: 4px 10px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #8c909a; font-size: 11px; font-weight: 500;")

        self.val_label = QLabel(initial_value)
        self.val_label.setStyleSheet(f"color: {value_color}; font-size: 14px; font-weight: bold;")

        layout.addWidget(self.title_label)
        layout.addWidget(self.val_label)

    def set_value(self, value: int | str):
        self.val_label.setText(str(value))


class SummaryBar(QWidget):
    """
    Bottom status and metrics bar showing aggregate counters and batch progress.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 8)
        main_layout.setSpacing(6)

        # Progress row
        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(10)

        self.status_label = QLabel("Ready. Select audio files or a folder to analyze.")
        self.status_label.setStyleSheet("color: #a4a8b2; font-size: 12px;")

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1c1e24;
                border: 1px solid #2e323b;
                border-radius: 6px;
            }
            QProgressBar::chunk {
                background-color: #3b82f6;
                border-radius: 5px;
            }
        """)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        progress_layout.addWidget(self.status_label, 1)
        progress_layout.addWidget(self.progress_bar, 1)

        # Metrics row
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(10)

        self.card_total = StatCard("TOTAL", "0", "#ffffff")
        self.card_genuine = StatCard("GENUINE", "0", "#2ecc71")
        self.card_fake = StatCard("FAKES / TRANSCODES", "0", "#ff5e57")
        self.card_suspicious = StatCard("SUSPICIOUS", "0", "#f1c40f")

        metrics_layout.addWidget(self.card_total)
        metrics_layout.addWidget(self.card_genuine)
        metrics_layout.addWidget(self.card_fake)
        metrics_layout.addWidget(self.card_suspicious)
        metrics_layout.addStretch(1)

        main_layout.addLayout(progress_layout)
        main_layout.addLayout(metrics_layout)

        self.reset_counts()

    def reset_counts(self):
        self.total = 0
        self.genuine = 0
        self.fakes = 0
        self.suspicious = 0
        self.update_card_displays()

    def update_card_displays(self):
        self.card_total.set_value(self.total)
        self.card_genuine.set_value(self.genuine)
        self.card_fake.set_value(self.fakes)
        self.card_suspicious.set_value(self.suspicious)

    def increment(self, category: str):
        self.total += 1
        if category == "genuine":
            self.genuine += 1
        elif category == "fake":
            self.fakes += 1
        elif category == "suspicious":
            self.suspicious += 1
        self.update_card_displays()

    def set_progress(self, current: int, total: int, current_file: str):
        if not self.progress_bar.isVisible():
            self.progress_bar.setVisible(True)
        percent = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"Analyzing ({current}/{total}): {current_file}")

    def set_status(self, text: str):
        self.status_label.setText(text)

    def finish_scan(self, total: int, fakes: int):
        self.progress_bar.setValue(100)
        self.status_label.setText(f"Scan complete. Audited {total} tracks, found {fakes} fake / transcoded files.")
