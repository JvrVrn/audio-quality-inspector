import sys
import subprocess
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTreeView, QFileDialog, QMessageBox, QSplitter, QComboBox,
    QLabel, QHeaderView, QMenu, QFrame, QApplication
)
from PySide6.QtCore import Qt, QItemSelection, QModelIndex
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QAction, QClipboard

from core.models import AnalysisResult, TrackInfo
from core.metadata import SUPPORTED_EXTENSIONS
from gui.table_model import AudioTreeModel, AudioFilterProxyModel, TrackNode, FolderNode
from gui.spectrogram import SpectrogramWidget
from gui.worker import BatchScanWorker
from gui.widgets.summary_bar import SummaryBar
from gui.widgets.filter_bar import FilterBar
from exporters.report_exporter import export_to_csv, export_to_json, export_to_html


def reveal_in_explorer(target_path: Path):
    """Opens Windows Explorer and reveals the target file or folder."""
    target_path = Path(target_path).resolve()
    try:
        if target_path.is_file() and target_path.exists():
            subprocess.Popen(["explorer", f"/select,{target_path}"])
        elif target_path.is_dir() and target_path.exists():
            subprocess.Popen(["explorer", str(target_path)])
        elif target_path.parent.exists():
            subprocess.Popen(["explorer", str(target_path.parent)])
    except Exception as e:
        print(f"Error opening Explorer for {target_path}: {e}")


