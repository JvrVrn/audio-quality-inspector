import os
from pathlib import Path
from typing import List
from PySide6.QtCore import QThread, Signal

from core.metadata import get_track_info, SUPPORTED_EXTENSIONS
from core.analyzer import evaluate_track, apply_album_consistency
from core.models import AnalysisResult, VerdictCategory


class BatchScanWorker(QThread):
    """
    Background worker thread that discovers and analyzes audio files,
    emitting progress and individual track results progressively,
    and cross-referencing album consistency upon completion.
    """
    sig_track_result = Signal(AnalysisResult)
    sig_progress = Signal(int, int, str)  # current, total, current_filename
    sig_finished = Signal(int, int)       # total_processed, fake_count
    sig_status = Signal(str)
    sig_batch_updated = Signal()

    def __init__(self, file_paths: List[Path], scan_mode: str = "fast", parent=None):
        super().__init__(parent)
        self.file_paths = file_paths
        self.scan_mode = scan_mode
        self._is_cancelled = False
        self._is_paused = False

    def cancel(self):
        self._is_cancelled = True

    def pause(self):
        self._is_paused = True

    def resume(self):
        self._is_paused = False

    def is_paused(self) -> bool:
        return self._is_paused

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def run(self):
        self.sig_status.emit("Discovering audio files...")
        
        # Single-pass directory walk to avoid network share overhead
        all_files: List[Path] = []
        for p in self.file_paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                try:
                    for root, _, files in os.walk(str(path_obj)):
                        for fname in files:
                            ext = os.path.splitext(fname)[1].lower()
                            if ext in SUPPORTED_EXTENSIONS:
                                all_files.append(Path(root) / fname)
                except Exception as e:
                    self.sig_status.emit(f"Error scanning folder: {e}")
            elif path_obj.is_file() and path_obj.suffix.lower() in SUPPORTED_EXTENSIONS:
                all_files.append(path_obj)

        unique_files = list(dict.fromkeys(all_files))
        total_count = len(unique_files)

        if total_count == 0:
            self.sig_status.emit("No supported audio files found.")
            self.sig_finished.emit(0, 0)
            return

        self.sig_status.emit(f"Found {total_count} tracks. Starting analysis...")
        results: List[AnalysisResult] = []

        for i, file_path in enumerate(unique_files, start=1):
            if self._is_cancelled:
                self.sig_status.emit("Scan cancelled by user.")
                break

            while self._is_paused and not self._is_cancelled:
                self.msleep(100)

            if self._is_cancelled:
                break

            self.sig_progress.emit(i, total_count, file_path.name)

            try:
                track_info = get_track_info(file_path)
                result = evaluate_track(track_info, mode=self.scan_mode)
                results.append(result)
                self.sig_track_result.emit(result)
            except Exception as e:
                pass

            # Yield briefly to ensure GUI thread responsiveness
            self.msleep(2)

        # Apply smart album consistency cross-validation across all scanned folders
        if not self._is_cancelled and len(results) > 1:
            self.sig_status.emit("Cross-referencing album consistency...")
            apply_album_consistency(results)
            self.sig_batch_updated.emit()

        final_fakes = sum(1 for r in results if r.verdict_category == VerdictCategory.FAKE)
        self.sig_finished.emit(total_count, final_fakes)
