#!/usr/bin/env python3
"""
Burn subtitles into video files using FFmpeg or HandBrake CLI.

Features:
- Supports external subtitle files (SRT, ASS, SSA, VTT, SUB)
- Supports embedded subtitles in MKV files (text-based and image-based)
- Interactive subtitle and audio track selection for MKV files
- Batch processing for multiple MKV files with smart track grouping
- Progressive playback support (play video in VLC while encoding)
- Automatic audio codec conversion to AAC for Chromecast compatibility
"""

import argparse
import subprocess
import sys
import os
import tempfile
import json
import re
import time
import shutil
from pathlib import Path
from collections import defaultdict

# Optional GUI support for file picker dialogs
try:
    import tkinter as tk
    from tkinter import filedialog
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

# Threading support for background VLC launch during encoding
try:
    import threading
except ImportError:
    pass


def get_all_tracks(video_path):
    """Extract both subtitle and audio track information in a single ffprobe call.
    
    Args:
        video_path: Path to the video file to analyze
        
    Returns:
        tuple: (subtitle_tracks, audio_tracks) - lists of stream dictionaries
        
    Raises:
        SystemExit: If ffprobe is not found or fails
    """
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
    """Extract subtitle track information from video file using ffprobe.
    
    Args:
        video_path: Path to the video file
        
    Returns:
        list: List of subtitle stream dictionaries, empty if none found
    """
    subtitle_tracks, _ = get_all_tracks(video_path)
    return subtitle_tracks


def get_audio_tracks(video_path):
    """Extract audio track information from video file using ffprobe.
    
    Args:
        video_path: Path to the video file
        
    Returns:
        list: List of audio stream dictionaries, empty if none found
    """
    _, audio_tracks = get_all_tracks(video_path)
    return audio_tracks


def is_image_based_subtitle(codec_name):
    """Check if subtitle codec is image-based (not text-based).
    
    Image-based subtitles (VobSub, PGS, DVB) require special handling as they
    cannot be processed directly by FFmpeg's subtitles filter from MKV containers.
    
    Args:
        codec_name: Subtitle codec name (e.g., 'dvd_subtitle', 'hdmv_pgs_subtitle')
        
    Returns:
        bool: True if codec is image-based, False otherwise
    """
    image_based_codecs = [
        'dvd_subtitle',      # VobSub (DVD subtitles)
        'hdmv_pgs_subtitle', # PGS (Blu-ray subtitles)
        'dvb_subtitle',      # DVB (Digital Video Broadcasting)
        'xsub',              # XSUB (Xbox subtitles)
        'vobsub'             # VobSub (alternative name)
    ]
    return codec_name.lower() in image_based_codecs


def format_track_info(track, index):
    """Format track information for display in interactive selection prompts.
    
    Args:
        track: Stream dictionary from ffprobe output
        index: Zero-based track index
        
    Returns:
        str: Formatted string showing track index, language, codec, and optional title
    """
    tags = track.get('tags', {})
    lang = tags.get('language', 'unknown')
    title = tags.get('title', '')
    codec = track.get('codec_name', 'unknown')
    
    # Add indicator for image-based subtitles
    subtitle_type = " (Image-based)" if is_image_based_subtitle(codec) else ""
    info_parts = [f"Language: {lang}", f"Codec: {codec}{subtitle_type}"]
    if title:
        info_parts.append(f"Title: {title}")
    
    return f"  [{index}] {' | '.join(info_parts)}"


def get_track_signature(tracks):
    """Generate a signature string representing the track structure.
    
    Used to group videos with identical track configurations for batch processing.
    Signature format: "lang:codec|lang:codec|..."
    
    Args:
        tracks: List of track stream dictionaries
        
    Returns:
        str: Signature string representing the track structure
    """
    signatures = []
    for track in tracks:
        tags = track.get('tags', {})
        lang = tags.get('language', 'unknown')
        codec = track.get('codec_name', 'unknown')
        signatures.append(f"{lang}:{codec}")
    return "|".join(signatures)


def analyze_video_tracks(video_path):
    """Analyze subtitle and audio tracks for a video file.
    
    Extracts track information and generates signatures for grouping videos
    with identical track structures during batch processing.
    
    Args:
        video_path: Path to the video file
        
    Returns:
        dict: Dictionary containing track info, signatures, and counts, or None on error
    """
    try:
        # Use single ffprobe call to get both track types (more efficient)
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
    """Group videos by their track structure for efficient batch processing.
    
    Videos with identical subtitle and audio track structures are grouped together,
    allowing users to select tracks once per group instead of per file.
    
    Args:
        video_paths: List of video file paths
        
    Returns:
        tuple: (video_info dict, subtitle_groups dict, audio_groups dict)
    """
    print("\nAnalyzing video files...")
    video_info = {}
    
    # Process sequentially to avoid overwhelming system resources
    for video_path in video_paths:
        try:
            info = analyze_video_tracks(video_path)
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
    """Interactively select a track for a group of files with identical structure.
    
    Args:
        group_files: List of file paths in this group
        tracks: List of track dictionaries to choose from
        track_type: Type of track ('subtitle' or 'audio')
        group_name: Display name for the group
        
    Returns:
        int: Selected track index (0-based), or None if no tracks available
    """
    if not tracks:
        return None
    
    # Display file names in group (truncate if too many)
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
                # Default to first track (index 0) for both subtitle and audio
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
    """Process a single video file. Used for sequential or parallel processing.
    
    Supports flexible argument tuple lengths for backward compatibility:
    - 8 args: Full tuple with play_during_encode and auto_launch_vlc
    - 7 args: With play_during_encode but no auto_launch_vlc
    - 6 args: With callback but no play_during_encode
    - 5 args: Basic tuple without callback or play_during_encode
    
    Args:
        args_tuple: Tuple containing (video_path, subtitle_track_index, audio_track_index,
                    output_path, subtitle_file, update_callback, play_during_encode, auto_launch_vlc)
                    (last 3 are optional)
        
    Returns:
        tuple: (video_path, output_path, success: bool, error: str or None)
    """
    # Handle different tuple lengths for backward compatibility
    tuple_len = len(args_tuple)
    
    if tuple_len == 8:
        # Full tuple with play_during_encode and auto_launch_vlc
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file, update_callback, play_during_encode, auto_launch_vlc = args_tuple
        silent = update_callback is not None  # Silent if callback provided (for parallel)
    elif tuple_len == 7:
        # Full tuple with play_during_encode but no auto_launch_vlc
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file, update_callback, play_during_encode = args_tuple
        auto_launch_vlc = True  # Default to True for backward compatibility
        silent = update_callback is not None  # Silent if callback provided (for parallel)
    elif tuple_len == 6:
        # Tuple with callback but no play_during_encode
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file, update_callback = args_tuple
        play_during_encode = False
        auto_launch_vlc = True  # Default to True
        silent = update_callback is not None
    else:
        # Tuple without callback or play_during_encode
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file = args_tuple
        update_callback = None
        play_during_encode = False
        auto_launch_vlc = True  # Default to True
        silent = False  # Show progress for sequential processing
    
    try:
        video_path_resolved = str(Path(video_path).resolve())
        file_name = os.path.basename(video_path)
        
        if subtitle_file:
            # External subtitle file
            subtitle_path = str(Path(subtitle_file).resolve())
            success = burn_subtitles_from_file(video_path_resolved, subtitle_path, output_path, audio_track_index, silent=silent, file_name=file_name, update_callback=update_callback, play_during_encode=play_during_encode, auto_launch_vlc=auto_launch_vlc)
        else:
            # Embedded subtitles
            success = burn_subtitles_from_mkv(video_path_resolved, subtitle_track_index, output_path, audio_track_index, silent=silent, file_name=file_name, update_callback=update_callback, play_during_encode=play_during_encode, auto_launch_vlc=auto_launch_vlc)
        
        return (video_path, output_path, success, None)
    except Exception as e:
        return (video_path, output_path, False, str(e))


