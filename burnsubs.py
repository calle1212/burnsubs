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
import time
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
import threading

try:
    import tkinter as tk
    from tkinter import filedialog
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


def get_all_tracks(video_path):
    """Extract both subtitle and audio track information in a single ffprobe call."""
    try:
        # Get all streams and filter by codec_type (more reliable than select_streams syntax)
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        
        # Separate into subtitle and audio tracks
        subtitle_tracks = [s for s in streams if s.get('codec_type') == 'subtitle']
        audio_tracks = [s for s in streams if s.get('codec_type') == 'audio']
        
        return subtitle_tracks, audio_tracks
    except subprocess.CalledProcessError as e:
        print(f"Error probing video file: {e}", file=sys.stderr)
        if e.stderr:
            error_msg = e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr
            print(f"ffprobe error: {error_msg}", file=sys.stderr)
        return [], []
    except FileNotFoundError:
        print("Error: ffprobe not found. Please install ffmpeg.", file=sys.stderr)
        sys.exit(1)


def get_subtitle_tracks(video_path):
    """Extract subtitle track information from video file using ffprobe."""
    subtitle_tracks, _ = get_all_tracks(video_path)
    return subtitle_tracks


def get_audio_tracks(video_path):
    """Extract audio track information from video file using ffprobe."""
    _, audio_tracks = get_all_tracks(video_path)
    return audio_tracks


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


def get_track_signature(tracks):
    """Get a signature string representing the track structure."""
    signatures = []
    for track in tracks:
        tags = track.get('tags', {})
        lang = tags.get('language', 'unknown')
        codec = track.get('codec_name', 'unknown')
        signatures.append(f"{lang}:{codec}")
    return "|".join(signatures)


def analyze_video_tracks(video_path):
    """Analyze subtitle and audio tracks for a video file."""
    try:
        # Use single ffprobe call to get both track types
        subtitle_tracks, audio_tracks = get_all_tracks(video_path)
        return {
            'subtitle_signature': get_track_signature(subtitle_tracks),
            'audio_signature': get_track_signature(audio_tracks),
            'subtitle_tracks': subtitle_tracks,
            'audio_tracks': audio_tracks,
            'subtitle_count': len(subtitle_tracks),
            'audio_count': len(audio_tracks)
        }
    except Exception as e:
        print(f"Error analyzing {video_path}: {e}", file=sys.stderr)
        return None


def group_videos_by_tracks(video_paths):
    """Group videos by their track structure. Parallelized for better performance."""
    print("\nAnalyzing video files...")
    video_info = {}
    
    # Parallelize track analysis
    max_workers = min(cpu_count(), len(video_paths))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {executor.submit(analyze_video_tracks, path): path for path in video_paths}
        
        for future in as_completed(future_to_path):
            video_path = future_to_path[future]
            try:
                info = future.result()
                if info:
                    video_info[video_path] = info
                    print(f"  {os.path.basename(video_path)}: {info['subtitle_count']} subtitle(s), {info['audio_count']} audio track(s)")
            except Exception as e:
                print(f"  Error analyzing {os.path.basename(video_path)}: {e}", file=sys.stderr)
    
    # Group by track signatures
    subtitle_groups = defaultdict(list)
    audio_groups = defaultdict(list)
    
    for video_path, info in video_info.items():
        subtitle_groups[info['subtitle_signature']].append(video_path)
        audio_groups[info['audio_signature']].append(video_path)
    
    return video_info, subtitle_groups, audio_groups


def select_tracks_for_group(group_files, tracks, track_type, group_name):
    """Select tracks for a group of files with the same structure."""
    if not tracks:
        return None
    
    file_names = [os.path.basename(f) for f in group_files]
    if len(file_names) > 3:
        display = f"{file_names[0]}, {file_names[1]}, ... ({len(group_files)} files)"
    else:
        display = ", ".join(file_names)
    
    print(f"\n{track_type.capitalize()} tracks for group [{display}]:")
    for i, track in enumerate(tracks):
        print(format_track_info(track, i))
    
    while True:
        try:
            choice = input(f"\nSelect {track_type} track (0-{len(tracks)-1}, or Enter to use first): ").strip()
            if not choice:
                # Default to first track for both subtitle and audio
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
            choice = input(f"\nSelect audio track (0-{len(tracks)-1}, or press Enter to use first): ").strip()
            if not choice:
                # Return 0 to use first track
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


