#!/usr/bin/env python3
import subprocess
import json
import os
#from rich import print

def get_subtitle_tracks(video_path):
    """Return a list of subtitle tracks (with language and title if available)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams", "-select_streams", "s",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    tracks = []
    for i, stream in enumerate(info.get("streams", [])):
        lang = stream.get("tags", {}).get("language", "unknown")
        title = stream.get("tags", {}).get("title", "")
        codec = stream.get("codec_name", "unknown")
        tracks.append({
            "index": i,
            "codec": codec,
            "language": lang,
            "title": title
        })
    return tracks

def burn_subtitles(video_path, track_index, output_path):
    """Burn the selected subtitle track into the video using FFmpeg."""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-map", "0:v:0", "-map", "0:a?",  # video + all audio
        "-vf", f"subtitles='{video_path}:si={track_index}'",
        "-c:a", "copy",  # don’t re-encode audio
        output_path
    ]
    subprocess.run(cmd)

def main():
    video_path = input("Enter path to MKV file: ").strip()
    if not os.path.exists(video_path):
        print(f"[red]Error:[/red] File not found: {video_path}")
        return

    tracks = get_subtitle_tracks(video_path)
    if not tracks:
        print("[red]No subtitle tracks found.[/red]")
        return

    print("\n[bold]Available subtitle tracks:[/bold]")
    for i, t in enumerate(tracks):
        print(f"  [cyan]{i}[/cyan]: lang={t['language']}, codec={t['codec']}, title={t['title']}")

    choice = input("\nEnter the track number to burn: ").strip()
    try:
        choice = int(choice)
        track = tracks[choice]
    except (ValueError, IndexError):
        print("[red]Invalid choice.[/red]")
        return

    base, ext = os.path.splitext(video_path)
    output_path = f"{base}_burned{ext}"

    print(f"\nBurning subtitles (track {choice}: {track['language']}) into video...")
    burn_subtitles(video_path, track["index"], output_path)
    print(f"\n✅ Done! Output file: {output_path}")

if __name__ == "__main__":
    main()

