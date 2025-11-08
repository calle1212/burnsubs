# burnsubs

Ett skript för att bränna in undertexter i video. Behövs när jag ska casta till min TV ibland.

## Installation

Detta skript kräver ffmpeg. Installera ffmpeg på ditt system:

- **Windows**: Ladda ner från https://ffmpeg.org/download.html eller använd chocolatey: `choco install ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt-get install ffmpeg` (eller motsvarande för din distribution)

## Användning

### Med extern undertextfil:
```bash
python burnsubs.py video.mp4 -s subtitles.srt
```

### Med inbäddade undertexter från MKV-fil:
```bash
python burnsubs.py video.mkv
```

Skriptet kommer automatiskt att välja den bästa undertextspåret baserat på prioritetsfilen.

### Anpassa språkprioritet:

Redigera `subtitle_priority.txt` för att ange önskade språk i prioritetsordning. Exempel:
```
en sv jp
```

Detta betyder: prioritera engelska, sedan svenska, sedan japanska. Om inget av dessa språk finns tillgängligt, väljs det första tillgängliga spåret.

### Ytterligare alternativ:
```bash
# Ange utdatafil manuellt
python burnsubs.py video.mkv -o output.mp4

# Använd en annan prioritetsfil
python burnsubs.py video.mkv -p my_priority.txt
```

## Exempel

```bash
# Bränn in undertexter från extern fil
python burnsubs.py movie.mp4 -s movie.srt

# Bränn in undertexter från MKV (använder prioritetsfilen)
python burnsubs.py movie.mkv

# Bränn in undertexter med anpassad utdatafil
python burnsubs.py movie.mkv -o movie_with_subs.mp4
```

---

## English

A script to burn subtitles into video files. Useful when casting to TV via VLC.

### Usage

**With external subtitle file:**
```bash
python burnsubs.py video.mp4 -s subtitles.srt
```

**With embedded subtitles from MKV file:**
```bash
python burnsubs.py video.mkv
```

The script will automatically select the best subtitle track based on the priority file.

**Customize language priority:**

Edit `subtitle_priority.txt` to specify preferred languages in priority order. Example:
```
en sv jp
```

This means: prioritize English, then Swedish, then Japanese. If none of these languages are available, the first available track will be selected.
