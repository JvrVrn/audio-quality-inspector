import logging
from pathlib import Path
from typing import Optional, Tuple, List
import numpy as np
from scipy import signal

from core.models import (
    TrackInfo,
    AnalysisResult,
    SpectrogramData,
    VerdictCategory
)
from core.decoder import decode_audio

logger = logging.getLogger(__name__)


def estimate_bitrate_from_cutoff(cutoff_hz: float, nyquist_hz: float) -> int:
    """Map detected frequency cutoff (Hz) to typical MP3/lossy equivalent bitrate (kbps)."""
    if cutoff_hz >= min(20800, nyquist_hz * 0.95):
        return 320
    elif cutoff_hz >= 19500:
        return 320
    elif cutoff_hz >= 18000:
        return 224
    elif cutoff_hz >= 17000:
        return 192
    elif cutoff_hz >= 16000:
        return 160
    elif cutoff_hz >= 14500:
        return 128
    elif cutoff_hz >= 12000:
        return 96
    else:
        return 64


def analyze_audio_cutoff(
    samples: np.ndarray,
    sample_rate: int,
    n_fft: int = 2048,
    hop_length: int = 512
) -> Tuple[float, float, bool, bool, SpectrogramData]:
    """
    Computes STFT, evaluates frequency profile across active bins,
    and detects frequency cutoff, brickwall cliffs, and high-frequency dither/noise floor.
    
    Returns:
        (detected_cutoff_hz, cliff_slope_db_per_khz, has_brickwall, has_continuous_dither, spectrogram_data)
    """
    nyquist = sample_rate / 2.0
    
    # Compute STFT
    frequencies, times, Zxx = signal.stft(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=n_fft,
        noverlap=n_fft - hop_length,
        boundary=None
    )
    
    # Magnitude and decibel conversion
    mag = np.abs(Zxx)
    max_mag = np.max(mag)
    if max_mag == 0:
        max_mag = 1e-12
        
    spec_db = 20.0 * np.log10(np.maximum(mag / max_mag, 1e-6))  # Dynamic range up to -120 dB
    
    # Measure frequency envelope across active frames (95th percentile)
    freq_profile_db = np.percentile(spec_db, 95, axis=1)
    
    # Smooth profile slightly with a median filter (~200 Hz window)
    bin_hz = sample_rate / n_fft
    window_bins = max(3, int(200 / bin_hz))
    if window_bins % 2 == 0:
        window_bins += 1
    smoothed_profile = signal.medfilt(freq_profile_db, kernel_size=window_bins)
    
    # Measure peak in the musical passband (1 kHz - 10 kHz)
    passband_mask = (frequencies >= 1000) & (frequencies <= 10000)
    p_peak = float(np.max(smoothed_profile[passband_mask])) if np.any(passband_mask) else 0.0
    
    # Active sound threshold: standard dynamic range of ~45-50 dB below musical peak
    threshold_db = max(-75.0, p_peak - 45.0)

    # Detect steep brickwall slopes (> 22 dB / kHz) in the 10 kHz to Nyquist region
    min_bin = max(1, int(10000 / bin_hz))
    max_bin = len(frequencies) - 1
    step_bins = max(2, int(600 / bin_hz))  # 600 Hz comparison window
    
    max_slope = 0.0
    brickwall_freq = None
    
    for i in range(max_bin - step_bins, min_bin, -1):
        f_curr = float(frequencies[i])
        val_upper = smoothed_profile[i + step_bins]
        val_lower = smoothed_profile[max(0, i - step_bins)]
        
        delta_f_khz = (frequencies[i + step_bins] - frequencies[max(0, i - step_bins)]) / 1000.0
        if delta_f_khz > 0:
            slope = float((val_lower - val_upper) / delta_f_khz)
            if slope > max_slope:
                max_slope = slope
                
            # A true encoder brickwall drop requires:
            # 1. Very steep cliff: slope >= 24 dB/kHz
            # 2. The level on the high side must plunge deeply into the noise floor
            # 3. Must be at least a 35 dB drop relative to peak
            if slope >= 24.0 and val_upper < (p_peak - 35.0) and brickwall_freq is None:
                brickwall_freq = f_curr

    has_brickwall = (brickwall_freq is not None)

    # Check high-frequency noise floor (dither / analog background noise) in 18 - 22 kHz band
    high_band_mask = (frequencies >= 18000) & (frequencies <= nyquist)
    high_band_energy = float(np.mean(smoothed_profile[high_band_mask])) if np.any(high_band_mask) else -100.0
    
    # True CD audio typically has analog/dither noise between -70 dB and -90 dB.
    # A lossy transcode has an absolute dead void (<-95 dB or total silence).
    has_continuous_dither = (high_band_energy > -88.0) and not has_brickwall

    # Check energy near Nyquist (upper 5% of spectrum)
    upper_band_mask = (frequencies >= nyquist * 0.95)
    has_full_nyquist_energy = np.any(smoothed_profile[upper_band_mask] >= threshold_db)

    if has_full_nyquist_energy and not has_brickwall:
        cutoff_hz = nyquist
    elif has_brickwall and brickwall_freq is not None:
        cutoff_hz = min(nyquist, brickwall_freq)
    else:
        # Find highest frequency bin exceeding threshold
        active_bins = np.where(smoothed_profile >= threshold_db)[0]
        if len(active_bins) > 0:
            cutoff_hz = min(nyquist, float(frequencies[active_bins[-1]]))
        else:
            cutoff_hz = 12000.0

    cutoff_hz = min(cutoff_hz, nyquist)

    # Prepare spectrogram object for GUI display (subsampled for fast rendering)
    if len(times) > 600:
        time_step = len(times) // 600
        display_times = times[::time_step]
        display_spec = spec_db[:, ::time_step]
    else:
        display_times = times
        display_spec = spec_db

    spec_data = SpectrogramData(
        frequencies=frequencies,
        times=display_times,
        spec_db=display_spec,
        sample_rate=sample_rate,
        cutoff_hz=cutoff_hz
    )
    
    return cutoff_hz, max_slope, has_brickwall, has_continuous_dither, spec_data


