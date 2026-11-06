from pathlib import Path
from typing import List, Dict, Optional, Any, Union
from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, QSortFilterProxyModel
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import QApplication

from core.models import AnalysisResult, VerdictCategory


class TreeNode:
    def __init__(self, parent=None):
        self.parent_node = parent
        self.children: List['TreeNode'] = []

    def child(self, row: int) -> Optional['TreeNode']:
        if 0 <= row < len(self.children):
            return self.children[row]
        return None

    def row(self) -> int:
        if self.parent_node:
            try:
                return self.parent_node.children.index(self)
            except ValueError:
                return 0
        return 0

    def child_count(self) -> int:
        return len(self.children)


class RootNode(TreeNode):
    pass


class FolderNode(TreeNode):
    def __init__(self, folder_path: Path, parent=None):
        super().__init__(parent)
        self.folder_path = Path(folder_path).resolve()
        self.folder_name = self.folder_path.name or str(self.folder_path)

    @property
    def track_count(self) -> int:
        return len(self.children)

    @property
    def fake_count(self) -> int:
        return sum(
            1 for c in self.children
            if isinstance(c, TrackNode) and (
                c.result.verdict_category == VerdictCategory.FAKE or
                (hasattr(c.result.verdict_category, 'value') and c.result.verdict_category.value == 'fake')
            )
        )

    @property
    def genuine_count(self) -> int:
        return sum(
            1 for c in self.children
            if isinstance(c, TrackNode) and (
                c.result.verdict_category == VerdictCategory.GENUINE or
                (hasattr(c.result.verdict_category, 'value') and c.result.verdict_category.value == 'genuine')
            )
        )

    @property
    def suspicious_count(self) -> int:
        return sum(
            1 for c in self.children
            if isinstance(c, TrackNode) and (
                c.result.verdict_category == VerdictCategory.SUSPICIOUS or
                (hasattr(c.result.verdict_category, 'value') and c.result.verdict_category.value == 'suspicious')
            )
        )


class TrackNode(TreeNode):
    def __init__(self, result: AnalysisResult, parent=None):
        super().__init__(parent)
        self.result = result


