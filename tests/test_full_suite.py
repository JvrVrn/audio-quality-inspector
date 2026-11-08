#!/usr/bin/env python3
"""
Full Test Suite for Audio Quality Inspector.
Tests DSP analysis, transcode detection, folder grouping, report exporters,
cross-platform file manager reveals (macOS Finder & Windows Explorer), and headless GUI.
"""

import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add repository root to Python path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _bootstrap_venv():
    try:
        import PySide6  # noqa: F401
    except ImportError:
        for venv_dir in (".venv", "venv"):
            if sys.platform == "win32":
                venv_py = REPO_ROOT / venv_dir / "Scripts" / "python.exe"
            else:
                venv_py = REPO_ROOT / venv_dir / "bin" / "python"
            if venv_py.is_file() and os.access(venv_py, os.X_OK):
                if str(venv_py) != sys.executable:
                    os.execv(str(venv_py), [str(venv_py)] + sys.argv)


_bootstrap_venv()

import numpy as np

# Set offscreen Qt platform before any PySide6 widgets are initialized
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QModelIndex

from core.models import TrackInfo, AnalysisResult, VerdictCategory, SpectrogramData
from core.metadata import get_track_info, SUPPORTED_EXTENSIONS
from core.decoder import decode_audio
from core.analyzer import (
    analyze_audio_cutoff,
    estimate_bitrate_from_cutoff,
    evaluate_track,
    apply_album_consistency,
)
from exporters.report_exporter import export_to_csv, export_to_json, export_to_html
from gui.table_model import AudioTreeModel, AudioFilterProxyModel, TrackNode, FolderNode
from gui.main_window import MainWindow, reveal_in_file_manager, reveal_in_explorer
from gui.widgets.summary_bar import SummaryBar
from gui.widgets.filter_bar import FilterBar
from gui.spectrogram import SpectrogramWidget


class TestPlatformFileManager(unittest.TestCase):
    """Verifies cross-platform file reveal in macOS Finder, Windows Explorer, and Linux."""

    def test_reveal_mac_file(self):
        with tempfile.NamedTemporaryFile() as tmp:
            test_file = Path(tmp.name)
            with patch("sys.platform", "darwin"), patch("subprocess.Popen") as mock_popen:
                reveal_in_file_manager(test_file)
                mock_popen.assert_called_once_with(["open", "-R", str(test_file.resolve())])

    def test_reveal_mac_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_dir = Path(tmp_dir)
            with patch("sys.platform", "darwin"), patch("subprocess.Popen") as mock_popen:
                reveal_in_file_manager(test_dir)
                mock_popen.assert_called_once_with(["open", str(test_dir.resolve())])

    def test_reveal_windows_file(self):
        with tempfile.NamedTemporaryFile() as tmp:
            test_file = Path(tmp.name)
            with patch("sys.platform", "win32"), patch("subprocess.Popen") as mock_popen:
                reveal_in_file_manager(test_file)
                mock_popen.assert_called_once_with(["explorer", f"/select,{test_file.resolve()}"])

    def test_reveal_windows_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_dir = Path(tmp_dir)
            with patch("sys.platform", "win32"), patch("subprocess.Popen") as mock_popen:
                reveal_in_file_manager(test_dir)
                mock_popen.assert_called_once_with(["explorer", str(test_dir.resolve())])

    def test_reveal_explorer_alias(self):
        """Ensure backwards compatibility alias reveal_in_explorer works identically."""
        self.assertIs(reveal_in_explorer, reveal_in_file_manager)