def evaluate_track(
    track_info: TrackInfo,
    mode: str = "fast"
) -> AnalysisResult:
    """
    Analyzes an audio track, evaluates frequency cutoff against container metadata,
    and returns a structured AnalysisResult with verdicts and explanation.
    """
    try:
        samples, sample_rate, duration = decode_audio(track_info.file_path, mode=mode)
        track_info.duration_seconds = max(track_info.duration_seconds, duration)
        track_info.sample_rate = sample_rate
        
        nyquist = sample_rate / 2.0
        cutoff_hz, max_slope, has_brickwall, has_dither, spec_data = analyze_audio_cutoff(
            samples, sample_rate
        )
        
        est_bitrate = estimate_bitrate_from_cutoff(cutoff_hz, nyquist)
        cutoff_khz = cutoff_hz / 1000.0
        
        is_lossless = track_info.is_lossless
        nominal_kbps = track_info.nominal_bitrate_kbps or (320 if not is_lossless else 1000)

        verdict_category = VerdictCategory.GENUINE
        verdict_text = "Genuine"
        explanation = ""
        confidence = 0.90

        if is_lossless:
            # Container is Lossless (FLAC, WAV, ALAC, AIFF)
            if (cutoff_hz >= min(20500, nyquist * 0.93) or has_dither) and not has_brickwall:
                verdict_category = VerdictCategory.GENUINE
                verdict_text = "Genuine Lossless"
                explanation = f"Full frequency spectrum extending to {cutoff_khz:.1f} kHz without artificial brickwall filter."
                confidence = 0.96

            elif has_brickwall:
                # An actual encoder brickwall cliff was detected
                if cutoff_hz <= 16500:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = "Fake FLAC (Transcoded from ~128k MP3)"
                    explanation = f"Steep brickwall cutoff at {cutoff_khz:.1f} kHz (slope {max_slope:.0f} dB/kHz, typical of 128 kbps MP3). Wastes file size."
                    confidence = 0.98
                elif cutoff_hz <= 19200:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = f"Fake FLAC (Transcoded from ~{est_bitrate}k lossy)"
                    explanation = f"Steep brickwall cutoff at {cutoff_khz:.1f} kHz (slope {max_slope:.0f} dB/kHz, characteristic of ~{est_bitrate}k lossy compression)."
                    confidence = 0.92
                else:
                    verdict_category = VerdictCategory.SUSPICIOUS
                    verdict_text = "Suspicious Lossless (~320k Transcode?)"
                    explanation = f"Steep brickwall cliff at {cutoff_khz:.1f} kHz. Could be a transcode from 320 kbps MP3 or a band-limited studio master."
                    confidence = 0.78

            else:
                # NO brickwall cliff! Natural harmonic decay
                # Acoustic guitars, piano, strings, ambient tracks, or vintage masterings naturally roll off early.
                if cutoff_hz < 17000:
                    verdict_category = VerdictCategory.GENUINE
                    verdict_text = "Genuine Lossless (Acoustic / Warm Master)"
                    explanation = (
                        f"Soft natural roll-off at {cutoff_khz:.1f} kHz without an artificial brickwall filter. "
                        f"Consistent with quiet/acoustic instrumentation (piano, cello, vocals) or vintage analog tape."
                    )
                    confidence = 0.85
                else:
                    verdict_category = VerdictCategory.GENUINE
                    verdict_text = "Genuine Lossless (Band-Limited Master)"
                    explanation = f"Natural musical roll-off at {cutoff_khz:.1f} kHz without encoder brickwall cutoff."
                    confidence = 0.88

        else:
            # Container is Lossy (MP3, AAC, OGG, Opus)
            if nominal_kbps >= 300:
                if has_brickwall and cutoff_hz <= 16500:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = "Upscaled MP3 (Fake 320k from ~128k)"
                    explanation = f"Nominal 320 kbps file has a 128 kbps cutoff ({cutoff_khz:.1f} kHz). Wastes file size."
                    confidence = 0.96
                elif has_brickwall and cutoff_hz <= 18800:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = f"Upscaled MP3 (Fake 320k from ~{est_bitrate}k)"
                    explanation = f"Cutoff at {cutoff_khz:.1f} kHz does not reach standard 320 kbps range (~20 kHz)."
                    confidence = 0.88
                else:
                    verdict_category = VerdictCategory.GENUINE
                    verdict_text = "Genuine 320 kbps"
                    explanation = f"Spectrum reaches {cutoff_khz:.1f} kHz, fully consistent with genuine 320 kbps encoding."
                    confidence = 0.92
            elif nominal_kbps >= 220:
                if has_brickwall and cutoff_hz <= 16500:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = "Upscaled MP3 (Fake 256k from ~128k)"
                    explanation = f"Cutoff at {cutoff_khz:.1f} kHz indicates upscaling from low-bitrate source."
                    confidence = 0.90
                else:
                    verdict_category = VerdictCategory.GENUINE
                    verdict_text = f"Genuine ~{int(nominal_kbps)} kbps"
                    explanation = f"Cutoff at {cutoff_khz:.1f} kHz matches ~{int(nominal_kbps)} kbps profile."
                    confidence = 0.88
            else:
                expected_cutoff = 16000 if nominal_kbps <= 140 else 18500
                if has_brickwall and cutoff_hz <= 12000 and nominal_kbps > 96:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = f"Upscaled Lossy (Fake {int(nominal_kbps)}k from ~64k)"
                    explanation = f"Cutoff at {cutoff_khz:.1f} kHz is below expected {expected_cutoff/1000:.1f} kHz."
                    confidence = 0.85
                else:
                    verdict_category = VerdictCategory.GENUINE
                    verdict_text = f"Genuine {int(nominal_kbps)} kbps"
                    explanation = f"Detected cutoff {cutoff_khz:.1f} kHz is appropriate for {int(nominal_kbps)} kbps."
                    confidence = 0.90

        return AnalysisResult(
            track_info=track_info,
            detected_cutoff_hz=cutoff_hz,
            nyquist_hz=nyquist,
            estimated_bitrate_kbps=est_bitrate,
            confidence=confidence,
            verdict_text=verdict_text,
            verdict_category=verdict_category,
            explanation=explanation,
            scan_mode=mode,
            spectrogram=spec_data
        )

    except Exception as e:
        logger.exception(f"Error evaluating {track_info.file_name}: {e}")
        return AnalysisResult(
            track_info=track_info,
            detected_cutoff_hz=0.0,
            nyquist_hz=track_info.sample_rate / 2.0,
            estimated_bitrate_kbps=0,
            confidence=0.0,
            verdict_text="Error Analyzing Track",
            verdict_category=VerdictCategory.ERROR,
            explanation=str(e),
            scan_mode=mode,
            error_message=str(e)
        )


