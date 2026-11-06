from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Any
import numpy as np


class VerdictCategory(str, Enum):
    GENUINE = "genuine"
    FAKE = "fake"
    SUSPICIOUS = "suspicious"
    ERROR = "error"


@dataclass
class TrackInfo:
    file_path: Path
    file_name: str
    container: str
    file_size_bytes: int
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_depth: Optional[int] = None
    nominal_bitrate_kbps: Optional[float] = None
    is_lossless: bool = False

    @property
    def file_size_mb(self) -> float:
        return self.file_size_bytes / (1024 * 1024)

    @property
    def duration_formatted(self) -> str:
        minutes = int(self.duration_seconds // 60)
        seconds = int(self.duration_seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"


@dataclass
class SpectrogramData:
    frequencies: np.ndarray  # 1D array of freq bins (Hz)
    times: np.ndarray        # 1D array of time points (seconds)
    spec_db: np.ndarray      # 2D array of magnitudes in dB
    sample_rate: int
    cutoff_hz: float


@dataclass
class AnalysisResult:
    track_info: TrackInfo
    detected_cutoff_hz: float
    nyquist_hz: float
    estimated_bitrate_kbps: int
    confidence: float
    verdict_text: str
    verdict_category: VerdictCategory
    explanation: str
    scan_mode: str  # "fast" or "deep"
    spectrogram: Optional[SpectrogramData] = None
    error_message: Optional[str] = None