class TestDSPAndAudioAnalysis(unittest.TestCase):
    """Tests audio decoding, cutoff detection, and transcode classification."""

    @classmethod
    def setUpClass(cls):
        # Create a synthetic 2-second WAV file with a 5 kHz tone
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.wav_path = Path(cls.temp_dir.name) / "test_track.wav"

        sr = 44100
        duration = 2.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        tone = (0.5 * np.sin(2 * np.pi * 5000 * t) * 32767).astype(np.int16)

        with wave.open(str(cls.wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(tone.tobytes())

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_metadata_extraction(self):
        info = get_track_info(self.wav_path)
        self.assertEqual(info.container, "WAV")
        self.assertTrue(info.is_lossless)
        self.assertEqual(info.sample_rate, 44100)
        self.assertEqual(info.channels, 1)
        self.assertAlmostEqual(info.duration_seconds, 2.0, places=1)

    def test_audio_decoding(self):
        samples, sr, duration = decode_audio(self.wav_path, mode="fast")
        self.assertEqual(sr, 44100)
        self.assertGreater(len(samples), 0)
        self.assertAlmostEqual(duration, 2.0, places=1)

    def test_cutoff_estimation(self):
        self.assertEqual(estimate_bitrate_from_cutoff(21000, 22050), 320)
        self.assertEqual(estimate_bitrate_from_cutoff(19800, 22050), 320)
        self.assertEqual(estimate_bitrate_from_cutoff(18500, 22050), 224)
        self.assertEqual(estimate_bitrate_from_cutoff(17500, 22050), 192)
        self.assertEqual(estimate_bitrate_from_cutoff(16200, 22050), 160)
        self.assertEqual(estimate_bitrate_from_cutoff(15000, 22050), 128)
        self.assertEqual(estimate_bitrate_from_cutoff(11000, 22050), 64)

    def test_evaluate_track(self):
        info = get_track_info(self.wav_path)
        result = evaluate_track(info, mode="fast")
        self.assertIsInstance(result, AnalysisResult)
        self.assertIn(result.verdict_category, (VerdictCategory.GENUINE, VerdictCategory.SUSPICIOUS, VerdictCategory.FAKE))
        self.assertIsNotNone(result.spectrogram)


class TestReportExporters(unittest.TestCase):
    """Tests CSV, JSON, and HTML report exporting."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        info = TrackInfo(
            file_path=self.dir_path / "song.flac",
            file_name="song.flac",
            container="FLAC",
            file_size_bytes=25000000,
            duration_seconds=210.0,
            sample_rate=44100,
            channels=2,
            bit_depth=16,
            nominal_bitrate_kbps=950.0,
            is_lossless=True
        )
        self.sample_result = AnalysisResult(
            track_info=info,
            detected_cutoff_hz=15500.0,
            nyquist_hz=22050.0,
            estimated_bitrate_kbps=128,
            confidence=0.98,
            verdict_text="Fake FLAC (Transcoded from ~128k MP3)",
            verdict_category=VerdictCategory.FAKE,
            explanation="Steep cutoff at 15.5 kHz",
            scan_mode="fast"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_csv_export(self):
        csv_file = self.dir_path / "report.csv"
        export_to_csv([self.sample_result], csv_file)
        self.assertTrue(csv_file.exists())
        content = csv_file.read_text(encoding="utf-8-sig")
        self.assertIn("song.flac", content)
        self.assertIn("Fake FLAC", content)

    def test_json_export(self):
        json_file = self.dir_path / "report.json"
        export_to_json([self.sample_result], json_file)
        self.assertTrue(json_file.exists())
        content = json_file.read_text(encoding="utf-8")
        self.assertIn("song.flac", content)
        self.assertIn("fake_count", content)

    def test_html_export(self):
        html_file = self.dir_path / "report.html"
        export_to_html([self.sample_result], html_file)
        self.assertTrue(html_file.exists())
        content = html_file.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", content)
        self.assertIn("song.flac", content)
        self.assertIn("badge-fake", content)


class TestGUIComponents(unittest.TestCase):
    """Tests Tree Model, Filtering, and GUI widgets in headless mode."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def test_table_model_and_filtering(self):
        model = AudioTreeModel()
        proxy = AudioFilterProxyModel()
        proxy.setSourceModel(model)

        info1 = TrackInfo(
            file_path=Path("/music/album1/01.flac"),
            file_name="01.flac",
            container="FLAC",
            file_size_bytes=30000000,
            duration_seconds=180.0,
            sample_rate=44100,
            channels=2,
            is_lossless=True
        )
        r1 = AnalysisResult(
            track_info=info1,
            detected_cutoff_hz=21500.0,
            nyquist_hz=22050.0,
            estimated_bitrate_kbps=320,
            confidence=0.96,
            verdict_text="Genuine Lossless",
            verdict_category=VerdictCategory.GENUINE,
            explanation="Full spectrum",
            scan_mode="fast"
        )

        info2 = TrackInfo(
            file_path=Path("/music/album1/02.flac"),
            file_name="02.flac",
            container="FLAC",
            file_size_bytes=30000000,
            duration_seconds=180.0,
            sample_rate=44100,
            channels=2,
            is_lossless=True
        )
        r2 = AnalysisResult(
            track_info=info2,
            detected_cutoff_hz=15500.0,
            nyquist_hz=22050.0,
            estimated_bitrate_kbps=128,
            confidence=0.98,
            verdict_text="Fake FLAC",
            verdict_category=VerdictCategory.FAKE,
            explanation="Cutoff at 15.5 kHz",
            scan_mode="fast"
        )

        model.add_result(r1)
        model.add_result(r2)
        self.assertEqual(model.rowCount(), 2)

        # Test Category Filter: Fakes
        proxy.set_category_filter("fake")
        self.assertEqual(proxy.rowCount(), 1)

        # Reset filter
        proxy.set_category_filter(None)
        self.assertEqual(proxy.rowCount(), 2)

        # Test Search Filter
        proxy.set_search_text("02.flac")
        self.assertEqual(proxy.rowCount(), 1)

    def test_main_window_instantiation(self):
        window = MainWindow()
        self.assertIsNotNone(window)
        self.assertIsNotNone(window.btn_go_to_folder)
        self.assertIsNotNone(window.spectrogram_widget)
        self.assertIsNotNone(window.summary_bar)
        self.assertIsNotNone(window.filter_bar)
        window.close()


if __name__ == "__main__":
    unittest.main()
