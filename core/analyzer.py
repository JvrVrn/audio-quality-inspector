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


def _detect_brickwall_in_profile(
    frequencies: np.ndarray,
    smoothed: np.ndarray,
    nyquist: float,
    p_peak: float,
    search_hi_hz: float = 20650.0,
    min_slope_override: Optional[float] = None,
    min_drop_override: Optional[float] = None,
) -> Tuple[Optional[float], float]:
    """
    Scans a smoothed frequency profile (10.5 kHz .. search_hi_hz) for a sharp
    encoder brickwall cliff whose upper shelf remains below the musical passband.
    Returns (brickwall_freq_hz_or_None, cliff_or_max_slope_db_per_khz).
    """
    search_hi = min(nyquist - 500.0, search_hi_hz)
    best_score = 0.0
    cliff_slope = 0.0
    max_observed_slope = 0.0
    brickwall_freq: Optional[float] = None

    if search_hi <= 10500.0:
        return None, 0.0

    for fc in np.arange(10500.0, search_hi, 50.0):
        lo_i0 = int(np.searchsorted(frequencies, fc - 550.0, side="left"))
        lo_i1 = int(np.searchsorted(frequencies, fc - 150.0, side="right"))
        hi_i0 = int(np.searchsorted(frequencies, fc + 150.0, side="left"))
        hi_i1 = int(np.searchsorted(frequencies, min(nyquist - 50.0, fc + 750.0), side="right"))
        broad_i0 = int(np.searchsorted(frequencies, max(8000.0, fc - 2000.0), side="left"))
        shelf_i0 = int(np.searchsorted(frequencies, fc + 350.0, side="left"))
        shelf_i1 = int(np.searchsorted(frequencies, min(nyquist - 100.0, fc + 3000.0, 23000.0), side="right"))
        full_shelf_i1 = int(np.searchsorted(frequencies, min(nyquist - 100.0, 23000.0), side="right"))

        if lo_i1 <= lo_i0 or hi_i1 <= hi_i0 or shelf_i1 <= shelf_i0 or lo_i1 <= broad_i0:
            continue

        val_lower = float(np.percentile(smoothed[lo_i0:lo_i1], 75))
        val_upper = float(np.median(smoothed[hi_i0:hi_i1]))
        broad_lower_med = float(np.median(smoothed[broad_i0:lo_i1]))
        shelf_slice = smoothed[shelf_i0:shelf_i1]
        shelf_med = float(np.median(shelf_slice))
        shelf_p90 = float(np.percentile(shelf_slice, 90))
        full_shelf_med = float(np.median(smoothed[shelf_i0:full_shelf_i1]))

        drop_db = val_lower - val_upper

        nlo_i0 = int(np.searchsorted(frequencies, fc - 350.0, side="left"))
        nlo_i1 = int(np.searchsorted(frequencies, fc - 100.0, side="right"))
        nhi_i0 = int(np.searchsorted(frequencies, fc + 100.0, side="left"))
        nhi_i1 = int(np.searchsorted(frequencies, fc + 350.0, side="right"))
        if nlo_i1 > nlo_i0 and nhi_i1 > nhi_i0:
            n_lo = float(np.median(smoothed[nlo_i0:nlo_i1]))
            n_hi = float(np.median(smoothed[nhi_i0:nhi_i1]))
            slope = (n_lo - n_hi) / 0.45
        else:
            slope = drop_db / 0.6

        if slope > max_observed_slope:
            max_observed_slope = slope

        into_void = (shelf_med <= -95.0)
        min_drop = min_drop_override if min_drop_override is not None else (10.0 if into_void else 14.0)
        min_slope = min_slope_override if min_slope_override is not None else (18.0 if into_void else 22.0)
        min_broad = 8.0 if into_void else 12.0

        shelf_stays_below = (
            (shelf_p90 < max(val_lower - 8.0, -100.0))
            and (full_shelf_med < max(val_lower - 6.0, -75.0))
        )
        upper_is_quiet = (shelf_med < p_peak - 40.0) and (shelf_med < -76.0)

        if (
            slope >= min_slope
            and drop_db >= min_drop
            and (broad_lower_med - shelf_med) >= min_broad
            and shelf_stays_below
            and upper_is_quiet
        ):
            score = drop_db + 0.5 * slope
            if score > best_score:
                best_score = score
                cliff_slope = slope
                mid_db = (val_lower + val_upper) / 2.0
                tr_i0 = int(np.searchsorted(frequencies, fc - 500.0, side="left"))
                tr_i1 = int(np.searchsorted(frequencies, fc + 500.0, side="right"))
                trans_f = frequencies[tr_i0:tr_i1]
                trans_v = smoothed[tr_i0:tr_i1]
                below_mid = trans_f[trans_v <= mid_db]
                brickwall_freq = float(below_mid[0]) if len(below_mid) > 0 else float(fc)

    return brickwall_freq, (cliff_slope if brickwall_freq is not None else max_observed_slope)


