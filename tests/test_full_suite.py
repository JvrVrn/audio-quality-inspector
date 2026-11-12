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

    def _write_test_audio(self, path: Path, samples: np.ndarray, codec_name: str, bitrate: int = None, sr: int = 44100):
        import av
        c = av.open(str(path), "w")
        s = c.add_stream(codec_name, rate=sr)
        if bitrate:
            s.bit_rate = bitrate
        frame_size = 1152 if "mp3" in codec_name else 4096
        for i in range(0, len(samples), frame_size):
            chunk = samples[i:i + frame_size]
            if len(chunk) < frame_size and codec_name != "flac":
                break
            stereo = np.stack([chunk, chunk])
            frame = av.AudioFrame.from_ndarray(stereo, format="fltp", layout="stereo")
            frame.sample_rate = sr
            for pkt in s.encode(frame):
                c.mux(pkt)
        for pkt in s.encode(None):
            c.mux(pkt)
        c.close()

    def _transcode_to_flac(self, src: Path, dst: Path, sample_format: str = "s16"):
        import av
        inp = av.open(str(src))
        out = av.open(str(dst), "w")
        try:
            in_s = inp.streams.audio[0]
            out_s = out.add_stream("flac", rate=in_s.rate)
            if sample_format == "s32":
                out_s.codec_context.format = "s32"
            resampler = av.AudioResampler(format=sample_format, layout="stereo", rate=in_s.rate)
            for frame in inp.decode(in_s):
                for rf in resampler.resample(frame):
                    for pkt in out_s.encode(rf):
                        out.mux(pkt)
            for rf in resampler.resample(None):
                for pkt in out_s.encode(rf):
                    out.mux(pkt)
            for pkt in out_s.encode(None):
                out.mux(pkt)
        finally:
            inp.close()
            out.close()

    def test_mp3_to_flac_transcode_detection_and_album_consistency(self):
        """Real MP3 (128k/192k/256k/320k) -> 16-bit & 24-bit FLAC transcodes must be detected as FAKE."""
        from scipy import signal

        sr = 44100
        dur = 12.0
        n = int(sr * dur)
        rng = np.random.default_rng(123)
        white = rng.standard_normal(n)
        fft = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        scale = 1.0 / np.maximum(freqs / 250.0, 1.0) ** 1.75
        music = np.fft.irfft(fft * scale, n)
        t = np.arange(n) / sr
        env = 0.25 + 0.75 * (0.5 + 0.5 * np.sin(2 * np.pi * 1.5 * t)) ** 2
        music = (music / np.max(np.abs(music)) * 0.6 * env).astype(np.float32)

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            orig_flac = td_path / "genuine.flac"
            self._write_test_audio(orig_flac, music, "flac", sr=sr)

            results = [evaluate_track(get_track_info(orig_flac), mode="fast")]
            self.assertEqual(results[0].verdict_category, VerdictCategory.GENUINE)

            for fmt in ("s16", "s32"):
                for br in (128000, 192000, 256000, 320000):
                    mp3_path = td_path / f"src_{br // 1000}_{fmt}.mp3"
                    fake_flac = td_path / f"fake_{br // 1000}_{fmt}.flac"
                    self._write_test_audio(mp3_path, music, "libmp3lame", bitrate=br, sr=sr)
                    self._transcode_to_flac(mp3_path, fake_flac, sample_format=fmt)
                    res = evaluate_track(get_track_info(fake_flac), mode="fast")
                    self.assertEqual(
                        res.verdict_category,
                        VerdictCategory.FAKE,
                        f"Expected FAKE for {br // 1000}k MP3 -> {fmt} FLAC, got {res.verdict_category} ({res.verdict_text}, cutoff={res.detected_cutoff_hz:.0f}Hz)"
                    )
                    results.append(res)

            # Warm acoustic genuine track (smooth natural roll-off, no brickwall)
            sos = signal.butter(2, 11000, btype="low", fs=sr, output="sos")
            warm = signal.sosfilt(sos, music).astype(np.float32)
            warm = warm + (rng.standard_normal(n) * 3e-5).astype(np.float32)
            warm_flac = td_path / "warm_genuine.flac"
            self._write_test_audio(warm_flac, warm, "flac", sr=sr)
            warm_res = evaluate_track(get_track_info(warm_flac), mode="fast")
            self.assertEqual(warm_res.verdict_category, VerdictCategory.GENUINE)

            # Album consistency in same folder must NOT hide confirmed FAKE transcodes
            apply_album_consistency(results)
            for fake_res in results[1:]:
                self.assertEqual(fake_res.verdict_category, VerdictCategory.FAKE)

    def test_long_quiet_intro_fast_scan_detects_transcode(self):
        """Fast scan must sample across the track so a long band-limited intro cannot hide an MP3 transcode."""
        from scipy import signal

        sr = 44100
        dur = 75.0
        n = int(sr * dur)
        rng = np.random.default_rng(321)
        white = rng.standard_normal(n)
        fft = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        scale = 1.0 / np.maximum(freqs / 250.0, 1.0) ** 1.65
        full_band = np.fft.irfft(fft * scale, n).astype(np.float32)

        # First 45s is a quiet band-limited intro (< 10 kHz); 45s..75s is full-band audio
        sos_intro = signal.butter(4, 9000, btype="low", fs=sr, output="sos")
        intro = (signal.sosfilt(sos_intro, full_band) * 0.25).astype(np.float32)
        split = int(45.0 * sr)
        track = np.concatenate([intro[:split], (full_band[split:] / np.max(np.abs(full_band[split:])) * 0.6).astype(np.float32)])

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            mp3_path = td_path / "long_intro_192.mp3"
            flac_path = td_path / "long_intro_192.flac"
            self._write_test_audio(mp3_path, track, "libmp3lame", bitrate=192000, sr=sr)
            self._transcode_to_flac(mp3_path, flac_path, sample_format="s32")

            res = evaluate_track(get_track_info(flac_path), mode="fast")
            self.assertEqual(
                res.verdict_category,
                VerdictCategory.FAKE,
                f"Fast mode failed to catch transcode after quiet intro: {res.verdict_text} ({res.explanation})"
            )

    def test_upscaled_170k_to_320k_mp3_and_clipping_splatter_flac(self):
        """170k MP3 -> 320k MP3 and clipped VBR MP3 -> 16-bit FLAC must be caught as FAKE, while genuine 320k stays GENUINE."""
        import av

        sr = 44100
        dur = 12.0
        n = int(sr * dur)
        rng = np.random.default_rng(999)
        white = rng.standard_normal(n)
        fft = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        scale = 1.0 / np.maximum(freqs / 250.0, 1.0) ** 1.65
        music = np.fft.irfft(fft * scale, n)
        # Scale close to full scale so MP3 inter-sample clipping occurs on ~3% of frames
        music = (music / np.max(np.abs(music)) * 0.95).astype(np.float32)

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            gen_320 = td_path / "genuine_320.mp3"
            self._write_test_audio(gen_320, music, "libmp3lame", bitrate=320000, sr=sr)
            gen_res = evaluate_track(get_track_info(gen_320), mode="fast")
            self.assertEqual(gen_res.verdict_category, VerdictCategory.GENUINE)

            src_170 = td_path / "src_170.mp3"
            self._write_test_audio(src_170, music, "libmp3lame", bitrate=170000, sr=sr)
            dec, _, _ = decode_audio(src_170, mode="deep")

            up_320 = td_path / "upscaled_320_from_170.mp3"
            self._write_test_audio(up_320, dec, "libmp3lame", bitrate=320000, sr=sr)
            up_res = evaluate_track(get_track_info(up_320), mode="fast")
            self.assertEqual(
                up_res.verdict_category,
                VerdictCategory.FAKE,
                f"Expected FAKE for 170k->320k MP3, got {up_res.verdict_category} ({up_res.verdict_text})"
            )

            # Simulate 3% inter-sample clipping splatter on a VBR/170k MP3 -> 16-bit FLAC
            clipped = np.copy(dec)
            for k in range(0, len(clipped), int(sr * 1.0)):
                clipped[k:k + 512] = np.clip(clipped[k:k + 512] * 3.0, -1.0, 1.0)
            clipped_flac = td_path / "clipped_from_170.flac"
            self._write_test_audio(clipped_flac, clipped, "flac", sr=sr)
            fl_res = evaluate_track(get_track_info(clipped_flac), mode="fast")
            self.assertEqual(
                fl_res.verdict_category,
                VerdictCategory.FAKE,
                f"Expected FAKE for clipped 170k MP3 -> FLAC, got {fl_res.verdict_category} ({fl_res.verdict_text})"
            )

    def test_dithered_320k_mp3_to_16bit_and_24bit_flac_wav(self):
        """320k MP3 with gentle lowpass exported with 16-bit TPDF dither to FLAC/WAV must be flagged FAKE."""
        from scipy import signal

        sr = 44100
        dur = 12.0
        n = int(sr * dur)
        rng = np.random.default_rng(777)
        white = rng.standard_normal(n)
        fft = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        scale = 1.0 / np.maximum(freqs / 250.0, 1.0) ** 1.65
        music = np.fft.irfft(fft * scale, n)
        music = (music / np.max(np.abs(music)) * 0.6).astype(np.float32)

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            mp3_320 = td_path / "src_320.mp3"
            self._write_test_audio(mp3_320, music, "libmp3lame", bitrate=320000, sr=sr)
            dec, _, _ = decode_audio(mp3_320, mode="deep")

            # Add 16-bit TPDF dither before exporting to 16-bit FLAC (mimics Audacity/foobar2000)
            lsb = 1.0 / 32768.0
            dither = (rng.uniform(-lsb, lsb, len(dec)) + rng.uniform(-lsb, lsb, len(dec))).astype(np.float32)
            dithered = np.clip(dec + dither, -1.0, 1.0).astype(np.float32)

            flac16 = td_path / "dithered_16.flac"
            self._write_test_audio(flac16, dithered, "flac", sr=sr)
            r16 = evaluate_track(get_track_info(flac16), mode="fast")
            self.assertEqual(r16.verdict_category, VerdictCategory.FAKE)

            flac24 = td_path / "transcoded_24.flac"
            self._transcode_to_flac(mp3_320, flac24, sample_format="s32")
            r24 = evaluate_track(get_track_info(flac24), mode="fast")
            self.assertEqual(r24.verdict_category, VerdictCategory.FAKE)

            wav16 = td_path / "transcoded_16.wav"
            with wave.open(str(wav16), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                pcm = (dithered * 32767).astype("<i2")
                wf.writeframes(np.stack([pcm, pcm], axis=1).tobytes())
            rw = evaluate_track(get_track_info(wav16), mode="fast")
            self.assertEqual(rw.verdict_category, VerdictCategory.FAKE)


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
