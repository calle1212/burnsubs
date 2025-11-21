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
import shutil
from pathlib import Path
from collections import defaultdict

try:
    import tkinter as tk
    from tkinter import filedialog
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

try:
    import http.server
    import socketserver
    import threading
    HTTP_SERVER_AVAILABLE = True
except ImportError:
    HTTP_SERVER_AVAILABLE = False


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


def is_image_based_subtitle(codec_name):
    """Check if subtitle codec is image-based (not text-based)."""
    image_based_codecs = [
        'dvd_subtitle',      # VobSub
        'hdmv_pgs_subtitle', # PGS
        'dvb_subtitle',      # DVB
        'xsub',              # XSUB
        'vobsub'             # VobSub (alternative name)
    ]
    return codec_name.lower() in image_based_codecs


def format_track_info(track, index):
    """Format track information for display."""
    tags = track.get('tags', {})
    lang = tags.get('language', 'unknown')
    title = tags.get('title', '')
    codec = track.get('codec_name', 'unknown')
    
    subtitle_type = " (Image-based)" if is_image_based_subtitle(codec) else ""
    info_parts = [f"Language: {lang}", f"Codec: {codec}{subtitle_type}"]
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
    """Group videos by their track structure."""
    print("\nAnalyzing video files...")
    video_info = {}
    
    # Process sequentially
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
    """Process a single video file. Used for sequential or parallel processing.
    Args: (video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file, update_callback)
    """
    # Handle both with and without callback
    if len(args_tuple) == 6:
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file, update_callback = args_tuple
        silent = update_callback is not None  # Silent if callback provided (for parallel)
    else:
        video_path, subtitle_track_index, audio_track_index, output_path, subtitle_file = args_tuple
        update_callback = None
        silent = False  # Show progress for sequential processing
    
    try:
        video_path_resolved = str(Path(video_path).resolve())
        file_name = os.path.basename(video_path)
        
        if subtitle_file:
            # External subtitle file
            subtitle_path = str(Path(subtitle_file).resolve())
            success = burn_subtitles_from_file(video_path_resolved, subtitle_path, output_path, audio_track_index, silent=silent, file_name=file_name, update_callback=update_callback)
        else:
            # Embedded subtitles
            success = burn_subtitles_from_mkv(video_path_resolved, subtitle_track_index, output_path, audio_track_index, silent=silent, file_name=file_name, update_callback=update_callback)
        
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


# Compile regex patterns once at module level for better performance
DURATION_PATTERN = re.compile(r'Duration: (\d{2}:\d{2}:\d{2}\.\d{2})')
TIME_PATTERN = re.compile(r'time=(\d{2}:\d{2}:\d{2}\.\d{2})')
SPEED_PATTERN = re.compile(r'speed=\s*([\d.]+)x')
# HandBrake progress patterns
HANDBRAKE_PROGRESS_PATTERN = re.compile(r'Encoding: task \d+ of \d+, (\d+\.\d+) %')
HANDBRAKE_FPS_PATTERN = re.compile(r'\((\d+\.\d+) fps\)')

def show_progress(process, total_duration=None, silent=False, file_name=None, update_callback=None):
    """Show progress bar while ffmpeg is running."""
    if silent:
        # Silent mode with optional callback for custom progress display
        
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
                    
                    # Use callback if provided
                    if update_callback:
                        # Callback can be called with (file_name, status) or just (status)
                        try:
                            update_callback(file_name, status_text)
                        except TypeError:
                            # Fallback if callback only accepts one argument
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


