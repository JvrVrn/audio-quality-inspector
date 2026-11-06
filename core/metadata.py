import os
from pathlib import Path
from typing import Optional
import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus

from core.models import TrackInfo

LOSSLESS_EXTENSIONS = {".flac", ".wav", ".alac", ".aif", ".aiff", ".ape", ".wv"}
LOSSY_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
SUPPORTED_EXTENSIONS = LOSSLESS_EXTENSIONS | LOSSY_EXTENSIONS


def get_track_info(file_path: Path) -> TrackInfo:
    """Extract metadata and stream properties using Mutagen and file attributes."""
    file_path = Path(file_path).resolve()
    file_name = file_path.name
    file_size = os.path.getsize(file_path)
    suffix = file_path.suffix.lower()

    container = suffix.lstrip(".").upper()
    is_lossless = suffix in LOSSLESS_EXTENSIONS

    duration = 0.0
    sample_rate = 44100
    channels = 2
    bit_depth: Optional[int] = None
    nominal_bitrate: Optional[float] = None

    try:
        audio = mutagen.File(str(file_path))
        if audio is not None and audio.info is not None:
            info = audio.info
            duration = getattr(info, "length", 0.0)
            sample_rate = getattr(info, "sample_rate", 44100)
            channels = getattr(info, "channels", 2)
            
            # Extract bit depth if available (FLAC, WAV, etc.)
            if hasattr(info, "bits_per_sample"):
                bit_depth = info.bits_per_sample
            elif hasattr(info, "bits"):
                bit_depth = info.bits

            # Extract bitrate (kbps)
            if hasattr(info, "bitrate") and info.bitrate:
                nominal_bitrate = round(info.bitrate / 1000.0, 1)
            elif duration > 0:
                # Approximate container bitrate from file size
                nominal_bitrate = round((file_size * 8.0) / (duration * 1000.0), 1)

            # Specific codec checks
            if isinstance(audio, FLAC):
                container = "FLAC"
                is_lossless = True
                bit_depth = info.bits_per_sample
            elif isinstance(audio, MP3):
                container = "MP3"
                is_lossless = False
            elif isinstance(audio, WAVE):
                container = "WAV"
                is_lossless = True
            elif isinstance(audio, MP4):
                # Could be ALAC (lossless) or AAC (lossy)
                is_alac = any(
                    track.codec and "alac" in track.codec.lower() 
                    for track in getattr(audio, "tracks", [])
                )
                if is_alac:
                    container = "ALAC"
                    is_lossless = True
                else:
                    container = "M4A/AAC"
                    is_lossless = False
            elif isinstance(audio, OggVorbis):
                container = "OGG Vorbis"
                is_lossless = False
            elif isinstance(audio, OggOpus):
                container = "Opus"
                is_lossless = False

    except Exception:
        # Mutagen fallback: estimate basic values from file size
        pass

    return TrackInfo(
        file_path=file_path,
        file_name=file_name,
        container=container,
        file_size_bytes=file_size,
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=bit_depth,
        nominal_bitrate_kbps=nominal_bitrate,
        is_lossless=is_lossless
    )
