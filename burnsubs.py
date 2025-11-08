#!/usr/bin/env python3
"""
Burn subtitles into video files.
Supports external subtitle files and embedded subtitles in MKV files.
For MKV files, interactively selects subtitle and audio tracks.
"""

import argparse
import subprocess
import sys
import os
import tempfile
import json
import re
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


def get_subtitle_tracks(video_path):
    """Extract subtitle track information from video file using ffprobe."""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 's',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return data.get('streams', [])
    except subprocess.CalledProcessError as e:
        print(f"Error probing video file: {e}", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("Error: ffprobe not found. Please install ffmpeg.", file=sys.stderr)
        sys.exit(1)


def get_audio_tracks(video_path):
    """Extract audio track information from video file using ffprobe."""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 'a',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return data.get('streams', [])
    except subprocess.CalledProcessError as e:
        print(f"Error probing video file: {e}", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("Error: ffprobe not found. Please install ffmpeg.", file=sys.stderr)
        sys.exit(1)


def extract_subtitle_track(video_path, track_index, output_path):
    """Extract a subtitle track from video file to a temporary file."""
    try:
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-map', f'0:s:{track_index}',
            '-c:s', 'srt',
            '-y',  # Overwrite output file
            output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error extracting subtitle track: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("Error: ffmpeg not found. Please install ffmpeg.", file=sys.stderr)
        sys.exit(1)


def format_track_info(track, index):
    """Format track information for display."""
    tags = track.get('tags', {})
    lang = tags.get('language', 'unknown')
    title = tags.get('title', '')
    codec = track.get('codec_name', 'unknown')
    
    info_parts = [f"Language: {lang}", f"Codec: {codec}"]
    if title:
        info_parts.append(f"Title: {title}")
    
    return f"  [{index}] {' | '.join(info_parts)}"


def select_subtitle_track(video_path):
    """Interactively select a subtitle track from available tracks."""
    tracks = get_subtitle_tracks(video_path)
    
    if not tracks:
        return None
    
    print("\nAvailable subtitle tracks:")
    for i, track in enumerate(tracks):
        print(format_track_info(track, i))
    
    while True:
        try:
            choice = input(f"\nSelect subtitle track (0-{len(tracks)-1}): ").strip()
            if not choice:
                # Default to first track if empty
                return 0
            track_index = int(choice)
            if 0 <= track_index < len(tracks):
                return track_index
            else:
                print(f"Please enter a number between 0 and {len(tracks)-1}")
        except ValueError:
            print("Please enter a valid number")
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(0)


def select_audio_track(video_path):
    """Interactively select an audio track from available tracks."""
    tracks = get_audio_tracks(video_path)
    
    if not tracks:
        return None
    
    print("\nAvailable audio tracks:")
    for i, track in enumerate(tracks):
        print(format_track_info(track, i))
    
    while True:
        try:
            choice = input(f"\nSelect audio track (0-{len(tracks)-1}, or press Enter to keep all): ").strip()
            if not choice:
                # Return None to keep all audio tracks
                return None
            track_index = int(choice)
            if 0 <= track_index < len(tracks):
                return track_index
            else:
                print(f"Please enter a number between 0 and {len(tracks)-1}")
        except ValueError:
            print("Please enter a valid number")
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(0)


def parse_duration(duration_str):
    """Parse duration string (HH:MM:SS.ms) to seconds."""
    try:
        parts = duration_str.split(':')
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        return 0
    except:
        return 0


def parse_time(time_str):
    """Parse time string (HH:MM:SS.ms) to seconds."""
    return parse_duration(time_str)


def show_progress(process, total_duration=None):
    """Show progress bar while ffmpeg is running."""
    duration_pattern = re.compile(r'Duration: (\d{2}:\d{2}:\d{2}\.\d{2})')
    time_pattern = re.compile(r'time=(\d{2}:\d{2}:\d{2}\.\d{2})')
    speed_pattern = re.compile(r'speed=\s*([\d.]+)x')
    
    duration_seconds = total_duration
    last_progress = 0
    
    # Read stderr line by line
    while True:
        line = process.stderr.readline()
        if not line:
            break
        
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors='ignore')
        
        # Try to get duration from output if not provided
        if duration_seconds is None:
            duration_match = duration_pattern.search(line)
            if duration_match:
                duration_seconds = parse_duration(duration_match.group(1))
        
        # Get current time
        time_match = time_pattern.search(line)
        if time_match and duration_seconds:
            current_time = parse_time(time_match.group(1))
            progress = min(current_time / duration_seconds, 1.0)
            
            # Get speed
            speed_match = speed_pattern.search(line)
            speed = speed_match.group(1) if speed_match else "?"
            
            # Update progress bar
            bar_length = 40
            filled = int(bar_length * progress)
            bar = '=' * filled + '-' * (bar_length - filled)
            percent = int(progress * 100)
            
            # Only update if progress changed significantly
            if abs(progress - last_progress) > 0.01 or progress == 1.0:
                remaining = (duration_seconds - current_time) / float(speed) if speed != "?" and speed != "0" else 0
                if remaining > 0:
                    remaining_str = f"{int(remaining//60)}m {int(remaining%60)}s"
                else:
                    remaining_str = "calculating..."
                print(f"\rProgress: [{bar}] {percent}% | Speed: {speed}x | ETA: {remaining_str}", end='', flush=True)
                last_progress = progress
        
        # Print other important messages (but skip common ffmpeg info lines)
        if 'error' in line.lower() and not any(x in line.lower() for x in ['frame=', 'fps=', 'bitrate=', 'time=']):
            print(f"\n{line.strip()}")
    
    print()  # New line after progress


def burn_subtitles_from_file(video_path, subtitle_path, output_path, audio_track_index=None):
    """Burn subtitles from external subtitle file into video using ffmpeg."""
    try:
        # Escape the subtitle path properly for ffmpeg
        # On Windows, we need to escape backslashes and colons
        escaped_path = subtitle_path.replace('\\', '/').replace(':', '\\:')
        
        # Get video duration for progress bar
        cmd_probe = [
            'ffprobe',
            '-v', 'quiet',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path
        ]
        try:
            result = subprocess.run(cmd_probe, capture_output=True, text=True, check=True)
            total_duration = float(result.stdout.strip())
        except:
            total_duration = None
        
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f"subtitles='{escaped_path}'",
            '-c:v', 'libx264',
        ]
        
        # Map audio tracks
        if audio_track_index is not None:
            cmd.extend(['-map', '0:a:' + str(audio_track_index)])
        else:
            cmd.extend(['-map', '0:a?'])
        
        cmd.extend([
            '-c:a', 'copy',
            '-y',  # Overwrite output file
            output_path
        ])
        
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        show_progress(process, total_duration)
        process.wait()
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
        
        return True
    except subprocess.CalledProcessError as e:
        print(f"\nError burning subtitles: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("Error: ffmpeg not found. Please install ffmpeg.", file=sys.stderr)
        sys.exit(1)


def burn_subtitles_from_mkv(video_path, track_index, output_path, audio_track_index=None):
    """Burn subtitles directly from MKV file using track index (more efficient)."""
    try:
        # Use the si parameter to select subtitle track directly from the video file
        escaped_video_path = video_path.replace('\\', '/').replace(':', '\\:')
        
        # Get video duration for progress bar
        cmd_probe = [
            'ffprobe',
            '-v', 'quiet',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path
        ]
        try:
            result = subprocess.run(cmd_probe, capture_output=True, text=True, check=True)
            total_duration = float(result.stdout.strip())
        except:
            total_duration = None
        
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-map', '0:v:0',  # Map video stream
        ]
        
        # Map audio tracks
        if audio_track_index is not None:
            cmd.extend(['-map', '0:a:' + str(audio_track_index)])
        else:
            cmd.extend(['-map', '0:a?'])
        
        cmd.extend([
            '-vf', f"subtitles='{escaped_video_path}':si={track_index}",
            '-c:v', 'libx264',
            '-c:a', 'copy',   # Don't re-encode audio
            '-y',  # Overwrite output file
            output_path
        ])
        
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        show_progress(process, total_duration)
        process.wait()
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
        
        return True
    except subprocess.CalledProcessError as e:
        print(f"\nError burning subtitles: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("Error: ffmpeg not found. Please install ffmpeg.", file=sys.stderr)
        sys.exit(1)


def pick_files():
    """Open file picker dialogs to select video and optional subtitle files."""
    if not TKINTER_AVAILABLE:
        print("Error: tkinter not available. Please provide file paths as arguments.", file=sys.stderr)
        print("Alternatively, install tkinter for your Python installation.", file=sys.stderr)
        sys.exit(1)
    
    # Hide the root window
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)  # Bring dialog to front
    
    # Common video file extensions
    video_extensions = [
        ('Video files', '*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v'),
        ('All files', '*.*')
    ]
    
    # Common subtitle file extensions
    subtitle_extensions = [
        ('Subtitle files', '*.srt *.ass *.ssa *.vtt *.sub'),
        ('All files', '*.*')
    ]
    
    print("Please select a video file...")
    video_file = filedialog.askopenfilename(
        title="Select Video File",
        filetypes=video_extensions
    )
    
    if not video_file:
        print("No video file selected. Exiting.")
        sys.exit(0)
    
    print(f"Selected video: {os.path.basename(video_file)}")
    
    # Ask if user wants to select a subtitle file
    print("\nDo you want to select an external subtitle file?")
    print("(Leave empty and press OK to use embedded subtitles from the video file)")
    subtitle_file = filedialog.askopenfilename(
        title="Select Subtitle File (Optional - Cancel to use embedded subtitles)",
        filetypes=subtitle_extensions
    )
    
    root.destroy()
    
    return video_file, subtitle_file if subtitle_file else None


