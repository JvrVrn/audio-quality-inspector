from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLineEdit, QLabel, QButtonGroup
)
from PySide6.QtCore import Signal, Qt


class FilterBar(QWidget):
    """
    Search, category filtering, and folder grouping toolbar.
    """
    sig_filter_category = Signal(object)  # None, "fake", "genuine", "suspicious"
    sig_search_changed = Signal(str)
    sig_group_by_folder_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Category buttons
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self.btn_all = QPushButton("All")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)

        self.btn_fakes = QPushButton("🔴 Fakes Only")
        self.btn_fakes.setCheckable(True)

        self.btn_genuine = QPushButton("🟢 Genuine Only")
        self.btn_genuine.setCheckable(True)

        self.btn_suspicious = QPushButton("🟡 Suspicious Only")
        self.btn_suspicious.setCheckable(True)

        button_style = """
            QPushButton {
                background-color: #20232a;
                color: #c8ccd4;
                border: 1px solid #333742;
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2b2f3a;
                border-color: #4a5060;
            }
            QPushButton:checked {
                background-color: #3b82f6;
                color: #ffffff;
                border-color: #3b82f6;
            }
        """
        for btn in (self.btn_all, self.btn_fakes, self.btn_genuine, self.btn_suspicious):
            btn.setStyleSheet(button_style)
            self.btn_group.addButton(btn)
            layout.addWidget(btn)

        self.btn_all.clicked.connect(lambda: self.sig_filter_category.emit(None))
        self.btn_fakes.clicked.connect(lambda: self.sig_filter_category.emit("fake"))
        self.btn_genuine.clicked.connect(lambda: self.sig_filter_category.emit("genuine"))
        self.btn_suspicious.clicked.connect(lambda: self.sig_filter_category.emit("suspicious"))

        layout.addSpacing(10)

        # Group by Folder toggle button
        self.btn_group_folder = QPushButton("📁  Group by Folder")
        self.btn_group_folder.setCheckable(True)
        self.btn_group_folder.setChecked(False)
        self.btn_group_folder.setToolTip("Organize files into expandable folder groups")
        self.btn_group_folder.setStyleSheet("""
            QPushButton {
                background-color: #20232a;
                color: #c8ccd4;
                border: 1px solid #333742;
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2b2f3a;
                border-color: #4a5060;
            }
            QPushButton:checked {
                background-color: #6366f1;
                color: #ffffff;
                border-color: #6366f1;
            }
        """)
        self.btn_group_folder.toggled.connect(self.sig_group_by_folder_toggled.emit)
        layout.addWidget(self.btn_group_folder)

        layout.addSpacing(12)

        # Search box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search by file name, format, folder, reason...")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1a1c22;
                color: #ffffff;
                border: 1px solid #30343e;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #3b82f6;
            }
        """)
        self.search_input.textChanged.connect(self.sig_search_changed.emit)
        layout.addWidget(self.search_input, 1)

    def set_group_by_folder(self, active: bool):
        self.btn_group_folder.blockSignals(True)
        self.btn_group_folder.setChecked(active)
        self.btn_group_folder.blockSignals(False)

    def reset_filters(self):
        self.btn_all.setChecked(True)
        self.search_input.clear()
        self.btn_group_folder.setChecked(False)