def parse_duration(duration_str):
    """Parse duration string (HH:MM:SS.ms) to total seconds.
    
    Used to parse FFmpeg duration and time output for progress calculation.
    
    Args:
        duration_str: Time string in format "HH:MM:SS.ms" or "HH:MM:SS"
        
    Returns:
        float: Total duration in seconds, or 0 if parsing fails
    """
    try:
        parts = duration_str.split(':')
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        return 0
    except:
        return 0


# Compile regex patterns once at module level for better performance
# These patterns extract progress information from FFmpeg/HandBrake output
DURATION_PATTERN = re.compile(r'Duration: (\d{2}:\d{2}:\d{2}\.\d{2})')  # Total video duration
TIME_PATTERN = re.compile(r'time=(\d{2}:\d{2}:\d{2}\.\d{2})')  # Current encoding time
SPEED_PATTERN = re.compile(r'speed=\s*([\d.]+)x')  # Encoding speed multiplier

# HandBrake CLI progress patterns (different output format than FFmpeg)
HANDBRAKE_PROGRESS_PATTERN = re.compile(r'Encoding: task \d+ of \d+, (\d+\.\d+) %')  # Percentage complete
HANDBRAKE_FPS_PATTERN = re.compile(r'\((\d+\.\d+) fps\)')  # Frames per second

def show_progress(process, total_duration=None, silent=False, file_name=None, update_callback=None):
    """Show progress bar while FFmpeg/HandBrake is running.
    
    Parses stderr/stdout output to extract encoding progress and displays either:
    - A progress bar with percentage, speed, and ETA (non-silent mode)
    - Calls update_callback with progress info (silent mode with callback)
    
    Args:
        process: subprocess.Popen object for the encoding process
        total_duration: Total video duration in seconds (optional, will be parsed if None)
        silent: If True, don't print progress (use callback instead)
        file_name: Name of file being processed (for callback)
        update_callback: Optional callback function(file_name, status_text) or (status_text)
    """
    if silent:
        # Silent mode: use callback for custom progress display (e.g., GUI updates)
        
        duration_seconds = total_duration
        last_progress = 0
        last_update_time = time.time()
        
        # Read stderr line by line to parse progress
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
                current_time = parse_duration(time_match.group(1))
                progress = min(current_time / duration_seconds, 1.0)
                
                # Get speed
                speed_match = SPEED_PATTERN.search(line)
                speed = speed_match.group(1) if speed_match else "?"
                
                # Update progress bar with file name
                bar_length = 30
                filled = int(bar_length * progress)
                bar = '=' * filled + '-' * (bar_length - filled)
                percent = int(progress * 100)
                
                # Throttle updates: only update if progress changed significantly (>5%) 
                # OR enough time has passed (>=0.5s) to avoid excessive callback calls
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
                    
                    # Use callback if provided (for GUI or custom progress display)
                    if update_callback:
                        # Try calling with both file_name and status (for batch processing)
                        try:
                            update_callback(file_name, status_text)
                        except TypeError:
                            # Fallback: callback only accepts status text (single argument)
                            update_callback(status_text)
                    
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
            current_time = parse_duration(time_match.group(1))
            progress = min(current_time / duration_seconds, 1.0)
            
            # Get speed
            speed_match = SPEED_PATTERN.search(line)
            speed = speed_match.group(1) if speed_match else "?"
            
            # Update progress bar
            bar_length = 40
            filled = int(bar_length * progress)
            bar = '=' * filled + '-' * (bar_length - filled)
            percent = int(progress * 100)
            
            # Throttle updates: only update if progress changed significantly (>1%)
            # or encoding is complete (100%)
            if abs(progress - last_progress) > 0.01 or progress == 1.0:
                remaining = (duration_seconds - current_time) / float(speed) if speed != "?" and speed != "0" else 0
                if remaining > 0:
                    remaining_str = f"{int(remaining//60)}m {int(remaining%60)}s"
                else:
                    remaining_str = "calculating..."
                print(f"\rProgress: [{bar}] {percent}% | Speed: {speed}x | ETA: {remaining_str}", end='', flush=True)
                last_progress = progress
        
        # Print error messages (but skip common FFmpeg info lines that contain "error" in context)
        if 'error' in line.lower() and not any(x in line.lower() for x in ['frame=', 'fps=', 'bitrate=', 'time=']):
            print(f"\n{line.strip()}")
    
    print()  # New line after progress


def get_audio_codec_info(video_path, audio_track_index=None):
    """Get audio codec information for a specific track or first audio track.
    
    Args:
        video_path: Path to the video file
        audio_track_index: Zero-based index of audio track (None for first track)
        
    Returns:
        tuple: (codec_name: str or None, channels: int or None)
    """
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
        
        # Select the specified audio stream or default to first track
        if audio_track_index is not None and audio_track_index < len(audio_streams):
            selected_stream = audio_streams[audio_track_index]
        else:
            selected_stream = audio_streams[0]  # Default to first audio track
        
        codec_name = selected_stream.get('codec_name', '')
        channels = selected_stream.get('channels', 2)
        
        return codec_name, channels
    except:
        return None, None