def process_single_video(args_tuple):
    """Process a single video file. Used for parallel processing.
    Args: (video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file, update_callback)
    """
    # Handle both with and without callback
    if len(args_tuple) == 6:
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file, update_callback = args_tuple
    else:
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file = args_tuple
        update_callback = None
    
    try:
        video_path_resolved = str(Path(video_path).resolve())
        file_name = os.path.basename(video_path)
        
        if subtitle_file:
            # External subtitle file
            subtitle_path = str(Path(subtitle_file).resolve())
            success = burn_subtitles_from_file(video_path_resolved, subtitle_path, output_path, audio_track_index, silent=True, file_name=file_name, update_callback=update_callback)
        else:
            # Embedded subtitles
            success = burn_subtitles_from_mkv(video_path_resolved, subtitle_track_index, output_path, audio_track_index, silent=True, file_name=file_name, update_callback=update_callback)
        
        return (video_path, output_path, success, None)
    except Exception as e:
        return (video_path, output_path, False, str(e))


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


# Thread-safe print lock for progress updates
progress_lock = threading.Lock()

# Shared progress dictionary for batch processing
batch_progress = {}
batch_progress_lock = threading.Lock()

# Compile regex patterns once at module level for better performance
DURATION_PATTERN = re.compile(r'Duration: (\d{2}:\d{2}:\d{2}\.\d{2})')
TIME_PATTERN = re.compile(r'time=(\d{2}:\d{2}:\d{2}\.\d{2})')
SPEED_PATTERN = re.compile(r'speed=\s*([\d.]+)x')

def show_progress(process, total_duration=None, silent=False, file_name=None, update_callback=None):
    """Show progress bar while ffmpeg is running."""
    if silent:
        # For parallel processing, show progress with file name
        
        duration_seconds = total_duration
        last_progress = 0
        last_update_time = time.time()
        
        # Read stderr line by line
        while True:
            line = process.stderr.readline()
            if not line:
                break
            
            if isinstance(line, bytes):
                line = line.decode('utf-8', errors='ignore')
            
            # Try to get duration from output if not provided
            if duration_seconds is None:
                duration_match = DURATION_PATTERN.search(line)
                if duration_match:
                    duration_seconds = parse_duration(duration_match.group(1))
            
            # Get current time
            time_match = TIME_PATTERN.search(line)
            if time_match and duration_seconds:
                current_time = parse_time(time_match.group(1))
                progress = min(current_time / duration_seconds, 1.0)
                
                # Get speed
                speed_match = SPEED_PATTERN.search(line)
                speed = speed_match.group(1) if speed_match else "?"
                
                # Update progress bar with file name
                bar_length = 30
                filled = int(bar_length * progress)
                bar = '=' * filled + '-' * (bar_length - filled)
                percent = int(progress * 100)
                
                # Only update if progress changed significantly OR enough time has passed (max 2 seconds)
                current_time_actual = time.time()
                time_since_update = current_time_actual - last_update_time
                should_update = (abs(progress - last_progress) > 0.05 or progress == 1.0) and time_since_update >= 0.5
                
                if should_update:
                    remaining = (duration_seconds - current_time) / float(speed) if speed != "?" and speed != "0" else 0
                    if remaining > 0:
                        remaining_str = f"{int(remaining//60)}m {int(remaining%60)}s"
                    else:
                        remaining_str = "calculating..."
                    
                    status_text = f"[{bar}] {percent:3d}% | {speed}x | ETA: {remaining_str}"
                    
                    # Use callback if provided, otherwise update shared dict
                    if update_callback:
                        # Callback can be called with (file_name, status) or just (status)
                        try:
                            update_callback(file_name, status_text)
                        except TypeError:
                            # Fallback if callback only accepts one argument
                            update_callback(status_text)
                    elif file_name:
                        with batch_progress_lock:
                            batch_progress[file_name] = status_text
                    
                    last_progress = progress
                    last_update_time = current_time_actual
        
        process.wait()
        return
    
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
            duration_match = DURATION_PATTERN.search(line)
            if duration_match:
                duration_seconds = parse_duration(duration_match.group(1))
        
        # Get current time
        time_match = TIME_PATTERN.search(line)
        if time_match and duration_seconds:
            current_time = parse_time(time_match.group(1))
            progress = min(current_time / duration_seconds, 1.0)
            
            # Get speed
            speed_match = SPEED_PATTERN.search(line)
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