class MainWindow(QMainWindow):
    """
    Main Application Window for Fakin' The Funk Audio Quality Inspector.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fakin' The Funk — Fake Audio & Bitrate Quality Inspector")
        self.resize(1280, 840)
        self.setAcceptDrops(True)

        self.worker: Optional[BatchScanWorker] = None
        self.selected_file_path: Optional[Path] = None
        self.selected_folder_path: Optional[Path] = None

        self._setup_ui()
        self._apply_dark_theme()

    def _setup_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 12, 12, 8)
        main_layout.setSpacing(8)

        # 1. Top Controls Bar
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)

        # Action Buttons
        self.btn_open_folder = QPushButton("📂  Scan Folder...")
        self.btn_open_files = QPushButton("📄  Scan Files...")
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        self.btn_open_files.clicked.connect(self._on_open_files)

        # Scan Mode Selector
        lbl_mode = QLabel("Scan Mode:")
        lbl_mode.setStyleSheet("color: #9aa0a6; font-size: 12px; font-weight: 500;")
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("⚡ Fast Scan (Sampled 30s)", "fast")
        self.combo_mode.addItem("🔍 Deep Scan (Full Track)", "deep")
        self.combo_mode.setToolTip(
            "Fast Mode samples active musical segments for maximum batch scanning speed.\n"
            "Deep Mode decodes the entire file from start to finish."
        )

        # Pause / Cancel buttons
        self.btn_pause = QPushButton("⏸️ Pause")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)

        self.btn_cancel = QPushButton("⏹️ Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_scan)

        # Dedicated "Go to Folder" Toolbar Button
        self.btn_go_to_folder = QPushButton("📁  Go to Folder")
        self.btn_go_to_folder.setEnabled(False)
        self.btn_go_to_folder.setToolTip("Open the folder of the selected file in Windows Explorer (Ctrl+O)")
        self.btn_go_to_folder.clicked.connect(self._on_go_to_folder)

        # Export Report Button
        self.btn_export = QPushButton("📊  Export Report ▼")
        export_menu = QMenu(self)
        action_csv = export_menu.addAction("Export as CSV (.csv)")
        action_json = export_menu.addAction("Export as JSON (.json)")
        action_html = export_menu.addAction("Export as HTML Report (.html)")
        action_csv.triggered.connect(lambda: self._export_report("csv"))
        action_json.triggered.connect(lambda: self._export_report("json"))
        action_html.triggered.connect(lambda: self._export_report("html"))
        self.btn_export.setMenu(export_menu)

        # Clear button
        self.btn_clear = QPushButton("🗑️ Clear")
        self.btn_clear.clicked.connect(self._clear_all)

        controls_layout.addWidget(self.btn_open_folder)
        controls_layout.addWidget(self.btn_open_files)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(lbl_mode)
        controls_layout.addWidget(self.combo_mode)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(self.btn_pause)
        controls_layout.addWidget(self.btn_cancel)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(self.btn_go_to_folder)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.btn_export)
        controls_layout.addWidget(self.btn_clear)

        main_layout.addLayout(controls_layout)

        # 2. Filter & Search Bar
        self.filter_bar = FilterBar()
        self.filter_bar.sig_filter_category.connect(self._on_category_filter_changed)
        self.filter_bar.sig_search_changed.connect(self._on_search_text_changed)
        self.filter_bar.sig_group_by_folder_toggled.connect(self._on_group_by_folder_toggled)
        main_layout.addWidget(self.filter_bar)

        # 3. Central Vertical Splitter (Tree/Table on top, Spectrogram on bottom)
        self.splitter = QSplitter(Qt.Orientation.Vertical)

        # Setup Tree / Table Model
        self.table_model = AudioTreeModel(self)
        self.proxy_model = AudioFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)

        # Use QTreeView for folder hierarchical grouping support
        self.tree_view = QTreeView()
        self.table_view = self.tree_view  # Alias for backward compatibility
        self.tree_view.selectRow = lambda r: self.tree_view.setCurrentIndex(self.proxy_model.index(r, 0))
        self.tree_view.setModel(self.proxy_model)
        self.tree_view.setSortingEnabled(True)
        self.tree_view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.tree_view.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setAnimated(True)
        self.tree_view.setExpandsOnDoubleClick(True)
        self.tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree_view.doubleClicked.connect(self._on_item_double_clicked)

        header = self.tree_view.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # File name expands
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch) # Explanation expands
        self.tree_view.setColumnWidth(0, 140) # Status / Folder
        self.tree_view.setColumnWidth(2, 190) # Verdict
        self.tree_view.setColumnWidth(3, 100) # Cutoff
        self.tree_view.setColumnWidth(4, 95)  # Est Quality
        self.tree_view.setColumnWidth(5, 75)  # Format
        self.tree_view.setColumnWidth(6, 90)  # Bitrate
        self.tree_view.setColumnWidth(7, 70)  # Duration
        self.tree_view.setColumnWidth(8, 80)  # Size
        self.tree_view.setColumnWidth(9, 130) # Folder

        self.tree_view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.splitter.addWidget(self.tree_view)

        # 4. Bottom Inspector Container (Track Header + Spectrogram Viewer)
        inspector_container = QWidget()
        inspector_layout = QVBoxLayout(inspector_container)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(4)

        # Track Inspector Header Card
        self.inspector_header = QFrame()
        self.inspector_header.setStyleSheet("""
            QFrame {
                background-color: #1a1c22;
                border: 1px solid #282a32;
                border-radius: 6px;
                padding: 4px 10px;
            }
        """)
        header_layout = QHBoxLayout(self.inspector_header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(10)

        self.lbl_selected_title = QLabel("Select an audio track to inspect its frequency spectrum")
        self.lbl_selected_title.setStyleSheet("color: #e1e3e6; font-size: 12px; font-weight: 600;")

        self.lbl_selected_folder = QLabel("")
        self.lbl_selected_folder.setStyleSheet("color: #8c909a; font-size: 11px;")

        self.btn_inspector_go_to_folder = QPushButton("📁  Go to Folder")
        self.btn_inspector_go_to_folder.setEnabled(False)
        self.btn_inspector_go_to_folder.setStyleSheet("""
            QPushButton {
                background-color: #2b303c;
                color: #ffffff;
                border: 1px solid #3d4354;
                border-radius: 5px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3b4253;
                border-color: #50586c;
            }
        """)
        self.btn_inspector_go_to_folder.clicked.connect(self._on_go_to_folder)

        header_layout.addWidget(self.lbl_selected_title)
        header_layout.addWidget(self.lbl_selected_folder, 1)
        header_layout.addWidget(self.btn_inspector_go_to_folder)

        inspector_layout.addWidget(self.inspector_header)

        # Setup Spectrogram Viewer
        self.spectrogram_widget = SpectrogramWidget()
        inspector_layout.addWidget(self.spectrogram_widget, 1)

        self.splitter.addWidget(inspector_container)

        # Splitter ratio: 55% tree, 45% inspector
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        main_layout.addWidget(self.splitter, 1)

        # 5. Summary & Progress Bar
        self.summary_bar = SummaryBar()
        main_layout.addWidget(self.summary_bar)

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #121316;
            }
            QWidget {
                color: #e1e3e6;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 12px;
            }
            QPushButton {
                background-color: #20232a;
                color: #e1e3e6;
                border: 1px solid #333742;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2c303a;
                border-color: #4a5060;
            }
            QPushButton:pressed {
                background-color: #1a1c22;
            }
            QPushButton:disabled {
                background-color: #16181d;
                color: #555861;
                border-color: #23252b;
            }
            QComboBox {
                background-color: #20232a;
                color: #e1e3e6;
                border: 1px solid #333742;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QTreeView {
                background-color: #16181d;
                color: #e1e3e6;
                gridline-color: #242730;
                border: 1px solid #282a32;
                border-radius: 6px;
                selection-background-color: #2a3b5c;
                selection-color: #ffffff;
                font-size: 12px;
            }
            QTreeView::item {
                padding: 4px;
            }
            QTreeView::item:alternate {
                background-color: #1a1c22;
            }
            QHeaderView::section {
                background-color: #20232a;
                color: #9aa0a6;
                padding: 6px 8px;
                border: none;
                border-right: 1px solid #282a32;
                border-bottom: 1px solid #282a32;
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
            }
            QSplitter::handle {
                background-color: #242730;
                height: 4px;
            }
            QMenu {
                background-color: #20232a;
                color: #e1e3e6;
                border: 1px solid #333742;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #3b82f6;
                color: #ffffff;
            }
        """)

    # --- Scanning & Worker Control ---

    def _start_scan(self, paths: List[Path]):
        if not paths:
            return

        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()

        scan_mode = self.combo_mode.currentData()
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸️ Pause")
        self.btn_cancel.setEnabled(True)
        self.combo_mode.setEnabled(False)

        self.worker = BatchScanWorker(paths, scan_mode=scan_mode, parent=self)
        self.worker.sig_track_result.connect(self._on_track_result)
        self.worker.sig_progress.connect(self.summary_bar.set_progress)
        self.worker.sig_status.connect(self.summary_bar.set_status)
        self.worker.sig_batch_updated.connect(self._on_batch_updated)
        self.worker.sig_finished.connect(self._on_scan_finished)
        self.worker.start()

    def _on_batch_updated(self):
        top_left = self.table_model.index(0, 0)
        bottom_right = self.table_model.index(self.table_model.rowCount() - 1, len(self.table_model.COLUMNS) - 1)
        self.table_model.dataChanged.emit(top_left, bottom_right)
        self.summary_bar.reset_counts()
        for r in self.table_model.results:
            self.summary_bar.increment(r.verdict_category.value)

    def _toggle_pause(self):
        if not self.worker:
            return
        if self.worker.is_paused():
            self.worker.resume()
            self.btn_pause.setText("⏸️ Pause")
            self.summary_bar.set_status("Scan resumed...")
        else:
            self.worker.pause()
            self.btn_pause.setText("▶️ Resume")
            self.summary_bar.set_status("Scan paused.")

    def _cancel_scan(self):
        if self.worker:
            self.worker.cancel()
            self.btn_pause.setEnabled(False)
            self.btn_cancel.setEnabled(False)
            self.summary_bar.set_status("Cancelling scan...")

    def _on_track_result(self, result: AnalysisResult):
        was_grouped = self.table_model.grouped_by_folder
        self.table_model.add_result(result)
        self.summary_bar.increment(result.verdict_category.value)

        # Reflect grouping state in filter button if newly grouped
        if self.table_model.grouped_by_folder and not was_grouped:
            self.filter_bar.set_group_by_folder(True)
            self.tree_view.expandAll()

        # If it's the very first row, auto-select it
        if len(self.table_model.results) == 1:
            first_index = self.proxy_model.index(0, 0)
            if first_index.isValid():
                self.tree_view.setCurrentIndex(first_index)

    def _on_scan_finished(self, total: int, fakes: int):
        self.btn_pause.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.combo_mode.setEnabled(True)
        self.summary_bar.finish_scan(total, fakes)
        if self.table_model.grouped_by_folder:
            self.tree_view.expandAll()

    # --- Tree Selection & "Go to Folder" ---

    def _on_selection_changed(self, selected: QItemSelection, deselected: QItemSelection):
        if not self.tree_view.selectionModel():
            return
        indexes = self.tree_view.selectionModel().selectedRows()
        if not indexes:
            self.selected_file_path = None
            self.selected_folder_path = None
            self.btn_go_to_folder.setEnabled(False)
            self.btn_inspector_go_to_folder.setEnabled(False)
            return

        proxy_index = indexes[0]
        if not proxy_index.isValid():
            return

        source_index = self.proxy_model.mapToSource(proxy_index)
        if not source_index.isValid():
            return

        node = source_index.internalPointer()
        if node is None:
            return

        if isinstance(node, TrackNode):
            result = node.result
            self.selected_file_path = result.track_info.file_path
            self.selected_folder_path = result.track_info.file_path.parent
            self.btn_go_to_folder.setEnabled(True)
            self.btn_inspector_go_to_folder.setEnabled(True)

            self.lbl_selected_title.setText(f"🎵 {result.track_info.file_name} — {result.verdict_text}")
            self.lbl_selected_folder.setText(f"📁 {self.selected_folder_path}")
            self.spectrogram_widget.display_result(result)

        elif isinstance(node, FolderNode):
            self.selected_file_path = None
            self.selected_folder_path = node.folder_path
            self.btn_go_to_folder.setEnabled(True)
            self.btn_inspector_go_to_folder.setEnabled(True)

            self.lbl_selected_title.setText(
                f"📁 Folder: {node.folder_name} ({node.track_count} tracks: {node.genuine_count} Genuine, {node.fake_count} Fakes)"
            )
            self.lbl_selected_folder.setText(str(node.folder_path))
            self.spectrogram_widget.show_placeholder(
                f"Folder: {node.folder_name}\n{node.track_count} tracks ({node.genuine_count} genuine, {node.fake_count} fakes)\n\n"
                f"Select an individual track to inspect its frequency spectrogram."
            )

    def _on_go_to_folder(self):
        """Action to reveal the selected file or folder in Windows Explorer."""
        if self.selected_file_path and self.selected_file_path.exists():
            reveal_in_explorer(self.selected_file_path)
        elif self.selected_folder_path and self.selected_folder_path.exists():
            reveal_in_explorer(self.selected_folder_path)

    def _on_item_double_clicked(self, proxy_index: QModelIndex):
        """Double clicking a track row reveals it in File Explorer."""
        source_index = self.proxy_model.mapToSource(proxy_index)
        node = source_index.internalPointer()
        if isinstance(node, TrackNode):
            reveal_in_explorer(node.result.track_info.file_path)

    def _on_tree_context_menu(self, pos):
        """Right-click context menu with Go to Folder and copy actions."""
        proxy_index = self.tree_view.indexAt(pos)
        if not proxy_index.isValid():
            return

        source_index = self.proxy_model.mapToSource(proxy_index)
        node = source_index.internalPointer()

        menu = QMenu(self)

        action_go_folder = menu.addAction("📁  Go to Folder (Reveal in Explorer)")
        menu.addSeparator()
        action_copy_path = menu.addAction("📋  Copy File Path")
        action_copy_dir = menu.addAction("📂  Copy Folder Path")

        action_go_folder.triggered.connect(self._on_go_to_folder)

        if isinstance(node, TrackNode):
            file_path = str(node.result.track_info.file_path)
            folder_path = str(node.result.track_info.file_path.parent)
            action_copy_path.triggered.connect(lambda: QApplication.clipboard().setText(file_path))
            action_copy_dir.triggered.connect(lambda: QApplication.clipboard().setText(folder_path))
        elif isinstance(node, FolderNode):
            folder_path = str(node.folder_path)
            action_copy_path.setVisible(False)
            action_copy_dir.triggered.connect(lambda: QApplication.clipboard().setText(folder_path))

        menu.exec(self.tree_view.viewport().mapToGlobal(pos))

    # --- Filtering & Search ---

    def _on_category_filter_changed(self, category: Optional[str]):
        if self.tree_view.selectionModel():
            self.tree_view.selectionModel().clear()
        self.proxy_model.set_category_filter(category)
        if self.table_model.grouped_by_folder and self.proxy_model.rowCount() > 0:
            self.tree_view.expandAll()

    def _on_search_text_changed(self, text: str):
        if self.tree_view.selectionModel():
            self.tree_view.selectionModel().clear()
        self.proxy_model.set_search_text(text)
        if self.table_model.grouped_by_folder and self.proxy_model.rowCount() > 0:
            self.tree_view.expandAll()

    def _on_group_by_folder_toggled(self, checked: bool):
        self.table_model.set_grouped_by_folder(checked)
        if checked:
            self.tree_view.expandAll()

    # --- File/Folder Dialogs ---

    def _on_open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing Audio Files")
        if folder:
            self._start_scan([Path(folder)])

    def _on_open_files(self):
        filter_str = "Audio Files (*.flac *.wav *.mp3 *.m4a *.aac *.ogg *.opus *.aif *.aiff *.ape *.wv);;All Files (*.*)"
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", filter=filter_str)
        if files:
            self._start_scan([Path(f) for f in files])

    def _clear_all(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
        self.table_model.clear()
        self.summary_bar.reset_counts()
        self.summary_bar.set_status("Ready. Select audio files or a folder to analyze.")
        self.spectrogram_widget.show_placeholder()
        self.filter_bar.reset_filters()
        self.lbl_selected_title.setText("Select an audio track to inspect its frequency spectrum")
        self.lbl_selected_folder.setText("")
        self.btn_go_to_folder.setEnabled(False)
        self.btn_inspector_go_to_folder.setEnabled(False)

    # --- Drag & Drop ---

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            local_path = Path(url.toLocalFile())
            if local_path.exists():
                paths.append(local_path)
        if paths:
            self._start_scan(paths)

    # --- Reporting / Export ---

    def _export_report(self, fmt: str):
        results = self.table_model.results
        if not results:
            QMessageBox.information(self, "Export", "No analyzed tracks to export.")
            return

        filters = {
            "csv": "CSV Spreadsheet (*.csv)",
            "json": "JSON File (*.json)",
            "html": "HTML Report (*.html)"
        }
        default_name = f"audio_audit_report.{fmt}"
        dest, _ = QFileDialog.getSaveFileName(
            self, f"Export as {fmt.upper()}", default_name, filters.get(fmt, "All Files (*.*)")
        )
        if not dest:
            return

        try:
            dest_path = Path(dest)
            if fmt == "csv":
                export_to_csv(results, dest_path)
            elif fmt == "json":
                export_to_json(results, dest_path)
            elif fmt == "html":
                export_to_html(results, dest_path)
            QMessageBox.information(self, "Export Successful", f"Report successfully exported to:\n{dest_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export report:\n{e}")
