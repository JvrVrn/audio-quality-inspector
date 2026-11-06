import logging
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
import av

logger = logging.getLogger(__name__)


def decode_audio(
    file_path: Path,
    mode: str = "fast",
    sample_window_seconds: float = 30.0
) -> Tuple[np.ndarray, int, float]:
    """
    Decodes audio into a 1D float32 numpy array (mono) using PyAV.
    
    In fast mode: sequentially streams a 30s window past the intro.
    Linear sequential streaming avoids multiple network seek round-trips
    and seek-table desynchronization over SMB network shares.
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

    resampler = av.AudioResampler(
        format="flt",
        layout="mono",
        rate=sample_rate
    )

    # Determine skip and target samples for fast mode
    skip_samples = 0
    target_samples = None

    if mode == "fast" and total_duration > (sample_window_seconds + 12.0):
        # Skip the intro (first 8-10 seconds) where tracks are often quiet or fade in
        skip_seconds = min(10.0, total_duration * 0.10)
        skip_samples = int(skip_seconds * sample_rate)
        target_samples = int(sample_window_seconds * sample_rate)
    elif mode == "fast":
        target_samples = int(sample_window_seconds * sample_rate)

    collected_chunks = []
    skipped_samples = 0
    collected_samples = 0

    try:
        for frame in container.decode(stream):
            resampled_frames = resampler.resample(frame)
            if not resampled_frames:
                continue
            for rf in resampled_frames:
                arr = rf.to_ndarray()
                chunk = arr[0] if arr.ndim == 2 else arr.ravel()
                chunk_len = len(chunk)

                # Handle skipping intro
                if skipped_samples < skip_samples:
                    needed_to_skip = skip_samples - skipped_samples
                    if chunk_len <= needed_to_skip:
                        skipped_samples += chunk_len
                        continue
                    else:
                        chunk = chunk[needed_to_skip:]
                        chunk_len = len(chunk)
                        skipped_samples = skip_samples

                collected_chunks.append(chunk)
                collected_samples += chunk_len

                if target_samples is not None and collected_samples >= target_samples:
                    break
            if target_samples is not None and collected_samples >= target_samples:
                break

        # Flush buffer
        flushed = resampler.resample(None)
        if flushed:
            for rf in flushed:
                arr = rf.to_ndarray()
                chunk = arr[0] if arr.ndim == 2 else arr.ravel()
                collected_chunks.append(chunk)
                collected_samples += len(chunk)

    finally:
        container.close()

    if not collected_chunks:
        raise ValueError(f"Could not decode any audio samples from {file_path.name}")

    samples = np.concatenate(collected_chunks, axis=0)

    # Trim to exact target if fast mode
    if target_samples is not None and len(samples) > target_samples:
        samples = samples[:target_samples]

    if total_duration <= 0.0:
        total_duration = len(samples) / float(sample_rate)

    return samples.astype(np.float32), sample_rate, total_duration