def main():
    parser = argparse.ArgumentParser(
        description='Burn subtitles into video files. Supports external subtitle files and embedded subtitles in MKV files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s video.mp4 -s subtitles.srt
  %(prog)s video.mkv  (interactive track selection for MKV files)
  %(prog)s  (runs file picker if no arguments provided)
        """
    )
    parser.add_argument('video', nargs='?', help='Input video file (optional - file picker will open if not provided)')
    parser.add_argument('-s', '--subtitle', dest='subtitle_file', 
                       help='External subtitle file (optional, will use embedded subtitles from MKV if not provided)')
    parser.add_argument('-o', '--output', dest='output_file',
                       help='Output video file (default: input_filename_with_subs.mp4)')
    
    args = parser.parse_args()
    
    # If no video file provided, use file picker
    if not args.video:
        video_file, subtitle_file = pick_files()
        args.video = video_file
        if subtitle_file:
            args.subtitle_file = subtitle_file
    
    # Validate input video file
    video_path = Path(args.video)
    if not video_path.exists():
        print(f"Error: Video file '{args.video}' not found.", file=sys.stderr)
        sys.exit(1)
    
    # Determine output file
    if args.output_file:
        output_path = args.output_file
    else:
        output_path = video_path.stem + '_with_subs.mp4'
    
    video_path_resolved = str(video_path.resolve())
    
    # Determine if this is an MKV file (for interactive track selection)
    is_mkv = video_path.suffix.lower() == '.mkv'
    audio_track_index = None
    
    # Get subtitle file or track index
    if args.subtitle_file:
        # Use provided subtitle file
        subtitle_path = Path(args.subtitle_file)
        if not subtitle_path.exists():
            print(f"Error: Subtitle file '{args.subtitle_file}' not found.", file=sys.stderr)
            sys.exit(1)
        subtitle_path = str(subtitle_path.resolve())
        
        # For MKV files, allow audio track selection
        if is_mkv:
            audio_track_index = select_audio_track(video_path_resolved)
        
        # Burn subtitles from external file
        print(f"\nBurning subtitles from external file into video...")
        print(f"Output will be saved as: {output_path}")
        
        if not burn_subtitles_from_file(video_path_resolved, subtitle_path, output_path, audio_track_index):
            sys.exit(1)
    else:
        # Try to use embedded subtitles from video file (for MKV files)
        if is_mkv:
            print(f"\nChecking for embedded subtitles in '{video_path.name}'...")
            subtitle_track_index = select_subtitle_track(video_path_resolved)
            
            if subtitle_track_index is None:
                print("Error: No subtitle tracks found in video file.", file=sys.stderr)
                sys.exit(1)
            
            # Allow audio track selection
            audio_track_index = select_audio_track(video_path_resolved)
            
            # Burn subtitles directly from MKV (more efficient than extracting first)
            print(f"\nBurning subtitles into video...")
            print(f"Output will be saved as: {output_path}")
            
            if not burn_subtitles_from_mkv(video_path_resolved, subtitle_track_index, output_path, audio_track_index):
                sys.exit(1)
        else:
            print("Error: No subtitle file provided and video file doesn't appear to have embedded subtitles.", file=sys.stderr)
            print("Please provide a subtitle file with -s option.", file=sys.stderr)
            sys.exit(1)
    
    print(f"Success! Subtitles burned into '{output_path}'")


if __name__ == '__main__':
    main()