class AudioTreeModel(QAbstractItemModel):
    """
    Hierarchical Tree Model supporting both flat and folder-grouped views.
    """
    COLUMNS = [
        "Status",
        "File Name",
        "Verdict",
        "Cutoff",
        "Est. Quality",
        "Format",
        "Bitrate",
        "Duration",
        "Size",
        "Folder",
        "Explanation"
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results: List[AnalysisResult] = []
        self.grouped_by_folder: bool = False
        self.root_node = RootNode()
        self.folder_nodes: Dict[Path, FolderNode] = {}
        self.folders_seen: set = set()

    def set_grouped_by_folder(self, grouped: bool):
        if self.grouped_by_folder == grouped:
            return
        self.beginResetModel()
        self.grouped_by_folder = grouped
        self._rebuild_tree()
        self.endResetModel()

    def _rebuild_tree(self):
        self.root_node = RootNode()
        self.folder_nodes.clear()

        if self.grouped_by_folder:
            for result in self.results:
                folder_path = result.track_info.file_path.parent
                if folder_path not in self.folder_nodes:
                    f_node = FolderNode(folder_path, parent=self.root_node)
                    self.root_node.children.append(f_node)
                    self.folder_nodes[folder_path] = f_node
                else:
                    f_node = self.folder_nodes[folder_path]

                t_node = TrackNode(result, parent=f_node)
                f_node.children.append(t_node)
        else:
            for result in self.results:
                t_node = TrackNode(result, parent=self.root_node)
                self.root_node.children.append(t_node)

    def add_result(self, result: AnalysisResult):
        self.results.append(result)
        folder = result.track_info.file_path.parent
        self.folders_seen.add(folder)

        # If several folders are detected and grouping was off, auto-enable grouping
        if len(self.folders_seen) > 1 and not self.grouped_by_folder:
            self.beginResetModel()
            self.grouped_by_folder = True
            self._rebuild_tree()
            self.endResetModel()
            return

        if self.grouped_by_folder:
            if folder not in self.folder_nodes:
                f_node = FolderNode(folder, parent=self.root_node)
                new_folder_row = len(self.root_node.children)
                self.beginInsertRows(QModelIndex(), new_folder_row, new_folder_row)
                self.root_node.children.append(f_node)
                self.folder_nodes[folder] = f_node
                self.endInsertRows()
            else:
                f_node = self.folder_nodes[folder]

            f_index = self.createIndex(f_node.row(), 0, f_node)
            track_row = len(f_node.children)
            self.beginInsertRows(f_index, track_row, track_row)
            t_node = TrackNode(result, parent=f_node)
            f_node.children.append(t_node)
            self.endInsertRows()

            # Refresh folder summary column
            self.dataChanged.emit(f_index, self.createIndex(f_node.row(), len(self.COLUMNS) - 1, f_node))
        else:
            row = len(self.root_node.children)
            self.beginInsertRows(QModelIndex(), row, row)
            t_node = TrackNode(result, parent=self.root_node)
            self.root_node.children.append(t_node)
            self.endInsertRows()

    def get_result(self, index_or_row: Union[QModelIndex, int]) -> Optional[AnalysisResult]:
        if isinstance(index_or_row, int):
            if 0 <= index_or_row < len(self.results):
                return self.results[index_or_row]
            return None

        if not index_or_row.isValid():
            return None
        node = index_or_row.internalPointer()
        if isinstance(node, TrackNode):
            return node.result
        return None

    def get_folder_path(self, index: QModelIndex) -> Optional[Path]:
        if not index.isValid():
            return None
        node = index.internalPointer()
        if isinstance(node, FolderNode):
            return node.folder_path
        elif isinstance(node, TrackNode):
            return node.result.track_info.file_path.parent
        return None

    def clear(self):
        self.beginResetModel()
        self.results.clear()
        self.folders_seen.clear()
        self.folder_nodes.clear()
        self.root_node = RootNode()
        self.grouped_by_folder = False
        self.endResetModel()

    # --- QAbstractItemModel overrides ---

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        parent_node = parent.internalPointer() if parent.isValid() else self.root_node
        if parent_node is None:
            return QModelIndex()

        child_node = parent_node.child(row)
        if child_node:
            return self.createIndex(row, column, child_node)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        node = index.internalPointer()
        if node is None:
            return QModelIndex()

        parent_node = node.parent_node
        if parent_node is None or parent_node == self.root_node:
            return QModelIndex()

        p_row = parent_node.row()
        return self.createIndex(p_row, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        # Crucial Qt rule: non-zero rowCount for column > 0 causes crashes in QSortFilterProxyModel
        if parent.isValid() and parent.column() > 0:
            return 0
        parent_node = parent.internalPointer() if parent.isValid() else self.root_node
        if parent_node is None:
            return 0
        return parent_node.child_count()

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        node = index.internalPointer()
        if node is None:
            return None

        col = index.column()

        # 1. Folder Header Node
        if isinstance(node, FolderNode):
            if role == Qt.ItemDataRole.DisplayRole:
                if col == 0:
                    summary = f"📁  {node.folder_name}  ({node.track_count} tracks"
                    if node.fake_count > 0:
                        summary += f" — 🔴 {node.fake_count} Fakes"
                    else:
                        summary += f" — 🟢 All Genuine"
                    summary += ")"
                    return summary
                elif col == 1:
                    return f"{node.folder_path}"
                elif col == 2:
                    return f"{node.fake_count} Fakes detected" if node.fake_count > 0 else "All Genuine"
                elif col == 9:
                    return node.folder_name
                return ""

            elif role == Qt.ItemDataRole.FontRole:
                if col == 0:
                    font = QFont(QApplication.font())
                    font.setBold(True)
                    if font.pointSize() <= 0:
                        font.setPointSize(10)
                    return font
                return None

            elif role == Qt.ItemDataRole.ForegroundRole:
                if node.fake_count > 0:
                    return QBrush(QColor("#ff7675"))
                return QBrush(QColor("#74b9ff"))

            elif role == Qt.ItemDataRole.BackgroundRole:
                return QBrush(QColor("#1e222b"))

            elif role == Qt.ItemDataRole.ToolTipRole:
                return (
                    f"Folder: {node.folder_path}\n"
                    f"Tracks: {node.track_count}\n"
                    f"Genuine: {node.genuine_count}\n"
                    f"Fakes: {node.fake_count}\n"
                    f"Suspicious: {node.suspicious_count}"
                )

        # 2. Track Node
        elif isinstance(node, TrackNode):
            result = node.result
            info = result.track_info
            cat_str = result.verdict_category.value if hasattr(result.verdict_category, 'value') else str(result.verdict_category)

            if role == Qt.ItemDataRole.DisplayRole:
                if col == 0:
                    if cat_str == "genuine":
                        return "🟢 Genuine"
                    elif cat_str == "fake":
                        return "🔴 Fake"
                    elif cat_str == "suspicious":
                        return "🟡 Warning"
                    else:
                        return "⚪ Error"
                elif col == 1:
                    return info.file_name
                elif col == 2:
                    return result.verdict_text
                elif col == 3:
                    return f"{result.detected_cutoff_hz / 1000.0:.1f} kHz" if result.detected_cutoff_hz > 0 else "N/A"
                elif col == 4:
                    if cat_str == "genuine" and info.is_lossless:
                        return "Lossless"
                    return f"~{result.estimated_bitrate_kbps} kbps" if result.estimated_bitrate_kbps > 0 else "N/A"
                elif col == 5:
                    return info.container
                elif col == 6:
                    return f"{info.nominal_bitrate_kbps:.0f} kbps" if info.nominal_bitrate_kbps else "Lossless"
                elif col == 7:
                    return info.duration_formatted
                elif col == 8:
                    return f"{info.file_size_mb:.2f} MB"
                elif col == 9:
                    return info.file_path.parent.name
                elif col == 10:
                    return result.explanation

            elif role == Qt.ItemDataRole.ForegroundRole:
                if col in (0, 2):
                    if cat_str == "genuine":
                        return QBrush(QColor("#2ecc71"))
                    elif cat_str == "fake":
                        return QBrush(QColor("#ff5e57"))
                    elif cat_str == "suspicious":
                        return QBrush(QColor("#f1c40f"))
                    else:
                        return QBrush(QColor("#bdc3c7"))

            elif role == Qt.ItemDataRole.TextAlignmentRole:
                if col in (0, 3, 4, 5, 6, 7, 8):
                    return int(Qt.AlignmentFlag.AlignCenter)
                return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

            elif role == Qt.ItemDataRole.ToolTipRole:
                return (
                    f"File: {info.file_name}\n"
                    f"Folder: {info.file_path.parent}\n"
                    f"Verdict: {result.verdict_text}\n"
                    f"Cutoff: {result.detected_cutoff_hz / 1000.0:.2f} kHz\n"
                    f"Details: {result.explanation}"
                )

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None


# Backward-compatible alias
AudioTableModel = AudioTreeModel


class AudioFilterProxyModel(QSortFilterProxyModel):
    """
    Search and category filtering for the audio tree model without crashing.
    Uses Qt's native recursive filtering to guarantee rock-solid tree hierarchy stability.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.category_filter: Optional[str] = None
        self.search_text: str = ""
        self.setRecursiveFilteringEnabled(True)

    def set_category_filter(self, category: Optional[str]):
        self.category_filter = category
        self.invalidate()

    def set_search_text(self, text: str):
        self.search_text = text.strip().lower()
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, AudioTreeModel):
            return True

        index = model.index(source_row, 0, source_parent)
        if not index.isValid():
            return False

        node = index.internalPointer()
        if node is None:
            return False

        # If it is a Track Node
        if isinstance(node, TrackNode):
            result = node.result
            cat_str = result.verdict_category.value if hasattr(result.verdict_category, 'value') else str(result.verdict_category)
            if self.category_filter is not None:
                if cat_str != self.category_filter:
                    return False

            if self.search_text:
                file_name = result.track_info.file_name
                parent_name = result.track_info.file_path.parent.name
                searchable = f"{file_name} {result.verdict_text} {result.explanation} {result.track_info.container} {parent_name}".lower()
                if self.search_text not in searchable:
                    return False

            return True

        # If it is a Folder Node
        elif isinstance(node, FolderNode):
            # If no filter and no search query, show all folders
            if self.category_filter is None and not self.search_text:
                return True

            # If user typed search text matching folder name, include folder and its contents
            if self.search_text and self.search_text in node.folder_name.lower():
                return True

            # Otherwise return False: Qt's native recursiveFilteringEnabled automatically
            # includes this folder if any descendant track matches the filter criteria!
            return False

        return True