def _analyze_spectrum_details(
    samples: np.ndarray,
    sample_rate: int,
    n_fft: int = 4096,
    hop_length: int = 1024
) -> Tuple[float, float, bool, bool, SpectrogramData, Optional[float], float, bool]:
    """
    Computes STFT and evaluates 97th/99th/50th-percentile active-frame profiles
    to catch hard brickwalls, sfb21 median zeroing, and dither-masked 320k lowpass steps.
    """
    nyquist = sample_rate / 2.0
    nperseg = max(64, min(n_fft, len(samples)))
    noverlap = min(nperseg - 1, max(0, nperseg - hop_length))

    frequencies, times, Zxx = signal.stft(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        boundary=None
    )

    mag = np.abs(Zxx)
    max_mag = float(np.max(mag))
    if max_mag <= 0:
        max_mag = 1e-12

    spec_db = 20.0 * np.log10(np.maximum(mag / max_mag, 1e-7))

    # Select active musical frames so quiet intros/outros don't mask spectral energy
    active_band_mask = (frequencies >= 200) & (frequencies <= min(10000.0, nyquist * 0.9))
    if np.any(active_band_mask) and spec_db.shape[1] >= 10:
        frame_energy = np.percentile(spec_db[active_band_mask, :], 90, axis=0)
        peak_frame_energy = float(np.max(frame_energy))
        active_indices = np.where(frame_energy >= peak_frame_energy - 25.0)[0]
        active_spec_db = spec_db[:, active_indices] if len(active_indices) >= 5 else spec_db
    else:
        active_spec_db = spec_db

    bin_hz = sample_rate / nperseg
    window_bins = max(3, int(110 / bin_hz))
    if window_bins % 2 == 0:
        window_bins += 1

    smoothed_p99 = signal.medfilt(np.percentile(active_spec_db, 99, axis=1), kernel_size=window_bins)
    smoothed_p97 = signal.medfilt(np.percentile(active_spec_db, 97, axis=1), kernel_size=window_bins)
    smoothed_p95 = signal.medfilt(np.percentile(active_spec_db, 95, axis=1), kernel_size=window_bins)
    smoothed_p50 = signal.medfilt(np.percentile(active_spec_db, 50, axis=1), kernel_size=window_bins)

    passband_mask = (frequencies >= 1000) & (frequencies <= min(10000.0, nyquist * 0.8))
    p_peak_99 = float(np.max(smoothed_p99[passband_mask])) if np.any(passband_mask) else 0.0
    p_peak_97 = float(np.max(smoothed_p97[passband_mask])) if np.any(passband_mask) else 0.0
    p_peak_50 = float(np.max(smoothed_p50[passband_mask])) if np.any(passband_mask) else 0.0

    # Primary brickwall check on active 97th-percentile envelope (capped at 20.65 kHz so 21+ kHz CD filters don't false-trigger)
    bw_p97, slope_p97 = _detect_brickwall_in_profile(
        frequencies, smoothed_p97, nyquist, p_peak_97, search_hi_hz=20650.0
    )
    # Secondary brickwall check on 50th-percentile (catches sfb21 ~16 kHz zeroing even when 3% clipping splatter fills p97)
    bw_p50, slope_p50 = _detect_brickwall_in_profile(
        frequencies, smoothed_p50, nyquist, p_peak_50, search_hi_hz=20650.0,
        min_slope_override=30.0, min_drop_override=22.0
    )

    has_brickwall = bw_p97 is not None
    brickwall_freq = bw_p97
    reported_slope = slope_p97

    if not has_brickwall and bw_p50 is not None:
        has_brickwall = True
        brickwall_freq = bw_p50
        reported_slope = slope_p50

    # Check high-bitrate (~320k) MP3 signatures that survive 16-bit dither or relaxed lowpass filters
    is_high_bitrate_transcode = False
    if nyquist >= 21800.0:
        m_15_16 = (frequencies >= 15200) & (frequencies <= 16200)
        m_pre = (frequencies >= 19400) & (frequencies <= 20100)
        m_post = (frequencies >= 20500) & (frequencies <= 21100)
        m_top = (frequencies >= 21200) & (frequencies <= min(nyquist - 100.0, 22000.0))

        if np.any(m_15_16) and np.any(m_pre) and np.any(m_post) and np.any(m_top):
            pre_99 = float(np.median(smoothed_p99[m_pre]))
            post_99 = float(np.median(smoothed_p99[m_post]))
            mid_99 = float(np.median(smoothed_p99[m_15_16]))
            top_95 = float(np.median(smoothed_p95[m_top]))
            slope_16_19 = (mid_99 - pre_99) / 4.05
            step_drop = pre_99 - post_99

            # Rule 1: Accelerated 20.0-20.5 kHz lowpass step in p99 above 16-bit TPDF dither floor
            rule1 = (
                pre_99 >= max(-85.0, p_peak_99 - 62.0)
                and step_drop >= max(8.0, 2.0 * max(0.0, slope_16_19))
                and top_95 <= min(-92.0, pre_99 - 12.0)
            )
        else:
            rule1 = False
            step_drop = 0.0

        # Rule 2: MP3 sfb21 psychoacoustic zeroing + 21.36 kHz (31/32 Nyquist) PQMF subband-31 cutoff
        m_17_20 = (frequencies >= 17500) & (frequencies <= 20000)
        m_20_21 = (frequencies >= 20200) & (frequencies <= 21200)
        m_21_22 = (frequencies >= 21400) & (frequencies <= 22000)
        if nyquist >= 21900.0 and np.any(m_17_20) and np.any(m_20_21) and np.any(m_21_22):
            fz20 = float(np.mean(np.median(active_spec_db[m_20_21, :], axis=0) < -115.0))
            fz21 = float(np.mean(np.median(active_spec_db[m_21_22, :], axis=0) < -115.0))
            hi_trans_99 = float(np.median(smoothed_p99[m_17_20]))
            rule2 = (fz20 >= 0.45 and fz21 >= 0.85 and hi_trans_99 >= -82.0)
        else:
            rule2 = False

        if rule1 or rule2:
            is_high_bitrate_transcode = True
            if not has_brickwall:
                has_brickwall = True
                brickwall_freq = 20300.0 if rule1 else 21300.0
                if rule1:
                    reported_slope = max(reported_slope, step_drop / 0.5)

    dither_lo = min(21000.0, nyquist * 0.94)
    dither_mask = (frequencies >= dither_lo) & (frequencies <= nyquist - 100.0)
    dither_energy = float(np.median(smoothed_p97[dither_mask])) if np.any(dither_mask) else -120.0
    has_continuous_dither = (
        (dither_energy > max(-90.0, p_peak_97 - 55.0))
        and not has_brickwall
        and not is_high_bitrate_transcode
    )

    threshold_db = max(-85.0, p_peak_97 - 55.0)
    upper_band_mask = frequencies >= nyquist * 0.94
    has_full_nyquist_energy = np.any(smoothed_p97[upper_band_mask] >= threshold_db)

    if has_brickwall and brickwall_freq is not None:
        cutoff_hz = min(nyquist, brickwall_freq)
    elif has_full_nyquist_energy:
        cutoff_hz = nyquist
    else:
        active_bins = np.where(smoothed_p97 >= threshold_db)[0]
        if len(active_bins) > 0:
            cutoff_hz = min(nyquist, float(frequencies[active_bins[-1]]))
        else:
            cutoff_hz = 12000.0

    cutoff_hz = min(cutoff_hz, nyquist)

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

    return cutoff_hz, reported_slope, has_brickwall, has_continuous_dither, spec_data, bw_p50, slope_p50, is_high_bitrate_transcode


