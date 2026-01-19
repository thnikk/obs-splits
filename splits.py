#!/usr/bin/env python3
"""
OBS Plugin for Speedrun Splits.
Requires: pip install evdev
Interfaces via KEY_RECORD (167). Single press: Start/Split. Hold: Reset/End.
"""

import obspython as obs
import threading
import time
import json
import os
import re
import base64
from evdev import InputDevice, ecodes, list_devices
from select import select

# Input Configuration
device_blacklist = ""
device_filter = ""
input_code = 167

def log(message, level=None):
    if level is None:
        try:
            level = obs.LOG_INFO
        except:
            level = 1 # Fallback to info level
    
    try:
        obs.script_log(level, f"[Splits] {message}")
    except:
        pass

log("Splits script loaded", obs.LOG_INFO)

# --- Global State ---
running = True
source_name = ""
splits_file_path = ""
history_file_path = ""
game_name = ""
category_name = ""
game_image_path = ""
split_names = []

# Configurable UI State
bg_color = "#1e1e1e"
bg_opacity = 100
corner_radius = 10
font_scale = 1.0
line_spacing = 30
show_ms = True
use_dynamic_height = True
height_setting = 600
svg_width = 400
# User-defined fonts (configured via OBS properties)
normal_font = "Nunito"
mono_font = "Courier New"

# Timer State
current_split_index = -1
start_time = 0
split_times = []
full_history = {} # Game -> Category -> Timestamp -> Segments
segment_history = {}  # Reference to full_history[game][category]
timer_running = False

# Comparison Data (Snapshots at start of run)
comparison_pb_segments = {} # segment_name: pb_time
comparison_best_segments = {} # segment_name: best_time
comparison_pb_total = None

# Input State
input_thread = None
gamepad = None
last_press_time = 0
HOLD_THRESHOLD = 1.0  # Adjusted to 1.0s per request
is_held = False
reset_triggered = False
debug_status = "Initializing..."


