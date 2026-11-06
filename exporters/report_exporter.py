import csv
import json
from pathlib import Path
from typing import List
from datetime import datetime

from core.models import AnalysisResult, VerdictCategory


def export_to_csv(results: List[AnalysisResult], destination_path: Path):
    """Exports analysis results to a CSV spreadsheet."""
    destination_path = Path(destination_path)
    
    headers = [
        "File Name",
        "Verdict",
        "Category",
        "Detected Cutoff (kHz)",
        "Estimated Bitrate (kbps)",
        "Container",
        "Nominal Bitrate (kbps)",
        "Sample Rate (Hz)",
        "Channels",
        "Bit Depth",
        "Duration",
        "File Size (MB)",
        "Confidence (%)",
        "Scan Mode",
        "Explanation",
        "Full Path"
    ]
    
    with open(destination_path, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        
        for r in results:
            info = r.track_info
            est_quality = "Lossless" if (r.verdict_category == VerdictCategory.GENUINE and info.is_lossless) else (f"~{r.estimated_bitrate_kbps} kbps" if r.estimated_bitrate_kbps else "N/A")
            writer.writerow([
                info.file_name,
                r.verdict_text,
                r.verdict_category.value,
                f"{r.detected_cutoff_hz / 1000.0:.2f}" if r.detected_cutoff_hz > 0 else "N/A",
                est_quality,
                info.container,
                f"{info.nominal_bitrate_kbps:.1f}" if info.nominal_bitrate_kbps else "N/A",
                info.sample_rate,
                info.channels,
                info.bit_depth or "N/A",
                info.duration_formatted,
                f"{info.file_size_mb:.2f}",
                f"{int(r.confidence * 100)}%",
                r.scan_mode,
                r.explanation,
                str(info.file_path)
            ])


def export_to_json(results: List[AnalysisResult], destination_path: Path):
    """Exports analysis results to a formatted JSON report."""
    destination_path = Path(destination_path)
    
    records = []
    for r in results:
        info = r.track_info
        records.append({
            "file_name": info.file_name,
            "file_path": str(info.file_path),
            "verdict": r.verdict_text,
            "category": r.verdict_category.value,
            "detected_cutoff_hz": r.detected_cutoff_hz,
            "detected_cutoff_khz": round(r.detected_cutoff_hz / 1000.0, 2),
            "estimated_bitrate_kbps": r.estimated_bitrate_kbps,
            "confidence": r.confidence,
            "container": info.container,
            "nominal_bitrate_kbps": info.nominal_bitrate_kbps,
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "bit_depth": info.bit_depth,
            "duration_seconds": round(info.duration_seconds, 2),
            "file_size_bytes": info.file_size_bytes,
            "scan_mode": r.scan_mode,
            "explanation": r.explanation,
            "error_message": r.error_message
        })
        
    summary = {
        "generated_at": datetime.now().isoformat(),
        "total_files": len(results),
        "genuine_count": sum(1 for r in results if r.verdict_category == VerdictCategory.GENUINE),
        "fake_count": sum(1 for r in results if r.verdict_category == VerdictCategory.FAKE),
        "suspicious_count": sum(1 for r in results if r.verdict_category == VerdictCategory.SUSPICIOUS),
        "error_count": sum(1 for r in results if r.verdict_category == VerdictCategory.ERROR),
        "results": records
    }
    
    with open(destination_path, mode="w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


def export_to_html(results: List[AnalysisResult], destination_path: Path):
    """Exports a self-contained, stylish HTML report."""
    destination_path = Path(destination_path)
    
    total = len(results)
    genuine = sum(1 for r in results if r.verdict_category == VerdictCategory.GENUINE)
    fake = sum(1 for r in results if r.verdict_category == VerdictCategory.FAKE)
    suspicious = sum(1 for r in results if r.verdict_category == VerdictCategory.SUSPICIOUS)
    errors = sum(1 for r in results if r.verdict_category == VerdictCategory.ERROR)
    
    rows_html = []
    for r in results:
        info = r.track_info
        est_quality = "Lossless" if (r.verdict_category == VerdictCategory.GENUINE and info.is_lossless) else f"~{r.estimated_bitrate_kbps} kbps"
        badge_class = {
            VerdictCategory.GENUINE: "badge-genuine",
            VerdictCategory.FAKE: "badge-fake",
            VerdictCategory.SUSPICIOUS: "badge-suspicious",
            VerdictCategory.ERROR: "badge-error"
        }.get(r.verdict_category, "badge-unknown")
        
        rows_html.append(f"""
        <tr>
            <td><strong>{info.file_name}</strong><br><small style="color: #888;">{info.file_path.parent}</small></td>
            <td><span class="badge {badge_class}">{r.verdict_text}</span></td>
            <td>{r.detected_cutoff_hz/1000.0:.1f} kHz</td>
            <td>{est_quality}</td>
            <td>{info.container} ({f'{info.nominal_bitrate_kbps:.0f}k' if info.nominal_bitrate_kbps else 'lossless'})</td>
            <td>{info.sample_rate / 1000.0:.1f} kHz</td>
            <td>{info.duration_formatted}</td>
            <td><small>{r.explanation}</small></td>
        </tr>
        """)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Audio Quality & Fake Transcode Audit Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #121316;
            color: #e1e3e6;
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #282a30;
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        h1 {{ margin: 0; font-size: 24px; color: #fff; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: #1a1c22;
            padding: 16px;
            border-radius: 8px;
            border: 1px solid #282a30;
        }}
        .stat-val {{ font-size: 28px; font-weight: bold; margin-bottom: 4px; }}
        .stat-label {{ font-size: 13px; color: #9aa0a6; text-transform: uppercase; letter-spacing: 0.5px; }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #1a1c22;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #282a30;
        }}
        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid #23252b;
        }}
        th {{
            background: #20222a;
            color: #9aa0a6;
            font-size: 13px;
            text-transform: uppercase;
        }}
        tr:hover td {{
            background: #22242d;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-genuine {{ background: rgba(46, 204, 113, 0.18); color: #2ecc71; border: 1px solid #2ecc71; }}
        .badge-fake {{ background: rgba(231, 76, 60, 0.2); color: #ff5e57; border: 1px solid #ff5e57; }}
        .badge-suspicious {{ background: rgba(241, 196, 15, 0.2); color: #f1c40f; border: 1px solid #f1c40f; }}
        .badge-error {{ background: rgba(149, 165, 166, 0.2); color: #bdc3c7; border: 1px solid #bdc3c7; }}
    </style>
</head>
<body>
<div class="container">
    <header>
        <div>
            <h1>Audio Quality & Fake Transcode Audit</h1>
            <p style="color: #9aa0a6; margin: 4px 0 0 0;">Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </header>
    
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-val" style="color: #fff;">{total}</div>
            <div class="stat-label">Total Tracks Analyzed</div>
        </div>
        <div class="stat-card">
            <div class="stat-val" style="color: #2ecc71;">{genuine}</div>
            <div class="stat-label">Genuine</div>
        </div>
        <div class="stat-card">
            <div class="stat-val" style="color: #ff5e57;">{fake}</div>
            <div class="stat-label">Fakes / Transcodes</div>
        </div>
        <div class="stat-card">
            <div class="stat-val" style="color: #f1c40f;">{suspicious}</div>
            <div class="stat-label">Suspicious / Ambiguous</div>
        </div>
    </div>
    
    <table>
        <thead>
            <tr>
                <th>Track / Path</th>
                <th>Verdict</th>
                <th>Cutoff</th>
                <th>Est. Quality</th>
                <th>Format</th>
                <th>Sample Rate</th>
                <th>Duration</th>
                <th>Reason / Details</th>
            </tr>
        </thead>
        <tbody>
            {"".join(rows_html)}
        </tbody>
    </table>
</div>
</body>
</html>
"""
    with open(destination_path, mode="w", encoding="utf-8") as f:
        f.write(html_content)