def find_vlc_executable():
    """Find VLC executable on the system."""
    # Common VLC installation paths
    if sys.platform == 'win32':
        common_paths = [
            r'C:\Program Files\VideoLAN\VLC\vlc.exe',
            r'C:\Program Files (x86)\VideoLAN\VLC\vlc.exe',
            os.path.expanduser(r'~\AppData\Local\Programs\VideoLAN\VLC\vlc.exe'),
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path
        # Try to find in PATH
        try:
            result = subprocess.run(['where', 'vlc'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split('\n')[0]
        except:
            pass
    else:
        # Unix-like systems
        try:
            result = subprocess.run(['which', 'vlc'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except:
            pass
    return None


class StreamingHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler for streaming video files with range request support."""
    def __init__(self, *args, video_file=None, **kwargs):
        self.video_file = video_file
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        if self.path == '/' or self.path == '/video.mp4':
            if self.video_file and os.path.exists(self.video_file):
                file_size = os.path.getsize(self.video_file)
                
                # Parse Range header if present
                range_header = self.headers.get('Range', None)
                if range_header:
                    # Handle range request for progressive playback
                    byte_start = 0
                    byte_end = file_size - 1
                    
                    # Parse range header (format: "bytes=start-end")
                    match = re.match(r'bytes=(\d+)-(\d*)', range_header)
                    if match:
                        byte_start = int(match.group(1))
                        if match.group(2):
                            byte_end = int(match.group(2))
                    
                    # Ensure valid range
                    byte_start = max(0, min(byte_start, file_size - 1))
                    byte_end = max(byte_start, min(byte_end, file_size - 1))
                    content_length = byte_end - byte_start + 1
                    
                    self.send_response(206)  # Partial Content
                    self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Content-Range', f'bytes {byte_start}-{byte_end}/{file_size}')
                    self.send_header('Content-Length', str(content_length))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    
                    # Send requested byte range
                    with open(self.video_file, 'rb') as f:
                        f.seek(byte_start)
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                else:
                    # Full file request
                    self.send_response(200)
                    self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Content-Length', str(file_size))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    
                    # Stream the file
                    with open(self.video_file, 'rb') as f:
                        shutil.copyfileobj(f, self.wfile)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress default logging for cleaner output
        pass


def start_streaming_server(video_file, port=8080):
    """Start a simple HTTP server to stream the video file."""
    if not HTTP_SERVER_AVAILABLE:
        return None, None
    
    handler = lambda *args, **kwargs: StreamingHTTPRequestHandler(*args, video_file=video_file, **kwargs)
    
    try:
        httpd = socketserver.TCPServer(("", port), handler)
        httpd.allow_reuse_address = True
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        return httpd, f"http://localhost:{port}/video.mp4"
    except OSError:
        # Port might be in use, try next port
        try:
            httpd = socketserver.TCPServer(("", port + 1), handler)
            httpd.allow_reuse_address = True
            server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            server_thread.start()
            return httpd, f"http://localhost:{port + 1}/video.mp4"
        except OSError:
            return None, None


def burn_subtitles_from_file(video_path, subtitle_path, output_path, audio_track_index=None, silent=False, file_name=None, update_callback=None, play_during_encode=False):
    """Burn subtitles from external subtitle file into video using ffmpeg."""
    temp_subtitle_path = None
    try:
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # On Windows, ffmpeg's subtitles filter has issues with paths containing special characters
        # (apostrophes, spaces, etc.). To avoid escaping issues, create a temporary copy of the
        # subtitle file in a location without special characters (temp directory).
        subtitle_path_resolved = Path(subtitle_path).resolve()
        
        # Check if the path contains problematic characters that might cause issues
        path_str = str(subtitle_path_resolved)
        has_special_chars = ("'" in path_str or ' ' in path_str or 
                             any(c in path_str for c in ['[', ']', '(', ')']))
        
        if has_special_chars:
            # Create a temporary subtitle file with a simple name
            # Use system temp directory to avoid any path issues
            temp_subtitle_path = str(Path(tempfile.gettempdir()) / f"burnsubs_temp_{os.getpid()}.srt")
            
            # Copy the subtitle file to the temporary location
            shutil.copy2(subtitle_path_resolved, temp_subtitle_path)
            
            # Use the temporary file path (much simpler, no special chars)
            subtitle_path_to_use = temp_subtitle_path.replace('\\', '/')
        else:
            # Path doesn't have problematic characters, use it directly
            subtitle_path_to_use = str(subtitle_path_resolved).replace('\\', '/')
        
        # Escape the path for ffmpeg filter (only need to escape colons now)
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
        
        # Build filter string with properly escaped path
        # The path is wrapped in single quotes, so apostrophes inside must be escaped
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
        
        # If playing during encode, use fragmented MP4 format for progressive playback
        if play_during_encode:
            cmd.extend([
                '-f', 'mp4',
                '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
                '-y',  # Overwrite output file
                output_path
            ])
        else:
            cmd.extend([
                '-y',  # Overwrite output file
                output_path
            ])
        
        # Start HTTP server and VLC if playing during encode
        httpd = None
        vlc_process = None
        if play_during_encode:
            # Wait a moment for file to start being written
            # Start HTTP server
            httpd, stream_url = start_streaming_server(output_path)
            if httpd:
                print(f"\nStreaming server started at: {stream_url}")
                # Wait a bit for file to have some content
                time.sleep(2)
                
                # Launch VLC
                vlc_exe = find_vlc_executable()
                if vlc_exe:
                    try:
                        vlc_process = subprocess.Popen(
                            [vlc_exe, stream_url, '--play-and-exit'],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        print("VLC launched - video will start playing as it's encoded...")
                    except Exception as e:
                        print(f"Warning: Could not launch VLC: {e}", file=sys.stderr)
                        print(f"You can manually open {stream_url} in VLC or any media player.")
                else:
                    print(f"VLC not found. You can manually open {stream_url} in any media player.")
            else:
                print("Warning: Could not start HTTP server. Playing during encode disabled.", file=sys.stderr)
        
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        show_progress(process, total_duration, silent=silent, file_name=file_name, update_callback=update_callback)
        process.wait()
        
        # Shutdown HTTP server if it was started
        if httpd:
            httpd.shutdown()
        
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
        # Clean up temporary subtitle file if it was created
        if temp_subtitle_path and os.path.exists(temp_subtitle_path):
            try:
                os.unlink(temp_subtitle_path)
            except Exception:
                pass  # Ignore errors during cleanup


def get_subtitle_codec(video_path, track_index):
    """Get the codec name for a specific subtitle track."""
    try:
        subtitle_tracks, _ = get_all_tracks(video_path)
        if track_index < len(subtitle_tracks):
            return subtitle_tracks[track_index].get('codec_name', '')
        return None
    except:
        return None


def burn_subtitles_from_mkv(video_path, track_index, output_path, audio_track_index=None, silent=False, file_name=None, update_callback=None, play_during_encode=False):
    """Burn subtitles directly from MKV file using track index (more efficient)."""
    try:
        # Check if subtitle is image-based
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
        
        # Handle image-based vs text-based subtitles differently
        # Escape apostrophes since path is wrapped in single quotes
        escaped_video_path = video_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
        temp_sub_path = None
        
        # Check if this is VobSub - need special handling
        is_vobsub = subtitle_codec and ('dvd_subtitle' in subtitle_codec.lower() or 'vobsub' in subtitle_codec.lower())
        handbrake_available = False
        used_handbrake = False
        
        if is_vobsub:
            # For VobSub, FFmpeg's subtitles filter doesn't support it
            # Try using HandBrake CLI if available (it supports VobSub)
            try:
                # Check if HandBrake CLI is available
                handbrake_check = subprocess.run(['HandBrakeCLI', '--version'], 
                                                 capture_output=True, text=True, timeout=5)
                handbrake_available = True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                handbrake_available = False
            
            if handbrake_available:
                used_handbrake = True
                # Use HandBrake CLI to burn VobSub subtitles
                if not silent:
                    print("Using HandBrake CLI to process VobSub subtitles...")
                
                # HandBrake uses 1-based indexing for subtitles
                # The track_index we have is 0-based, so convert to 1-based
                # HandBrake should match the subtitle track order from the file
                handbrake_subtitle_track = track_index + 1
                
                # Build HandBrake command
                handbrake_cmd = [
                    'HandBrakeCLI',
                    '-i', video_path,
                    '-o', output_path,
                    '--subtitle', str(handbrake_subtitle_track),
                    '--subtitle-burn',  # Burn the subtitle into the video
                    '--encoder', 'x264',
                    '--quality', '20',  # Good quality setting
                ]
                
                # Add audio track selection
                if audio_track_index is not None:
                    # HandBrake uses 1-based audio track indexing
                    handbrake_cmd.extend(['--audio', str(audio_track_index + 1)])
                    if convert_audio:
                        handbrake_cmd.extend(['--aencoder', 'av_aac'])
                        if audio_channels and audio_channels > 2:
                            handbrake_cmd.extend(['--ab', '256'])
                        else:
                            handbrake_cmd.extend(['--ab', '192'])
                else:
                    # Use first audio track
                    handbrake_cmd.extend(['--audio', '1'])
                    if convert_audio:
                        handbrake_cmd.extend(['--aencoder', 'av_aac'])
                
                # Run HandBrake
                process = subprocess.Popen(
                    handbrake_cmd,
                    stderr=subprocess.STDOUT,  # HandBrake outputs to stdout
                    stdout=subprocess.PIPE,
                    universal_newlines=False
                )
                
                # HandBrake outputs progress differently - handle it separately
                if not silent:
                    print("Processing with HandBrake...")
                
                # Read HandBrake output and show progress
                last_progress = 0
                last_update_time = time.time()
                
                while True:
                    line = process.stdout.readline()
                    if not line:
                        break
                    
                    if isinstance(line, bytes):
                        line = line.decode('utf-8', errors='ignore')
                    
                    # Parse HandBrake progress (format: "Encoding: task 1 of 1, 45.23 % (23.45 fps)")
                    progress_match = HANDBRAKE_PROGRESS_PATTERN.search(line)
                    if progress_match:
                        progress_pct = float(progress_match.group(1)) / 100.0
                        fps_match = HANDBRAKE_FPS_PATTERN.search(line)
                        fps = fps_match.group(1) if fps_match else "?"
                        
                        # Update progress display
                        current_time_actual = time.time()
                        time_since_update = current_time_actual - last_update_time
                        should_update = (abs(progress_pct - last_progress) > 0.05 or progress_pct >= 1.0) and time_since_update >= 0.5
                        
                        if should_update:
                            bar_length = 30
                            filled = int(bar_length * progress_pct)
                            bar = '=' * filled + '-' * (bar_length - filled)
                            percent = int(progress_pct * 100)
                            
                            status_text = f"[{bar}] {percent:3d}% | {fps} fps"
                            
                            if update_callback:
                                try:
                                    update_callback(file_name, status_text)
                                except TypeError:
                                    update_callback(status_text)
                            elif not silent:
                                print(f"\rProgress: {status_text}", end='', flush=True)
                            
                            last_progress = progress_pct
                            last_update_time = current_time_actual
                    
                    # Print important messages
                    if 'error' in line.lower() or 'Error' in line:
                        if not silent:
                            print(f"\n{line.strip()}")
                
                if not silent:
                    print()  # New line after progress
                
                process.wait()
                
                if process.returncode == 0:
                    return True
                else:
                    used_handbrake = False  # HandBrake failed, will use FFmpeg
                    if not silent:
                        print(f"\nHandBrake processing failed. Trying FFmpeg fallback...", file=sys.stderr)
                    # Fall through to FFmpeg attempt
            
            # If HandBrake not available or failed, try FFmpeg (will likely fail but worth trying)
            if not silent:
                if not handbrake_available:
                    print(f"\nWarning: HandBrake CLI not found. VobSub subtitles may not work with FFmpeg.", file=sys.stderr)
                print(f"Attempting FFmpeg workaround - this may not work...", file=sys.stderr)
            
            # Try extracting VobSub and using it - this likely won't work but worth trying
            with tempfile.NamedTemporaryFile(suffix='.sub', delete=False) as temp_sub:
                temp_sub_path = temp_sub.name
            
            try:
                # Extract VobSub track
                extract_cmd = [
                    'ffmpeg',
                    '-i', video_path,
                    '-map', f'0:s:{track_index}',
                    '-c:s', 'copy',
                    '-y',
                    temp_sub_path
                ]
                subprocess.run(extract_cmd, capture_output=True, text=True, check=True, timeout=60)
                
                # Try using extracted file - this will likely fail but worth trying
                # Escape apostrophes since path is wrapped in single quotes
                escaped_sub_path = temp_sub_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
                cmd.extend([
                    '-vf', f"subtitles='{escaped_sub_path}'",
                    '-c:v', 'libx264',
                    '-y',
                    output_path
                ])
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                # Clean up and provide helpful error
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
            # For image-based subtitles (PGS, etc.), FFmpeg's subtitles filter can't read from MKV directly
            # We need to extract first, then burn. For PGS, extract as .sup file
            # Note: VobSub is handled separately above and returns early
            
            # Determine file extension based on codec
            # PGS and other image-based formats use .sup
            temp_ext = '.sup'
            
            with tempfile.NamedTemporaryFile(suffix=temp_ext, delete=False) as temp_sub:
                temp_sub_path = temp_sub.name
            
            try:
                # Extract PGS subtitle using ffmpeg
                extract_cmd = [
                    'ffmpeg',
                    '-i', video_path,
                    '-map', f'0:s:{track_index}',
                    '-c:s', 'copy',
                    '-y',
                    temp_sub_path
                ]
                subprocess.run(extract_cmd, capture_output=True, text=True, check=True, timeout=60)
                
                # Now use the extracted file with subtitles filter
                # Escape apostrophes since path is wrapped in single quotes
                escaped_sub_path = temp_sub_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
                cmd.extend([
                    '-vf', f"subtitles='{escaped_sub_path}'",
                    '-c:v', 'libx264',
                    '-y',
                    output_path
                ])
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                # Clean up on error
                if temp_sub_path and os.path.exists(temp_sub_path):
                    try:
                        os.unlink(temp_sub_path)
                    except:
                        pass
                # VobSub subtitles cannot be processed by FFmpeg's subtitles filter
                # Provide helpful error message
                if not silent:
                    error_msg = (
                        f"\nError: Cannot process image-based subtitle (VobSub/PGS) from MKV file.\n"
                        f"FFmpeg's subtitles filter does not support VobSub subtitles in MKV containers.\n"
                        f"Please either:\n"
                        f"  1. Install MKVToolNix and ensure 'mkvextract' is in your PATH\n"
                        f"  2. Extract the subtitle track manually and use it as an external subtitle file\n"
                        f"  3. Convert the VobSub subtitles to a text-based format (SRT/ASS) first\n"
                    )
                    print(error_msg, file=sys.stderr)
                raise subprocess.CalledProcessError(1, extract_cmd if 'extract_cmd' in locals() else [])
        else:
            # For text-based subtitles, use the subtitles filter directly
            if play_during_encode:
                cmd.extend([
                    '-vf', f"subtitles='{escaped_video_path}':si={track_index}",
                    '-c:v', 'libx264',
                    '-f', 'mp4',
                    '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
                    '-y',  # Overwrite output file
                    output_path
                ])
            else:
                cmd.extend([
                    '-vf', f"subtitles='{escaped_video_path}':si={track_index}",
                    '-c:v', 'libx264',
                    '-y',  # Overwrite output file
                    output_path
                ])
        
        # For image-based subtitles (PGS), add fragmented MP4 if playing during encode
        if play_during_encode and is_image_based and not is_vobsub:
            # Find output_path in cmd and insert format flags before it
            for i in range(len(cmd) - 1, -1, -1):
                if cmd[i] == output_path and i > 0 and cmd[i-1] == '-y':
                    # Insert format flags before -y
                    cmd.insert(i-1, 'default_base_moof')
                    cmd.insert(i-1, 'empty_moov+')
                    cmd.insert(i-1, 'frag_keyframe+')
                    cmd.insert(i-1, '-movflags')
                    cmd.insert(i-1, 'mp4')
                    cmd.insert(i-1, '-f')
                    break
        
        # For VobSub fallback path, also add fragmented MP4 if playing during encode
        if play_during_encode and is_vobsub and temp_sub_path:
            # Find output_path in cmd and insert format flags before it
            for i in range(len(cmd) - 1, -1, -1):
                if cmd[i] == output_path and i > 0 and cmd[i-1] == '-y':
                    # Insert format flags before -y
                    cmd.insert(i-1, 'default_base_moof')
                    cmd.insert(i-1, 'empty_moov+')
                    cmd.insert(i-1, 'frag_keyframe+')
                    cmd.insert(i-1, '-movflags')
                    cmd.insert(i-1, 'mp4')
                    cmd.insert(i-1, '-f')
                    break
        
        # Start HTTP server and VLC if playing during encode (only for FFmpeg paths, not HandBrake)
        httpd = None
        vlc_process = None
        if play_during_encode and not used_handbrake:
            # Wait a moment for file to start being written
            # Start HTTP server
            httpd, stream_url = start_streaming_server(output_path)
            if httpd:
                print(f"\nStreaming server started at: {stream_url}")
                # Wait a bit for file to have some content
                time.sleep(2)
                
                # Launch VLC
                vlc_exe = find_vlc_executable()
                if vlc_exe:
                    try:
                        vlc_process = subprocess.Popen(
                            [vlc_exe, stream_url, '--play-and-exit'],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        print("VLC launched - video will start playing as it's encoded...")
                    except Exception as e:
                        print(f"Warning: Could not launch VLC: {e}", file=sys.stderr)
                        print(f"You can manually open {stream_url} in VLC or any media player.")
                else:
                    print(f"VLC not found. You can manually open {stream_url} in any media player.")
            else:
                print("Warning: Could not start HTTP server. Playing during encode disabled.", file=sys.stderr)
        
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=False
        )
        
        show_progress(process, total_duration, silent=silent, file_name=file_name, update_callback=update_callback)
        process.wait()
        
        # Shutdown HTTP server if it was started
        if httpd:
            httpd.shutdown()
        
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


def process_batch_mkv(video_files, play_during_encode=False):
    """Process multiple MKV files with smart track selection and sequential processing."""
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
        
        processing_tasks.append((video_path, subtitle_idx, audio_idx, output_path, None, None, play_during_encode))
    
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
        video_path, subtitle_idx, audio_idx, output_path, _ = task
        video_name = os.path.basename(video_path)
        
        print(f"[{idx}/{len(processing_tasks)}] Processing: {video_name}")
        
        # Process the video (without callback for simpler sequential output)
        video_path, output_path, success, error = process_single_video(task)
        
        if success:
            print(f"  ✓ Complete -> {os.path.basename(output_path)}\n")
            completed += 1
            # Save metadata indicating video was processed
            save_burnsubs_metadata(video_path, output_path)
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