def get_audio_codec_info(video_path, audio_track_index=None):
    """Get audio codec information for a specific track or first audio track."""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        
        audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
        if not audio_streams:
            return None, None
        
        # Select the appropriate audio stream
        if audio_track_index is not None and audio_track_index < len(audio_streams):
            selected_stream = audio_streams[audio_track_index]
        else:
            selected_stream = audio_streams[0]
        
        codec_name = selected_stream.get('codec_name', '')
        channels = selected_stream.get('channels', 2)
        
        return codec_name, channels
    except:
        return None, None


def should_convert_audio_to_aac(codec_name, output_path):
    """Determine if audio should be converted to AAC for Chromecast compatibility."""
    # Always convert to AAC for MP4 output to ensure Chromecast compatibility
    if output_path.lower().endswith('.mp4'):
        # Convert Opus and other non-AAC codecs to AAC
        if codec_name and codec_name.lower() not in ['aac', 'mp3']:
            return True
    return False


def burn_subtitles_from_file(video_path, subtitle_path, output_path, audio_track_index=None, silent=False, file_name=None, update_callback=None):
    """Burn subtitles from external subtitle file into video using ffmpeg."""
    try:
        # Escape the subtitle path properly for ffmpeg
        # On Windows, we need to escape backslashes and colons
        escaped_path = subtitle_path.replace('\\', '/').replace(':', '\\:')
        
        # Get video duration for progress bar (only if not silent)
        total_duration = None
        if not silent:
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
        
        # Check if we need to convert audio to AAC for Chromecast compatibility
        audio_codec, audio_channels = get_audio_codec_info(video_path, audio_track_index)
        convert_audio = should_convert_audio_to_aac(audio_codec, output_path)
        
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
        
        # Set audio codec - convert to AAC if needed for Chromecast compatibility
        if convert_audio:
            # Use AAC with appropriate bitrate based on channel count
            if audio_channels and audio_channels > 2:
                # 5.1 or more channels - use higher bitrate
                cmd.extend(['-c:a', 'aac', '-b:a', '256k'])
            else:
                # Stereo or mono - use standard bitrate
                cmd.extend(['-c:a', 'aac', '-b:a', '192k'])
        else:
            cmd.extend(['-c:a', 'copy'])
        
        cmd.extend([
            '-y',  # Overwrite output file
            output_path
        ])
        
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        show_progress(process, total_duration, silent=silent, file_name=file_name, update_callback=update_callback)
        process.wait()
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
        
        return True
    except subprocess.CalledProcessError as e:
        if not silent:
            print(f"\nError burning subtitles: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        if not silent:
            print("Error: ffmpeg not found. Please install ffmpeg.", file=sys.stderr)
        sys.exit(1)


def burn_subtitles_from_mkv(video_path, track_index, output_path, audio_track_index=None, silent=False, file_name=None, update_callback=None):
    """Burn subtitles directly from MKV file using track index (more efficient)."""
    try:
        # Use the si parameter to select subtitle track directly from the video file
        escaped_video_path = video_path.replace('\\', '/').replace(':', '\\:')
        
        # Get video duration for progress bar (only if not silent)
        total_duration = None
        if not silent:
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
        
        # Check if we need to convert audio to AAC for Chromecast compatibility
        audio_codec, audio_channels = get_audio_codec_info(video_path, audio_track_index)
        convert_audio = should_convert_audio_to_aac(audio_codec, output_path)
        
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
        
        # Set audio codec - convert to AAC if needed for Chromecast compatibility
        if convert_audio:
            # Use AAC with appropriate bitrate based on channel count
            if audio_channels and audio_channels > 2:
                # 5.1 or more channels - use higher bitrate
                cmd.extend(['-c:a', 'aac', '-b:a', '256k'])
            else:
                # Stereo or mono - use standard bitrate
                cmd.extend(['-c:a', 'aac', '-b:a', '192k'])
        else:
            cmd.extend(['-c:a', 'copy'])
        
        cmd.extend([
            '-vf', f"subtitles='{escaped_video_path}':si={track_index}",
            '-c:v', 'libx264',
            '-y',  # Overwrite output file
            output_path
        ])
        
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        show_progress(process, total_duration, silent=silent, file_name=file_name, update_callback=update_callback)
        process.wait()
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
        
        return True
    except subprocess.CalledProcessError as e:
        if not silent:
            print(f"\nError burning subtitles: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        if not silent:
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
    
    print("Please select video file(s)...")
    print("(Hold Ctrl/Cmd to select multiple files)")
    video_files = filedialog.askopenfilenames(
        title="Select Video File(s)",
        filetypes=video_extensions
    )
    
    if not video_files:
        print("No video files selected. Exiting.")
        sys.exit(0)
    
    video_files = list(video_files)
    print(f"Selected {len(video_files)} video file(s)")
    for i, vf in enumerate(video_files, 1):
        print(f"  {i}. {os.path.basename(vf)}")
    
    # For batch processing, we don't support external subtitle files
    # (would be too complex to match multiple files)
    if len(video_files) > 1:
        print("\nNote: Using embedded subtitles from video files for batch processing.")
        root.destroy()
        return video_files, None
    
    # Single file - ask for subtitle file
    print("\nDo you want to select an external subtitle file?")
    print("(Leave empty and press OK to use embedded subtitles from the video file)")
    subtitle_file = filedialog.askopenfilename(
        title="Select Subtitle File (Optional - Cancel to use embedded subtitles)",
        filetypes=subtitle_extensions
    )
    
    root.destroy()
    
    return video_files, subtitle_file if subtitle_file else None


def process_batch_mkv(video_files):
    """Process multiple MKV files with smart track selection and parallel processing."""
    # Filter to only MKV files
    mkv_files = [f for f in video_files if Path(f).suffix.lower() == '.mkv']
    non_mkv_files = [f for f in video_files if Path(f).suffix.lower() != '.mkv']
    
    if non_mkv_files:
        print(f"\nWarning: Skipping {len(non_mkv_files)} non-MKV file(s). Batch processing only supports MKV files.")
        for f in non_mkv_files:
            print(f"  - {os.path.basename(f)}")
    
    if not mkv_files:
        print("Error: No MKV files found for batch processing.", file=sys.stderr)
        sys.exit(1)
    
    # Analyze and group files
    video_info, subtitle_groups, audio_groups = group_videos_by_tracks(mkv_files)
    
    # Build track selection map - initialize all videos
    track_selections = {video_path: (None, None) for video_path in mkv_files}
    
    # Handle subtitle track selection
    print("\n" + "="*60)
    print("SUBTITLE TRACK SELECTION")
    print("="*60)
    
    for sig, group_files in subtitle_groups.items():
        if len(group_files) == 0:
            continue
        
        # Get tracks from first file in group (they should all be the same)
        first_file = group_files[0]
        tracks = video_info[first_file]['subtitle_tracks']
        
        if not tracks:
            # No subtitles, mark as None
            for f in group_files:
                current = track_selections.get(f, (None, None))
                track_selections[f] = (None, current[1])
            continue
        
        # Select track for this group
        subtitle_index = select_tracks_for_group(group_files, tracks, 'subtitle', f"Group {len(subtitle_groups)}")
        
        # Apply to all files in group
        for f in group_files:
            current = track_selections.get(f, (None, None))
            track_selections[f] = (subtitle_index, current[1])
    
    # Handle audio track selection
    print("\n" + "="*60)
    print("AUDIO TRACK SELECTION")
    print("="*60)
    
    for sig, group_files in audio_groups.items():
        if len(group_files) == 0:
            continue
        
        # Get tracks from first file in group
        first_file = group_files[0]
        tracks = video_info[first_file]['audio_tracks']
        
        if not tracks:
            # No audio tracks, leave as None (will map all tracks)
            continue
        
        # Select track for this group
        audio_index = select_tracks_for_group(group_files, tracks, 'audio', f"Group {len(audio_groups)}")
        
        # Apply to all files in group
        for f in group_files:
            current = track_selections.get(f, (None, None))
            track_selections[f] = (current[0], audio_index)
    
    # Prepare processing tasks
    processing_tasks = []
    for video_path in mkv_files:
        if video_path not in track_selections:
            print(f"Warning: No track selection for {os.path.basename(video_path)}, skipping.", file=sys.stderr)
            continue
        
        subtitle_idx, audio_idx = track_selections[video_path]
        if subtitle_idx is None:
            print(f"Warning: No subtitle track selected for {os.path.basename(video_path)}, skipping.", file=sys.stderr)
            continue
        
        # Determine output path
        video_path_obj = Path(video_path)
        output_path = str(video_path_obj.parent / (video_path_obj.stem + '_with_subs.mp4'))
        
        processing_tasks.append((video_path, subtitle_idx, audio_idx, output_path, None))
    
    if not processing_tasks:
        print("Error: No valid processing tasks created.", file=sys.stderr)
        sys.exit(1)
    
    # Process in parallel
    max_workers = min(cpu_count(), len(processing_tasks))
    
    # Initialize progress tracking
    file_names = [os.path.basename(task[0]) for task in processing_tasks]
    file_line_map = {}  # Map file names to their line numbers
    with batch_progress_lock:
        batch_progress.clear()
        for name in file_names:
            batch_progress[name] = "Starting..."
    
    # Print initial header and file list (only once)
    print("\n" + "="*60)
    print(f"PROCESSING {len(processing_tasks)} FILE(S) IN PARALLEL")
    print("="*60)
    print(f"Using {max_workers} worker(s)...\n")
    
    # Print each file on its own line and track line numbers
    for idx, name in enumerate(file_names):
        print(f"{name[:45]:<45} Starting...")
        file_line_map[name] = idx + 5  # +5 for header lines above
    sys.stdout.flush()
    
    def update_file_progress(file_name, status):
        """Update progress for a specific file on its line."""
        if file_name not in file_line_map:
            return
        
        line_num = file_line_map[file_name]
        # Move cursor to the line, clear it, and write new status
        # Use ANSI escape codes: \033[nA moves up n lines, \033[K clears to end of line
        # We need to calculate how many lines from bottom
        total_lines = len(file_names) + 5
        lines_from_bottom = total_lines - line_num
        
        with batch_progress_lock:
            batch_progress[file_name] = status
            # Move to beginning of line, clear it, write new content
            print(f"\033[{lines_from_bottom}A\r\033[K{file_name[:45]:<45} {status}", end='', flush=True)
            # Move cursor back down
            if lines_from_bottom > 0:
                print(f"\033[{lines_from_bottom}B", end='', flush=True)
    
    completed = 0
    failed = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks with update callback
        future_to_video = {}
        for task in processing_tasks:
            video_path = task[0]
            video_name = os.path.basename(video_path)
            # Create callback for this specific file (use default parameter to avoid closure issue)
            def make_callback(name):
                # Callback accepts (file_name, status) but we use the captured name
                def callback_func(file_name, status):
                    update_file_progress(name, status)
                return callback_func
            callback = make_callback(video_name)
            task_with_callback = task + (callback,)
            future_to_video[executor.submit(process_single_video, task_with_callback)] = video_path
        
        # Process completed tasks
        for future in as_completed(future_to_video):
            video_path, output_path, success, error = future.result()
            completed += 1
            video_name = os.path.basename(video_path)
            
            if success:
                update_file_progress(video_name, f"✓ Complete -> {os.path.basename(output_path)}")
            else:
                update_file_progress(video_name, f"✗ Error: {error}")
                failed.append((video_path, error))
    
    # Move cursor to end
    print("\n" + "="*60)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Completed: {completed - len(failed)}/{len(processing_tasks)}")
    if failed:
        print(f"Failed: {len(failed)}")
        for video_path, error in failed:
            print(f"  - {os.path.basename(video_path)}: {error}")


def main():
    parser = argparse.ArgumentParser(
        description='Burn subtitles into video files. Supports external subtitle files and embedded subtitles in MKV files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s video.mp4 -s subtitles.srt
  %(prog)s video.mkv  (interactive track selection for MKV files)
  %(prog)s  (runs file picker if no arguments provided - supports multiple files)
        """
    )
    parser.add_argument('video', nargs='*', help='Input video file(s) (optional - file picker will open if not provided)')
    parser.add_argument('-s', '--subtitle', dest='subtitle_file', 
                       help='External subtitle file (optional, will use embedded subtitles from MKV if not provided)')
    parser.add_argument('-o', '--output', dest='output_file',
                       help='Output video file (default: input_filename_with_subs.mp4) - only for single file')
    
    args = parser.parse_args()
    
    # If no video file provided, use file picker
    if not args.video:
        video_files, subtitle_file = pick_files()
        if isinstance(video_files, str):
            video_files = [video_files]
        args.video = video_files
        if subtitle_file:
            args.subtitle_file = subtitle_file
    else:
        video_files = args.video if isinstance(args.video, list) else [args.video]
    
    # Validate input video files
    valid_files = []
    for video_file in video_files:
        video_path = Path(video_file)
        if not video_path.exists():
            print(f"Error: Video file '{video_file}' not found.", file=sys.stderr)
        else:
            valid_files.append(str(video_path.resolve()))
    
    if not valid_files:
        sys.exit(1)
    
    # Batch processing (multiple files)
    if len(valid_files) > 1:
        # Batch processing only supports MKV files with embedded subtitles
        process_batch_mkv(valid_files)
        return
    
    # Single file processing
    video_path = Path(valid_files[0])
    
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