def should_convert_audio_to_aac(codec_name, output_path):
    """Determine if audio should be converted to AAC for Chromecast compatibility.
    
    Chromecast devices have limited codec support. Converting to AAC ensures
    compatibility when streaming MP4 files.
    
    Args:
        codec_name: Current audio codec name (e.g., 'opus', 'aac', 'ac3')
        output_path: Output file path
        
    Returns:
        bool: True if audio should be converted to AAC, False otherwise
    """
    # Convert to AAC for MP4 output to ensure Chromecast compatibility
    if output_path.lower().endswith('.mp4'):
        # Convert Opus and other non-AAC codecs to AAC (MP3 is already compatible)
        if codec_name and codec_name.lower() not in ['aac', 'mp3']:
            return True
    return False


def find_vlc_executable():
    """Find VLC executable on the system.
    
    Searches common installation paths and system PATH for VLC executable.
    Required for progressive playback feature (--play-during-encode).
    
    Returns:
        str: Path to VLC executable, or None if not found
    """
    # Common VLC installation paths on Windows
    if sys.platform == 'win32':
        common_paths = [
            r'C:\Program Files\VideoLAN\VLC\vlc.exe',
            r'C:\Program Files (x86)\VideoLAN\VLC\vlc.exe',
            os.path.expanduser(r'~\AppData\Local\Programs\VideoLAN\VLC\vlc.exe'),
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path
        # Try to find VLC in system PATH
        try:
            result = subprocess.run(['where', 'vlc'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split('\n')[0]
        except:
            pass
    else:
        # Unix-like systems (Linux, macOS)
        try:
            result = subprocess.run(['which', 'vlc'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except:
            pass
    return None  # VLC not found


def burn_subtitles_from_file(video_path, subtitle_path, output_path, audio_track_index=None, silent=False, file_name=None, update_callback=None, play_during_encode=False, auto_launch_vlc=True):
    """Burn subtitles from external subtitle file into video using FFmpeg.
    
    Args:
        video_path: Path to input video file
        subtitle_path: Path to external subtitle file (SRT, ASS, SSA, VTT, etc.)
        output_path: Path for output video file
        audio_track_index: Zero-based audio track index (None for first track)
        silent: If True, suppress progress output (use callback instead)
        file_name: Display name for progress updates
        update_callback: Optional callback for progress updates
        play_during_encode: If True, use fragmented MP4 for progressive playback
        auto_launch_vlc: If True, automatically launch VLC when file is ready
        
    Returns:
        bool: True if successful, False otherwise
    """
    temp_subtitle_path = None
    try:
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # FFmpeg's subtitles filter has issues with paths containing special characters
        # (apostrophes, spaces, brackets, etc.) on Windows. To avoid escaping issues,
        # create a temporary copy of the subtitle file in a location without special characters.
        subtitle_path_resolved = Path(subtitle_path).resolve()
        
        # Check if the path contains problematic characters that might cause issues
        path_str = str(subtitle_path_resolved)
        has_special_chars = ("'" in path_str or ' ' in path_str or 
                             any(c in path_str for c in ['[', ']', '(', ')']))
        
        if has_special_chars:
            # Create a temporary subtitle file with a simple name in system temp directory
            temp_subtitle_path = str(Path(tempfile.gettempdir()) / f"burnsubs_temp_{os.getpid()}.srt")
            
            # Copy the subtitle file to the temporary location
            shutil.copy2(subtitle_path_resolved, temp_subtitle_path)
            
            # Use the temporary file path (no special characters to escape)
            subtitle_path_to_use = temp_subtitle_path.replace('\\', '/')
        else:
            # Path doesn't have problematic characters, use it directly
            subtitle_path_to_use = str(subtitle_path_resolved).replace('\\', '/')
        
        # Escape colons for FFmpeg filter syntax (colons have special meaning in filters)
        escaped_path = subtitle_path_to_use.replace(':', '\\:')
        
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
        
        # Build FFmpeg subtitles filter string with properly escaped path
        # Path is wrapped in single quotes for FFmpeg filter syntax
        filter_str = f"subtitles='{escaped_path}'"
        
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-map', '0:v:0',  # Map video stream
            '-vf', filter_str,
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
        
        # Configure output format: fragmented MP4 for progressive playback, or standard MP4
        if play_during_encode:
            # Fragmented MP4 allows VLC to start playing before encoding completes
            # movflags: frag_keyframe (fragment at keyframes), empty_moov (moov atom at end),
            #           default_base_moof (enables progressive download)
            cmd.extend([
                '-f', 'mp4',
                '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
                '-y',  # Overwrite output file
                output_path
            ])
        else:
            # Standard MP4 format (complete file required before playback)
            cmd.extend([
                '-y',  # Overwrite output file
                output_path
            ])
        
        # Start FFmpeg encoding process
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        # Handle VLC launch for progressive playback (in background thread)
        if play_during_encode and not auto_launch_vlc:
            print(f"\n[VLC OPERATION: SKIP AUTO-LAUNCH] (auto_launch_vlc=False)")
            print(f"  File: {os.path.basename(output_path)}")
            print(f"  VLC will NOT be started automatically - batch processing will handle queue")
        
        if play_during_encode and auto_launch_vlc:
            def launch_vlc_when_ready():
                """Launch VLC when fragmented MP4 file is ready for progressive playback.
                
                Waits for file to exist, be readable, and have sufficient content
                (initial moov atom + first fragment) before launching VLC.
                """
                # Wait for file to exist and have minimum content
                # Fragmented MP4 needs initial moov atom before playback can start
                max_wait_time = 30  # Maximum wait time in seconds
                wait_interval = 0.5  # Check every 0.5 seconds
                waited = 0
                min_file_size = 1024 * 100  # At least 100KB for initial moov atom and first fragment
                
                print("Waiting for video file to be ready for playback...")
                while waited < max_wait_time:
                    if os.path.exists(output_path):
                        try:
                            file_size = os.path.getsize(output_path)
                            # Verify file is readable (not locked by FFmpeg)
                            with open(output_path, 'rb') as test_file:
                                test_file.read(1)
                            
                            if file_size >= min_file_size:
                                # File exists, is readable, and has enough content - launch VLC
                                vlc_exe = find_vlc_executable()
                                if vlc_exe:
                                    try:
                                        # Additional wait to ensure file is stable and moov atom is written
                                        # Fragmented MP4 needs the initial moov atom before VLC can start
                                        time.sleep(5)
                                        
                                        # VLC can read fragmented MP4 files directly from disk while being written
                                        # Store VLC process globally for batch processing queue management
                                        global _current_vlc_process
                                        # --one-instance: reuse existing VLC window for subsequent videos
                                        # --no-video-title-show: cleaner interface
                                        # First video starts immediately (no --playlist-enqueue)
                                        print(f"\n[VLC OPERATION: START PLAYBACK] (NOT a queue operation)")
                                        print(f"  Command: {vlc_exe} \"{output_path}\" --one-instance --no-video-title-show")
                                        print(f"  File: {os.path.basename(output_path)}")
                                        _current_vlc_process = subprocess.Popen(
                                            [vlc_exe, output_path, '--one-instance', '--no-video-title-show'],
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL
                                        )
                                        print(f"  ✓ VLC process started (PID: {_current_vlc_process.pid})")
                                        print(f"  ✓ Video will start playing as it's encoded...")
                                        return
                                    except Exception as e:
                                        print(f"Warning: Could not launch VLC: {e}", file=sys.stderr)
                                        print(f"You can manually open the file in VLC once encoding completes.")
                                        return
                                else:
                                    print(f"VLC not found. You can manually open the file in VLC once encoding completes.")
                                    return
                        except (IOError, OSError, PermissionError):
                            # File is locked or not readable yet, continue waiting
                            pass
                    time.sleep(wait_interval)
                    waited += wait_interval
                
                if waited >= max_wait_time:
                    print(f"Warning: File not ready after {max_wait_time} seconds. VLC not launched automatically.", file=sys.stderr)
                    print(f"You can manually open the file in VLC once encoding completes.")
            
            # Launch VLC in background thread (non-blocking)
            vlc_thread = threading.Thread(target=launch_vlc_when_ready, daemon=True)
            vlc_thread.start()
        
        # Monitor encoding progress and wait for completion
        show_progress(process, total_duration, silent=silent, file_name=file_name, update_callback=update_callback)
        process.wait()
        
        # Check if encoding was successful
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
    finally:
        # Clean up temporary subtitle file if it was created (for special character handling)
        if temp_subtitle_path and os.path.exists(temp_subtitle_path):
            try:
                os.unlink(temp_subtitle_path)
            except Exception:
                pass  # Ignore errors during cleanup


def get_subtitle_codec(video_path, track_index):
    """Get the codec name for a specific subtitle track.
    
    Args:
        video_path: Path to the video file
        track_index: Zero-based subtitle track index
        
    Returns:
        str: Codec name (e.g., 'srt', 'ass', 'dvd_subtitle'), or None if not found
    """
    try:
        subtitle_tracks, _ = get_all_tracks(video_path)
        if track_index < len(subtitle_tracks):
            return subtitle_tracks[track_index].get('codec_name', '')
        return None
    except:
        return None


def burn_subtitles_from_mkv(video_path, track_index, output_path, audio_track_index=None, silent=False, file_name=None, update_callback=None, play_during_encode=False, auto_launch_vlc=True):
    """Burn subtitles directly from MKV file using track index.
    
    More efficient than extracting subtitles first. Handles text-based subtitles
    directly, and image-based subtitles (PGS, VobSub) via extraction or HandBrake.
    
    Args:
        video_path: Path to input MKV file
        track_index: Zero-based subtitle track index
        output_path: Path for output video file
        audio_track_index: Zero-based audio track index (None for first track)
        silent: If True, suppress progress output (use callback instead)
        file_name: Display name for progress updates
        update_callback: Optional callback for progress updates
        play_during_encode: If True, use fragmented MP4 for progressive playback
        auto_launch_vlc: If True, automatically launch VLC when file is ready
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Determine subtitle type: image-based (PGS, VobSub) vs text-based (SRT, ASS)
        subtitle_codec = get_subtitle_codec(video_path, track_index)
        is_image_based = is_image_based_subtitle(subtitle_codec) if subtitle_codec else False
        
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
        
        # Prepare video path for FFmpeg filter (escape special characters)
        # Path is wrapped in single quotes, so apostrophes must be escaped
        escaped_video_path = video_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
        temp_sub_path = None
        
        # VobSub requires special handling: FFmpeg's subtitles filter doesn't support it
        # HandBrake CLI can handle VobSub, so we try that first
        is_vobsub = subtitle_codec and ('dvd_subtitle' in subtitle_codec.lower() or 'vobsub' in subtitle_codec.lower())
        handbrake_available = False
        used_handbrake = False
        
        if is_vobsub:
            # Check if HandBrake CLI is available (required for VobSub support)
            try:
                handbrake_check = subprocess.run(['HandBrakeCLI', '--version'], 
                                                 capture_output=True, text=True, timeout=5)
                handbrake_available = True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                handbrake_available = False
            
            if handbrake_available:
                used_handbrake = True
                # Use HandBrake CLI to burn VobSub subtitles (FFmpeg cannot handle VobSub)
                if not silent:
                    print("Using HandBrake CLI to process VobSub subtitles...")
                
                # HandBrake uses 1-based indexing for subtitles (FFmpeg uses 0-based)
                # Convert our 0-based track_index to HandBrake's 1-based format
                handbrake_subtitle_track = track_index + 1
                
                # Build HandBrake CLI command for VobSub subtitle burning
                handbrake_cmd = [
                    'HandBrakeCLI',
                    '-i', video_path,
                    '-o', output_path,
                    '--subtitle', str(handbrake_subtitle_track),
                    '--subtitle-burn',  # Burn subtitle into video (hardcode)
                    '--encoder', 'x264',  # H.264 video encoder
                    '--quality', '20',  # RF quality 20 (good balance of quality/size)
                ]
                
                # Configure audio track selection and encoding
                if audio_track_index is not None:
                    # HandBrake uses 1-based audio track indexing (convert from 0-based)
                    handbrake_cmd.extend(['--audio', str(audio_track_index + 1)])
                    if convert_audio:
                        handbrake_cmd.extend(['--aencoder', 'av_aac'])  # AAC encoder
                        # Set bitrate based on channel count
                        if audio_channels and audio_channels > 2:
                            handbrake_cmd.extend(['--ab', '256'])  # 256kbps for 5.1+
                        else:
                            handbrake_cmd.extend(['--ab', '192'])  # 192kbps for stereo/mono
                else:
                    # Use first audio track (HandBrake index 1)
                    handbrake_cmd.extend(['--audio', '1'])
                    if convert_audio:
                        handbrake_cmd.extend(['--aencoder', 'av_aac'])
                
                # Run HandBrake CLI process
                process = subprocess.Popen(
                    handbrake_cmd,
                    stderr=subprocess.STDOUT,  # HandBrake outputs progress to stdout (not stderr)
                    stdout=subprocess.PIPE,
                    universal_newlines=False
                )
                
                # HandBrake uses different progress output format than FFmpeg
                if not silent:
                    print("Processing with HandBrake...")
                
                # Parse HandBrake progress output and display updates
                last_progress = 0
                last_update_time = time.time()
                
                while True:
                    line = process.stdout.readline()
                    if not line:
                        break
                    
                    if isinstance(line, bytes):
                        line = line.decode('utf-8', errors='ignore')
                    
                    # Parse HandBrake progress line format: "Encoding: task 1 of 1, 45.23 % (23.45 fps)"
                    progress_match = HANDBRAKE_PROGRESS_PATTERN.search(line)
                    if progress_match:
                        progress_pct = float(progress_match.group(1)) / 100.0
                        fps_match = HANDBRAKE_FPS_PATTERN.search(line)
                        fps = fps_match.group(1) if fps_match else "?"
                        
                        # Throttle progress updates (similar to FFmpeg progress handling)
                        current_time_actual = time.time()
                        time_since_update = current_time_actual - last_update_time
                        should_update = (abs(progress_pct - last_progress) > 0.05 or progress_pct >= 1.0) and time_since_update >= 0.5
                        
                        if should_update:
                            # Build progress bar display
                            bar_length = 30
                            filled = int(bar_length * progress_pct)
                            bar = '=' * filled + '-' * (bar_length - filled)
                            percent = int(progress_pct * 100)
                            
                            status_text = f"[{bar}] {percent:3d}% | {fps} fps"
                            
                            # Update via callback or print directly
                            if update_callback:
                                try:
                                    update_callback(file_name, status_text)
                                except TypeError:
                                    update_callback(status_text)
                            elif not silent:
                                print(f"\rProgress: {status_text}", end='', flush=True)
                            
                            last_progress = progress_pct
                            last_update_time = current_time_actual
                    
                    # Print error messages
                    if 'error' in line.lower() or 'Error' in line:
                        if not silent:
                            print(f"\n{line.strip()}")
                
                if not silent:
                    print()  # New line after progress bar
                
                process.wait()
                
                if process.returncode == 0:
                    return True  # HandBrake succeeded
                else:
                    # HandBrake failed, fall back to FFmpeg attempt (will likely fail but worth trying)
                    used_handbrake = False
                    if not silent:
                        print(f"\nHandBrake processing failed. Trying FFmpeg fallback...", file=sys.stderr)
                    # Fall through to FFmpeg attempt below
            
            # HandBrake not available or failed - try FFmpeg workaround (will likely fail)
            if not silent:
                if not handbrake_available:
                    print(f"\nWarning: HandBrake CLI not found. VobSub subtitles may not work with FFmpeg.", file=sys.stderr)
                print(f"Attempting FFmpeg workaround - this may not work...", file=sys.stderr)
            
            # Attempt to extract VobSub track and use FFmpeg subtitles filter
            # This typically fails because FFmpeg's subtitles filter doesn't support VobSub
            with tempfile.NamedTemporaryFile(suffix='.sub', delete=False) as temp_sub:
                temp_sub_path = temp_sub.name
            
            try:
                # Extract VobSub track to temporary file
                extract_cmd = [
                    'ffmpeg',
                    '-i', video_path,
                    '-map', f'0:s:{track_index}',
                    '-c:s', 'copy',  # Copy subtitle stream without conversion
                    '-y',
                    temp_sub_path
                ]
                subprocess.run(extract_cmd, capture_output=True, text=True, check=True, timeout=60)
                
                # Try using extracted file with FFmpeg subtitles filter (will likely fail)
                escaped_sub_path = temp_sub_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
                cmd.extend([
                    '-vf', f"subtitles='{escaped_sub_path}'",
                    '-c:v', 'libx264',
                    '-y',
                    output_path
                ])
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                # Clean up temporary file and provide helpful error message
                if temp_sub_path and os.path.exists(temp_sub_path):
                    try:
                        os.unlink(temp_sub_path)
                    except:
                        pass
                temp_sub_path = None
                
                if not silent:
                    error_msg = (
                        f"\nError: Cannot process VobSub subtitles automatically.\n"
                        f"FFmpeg does not support burning VobSub subtitles from MKV files.\n\n"
                        f"To enable automatic VobSub support, install HandBrake CLI:\n"
                        f"  1. Download from: https://handbrake.fr/downloads2.php\n"
                        f"  2. Add HandBrakeCLI to your PATH\n"
                        f"  3. Run this script again\n\n"
                        f"Alternative options:\n"
                        f"  - Extract subtitles manually and convert to SRT using Subtitle Edit\n"
                        f"  - Use a different video file with text-based subtitles (SRT/ASS)\n"
                    )
                    print(error_msg, file=sys.stderr)
                return False
        elif is_image_based:
            # For image-based subtitles (PGS, DVB), FFmpeg's subtitles filter can't read from MKV directly
            # Extract subtitle track first, then burn using the extracted file
            # Note: VobSub is handled separately above with HandBrake
            
            # PGS and other image-based formats use .sup extension
            temp_ext = '.sup'
            
            with tempfile.NamedTemporaryFile(suffix=temp_ext, delete=False) as temp_sub:
                temp_sub_path = temp_sub.name
            
            try:
                # Extract image-based subtitle track (PGS, DVB, etc.) to temporary file
                extract_cmd = [
                    'ffmpeg',
                    '-i', video_path,
                    '-map', f'0:s:{track_index}',
                    '-c:s', 'copy',  # Copy subtitle stream without conversion
                    '-y',
                    temp_sub_path
                ]
                subprocess.run(extract_cmd, capture_output=True, text=True, check=True, timeout=60)
                
                # Use extracted file with FFmpeg subtitles filter
                escaped_sub_path = temp_sub_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
                cmd.extend([
                    '-vf', f"subtitles='{escaped_sub_path}'",
                    '-c:v', 'libx264',
                    '-y',
                    output_path
                ])
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                # Clean up temporary file on error
                if temp_sub_path and os.path.exists(temp_sub_path):
                    try:
                        os.unlink(temp_sub_path)
                    except:
                        pass
                # Provide helpful error message
                if not silent:
                    error_msg = (
                        f"\nError: Cannot process image-based subtitle (PGS/DVB) from MKV file.\n"
                        f"FFmpeg's subtitles filter may have issues with some image-based formats.\n"
                        f"Please either:\n"
                        f"  1. Install MKVToolNix and ensure 'mkvextract' is in your PATH\n"
                        f"  2. Extract the subtitle track manually and use it as an external subtitle file\n"
                        f"  3. Convert the subtitles to a text-based format (SRT/ASS) first\n"
                    )
                    print(error_msg, file=sys.stderr)
                raise subprocess.CalledProcessError(1, extract_cmd if 'extract_cmd' in locals() else [])
        else:
            # For text-based subtitles (SRT, ASS), use FFmpeg subtitles filter directly
            # si={track_index} specifies which subtitle track to use from the input file
            if play_during_encode:
                # Fragmented MP4 format for progressive playback
                cmd.extend([
                    '-vf', f"subtitles='{escaped_video_path}':si={track_index}",
                    '-c:v', 'libx264',
                    '-f', 'mp4',
                    '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
                    '-y',  # Overwrite output file
                    output_path
                ])
            else:
                # Standard MP4 format
                cmd.extend([
                    '-vf', f"subtitles='{escaped_video_path}':si={track_index}",
                    '-c:v', 'libx264',
                    '-y',  # Overwrite output file
                    output_path
                ])
        
        # For image-based subtitles (PGS), add fragmented MP4 format if playing during encode
        # This is needed because the cmd was built before we knew about play_during_encode
        if play_during_encode and is_image_based and not is_vobsub:
            # Insert fragmented MP4 format flags before output path in command
            for i in range(len(cmd) - 1, -1, -1):
                if cmd[i] == output_path and i > 0 and cmd[i-1] == '-y':
                    # Insert format flags before -y flag
                    cmd.insert(i-1, 'default_base_moof')
                    cmd.insert(i-1, 'empty_moov+')
                    cmd.insert(i-1, 'frag_keyframe+')
                    cmd.insert(i-1, '-movflags')
                    cmd.insert(i-1, 'mp4')
                    cmd.insert(i-1, '-f')
                    break
        
        # For VobSub fallback path (FFmpeg attempt), also add fragmented MP4 if playing during encode
        if play_during_encode and is_vobsub and temp_sub_path:
            # Insert fragmented MP4 format flags before output path in command
            for i in range(len(cmd) - 1, -1, -1):
                if cmd[i] == output_path and i > 0 and cmd[i-1] == '-y':
                    # Insert format flags before -y flag
                    cmd.insert(i-1, 'default_base_moof')
                    cmd.insert(i-1, 'empty_moov+')
                    cmd.insert(i-1, 'frag_keyframe+')
                    cmd.insert(i-1, '-movflags')
                    cmd.insert(i-1, 'mp4')
                    cmd.insert(i-1, '-f')
                    break
        
        # Start FFmpeg encoding process (HandBrake path returns early if successful)
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        # Handle VLC launch for progressive playback (only for FFmpeg, not HandBrake)
        if play_during_encode and not auto_launch_vlc:
            # Batch processing mode: VLC queue will be managed by batch processing code
            print(f"\n[VLC OPERATION: SKIP AUTO-LAUNCH] (auto_launch_vlc=False)")
            print(f"  File: {os.path.basename(output_path)}")
            print(f"  VLC will NOT be started automatically - batch processing will handle queue")
        
        if play_during_encode and auto_launch_vlc and not used_handbrake:
            def launch_vlc_when_ready():
                # Wait for file to exist and have minimum content (fragmented MP4 needs initial moov atom)
                max_wait_time = 30  # Maximum wait time in seconds
                wait_interval = 0.5  # Check every 0.5 seconds
                waited = 0
                min_file_size = 1024 * 100  # At least 100KB for initial moov atom and first fragment
                
                print("Waiting for video file to be ready for playback...")
                while waited < max_wait_time:
                    if os.path.exists(output_path):
                        try:
                            file_size = os.path.getsize(output_path)
                            # Also check if file is readable (not locked)
                            with open(output_path, 'rb') as test_file:
                                test_file.read(1)
                            
                            if file_size >= min_file_size:
                                # File exists, is readable, and has enough content, launch VLC
                                vlc_exe = find_vlc_executable()
                                if vlc_exe:
                                    try:
                                        # Wait a bit more to ensure file is stable and has moov atom
                                        # For fragmented MP4, we need to wait longer for the initial moov atom
                                        time.sleep(5)
                                        
                                        # Try opening file directly first (works better for fragmented MP4)
                                        # VLC can read fragmented MP4 files directly from disk while they're being written
                                        # Store VLC process in a way that can be accessed by batch processing
                                        global _current_vlc_process
                                        # Use --one-instance so subsequent videos can be added to the same VLC instance
                                        # Remove --play-and-exit so VLC stays open for the queue
                                        # First video should NOT use --playlist-enqueue (it should start playing immediately)
                                        print(f"\n[VLC OPERATION: START PLAYBACK] (NOT a queue operation)")
                                        print(f"  Command: {vlc_exe} \"{output_path}\" --one-instance --no-video-title-show")
                                        print(f"  File: {os.path.basename(output_path)}")
                                        _current_vlc_process = subprocess.Popen(
                                            [vlc_exe, output_path, '--one-instance', '--no-video-title-show'],
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL
                                        )
                                        print(f"  ✓ VLC process started (PID: {_current_vlc_process.pid})")
                                        print(f"  ✓ Video will start playing as it's encoded...")
                                        return
                                    except Exception as e:
                                        print(f"Warning: Could not launch VLC: {e}", file=sys.stderr)
                                        print(f"You can manually open the file in VLC once encoding completes.")
                                        return
                                else:
                                    print(f"VLC not found. You can manually open the file in VLC once encoding completes.")
                                    return
                        except (IOError, OSError, PermissionError):
                            # File is locked or not readable yet, continue waiting
                            pass
                    time.sleep(wait_interval)
                    waited += wait_interval
                
                if waited >= max_wait_time:
                    print(f"Warning: File not ready after {max_wait_time} seconds. VLC not launched automatically.", file=sys.stderr)
                    print(f"You can manually open the file in VLC once encoding completes.")
            
            # Launch VLC in background thread
            vlc_thread = threading.Thread(target=launch_vlc_when_ready, daemon=True)
            vlc_thread.start()
        
        show_progress(process, total_duration, silent=silent, file_name=file_name, update_callback=update_callback)
        process.wait()
        
        # Clean up temporary subtitle file if it was created
        if 'temp_sub_path' in locals() and temp_sub_path and os.path.exists(temp_sub_path):
            try:
                os.unlink(temp_sub_path)
            except:
                pass
        
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
        filetypes=video_extensions,
        initialdir=os.getcwd()
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


def clean_directory_name(dir_name):
    """Remove square brackets and their contents from directory name."""
    # Remove all occurrences of [content] from the directory name
    cleaned = re.sub(r'\[.*?\]', '', dir_name)
    # Clean up extra spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def save_burnsubs_metadata(video_path, output_path):
    """Save metadata indicating that burnsubs has been run on this video.
    Metadata tracks the INPUT video file, so we know it's been processed."""
    try:
        folder_path = os.path.dirname(video_path)
        video_name = os.path.basename(video_path)
        metadata_file = os.path.join(folder_path, '.burnsubs_metadata.json')
        
        # Load existing metadata
        metadata = {}
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
            except:
                metadata = {}
        
        # Add entry for the INPUT file (so we know it's been processed)
        metadata[video_name] = {
            'processed': True,
            'output_file': os.path.basename(output_path),
            'timestamp': time.time()
        }
        
        # Save metadata
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    except Exception as e:
        # Don't fail if metadata writing fails
        print(f"Warning: Could not save metadata: {e}", file=sys.stderr)


# Global variable to track VLC process for batch queue management
_current_vlc_process = None

def process_batch_mkv(video_files, play_during_encode=False):
    """Process multiple MKV files with smart track selection and sequential processing."""
    global _current_vlc_process
    _current_vlc_process = None  # Reset for new batch
    
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
    
    # Determine output directory
    # Get parent directory of first video file (assuming all are in the same directory)
    first_video_path = Path(mkv_files[0])
    parent_dir = first_video_path.parent
    parent_dir_name = parent_dir.name
    
    # Clean the directory name (remove square brackets and contents)
    cleaned_dir_name = clean_directory_name(parent_dir_name)
    
    # Create output subdirectory
    output_dir_name = cleaned_dir_name + "_with_subs"
    output_dir = parent_dir / output_dir_name
    output_dir.mkdir(exist_ok=True)
    
    print(f"\nOutput directory: {output_dir}")
    
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
        
        # Determine output path in the output subdirectory
        video_path_obj = Path(video_path)
        output_path = str(output_dir / (video_path_obj.stem + '_with_subs.mp4'))
        
        # For batch processing: only auto-launch VLC for the first video
        # Subsequent videos will be added to queue by batch processing code
        auto_launch_vlc = (len(processing_tasks) == 0)  # True for first video, False for others
        
        if not auto_launch_vlc:
            print(f"[BATCH TASK CREATION] Video {len(processing_tasks) + 1}: auto_launch_vlc=False (will be queued)")
        
        processing_tasks.append((video_path, subtitle_idx, audio_idx, output_path, None, None, play_during_encode, auto_launch_vlc))
    
    if not processing_tasks:
        print("Error: No valid processing tasks created.", file=sys.stderr)
        sys.exit(1)
    
    # Process sequentially
    print("\n" + "="*60)
    print(f"PROCESSING {len(processing_tasks)} FILE(S) SEQUENTIALLY")
    print("="*60 + "\n")
    
    completed = 0
    failed = []
    
    # Process each file one by one
    for idx, task in enumerate(processing_tasks, 1):
        # Unpack task tuple: (video_path, subtitle_idx, audio_idx, output_path, subtitle_file, update_callback, play_during_encode, auto_launch_vlc)
        video_path, subtitle_idx, audio_idx, output_path, subtitle_file, update_callback, task_play_during_encode, task_auto_launch_vlc = task
        video_name = os.path.basename(video_path)
        
        print(f"[{idx}/{len(processing_tasks)}] Processing: {video_name}")
        
        # Process the video (without callback for simpler sequential output)
        # First video will launch VLC automatically when ready (if play_during_encode)
        video_path, output_path, success, error = process_single_video(task)
        
        if success:
            print(f"  ✓ Complete -> {os.path.basename(output_path)}\n")
            completed += 1
            # Save metadata indicating video was processed
            save_burnsubs_metadata(video_path, output_path)
            
            # For batch processing with streaming: Add subsequent videos to VLC queue
            # IMPORTANT: We need to wait until the previous video is actually playing
            # before adding the next one to the queue, otherwise VLC will start it immediately
            if play_during_encode and idx > 1 and os.path.exists(output_path):
                # Wait a bit for file to be stable
                time.sleep(3)
                
                # Wait for previous video to actually start playing before adding next to queue
                # This ensures VLC's playlist queue works correctly
                if _current_vlc_process and _current_vlc_process.poll() is None:
                    print(f"\n[VLC QUEUE CHECK] Previous video is still playing")
                    print(f"  → Waiting for previous video to start playing before adding to queue...")
                    print(f"  Previous VLC process PID: {_current_vlc_process.pid}, poll()={_current_vlc_process.poll()}")
                    # Give the first video time to actually start playing
                    # VLC needs time to open the file, parse it, and start playback
                    time.sleep(10)
                    
                    # Double-check VLC is still running (video is still playing)
                    if _current_vlc_process.poll() is None:
                        vlc_exe = find_vlc_executable()
                        if vlc_exe:
                            try:
                                # Add video to VLC's queue using --one-instance and --playlist-enqueue
                                # Use subprocess to properly handle paths with spaces (works on all platforms)
                                print(f"\n[VLC OPERATION: QUEUE ADD] (This IS a queue operation)")
                                print(f"  Command: {vlc_exe} --one-instance --playlist-enqueue --no-video-title-show \"{output_path}\"")
                                print(f"  File: {os.path.basename(output_path)}")
                                print(f"  Previous VLC process still running (PID: {_current_vlc_process.pid})")
                                
                                # Use subprocess.Popen with list of arguments to avoid shell parsing issues
                                # This properly handles paths with spaces on all platforms
                                queue_process = subprocess.Popen(
                                    [vlc_exe, '--one-instance', '--playlist-enqueue', '--no-video-title-show', output_path],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL
                                )
                                print(f"  ✓ Command executed (PID: {queue_process.pid}) - video should be added to queue")
                                print(f"  → Video will play after current video finishes")
                            except Exception as e:
                                print(f"  ✗ Could not add to VLC queue: {e}")
                    else:
                        print(f"  → Previous video finished, will start next video normally")
                        vlc_exe = find_vlc_executable()
                        if vlc_exe:
                            try:
                                # Previous video finished, start next video normally (no --playlist-enqueue needed)
                                print(f"\n[VLC OPERATION: START PLAYBACK] (NOT a queue operation - previous video finished)")
                                print(f"  Command: {vlc_exe} --one-instance \"{output_path}\" --no-video-title-show")
                                print(f"  File: {os.path.basename(output_path)}")
                                print(f"  Previous VLC process finished (poll() returned: {_current_vlc_process.poll()})")
                                process = subprocess.Popen(
                                    [vlc_exe, '--one-instance', output_path, '--no-video-title-show'],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL
                                )
                                print(f"  ✓ VLC process started (PID: {process.pid})")
                                print(f"  → Started next video (previous finished)")
                            except Exception as e:
                                print(f"  ✗ Could not start next video: {e}")
                else:
                    # Previous video finished or VLC closed - start normally
                    vlc_exe = find_vlc_executable()
                    if vlc_exe:
                        try:
                            # No previous video, start normally (no --playlist-enqueue needed)
                            print(f"\n[VLC OPERATION: START PLAYBACK] (NOT a queue operation - no previous video)")
                            print(f"  Command: {vlc_exe} --one-instance \"{output_path}\" --no-video-title-show")
                            print(f"  File: {os.path.basename(output_path)}")
                            if _current_vlc_process is None:
                                print(f"  No previous VLC process found")
                            else:
                                print(f"  Previous VLC process status: poll()={_current_vlc_process.poll()}")
                            process = subprocess.Popen(
                                [vlc_exe, '--one-instance', output_path, '--no-video-title-show'],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL
                            )
                            print(f"  ✓ VLC process started (PID: {process.pid})")
                            print(f"  → Started next video (previous finished)")
                        except Exception as e:
                            print(f"  → Could not start next video: {e}")
        else:
            print(f"  ✗ Error: {error}\n", file=sys.stderr)
            failed.append((video_path, error))
    
    print("="*60)
    
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
    parser.add_argument('--play-during-encode', dest='play_during_encode', action='store_true',
                       help='Play video in VLC while encoding (uses fragmented MP4 format for progressive playback)')
    
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
        process_batch_mkv(valid_files, play_during_encode=args.play_during_encode)
        return
    
    # Single file processing
    video_path = Path(valid_files[0])
    
    # Determine output file
    if args.output_file:
        output_path = args.output_file
    else:
        # Output in the same directory as the input file
        output_path = str(video_path.parent / (video_path.stem + '_with_subs.mp4'))
    
    # Safety check: ensure output path is different from input path
    output_path_resolved = str(Path(output_path).resolve())
    video_path_resolved = str(video_path.resolve())
    
    if output_path_resolved == video_path_resolved:
        print(f"Error: Output file cannot be the same as input file!", file=sys.stderr)
        print(f"Input:  {video_path_resolved}", file=sys.stderr)
        print(f"Output: {output_path_resolved}", file=sys.stderr)
        print(f"\nPlease specify a different output file with -o option.", file=sys.stderr)
        sys.exit(1)
    
    # Use the resolved output path
    output_path = output_path_resolved
    
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
        
        if not burn_subtitles_from_file(video_path_resolved, subtitle_path, output_path, audio_track_index, play_during_encode=args.play_during_encode):
            sys.exit(1)
        
        # Save metadata indicating video was processed
        save_burnsubs_metadata(video_path_resolved, output_path)
    else:
        # Try to use embedded subtitles from video file (for MKV files)
        if is_mkv:
            print(f"\nChecking for embedded subtitles in '{video_path.name}'...")
            subtitle_track_index = select_subtitle_track(video_path_resolved)
            
            if subtitle_track_index is None:
                # No embedded subtitles found - ask user to provide one
                print("\nNo embedded subtitle tracks found in this video file.")
                if TKINTER_AVAILABLE:
                    print("Please select a subtitle file to use...")
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes('-topmost', True)
                    
                    subtitle_extensions = [
                        ('Subtitle files', '*.srt *.ass *.ssa *.vtt *.sub'),
                        ('All files', '*.*')
                    ]
                    
                    subtitle_file = filedialog.askopenfilename(
                        title="Select Subtitle File (Required)",
                        filetypes=subtitle_extensions,
                        initialdir=str(video_path.parent)
                    )
                    root.destroy()
                    
                    if not subtitle_file:
                        print("Error: No subtitle file selected. Exiting.", file=sys.stderr)
                        sys.exit(1)
                    
                    subtitle_path = Path(subtitle_file)
                    if not subtitle_path.exists():
                        print(f"Error: Subtitle file '{subtitle_file}' not found.", file=sys.stderr)
                        sys.exit(1)
                    subtitle_path = str(subtitle_path.resolve())
                    
                    # Allow audio track selection
                    audio_track_index = select_audio_track(video_path_resolved)
                    
                    # Burn subtitles from external file
                    print(f"\nBurning subtitles from external file into video...")
                    print(f"Output will be saved as: {output_path}")
                    
                    if not burn_subtitles_from_file(video_path_resolved, subtitle_path, output_path, audio_track_index, play_during_encode=args.play_during_encode):
                        sys.exit(1)
                    
                    # Save metadata indicating video was processed
                    save_burnsubs_metadata(video_path_resolved, output_path)
                else:
                    print("Error: No subtitle tracks found in video file.", file=sys.stderr)
                    print("Please provide a subtitle file with -s option.", file=sys.stderr)
                    sys.exit(1)
            else:
                # Embedded subtitles found - proceed normally
                # Allow audio track selection
                audio_track_index = select_audio_track(video_path_resolved)
                
                # Burn subtitles directly from MKV (more efficient than extracting first)
                print(f"\nBurning subtitles into video...")
                print(f"Output will be saved as: {output_path}")
                
                if not burn_subtitles_from_mkv(video_path_resolved, subtitle_track_index, output_path, audio_track_index, play_during_encode=args.play_during_encode):
                    sys.exit(1)
                
                # Save metadata indicating video was processed
                save_burnsubs_metadata(video_path_resolved, output_path)
        else:
            print("Error: No subtitle file provided and video file doesn't appear to have embedded subtitles.", file=sys.stderr)
            print("Please provide a subtitle file with -s option.", file=sys.stderr)
            sys.exit(1)
    
    print(f"Success! Subtitles burned into '{output_path}'")


if __name__ == '__main__':
    main()