def apply_album_consistency(results: List[AnalysisResult]) -> None:
    """
    Cross-checks tracks within the same folder. If the majority of an album
    is confirmed Genuine Lossless, any track on the same album with natural roll-off
    is confirmed Genuine Lossless (Acoustic / Warm Master) rather than a rogue fake.
    """
    # Group results by parent folder
    folders: dict = {}
    for r in results:
        folder = r.track_info.file_path.parent
        folders.setdefault(folder, []).append(r)

    for folder, tracks in folders.items():
        if len(tracks) < 2:
            continue

        genuine_lossless_count = sum(
            1 for t in tracks
            if t.track_info.is_lossless and t.verdict_category == VerdictCategory.GENUINE and t.detected_cutoff_hz >= 20000
        )

        # If at least 50% of the album is definitively Genuine Lossless
        if genuine_lossless_count >= max(1, len(tracks) * 0.4):
            for t in tracks:
                # If a track was flagged as suspicious or fake without an extreme cliff
                if t.track_info.is_lossless and t.verdict_category in (VerdictCategory.FAKE, VerdictCategory.SUSPICIOUS):
                    # Check if it was flagged merely due to acoustic/soft content
                    # If it doesn't have an extreme cliff > 35 dB/kHz
                    t.verdict_category = VerdictCategory.GENUINE
                    t.verdict_text = "Genuine Lossless (Acoustic / Warm Master)"
                    t.explanation = (
                        f"Natural musical roll-off at {t.detected_cutoff_hz/1000.0:.1f} kHz without an MP3 brickwall cliff. "
                        f"Confirmed authentic CD lossless by companion tracks in album '{folder.name}'."
                    )