def load_splits_data():
    """Helper to load the raw JSON data for property population."""
    if not splits_file_path or not os.path.exists(splits_file_path):
        return None
    try:
        with open(splits_file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        log(f"Error reading JSON: {e}")
        return None


def load_splits():
    global game_name, category_name, split_names, segment_history, history_file_path, game_image_path
    data = load_splits_data()
    if not data:
        return

    try:
        if game_name in data:
            game_data = data[game_name]
            game_image_path = game_data.get("image", "")
            categories = game_data.get("categories", {})

            if category_name in categories:
                split_names = categories[category_name]
            elif categories:
                category_name = list(categories.keys())[0]
                split_names = categories[category_name]
        else:
            if not data:
                return
            game_name = list(data.keys())[0]
            game_data = data[game_name]
            game_image_path = game_data.get("image", "")
            categories = game_data.get("categories", {})
            if categories:
                category_name = list(categories.keys())[0]
                split_names = categories[category_name]

        history_file_path = splits_file_path.replace(".json", "_history.json")
        global full_history
        full_history = {}
        if os.path.exists(history_file_path):
            with open(history_file_path, 'r') as f:
                try:
                    full_history = json.load(f)
                except Exception as e:
                    log(f"Error parsing history JSON: {e}")
                    full_history = {}

        # Migration: Check if the loaded history is in the old flat format
        # Old format: top-level keys are timestamps (e.g., "2026-01-19 12:00:00")
        is_old_format = False
        for k in full_history.keys():
            if re.match(r'\d{4}-\d{2}-\d{2}', str(k)):
                is_old_format = True
                break
        
        if is_old_format:
            log("Migrating old flat history to nested format...")
            full_history = {game_name: {category_name: full_history}}
            save_history()

        # Set segment_history to the current category's data
        if game_name not in full_history:
            full_history[game_name] = {}
        if category_name not in full_history[game_name]:
            full_history[game_name][category_name] = {}
        
        segment_history = full_history[game_name][category_name]
    except Exception as e:
        log(f"Error loading splits: {e}")


def save_history():
    if not history_file_path:
        return
    try:
        with open(history_file_path, 'w') as f:
            json.dump(full_history, f, indent='\t')
    except Exception as e:
        log(f"Error saving history: {e}")


def save_run_to_history():
    global segment_history
    if len(split_times) != len(split_names):
        return

    from datetime import datetime
    run_key = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_data = {}

    for i, name in enumerate(split_names):
        prev_total = split_times[i - 1] if i > 0 else 0
        segment_time = split_times[i] - prev_total
        run_data[name] = round(segment_time, 2)

    segment_history[run_key] = run_data
    save_history()


def format_time(seconds, show_plus=False, decimal_places=2, strip_leading_zero=False, delta_format=False):
    """Formats seconds into MM:SS.h or HH:MM:SS.h. Optionally strips leading zero.
    If delta_format=True, omits minutes unless time is >= 60s."""
    if seconds == 0 and not show_plus:
        return "--:--"

    prefix = ""
    if show_plus:
        if seconds > 0.001:
            prefix = "+"
        elif seconds < -0.001:
            prefix = "-"
        else:
            prefix = "-"
        seconds = abs(seconds)
    elif seconds < 0:
        prefix = "-"
        seconds = abs(seconds)

    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = seconds % 60

    if decimal_places == 1:
        sec_str = f"{secs:04.1f}"
    elif decimal_places == 0:
        sec_str = f"{int(secs):02d}"
    else:
        sec_str = f"{secs:05.2f}"

    if hrs > 0:
        time_str = f"{hrs:02}:{mins:02}:{sec_str}"
    elif delta_format and mins < 1:
        time_str = sec_str
    else:
        time_str = f"{mins:02}:{sec_str}"

    if strip_leading_zero and not delta_format:
        time_str = re.sub(r'^0', '', time_str)

    return f"{prefix}{time_str}"


def get_best_segment(index):
    if index < 0 or index >= len(split_names):
        return None
    name = split_names[index]
    best_times = []
    for run_data in segment_history.values():
        if name in run_data:
            best_times.append(run_data[name])
    return min(best_times) if best_times else None


def get_image_data_uri(path):
    """Converts a local image to a base64 data URI for SVG embedding."""
    if not path or not os.path.exists(path):
        return None
    try:
        ext = os.path.splitext(path)[1].lower().strip(".")
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
        with open(path, "rb") as img_file:
            b64_string = base64.b64encode(img_file.read()).decode("utf-8")
        return f"data:{mime};base64,{b64_string}"
    except Exception as e:
        log(f"Error encoding image: {e}")
        return None


def input_monitor():
    global gamepad, timer_running, current_split_index, start_time, last_press_time, running
    global is_held, reset_triggered, device_blacklist, device_filter, debug_status, input_code

    log("Input monitor thread started", obs.LOG_INFO)
    debug_status = "Monitor Started"
    try:
        while running:
            if gamepad is None:
                debug_status = "Searching..."
                blacklist_terms = [term.strip().lower() for term in device_blacklist.split(",") if term.strip()]
                filter_term = device_filter.strip().lower()
                all_paths = list_devices()
                if not all_paths:
                    debug_status = "No devices found"
                    time.sleep(1)
                    continue

                log(f"Searching for controller among {len(all_paths)} devices...")
                for path in all_paths:
                    try:
                        dev = InputDevice(path)
                    except Exception:
                        continue

                    # Check blacklist
                    dev_name = dev.name.lower()
                    # log(f"Checking device: {dev.name} ({path})") # Too spammy even for info
                    if any(term in dev_name for term in blacklist_terms):
                        log(f"Skipping blacklisted device: {dev.name} ({path})")
                        dev.close()
                        continue
                    
                    # Check filter
                    if filter_term and filter_term not in dev_name:
                        log(f"Skipping device (doesn't match filter): {dev.name} ({path})")
                        dev.close()
                        continue

                    is_match = False
                    # Check capabilities
                    try:
                        caps = dev.capabilities(verbose=False)
                        if ecodes.EV_KEY in caps:
                            key_caps = caps[ecodes.EV_KEY]
                            # Require BOTH BTN_GAMEPAD and input_code
                            if ecodes.BTN_GAMEPAD in key_caps and input_code in key_caps:
                                is_match = True
                    except Exception as e:
                        log(f"Error checking capabilities for {dev.name}: {e}")
                    
                    if is_match:
                        gamepad = dev
                        log(f"Controller connected: {dev.name} ({dev.path})", obs.LOG_INFO)
                        debug_status = f"Connected: {dev.name}"
                        break
                    else:
                        dev.close()

                if not gamepad:
                    time.sleep(1)
                    continue

            try:
                r, w, x = select([gamepad.fd], [], [], 0.1)
                
                if is_held and not reset_triggered:
                    if (time.time() - last_press_time) > HOLD_THRESHOLD:
                        reset_timer()
                        reset_triggered = True

                if r:
                    debug_status = f"Active: {gamepad.name}"
                    for event in gamepad.read():
                        if event.type == ecodes.EV_KEY and event.code == input_code:
                            if event.value == 1:  # Key Down
                                last_press_time = time.time()
                                is_held = True
                                reset_triggered = False
                            elif event.value == 0:  # Key Up
                                is_held = False
                                if not reset_triggered:
                                    trigger_split()
                                reset_triggered = False
            except (OSError, Exception) as e:
                if gamepad:
                    log(f"Controller disconnected: {gamepad.name} - Error: {e}", obs.LOG_INFO)
                    try:
                        gamepad.close()
                    except:
                        pass
                gamepad = None
                is_held = False
                reset_triggered = False
                debug_status = "Disconnected"
    except Exception as e:
        log(f"Input monitor fatal error: {e}", obs.LOG_INFO)
        debug_status = f"Fatal Error: {e}"
    finally:
        log("Input monitor thread stopped", obs.LOG_INFO)
        debug_status = "Thread Stopped"


def trigger_split():
    global current_split_index, timer_running, start_time, split_times
    global comparison_pb_segments, comparison_best_segments, comparison_pb_total
    now = time.time()
    log(f"Trigger split. Current index: {current_split_index}, Running: {timer_running}")

    if not timer_running and current_split_index == -1:
        # Initialize comparison snapshots
        comparison_pb_segments = {}
        comparison_best_segments = {}
        
        # Find PB run
        pb_run = None
        min_total = None
        for run_data in segment_history.values():
            run_total = sum(run_data.values())
            if min_total is None or run_total < min_total:
                min_total = run_total
                pb_run = run_data
        
        comparison_pb_total = min_total
        
        for i, name in enumerate(split_names):
            # Best segment ever
            best_seg = get_best_segment(i)
            if best_seg is not None:
                comparison_best_segments[name] = best_seg
            
            # PB run segment
            if pb_run and name in pb_run:
                comparison_pb_segments[name] = pb_run[name]

        start_time = now
        timer_running = True
        current_split_index = 0
        split_times = []
    elif timer_running:
        elapsed = now - start_time
        split_times.append(elapsed)

        prev_total = split_times[-2] if len(split_times) > 1 else 0
        segment_time = elapsed - prev_total

        if current_split_index >= len(split_names) - 1:
            timer_running = False
            save_run_to_history()
        else:
            current_split_index += 1


def reset_timer():
    global current_split_index, timer_running, split_times
    log("Reset timer triggered", obs.LOG_INFO)
    current_split_index = -1
    timer_running = False
    split_times = []


def generate_svg():
    global bg_color, bg_opacity, corner_radius, font_scale, line_spacing, show_ms
    global current_split_index, start_time, timer_running, split_times, segment_history, game_image_path
    global use_dynamic_height, height_setting, svg_width, normal_font, mono_font
    global comparison_pb_segments, comparison_best_segments, comparison_pb_total, debug_status

    hex_color = bg_color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    rgba_str = f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {bg_opacity / 100})"

    text_color = "#ffffff"
    highlight_color = "#4c91f0"
    gold_color = "#ffda44"
    green_color = "#44ff44"
    red_color = "#ff4444"

    now = time.time()
    current_total_elapsed = (
        now - start_time) if timer_running else (split_times[-1] if split_times else 0)

    # Height Logic
    header_height = 90
    splits_height = len(split_names) * line_spacing
    footer_height = 100
    content_height = header_height + splits_height + footer_height

    total_svg_height = height_setting

    if use_dynamic_height:
        content_start_y = max(0, total_svg_height - content_height)
        render_height = min(content_height, total_svg_height)
    else:
        content_start_y = 0
        render_height = total_svg_height

    svg = [
        f'<svg width="{svg_width}" height="{total_svg_height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect x="0" y="{content_start_y}" width="100%" height="{render_height}" fill="{rgba_str}" rx="{corner_radius}"/>'
    ]

    text_x_start = 20
    image_uri = get_image_data_uri(game_image_path)
    if image_uri:
        img_size = 50 * font_scale
        svg.append(
            f'<image href="{image_uri}" x="20" y="{content_start_y + 20}" width="{img_size}" height="{img_size}" />')
        text_x_start = 30 + img_size

    svg.extend([
        f'<text x="{text_x_start}" y="{content_start_y + 40}" fill="{highlight_color}" font-family="{normal_font}" font-size="{20 * font_scale}" font-weight="bold">{game_name}</text>',
        f'<text x="{text_x_start}" y="{content_start_y + 65}" fill="{text_color}" font-family="{normal_font}" font-size="{14 * font_scale}">{category_name}</text>',
        f'<line x1="20" y1="{content_start_y + 80}" x2="380" y2="{content_start_y + 80}" stroke="#444" stroke-width="1"/>'
    ])

    timer_display_color = text_color
    if timer_running and current_split_index >= 0:
        best_seg = get_best_segment(current_split_index)
        prev_total = split_times[current_split_index-1] if (
            current_split_index > 0 and len(split_times) >= current_split_index) else 0
        current_seg_elapsed = current_total_elapsed - prev_total
        if best_seg:
            timer_display_color = green_color if current_seg_elapsed < best_seg else red_color

    y_offset = content_start_y + 110
    seg_decimals = 2 if show_ms else 0

    # Calculate current PB and SoB from snapshots if timer is running, else from current history
    if timer_running or current_split_index >= 0:
        pb_total = comparison_pb_total
        sob_total = sum(comparison_best_segments.values()) if comparison_best_segments else 0
    else:
        # Before a run starts, calculate from current history
        sob_total = 0
        for i in range(len(split_names)):
            best_time = get_best_segment(i)
            if best_time is not None:
                sob_total += best_time

        pb_total = None
        for run_data in segment_history.values():
            run_total = sum(run_data.values())
            if pb_total is None or run_total < pb_total:
                pb_total = run_total

    # Color final timer relative to PB
    final_timer_color = text_color
    if pb_total is not None and pb_total > 0:
        final_timer_color = green_color if current_total_elapsed < pb_total else red_color

    for i, name in enumerate(split_names):
        time_str = "--:--"
        delta_str = ""
        segment_time_color = text_color
        delta_time_color = text_color

        prev_total = split_times[i - 1] if (i > 0 and len(split_times) >= i) else 0

        if i == current_split_index:
            font_size = 16 * font_scale
            text_center_offset = font_size * 0.35
            rect_y = y_offset - text_center_offset - line_spacing / 2
            svg.append(
                f'<rect x="5" y="{rect_y}" width="390" height="{line_spacing}" rx="8" fill="#2b303b" opacity="0.9"/>')

        if i < len(split_times):
            actual_seg = split_times[i] - prev_total
            time_str = format_time(actual_seg, decimal_places=seg_decimals)
            
            # Use snapshots for comparison if available
            if timer_running or current_split_index >= 0:
                comp_best = comparison_best_segments.get(name)
                comp_pb = comparison_pb_segments.get(name)
            else:
                comp_best = get_best_segment(i)
                comp_pb = None # Not used for delta when not running

            if comp_best is not None:
                # Delta vs PB or Best? Standard is vs PB, but let's stick to what was there or improve.
                # Currently delta_str was using min(all_times_for_segment) which is Gold.
                delta = actual_seg - comp_best
                delta_str = format_time(
                    delta, show_plus=True, decimal_places=1, delta_format=True)
                
                # Colors: 
                # Gold: actual_seg <= comp_best
                # Green: actual_seg < comp_pb (if comp_pb exists)
                # Red: actual_seg > comp_pb (or > comp_best if no PB)
                
                if actual_seg <= comp_best + 0.001:
                    segment_time_color = gold_color
                    delta_time_color = gold_color
                elif comp_pb is not None:
                    if actual_seg < comp_pb:
                        segment_time_color = green_color
                        delta_time_color = green_color
                    else:
                        segment_time_color = red_color
                        delta_time_color = red_color
                else:
                    # Fallback if no PB data
                    delta_time_color = green_color if delta < 0 else red_color
                    segment_time_color = green_color if delta < 0 else red_color
        elif i == current_split_index:
            segment_time_color = timer_display_color
            time_str = format_time(
                current_total_elapsed - prev_total, decimal_places=seg_decimals)

        svg.append(
            f'<text x="15" y="{y_offset}" fill="{text_color}" font-family="{normal_font}" font-size="{16 * font_scale}">{name}</text>')
        if delta_str:
            svg.append(
                f'<text x="280" y="{y_offset}" fill="{delta_time_color}" font-family="{mono_font}" font-size="{13 * font_scale}" text-anchor="end" opacity="0.9">{delta_str}</text>')
        svg.append(
            f'<text x="385" y="{y_offset}" fill="{segment_time_color}" font-family="{mono_font}" font-size="{16 * font_scale}" text-anchor="end">{time_str}</text>')
        y_offset += line_spacing

    pb_str = format_time(pb_total) if pb_total is not None and pb_total > 0 else "--:--"
    sob_str = format_time(sob_total) if sob_total > 0 else "--:--"

    # Footer rendering
    footer_y = content_start_y + render_height
    svg.append(f'<text x="20" y="{footer_y - 45}" fill="{text_color}" font-family="{normal_font}" font-size="{12 * font_scale}" opacity="0.7">PB: <tspan font-family="{mono_font}">{pb_str}</tspan></text>')
    svg.append(f'<text x="20" y="{footer_y - 25}" fill="{text_color}" font-family="{normal_font}" font-size="{12 * font_scale}" opacity="0.7">SoB: <tspan font-family="{mono_font}">{sob_str}</tspan></text>')
    
    svg.append(f'<text x="380" y="{footer_y - 30}" fill="{final_timer_color}" font-family="{mono_font}" font-size="{48 * font_scale}" font-weight="bold" text-anchor="end">{format_time(current_total_elapsed)}</text>')

    svg.append('</svg>')
    return "".join(svg)


