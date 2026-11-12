import logging
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
import av

logger = logging.getLogger(__name__)


def _decode_sequential(
    container,
    stream,
    sample_rate: int,
    mode: str,
    sample_window_seconds: float,
    total_duration: float
) -> np.ndarray:
    """Sequential single-pass decode fallback (used for short tracks or unseekable streams)."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)

    skip_samples = 0
    target_samples = None
    if mode == "fast" and total_duration > (sample_window_seconds + 12.0):
        skip_seconds = min(25.0, total_duration * 0.20) if total_duration > 70.0 else min(10.0, total_duration * 0.10)
        skip_samples = int(skip_seconds * sample_rate)
        target_samples = int(sample_window_seconds * sample_rate)
    elif mode == "fast":
        target_samples = int(sample_window_seconds * sample_rate)

    collected_chunks = []
    skipped_samples = 0
    collected_samples = 0

    for frame in container.decode(stream):
        resampled_frames = resampler.resample(frame)
        if not resampled_frames:
            continue
        for rf in resampled_frames:
            arr = rf.to_ndarray()
            chunk = arr[0] if arr.ndim == 2 else arr.ravel()
            chunk_len = len(chunk)

            if skipped_samples < skip_samples:
                needed_to_skip = skip_samples - skipped_samples
                if chunk_len <= needed_to_skip:
                    skipped_samples += chunk_len
                    continue
                chunk = chunk[needed_to_skip:]
                chunk_len = len(chunk)
                skipped_samples = skip_samples

            collected_chunks.append(chunk)
            collected_samples += chunk_len
            if target_samples is not None and collected_samples >= target_samples:
                break
        if target_samples is not None and collected_samples >= target_samples:
            break

    flushed = resampler.resample(None)
    if flushed:
        for rf in flushed:
            arr = rf.to_ndarray()
            chunk = arr[0] if arr.ndim == 2 else arr.ravel()
            collected_chunks.append(chunk)

    if not collected_chunks:
        return np.array([], dtype=np.float32)

    samples = np.concatenate(collected_chunks, axis=0)
    if target_samples is not None and len(samples) > target_samples:
        samples = samples[:target_samples]
    return samples.astype(np.float32)


def _decode_multi_window(
    container,
    stream,
    sample_rate: int,
    sample_window_seconds: float,
    total_duration: float
) -> Optional[np.ndarray]:
    """
    Samples 3 windows across the track (20%, 50%, 75%) in fast mode so long quiet intros
    cannot hide high-frequency cutoffs later in the track. Returns None if seeking fails.
    """
    fractions = (0.20, 0.50, 0.75)
    window_sec = sample_window_seconds / len(fractions)
    target_per_window = int(window_sec * sample_rate)
    fade_len = min(int(0.02 * sample_rate), target_per_window // 10)

    window_arrays = []
    try:
        for frac in fractions:
            target_sec = max(0.0, min(total_duration - window_sec, total_duration * frac))
            if stream.time_base is not None:
                target_ts = int(target_sec / float(stream.time_base))
                container.seek(target_ts, stream=stream, any_frame=False, backward=True)
            else:
                container.seek(int(target_sec * av.time_base), any_frame=False, backward=True)

            resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
            chunks = []
            got = 0

            for frame in container.decode(stream):
                if frame.pts is not None and stream.time_base is not None:
                    frame_sec = float(frame.pts * stream.time_base)
                    if frame_sec < target_sec - 0.5:
                        continue
                resampled_frames = resampler.resample(frame)
                if not resampled_frames:
                    continue
                for rf in resampled_frames:
                    arr = rf.to_ndarray()
                    chunk = arr[0] if arr.ndim == 2 else arr.ravel()
                    chunks.append(chunk)
                    got += len(chunk)
                    if got >= target_per_window:
                        break
                if got >= target_per_window:
                    break

            flushed = resampler.resample(None)
            if flushed:
                for rf in flushed:
                    arr = rf.to_ndarray()
                    chunk = arr[0] if arr.ndim == 2 else arr.ravel()
                    chunks.append(chunk)

            if not chunks:
                return None

            win = np.concatenate(chunks, axis=0)[:target_per_window].astype(np.float32)
            if fade_len > 1 and len(win) > 2 * fade_len:
                fade = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
                win[:fade_len] *= fade
                win[-fade_len:] *= fade[::-1]
            window_arrays.append(win)

        return np.concatenate(window_arrays, axis=0)
    except Exception as exc:
        logger.debug(f"Multi-window seek failed, falling back to sequential decode: {exc}")
        return None


def decode_audio(
    file_path: Path,
    mode: str = "fast",
    sample_window_seconds: float = 30.0
) -> Tuple[np.ndarray, int, float]:
    """
    Decodes audio into a 1D float32 numpy array (mono) using PyAV.

    In fast mode on longer tracks (>45s), samples 3 distributed windows (20%, 50%, 75%)
    to avoid missing cutoffs after quiet intros, with automatic sequential fallback.
    """
    file_path = Path(file_path)
    container = av.open(str(file_path))

    if not container.streams.audio:
        container.close()
        raise ValueError(f"No audio streams found in {file_path.name}")

    stream = container.streams.audio[0]
    sample_rate = stream.sample_rate or 44100

    total_duration = 0.0
    if stream.duration is not None and stream.time_base is not None:
        total_duration = float(stream.duration * stream.time_base)
    elif container.duration is not None:
        total_duration = float(container.duration / av.time_base)

    samples: Optional[np.ndarray] = None
    try:
        if mode == "fast" and total_duration > (sample_window_seconds + 15.0):
            samples = _decode_multi_window(container, stream, sample_rate, sample_window_seconds, total_duration)
            if samples is None or len(samples) < int(sample_rate * 2.0):
                # Reopen cleanly if seek left the stream in an indeterminate state
                container.close()
                container = av.open(str(file_path))
                stream = container.streams.audio[0]
                samples = _decode_sequential(container, stream, sample_rate, mode, sample_window_seconds, total_duration)
        else:
            samples = _decode_sequential(container, stream, sample_rate, mode, sample_window_seconds, total_duration)
    finally:
        container.close()

    if samples is None or len(samples) == 0:
        raise ValueError(f"Could not decode any audio samples from {file_path.name}")

    if total_duration <= 0.0:
        total_duration = len(samples) / float(sample_rate)

    return samples.astype(np.float32), sample_rate, total_duration