def analyze_audio_cutoff(
    samples: np.ndarray,
    sample_rate: int,
    n_fft: int = 4096,
    hop_length: int = 1024
) -> Tuple[float, float, bool, bool, SpectrogramData]:
    """
    Computes STFT, evaluates frequency profile across active musical frames,
    and detects frequency cutoff, brickwall cliffs, and high-frequency dither/noise floor.

    Returns:
        (detected_cutoff_hz, cliff_slope_db_per_khz, has_brickwall, has_continuous_dither, spectrogram_data)
    """
    cutoff_hz, reported_slope, has_brickwall, has_dither, spec_data, _, _, _ = _analyze_spectrum_details(
        samples, sample_rate, n_fft=n_fft, hop_length=hop_length
    )
    return cutoff_hz, reported_slope, has_brickwall, has_dither, spec_data


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
        cutoff_hz, max_slope, has_brickwall, has_dither, spec_data, bw_p50, slope_p50, is_high_bitrate_transcode = _analyze_spectrum_details(
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
            if cutoff_hz >= min(20800, nyquist * 0.94) and not has_brickwall:
                verdict_category = VerdictCategory.GENUINE
                verdict_text = "Genuine Lossless"
                explanation = f"Full frequency spectrum extending to {cutoff_khz:.1f} kHz without artificial brickwall filter."
                confidence = 0.96

            elif has_dither and not has_brickwall:
                verdict_category = VerdictCategory.GENUINE
                if cutoff_hz >= min(19000, nyquist * 0.86):
                    verdict_text = "Genuine Lossless"
                    explanation = f"Continuous high-frequency dither/noise floor with natural roll-off at {cutoff_khz:.1f} kHz and no brickwall filter."
                    confidence = 0.93
                else:
                    verdict_text = "Genuine Lossless (Acoustic / Warm Master)"
                    explanation = (
                        f"Natural roll-off at {cutoff_khz:.1f} kHz with continuous CD dither/analog noise floor "
                        f"and no artificial brickwall filter."
                    )
                    confidence = 0.90

            elif has_brickwall:
                # An actual encoder brickwall cliff was detected
                if cutoff_hz <= 16800:
                    verdict_category = VerdictCategory.FAKE
                    if cutoff_hz < 14500:
                        verdict_text = f"Fake FLAC (Transcoded from ~{est_bitrate}k lossy)"
                    else:
                        verdict_text = "Fake FLAC (Transcoded from ~128k MP3)"
                    explanation = f"Steep brickwall cutoff at {cutoff_khz:.1f} kHz (slope {max_slope:.0f} dB/kHz, typical of low-bitrate MP3). Wastes file size."
                    confidence = 0.98
                elif cutoff_hz <= 19800:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = f"Fake FLAC (Transcoded from ~{est_bitrate}k lossy)"
                    explanation = f"Steep brickwall cutoff at {cutoff_khz:.1f} kHz (slope {max_slope:.0f} dB/kHz, characteristic of ~{est_bitrate}k lossy compression)."
                    confidence = 0.94
                elif cutoff_hz <= 20850:
                    if (max_slope >= 25.0 or is_high_bitrate_transcode) and not has_dither:
                        verdict_category = VerdictCategory.FAKE
                        verdict_text = "Fake FLAC (Transcoded from ~320k MP3)"
                        explanation = (
                            f"Brickwall / lowpass cutoff at {cutoff_khz:.1f} kHz (slope {max_slope:.0f} dB/kHz) "
                            f"with dead high-frequency floor, characteristic of a 320 kbps MP3 transcode."
                        )
                        confidence = 0.88
                    else:
                        verdict_category = VerdictCategory.SUSPICIOUS
                        verdict_text = "Suspicious Lossless (~320k Transcode?)"
                        explanation = f"Brickwall cliff at {cutoff_khz:.1f} kHz (slope {max_slope:.0f} dB/kHz). Could be a 320 kbps MP3 transcode or a band-limited master."
                        confidence = 0.78
                elif is_high_bitrate_transcode:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = "Fake FLAC (Transcoded from ~320k MP3)"
                    explanation = (
                        f"MP3 psychoacoustic zeroing and PQMF subband cutoff at {cutoff_khz:.1f} kHz "
                        f"with silent upper band, characteristic of a high-bitrate MP3 transcode."
                    )
                    confidence = 0.86
                else:
                    verdict_category = VerdictCategory.SUSPICIOUS
                    verdict_text = "Suspicious Lossless (Band-Limited Master)"
                    explanation = f"Brickwall cliff at {cutoff_khz:.1f} kHz. Could be an upsampled 44.1 kHz source or band-limited master."
                    confidence = 0.72

            else:
                # NO brickwall cliff! Natural harmonic decay
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
                if has_brickwall and cutoff_hz <= 16800:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = "Upscaled MP3 (Fake 320k from ~128k)"
                    explanation = f"Nominal 320 kbps file has a ~128 kbps cutoff ({cutoff_khz:.1f} kHz). Wastes file size."
                    confidence = 0.96
                elif has_brickwall and cutoff_hz <= 19400:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = f"Upscaled MP3 (Fake 320k from ~{est_bitrate}k)"
                    explanation = f"Cutoff at {cutoff_khz:.1f} kHz does not reach standard 320 kbps range (~20 kHz)."
                    confidence = 0.88
                elif bw_p50 is not None and bw_p50 <= 17500 and slope_p50 >= 35.0:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = "Upscaled MP3 (Fake 320k from ~170-220k VBR)"
                    explanation = (
                        f"Median active spectrum has a sharp {bw_p50/1000.0:.1f} kHz sfb21 cutoff (slope {slope_p50:.0f} dB/kHz) "
                        f"characteristic of a lower-bitrate VBR/MP3 source re-encoded to 320 kbps."
                    )
                    confidence = 0.90
                else:
                    verdict_category = VerdictCategory.GENUINE
                    verdict_text = "Genuine 320 kbps"
                    explanation = f"Spectrum reaches {cutoff_khz:.1f} kHz, fully consistent with genuine 320 kbps encoding."
                    confidence = 0.92
            elif nominal_kbps >= 220:
                if has_brickwall and cutoff_hz <= 16800:
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
                if has_brickwall and cutoff_hz <= 15500 and nominal_kbps >= 170:
                    verdict_category = VerdictCategory.FAKE
                    verdict_text = f"Upscaled Lossy (Fake {int(nominal_kbps)}k from ~{est_bitrate}k)"
                    explanation = f"Cutoff at {cutoff_khz:.1f} kHz is below expected ~18.5 kHz for {int(nominal_kbps)} kbps."
                    confidence = 0.86
                elif has_brickwall and cutoff_hz <= 12000 and nominal_kbps > 96:
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
            spectrogram=spec_data,
            has_brickwall=has_brickwall,
            cliff_slope_db_per_khz=max_slope,
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
    is confirmed Genuine Lossless, any suspicious track on the same album without
    an encoder brickwall cliff is confirmed Genuine Lossless (Acoustic / Warm Master).
    Confirmed FAKE tracks or tracks with detected brickwall cliffs are never overridden.
    """
    folders: dict = {}
    for r in results:
        folder = r.track_info.file_path.parent
        folders.setdefault(folder, []).append(r)

    for folder, tracks in folders.items():
        if len(tracks) < 3:
            continue

        genuine_lossless_count = sum(
            1 for t in tracks
            if t.track_info.is_lossless and t.verdict_category == VerdictCategory.GENUINE and t.detected_cutoff_hz >= 20000
        )

        # Only rescue uncertain SUSPICIOUS tracks without brickwall cliffs when >= 60% of the album is confirmed Genuine Lossless
        if genuine_lossless_count >= max(2, len(tracks) * 0.6):
            for t in tracks:
                if (
                    t.track_info.is_lossless
                    and t.verdict_category == VerdictCategory.SUSPICIOUS
                    and not t.has_brickwall
                    and t.cliff_slope_db_per_khz < 20.0
                ):
                    t.verdict_category = VerdictCategory.GENUINE
                    t.verdict_text = "Genuine Lossless (Acoustic / Warm Master)"
                    t.explanation = (
                        f"Natural musical roll-off at {t.detected_cutoff_hz/1000.0:.1f} kHz without an MP3 brickwall cliff. "
                        f"Confirmed authentic CD lossless by companion tracks in album '{folder.name}'."
                    )