def update_source():
    global source_name
    source = obs.obs_get_source_by_name(source_name)
    if source is not None:
        svg_content = generate_svg()
        svg_path = "/tmp/obs_splits.svg"
        try:
            with open(svg_path, 'w') as f:
                f.write(svg_content)
            settings = obs.obs_data_create()
            obs.obs_data_set_string(settings, "file", svg_path)
            obs.obs_source_update(source, settings)
            obs.obs_data_release(settings)
        except Exception as e:
            log(f"Error updating SVG: {e}")
        obs.obs_source_release(source)


def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "input_code", 167)
    obs.obs_data_set_default_bool(settings, "show_ms", True)
    obs.obs_data_set_default_bool(settings, "use_dynamic_height", True)
    obs.obs_data_set_default_int(settings, "height_setting", 600)
    obs.obs_data_set_default_int(settings, "bg_opacity", 100)
    obs.obs_data_set_default_int(settings, "corner_radius", 10)
    obs.obs_data_set_default_double(settings, "font_scale", 1.0)
    obs.obs_data_set_default_int(settings, "line_spacing", 30)


def script_description():
    return "SVG Speedrun Splits Display.\nPress KEY_RECORD to split, hold for 1s to reset.\nRequires 'evdev' and system fonts."


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_path(
        props, "splits_file", "Splits JSON File", obs.OBS_PATH_FILE, "*.json", None)

    g_list = obs.obs_properties_add_list(
        props, "game_select", "Game", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    c_list = obs.obs_properties_add_list(
        props, "category_select", "Category", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)

    obs.obs_properties_add_color(props, "bg_color", "Background Color")
    obs.obs_properties_add_int(props, "bg_opacity", "Opacity (%)", 0, 100, 1)
    obs.obs_properties_add_int(
        props, "corner_radius", "Corner Radius", 0, 100, 1)
    
    # Font Selection Menus
    obs.obs_properties_add_font(props, "normal_font_select", "Normal Font")
    obs.obs_properties_add_font(props, "mono_font_select", "Monospace Font")

    obs.obs_properties_add_float(
        props, "font_scale", "Font Scale", 0.1, 5.0, 0.1)
    obs.obs_properties_add_int(
        props, "line_spacing", "Line Spacing (px)", 10, 100, 1)
    obs.obs_properties_add_bool(
        props, "show_ms", "Show Milliseconds in Segments")

    obs.obs_properties_add_bool(
        props, "use_dynamic_height", "Fit Height to Categories")
    obs.obs_properties_add_int(
        props, "height_setting", "Height", 100, 2000, 10)

    obs.obs_properties_add_text(props, "device_blacklist", "Device Blacklist (comma-separated)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(props, "device_filter", "Device Filter (substring)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(props, "input_code", "Input Event Code", 0, 1000, 1)

    data = load_splits_data()

    if data:
        for g in data.keys():
            obs.obs_property_list_add_string(g_list, g, g)
        current_g = list(data.keys())[0]
        categories = data[current_g].get("categories", {})
        for c in categories.keys():
            obs.obs_property_list_add_string(c_list, c, c)

    p = obs.obs_properties_add_list(
        props, "source", "Image Source", obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING)
    sources = obs.obs_enum_sources()
    if sources:
        for s in sources:
            if obs.obs_source_get_unversioned_id(s) == "image_source":
                name = obs.obs_source_get_name(s)
                obs.obs_property_list_add_string(p, name, name)
        obs.source_list_release(sources)
    return props


def script_update(settings):
    global source_name, splits_file_path, input_thread, running
    global game_name, category_name, bg_color, bg_opacity, corner_radius, font_scale, line_spacing, show_ms
    global use_dynamic_height, height_setting, normal_font, mono_font, device_blacklist, device_filter, input_code

    source_name = obs.obs_data_get_string(settings, "source")
    splits_file_path = obs.obs_data_get_string(settings, "splits_file")
    game_name = obs.obs_data_get_string(settings, "game_select")
    category_name = obs.obs_data_get_string(settings, "category_select")
    show_ms = obs.obs_data_get_bool(settings, "show_ms")
    device_blacklist = obs.obs_data_get_string(settings, "device_blacklist")
    device_filter = obs.obs_data_get_string(settings, "device_filter")
    input_code = obs.obs_data_get_int(settings, "input_code")
    log(f"Script updated. Blacklist: {device_blacklist}, Filter: {device_filter}, Code: {input_code}")


    # Update font families from font settings
    n_font_data = obs.obs_data_get_obj(settings, "normal_font_select")
    if n_font_data:
        normal_font = obs.obs_data_get_string(n_font_data, "face")
        obs.obs_data_release(n_font_data)
    
    m_font_data = obs.obs_data_get_obj(settings, "mono_font_select")
    if m_font_data:
        mono_font = obs.obs_data_get_string(m_font_data, "face")
        obs.obs_data_release(m_font_data)

    use_dynamic_height = obs.obs_data_get_bool(settings, "use_dynamic_height")
    height_setting = obs.obs_data_get_int(settings, "height_setting")

    font_scale = obs.obs_data_get_double(settings, "font_scale")
    if font_scale <= 0:
        font_scale = 1.0

    line_spacing = obs.obs_data_get_int(settings, "line_spacing")
    if line_spacing <= 0:
        line_spacing = 30

    bg_color_int = obs.obs_data_get_int(settings, "bg_color")
    bg_color = "#{:06x}".format(bg_color_int & 0xFFFFFF)
    bg_color = "#" + bg_color[5:7] + bg_color[3:5] + bg_color[1:3]

    bg_opacity = obs.obs_data_get_int(settings, "bg_opacity")
    corner_radius = obs.obs_data_get_int(settings, "corner_radius")

    load_splits()

    if input_thread is None or not input_thread.is_alive():
        log("Starting input monitor thread...")
        running = True
        input_thread = threading.Thread(target=input_monitor, daemon=True)
        input_thread.start()
    else:
        log("Input monitor thread already running.")


def script_tick(seconds):
    global input_thread
    if source_name:
        update_source()
    
    # Ensure input thread is running
    if running and (input_thread is None or not input_thread.is_alive()):
        log("Input thread not running, starting/restarting...")
        input_thread = threading.Thread(target=input_monitor, daemon=True)
        input_thread.start()


def script_unload():
    global running
    running = False
