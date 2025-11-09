# burnsubs

Ett skript för att bränna in undertexter i video. Behövs när jag ska casta till min TV ibland.

## Installation

Detta skript kräver ffmpeg. Installera ffmpeg på ditt system:

- **Windows**: Ladda ner från https://ffmpeg.org/download.html eller använd chocolatey: `choco install ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt-get install ffmpeg` (eller motsvarande för din distribution)

Python's `tkinter` används för filväljaren (kommer med de flesta Python-installationer).

## Användning

### Filväljare (rekommenderat)

Kör skriptet utan argument för att öppna en grafisk filväljare:

```bash
python burnsubs.py
```

Du kommer att få välja:
1. Video-fil (kan vara var som helst på datorn)
2. Valfritt: Undertextfil (eller avbryt för att använda inbäddade undertexter från MKV)

### Med extern undertextfil:

```bash
python burnsubs.py video.mp4 -s subtitles.srt
```

### Med inbäddade undertexter från MKV-fil:

```bash
python burnsubs.py video.mkv
```

För MKV-filer kommer skriptet att:
1. Visa alla tillgängliga undertextspår med språk, codec och titel
2. Låta dig välja vilket undertextspår som ska brännas in
3. Visa alla tillgängliga ljudspår
4. Låta dig välja vilket ljudspår som ska inkluderas (eller tryck Enter för att behålla alla)

### Ytterligare alternativ:

```bash
# Ange utdatafil manuellt
python burnsubs.py video.mkv -o output.mp4

# Med extern undertextfil och anpassad utdatafil
python burnsubs.py video.mp4 -s subtitles.srt -o output.mp4
```

## Funktioner

- **Filväljare**: Grafiskt gränssnitt för att välja filer utan att behöva skriva sökvägar
- **Interaktiv spårval**: För MKV-filer kan du välja vilka undertext- och ljudspår som ska användas
- **Förloppsindikator**: Visar förlopp, hastighet och återstående tid under encoding
- **Stöd för flera format**: MP4, MKV, AVI, MOV, och fler
- **Inbäddade undertexter**: Automatisk extraktion från MKV-filer

## Exempel

```bash
# Använd filväljare
python burnsubs.py

# Bränn in undertexter från extern fil
python burnsubs.py movie.mp4 -s movie.srt

# Bränn in undertexter från MKV (interaktivt spårval)
python burnsubs.py movie.mkv

# Bränn in undertexter med anpassad utdatafil
python burnsubs.py movie.mkv -o movie_with_subs.mp4
```

## Förloppsindikator

Under encoding visas en förloppsindikator som visar:
- Visuell förloppsbar
- Procent färdigt
- Encoding-hastighet (t.ex. "2.5x")
- Uppskattad återstående tid (ETA)

Exempel:
```
Progress: [====================----------------] 50% | Speed: 2.3x | ETA: 5m 23s
```

---

## English

A script to burn subtitles into video files. Useful when casting to TV via VLC.

### Installation

This script requires ffmpeg. Install ffmpeg on your system:

- **Windows**: Download from https://ffmpeg.org/download.html or use chocolatey: `choco install ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt-get install ffmpeg` (or equivalent for your distribution)

Python's `tkinter` is used for the file picker (comes with most Python installations).

### Usage

#### File Picker (Recommended)

Run the script without arguments to open a graphical file picker:

```bash
python burnsubs.py
```

You will be prompted to select:
1. Video file (can be anywhere on your computer)
2. Optional: Subtitle file (or cancel to use embedded subtitles from MKV)

#### With External Subtitle File:

```bash
python burnsubs.py video.mp4 -s subtitles.srt
```

#### With Embedded Subtitles from MKV File:

```bash
python burnsubs.py video.mkv
```

For MKV files, the script will:
1. Display all available subtitle tracks with language, codec, and title
2. Let you choose which subtitle track to burn in
3. Display all available audio tracks
4. Let you choose which audio track to include (or press Enter to keep all)

#### Additional Options:

```bash
# Specify output file manually
python burnsubs.py video.mkv -o output.mp4

# With external subtitle file and custom output
python burnsubs.py video.mp4 -s subtitles.srt -o output.mp4
```

## Features

- **File Picker**: Graphical interface to select files without typing paths
- **Interactive Track Selection**: For MKV files, choose which subtitle and audio tracks to use
- **Progress Indicator**: Shows progress, speed, and remaining time during encoding
- **Multiple Format Support**: MP4, MKV, AVI, MOV, and more
- **Embedded Subtitles**: Automatic extraction from MKV files

## Examples

```bash
# Use file picker
python burnsubs.py

# Burn subtitles from external file
python burnsubs.py movie.mp4 -s movie.srt

# Burn subtitles from MKV (interactive track selection)
python burnsubs.py movie.mkv

# Burn subtitles with custom output file
python burnsubs.py movie.mkv -o movie_with_subs.mp4
```

## Progress Indicator

During encoding, a progress indicator displays:
- Visual progress bar
- Percentage complete
- Encoding speed (e.g., "2.5x")
- Estimated time remaining (ETA)

Example:
```
Progress: [====================----------------] 50% | Speed: 2.3x | ETA: 5m 23s
```
