# Audio Quality Inspector

A desktop GUI application that inspects audio files (single tracks or entire directory hierarchies) on **macOS**, **Windows**, and **Linux** to verify whether they truly leverage all the space they occupy. It detects lossy transcodes masquerading as lossless (e.g. MP3 128 kbps converted to FLAC/WAV) and low-bitrate lossy files upscaled to higher bitrates (e.g. 128 kbps re-encoded to 320 kbps MP3).

---

## ✨ Features

- **Cross-Platform (macOS, Windows, Linux)**:
  - Full native support on macOS (Apple Silicon M1/M2/M3/M4 & Intel) and Windows (10/11).
  - Consistent modern dark theme rendered with Qt's Fusion engine.
- **Accurate Frequency & Transcode Detection**:
  - Distinguishes **Genuine Lossless (CD/Hi-Res)** from **Fake FLACs** and **Upscaled MP3s**.
  - Identifies artificial brickwall filters (e.g. 15–16 kHz for ~128 kbps, 18–19 kHz for ~192 kbps, 20.5 kHz for 320 kbps).
  - Distinguishes artificial cliffs from natural vintage/acoustic roll-offs.
- **Automatic Folder Grouping & Hierarchical Tree View**:
  - **Smart Multi-Folder Grouping**: When an analyzed folder contains subfolders or multiple directories, tracks are automatically organized into expandable folder groups showing aggregate track counts and fake counts (`📁 Album Name (12 tracks — 🔴 2 Fakes)`).
  - **Single-Click Toggle**: Switch between **Grouped by Folder** tree view and **Flat list** view using the `📁 Group by Folder` toolbar button.
- **Go to Folder / Reveal in Finder & Explorer**:
  - **Dedicated Buttons**: One-click `📁 Go to Folder` button in the top toolbar and directly within the spectrogram inspector header.
  - **Direct Actions**: Double-clicking any track opens its containing folder and highlights the file in **macOS Finder** (`open -R`) or **Windows File Explorer** (`explorer /select`).
  - **Keyboard Shortcut**: `⌘O` on macOS / `Ctrl+O` on Windows to immediately reveal the selected track in Finder / Explorer.
  - **Context Menu**: Right-click any file or folder to "Go to Folder", copy file paths, or copy directory paths.
- **Integrated Spectrogram Waterfall Viewer (like Spek)**:
  - Instant high-resolution visual frequency plot with dB magnitude color mapping.
  - Reference guidelines for 15 kHz (128k MP3 limit), 19 kHz (192k limit), 20.5 kHz (320k limit), and 22.05 kHz (CD Nyquist).
  - Cyan marker line highlighting the detected mathematical cutoff frequency.
- **Configurable Scan Modes**:
  - ⚡ **Fast Scan**: Samples active 30s musical windows past track intros for lightning-fast batch processing across large music libraries.
  - 🔍 **Deep Scan**: Decodes and audits 100% of audio frames from start to finish.
- **Non-blocking Multi-threaded Batch Processing**:
  - Live progress updates with responsive Pause, Resume, and Cancel controls.
- **Filtering & Search**:
  - Quick-filter buttons: `All`, `🔴 Fakes Only`, `🟢 Genuine Only`, `🟡 Suspicious Only`.
  - Instant text search across track names, formats, cutoffs, folders, and explanations.
- **Comprehensive Audit Reporting**:
  - One-click export to **CSV**, **JSON**, and standalone styled **HTML** reports.
- **Universal Format Support (PyAV / FFmpeg Engine)**:
  - Supports `.flac`, `.wav`, `.mp3`, `.m4a`, `.aac`, `.ogg`, `.opus`, `.alac`, `.aiff`, `.ape`, `.wv` without requiring external FFmpeg binaries installed on your system.
- **Drag & Drop**:
  - Drag audio files or whole music folders straight from **macOS Finder** or **Windows File Explorer** into the window.
  - Supports macOS Dock drag-and-drop and "Open With...".

---

## 🚀 Quick Start

Ensure Python 3.10+ is installed.

### macOS & Linux

You can run the launcher script directly (which automatically manages dependencies and virtual environments):

```bash
./run.sh
```

Or run directly with Python:

```bash
# Setup virtual environment and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Launch application
python3 app.py
```

*(Note: `python3 app.py` also features automatic virtual environment bootstrapping — if `.venv` exists, it will automatically run using it.)*

You can also pass a directory or file directly:

```bash
./run.sh ~/Music/Albums
```

### Windows

Run the launcher script:

```bat
run.bat
```

Or from PowerShell / Command Prompt:

```bat
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt

python app.py "D:\Music\Albums"
```

---

## 🧪 Running Tests

To verify the cross-platform file reveal (Finder/Explorer), DSP analyzer, transcode detection, folder grouping, report exporters, and headless GUI:

```bash
python3 tests/test_full_suite.py
```

---

## 📐 How Detection Works

Lossy codecs (MP3, AAC, OGG) discard psychoacoustically inaudible frequencies to save space:
- **MP3 ~128 kbps**: Brickwall lowpass filter around **15.5 – 16.0 kHz**.
- **MP3 ~192 kbps**: Cutoff around **18.5 – 19.0 kHz**.
- **MP3 ~320 kbps**: Cutoff around **20.0 – 20.5 kHz**.
- **Genuine CD FLAC (16-bit / 44.1 kHz)**: Spectrum extends smoothly up to the **22.05 kHz Nyquist limit** without artificial cliff drops.

When someone converts a 128 kbps MP3 to FLAC, the container is 800–1000 kbps, but the audio content above 16 kHz remains dead silence or noise-shaped artifacts. This program identifies the true frequency cutoff, flags the discrepancy, calculates estimated real bitrate, and alerts you with a visual explanation.
