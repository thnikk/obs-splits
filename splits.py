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
svg_width = 400
svg_height = 600

# Timer State
current_split_index = -1
start_time = 0
split_times = []
segment_history = {} # segment_name: [list of best times]
timer_running = False

# Input State
input_thread = None
gamepad = None
last_press_time = 0
HOLD_THRESHOLD = 1.5

def load_splits_data():
    """Helper to load the raw JSON data for property population."""
    if not splits_file_path or not os.path.exists(splits_file_path):
        return None
    try:
        with open(splits_file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading JSON: {e}")
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
            # Fallback to first available game
            game_name = list(data.keys())[0]
            game_data = data[game_name]
            game_image_path = game_data.get("image", "")
            categories = game_data.get("categories", {})
            if categories:
                category_name = list(categories.keys())[0]
                split_names = categories[category_name]
            
        history_file_path = splits_file_path.replace(".json", "_history.json")
        if os.path.exists(history_file_path):
            with open(history_file_path, 'r') as f:
                segment_history = json.load(f)
    except Exception as e:
        print(f"Error loading splits: {e}")

def save_history():
    if not history_file_path:
        return
    try:
        with open(history_file_path, 'w') as f:
            json.dump(segment_history, f)
    except Exception as e:
        print(f"Error saving history: {e}")

def format_time(seconds, show_plus=False, decimal_places=2, strip_leading_zero=False):
    """Formats seconds into MM:SS.h or HH:MM:SS.h. Optionally strips the leftmost leading zero."""
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
    else:
        time_str = f"{mins:02}:{sec_str}"

    if strip_leading_zero:
        time_str = re.sub(r'^0', '', time_str)
        
    return f"{prefix}{time_str}"

def get_best_segment(index):
    if index < 0 or index >= len(split_names):
        return None
    name = split_names[index]
    history = segment_history.get(name, [])
    return min(history) if history else None

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
        print(f"Error encoding image: {e}")
        return None

def input_monitor():
    global gamepad, timer_running, current_split_index, start_time, last_press_time, running
    
    while running:
        if gamepad is None:
            devices = [InputDevice(path) for path in list_devices()]
            for dev in devices:
                if ecodes.EV_KEY in dev.capabilities():
                    caps = dev.capabilities(verbose=False)
                    if ecodes.EV_KEY in caps:
                        if ecodes.BTN_GAMEPAD in caps[ecodes.EV_KEY] or 167 in caps[ecodes.EV_KEY]:
                            gamepad = dev
                            break
            if not gamepad:
                time.sleep(1)
                continue

        try:
            r, w, x = select([gamepad.fd], [], [], 0.5)
            if r:
                for event in gamepad.read():
                    if event.type == ecodes.EV_KEY and event.code == 167:
                        if event.value == 1:
                            last_press_time = time.time()
                        elif event.value == 0:
                            duration = time.time() - last_press_time
                            if duration > HOLD_THRESHOLD:
                                reset_timer()
                            else:
                                trigger_split()
        except:
            gamepad = None

def trigger_split():
    global current_split_index, timer_running, start_time, split_times, segment_history
    now = time.time()
    
    if not timer_running and current_split_index == -1:
        start_time = now
        timer_running = True
        current_split_index = 0
        split_times = []
    elif timer_running:
        elapsed = now - start_time
        split_times.append(elapsed)
        
        prev_total = split_times[-2] if len(split_times) > 1 else 0
        segment_time = elapsed - prev_total
        
        if current_split_index < len(split_names):
            name = split_names[current_split_index]
            if name not in segment_history:
                segment_history[name] = []
            segment_history[name].append(segment_time)
        
        if current_split_index >= len(split_names) - 1:
            timer_running = False
            save_history()
        else:
            current_split_index += 1

def reset_timer():
    global current_split_index, timer_running, split_times
    current_split_index = -1
    timer_running = False
    split_times = []

def generate_svg():
    global bg_color, bg_opacity, corner_radius, font_scale, line_spacing, show_ms
    global current_split_index, start_time, timer_running, split_times, segment_history, game_image_path
    
    hex_color = bg_color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    rgba_str = f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {bg_opacity / 100})"

    text_color = "#ffffff"
    highlight_color = "#4c91f0"
    gold_color = "#ffda44"
    green_color = "#44ff44"
    red_color = "#ff4444"
    font_family = "'Nunito', sans-serif"
    
    now = time.time()
    current_total_elapsed = (now - start_time) if timer_running else (split_times[-1] if split_times else 0)

    svg = [
        f'<svg width="{svg_width}" height="{svg_height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="100%" height="100%" fill="{rgba_str}" rx="{corner_radius}"/>'
    ]

    text_x_start = 20
    image_uri = get_image_data_uri(game_image_path)
    if image_uri:
        img_size = 50 * font_scale
        svg.append(f'<image href="{image_uri}" x="20" y="20" width="{img_size}" height="{img_size}" />')
        text_x_start = 30 + img_size

    svg.extend([
        f'<text x="{text_x_start}" y="40" fill="{highlight_color}" font-family="{font_family}" font-size="{20 * font_scale}" font-weight="bold">{game_name}</text>',
        f'<text x="{text_x_start}" y="65" fill="{text_color}" font-family="{font_family}" font-size="{14 * font_scale}">{category_name}</text>',
        '<line x1="20" y1="80" x2="380" y2="80" stroke="#444" stroke-width="1"/>'
    ])

    timer_display_color = text_color
    if timer_running and current_split_index >= 0:
        best_seg = get_best_segment(current_split_index)
        prev_total = split_times[current_split_index-1] if (current_split_index > 0 and len(split_times) >= current_split_index) else 0
        current_seg_elapsed = current_total_elapsed - prev_total
        if best_seg:
            timer_display_color = green_color if current_seg_elapsed < best_seg else red_color

    y_offset = 110
    seg_decimals = 2 if show_ms else 0

    for i, name in enumerate(split_names):
        time_str = "--:--"
        delta_str = ""
        segment_time_color = text_color
        delta_time_color = text_color
        
        prev_total = split_times[i-1] if (i > 0 and len(split_times) >= i) else 0
        history_list = segment_history.get(name, [])

        if i < len(split_times):
            actual_seg = split_times[i] - prev_total
            time_str = format_time(actual_seg, decimal_places=seg_decimals)
            if len(history_list) > 1:
                comparison_best = min(history_list[:-1])
            elif len(history_list) == 1:
                comparison_best = history_list[0]
            else:
                comparison_best = None

            if comparison_best is not None:
                delta = actual_seg - comparison_best
                delta_str = format_time(delta, show_plus=True, decimal_places=1, strip_leading_zero=True)
                delta_time_color = green_color if delta < 0 else red_color
                segment_time_color = gold_color if actual_seg <= (comparison_best + 0.001) else (green_color if delta < 0 else red_color)
        elif i == current_split_index:
            segment_time_color = timer_display_color
            time_str = format_time(current_total_elapsed - prev_total, decimal_places=seg_decimals)
            
        svg.append(f'<text x="20" y="{y_offset}" fill="{text_color}" font-family="{font_family}" font-size="{16 * font_scale}">{name}</text>')
        if delta_str:
            svg.append(f'<text x="310" y="{y_offset}" fill="{delta_time_color}" font-family="{font_family}" font-size="{13 * font_scale}" text-anchor="end" opacity="0.9">{delta_str}</text>')
        svg.append(f'<text x="380" y="{y_offset}" fill="{segment_time_color}" font-family="{font_family}" font-size="{16 * font_scale}" text-anchor="end">{time_str}</text>')
        y_offset += line_spacing

    svg.append(f'<text x="380" y="{svg_height - 30}" fill="{timer_display_color}" font-family="{font_family}" font-size="{48 * font_scale}" font-weight="bold" text-anchor="end">{format_time(current_total_elapsed)}</text>')
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
            print(f"Error updating SVG: {e}")
        obs.obs_source_release(source)

def script_description():
    return "SVG Speedrun Splits Display.\nPress KEY_RECORD to split, hold to reset.\nRequires 'evdev' and 'Nunito' font."

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_path(props, "splits_file", "Splits JSON File", obs.OBS_PATH_FILE, "*.json", None)
    
    g_list = obs.obs_properties_add_list(props, "game_select", "Game", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    c_list = obs.obs_properties_add_list(props, "category_select", "Category", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    
    obs.obs_properties_add_color(props, "bg_color", "Background Color")
    obs.obs_properties_add_int(props, "bg_opacity", "Opacity (%)", 0, 100, 1)
    obs.obs_properties_add_int(props, "corner_radius", "Corner Radius", 0, 100, 1)
    obs.obs_properties_add_float(props, "font_scale", "Font Scale", 0.1, 5.0, 0.1)
    obs.obs_properties_add_int(props, "line_spacing", "Line Spacing (px)", 10, 100, 1)
    obs.obs_properties_add_bool(props, "show_ms", "Show Milliseconds in Segments")

    data = load_splits_data()
    if data:
        for g in data.keys():
            obs.obs_property_list_add_string(g_list, g, g)
        
        # Populate categories for the currently selected game
        current_g = list(data.keys())[0]
        categories = data[current_g].get("categories", {})
        for c in categories.keys():
            obs.obs_property_list_add_string(c_list, c, c)

    p = obs.obs_properties_add_list(props, "source", "Image Source", obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING)
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
    
    source_name = obs.obs_data_get_string(settings, "source")
    splits_file_path = obs.obs_data_get_string(settings, "splits_file")
    game_name = obs.obs_data_get_string(settings, "game_select")
    category_name = obs.obs_data_get_string(settings, "category_select")
    show_ms = obs.obs_data_get_bool(settings, "show_ms")
    
    font_scale = obs.obs_data_get_double(settings, "font_scale")
    if font_scale <= 0: font_scale = 1.0

    line_spacing = obs.obs_data_get_int(settings, "line_spacing")
    if line_spacing <= 0: line_spacing = 30
    
    bg_color_int = obs.obs_data_get_int(settings, "bg_color")
    bg_color = "#{:06x}".format(bg_color_int & 0xFFFFFF)
    bg_color = "#" + bg_color[5:7] + bg_color[3:5] + bg_color[1:3]
    
    bg_opacity = obs.obs_data_get_int(settings, "bg_opacity")
    corner_radius = obs.obs_data_get_int(settings, "corner_radius")
    
    load_splits()
    
    if not input_thread:
        running = True
        input_thread = threading.Thread(target=input_monitor, daemon=True)
        input_thread.start()

def script_tick(seconds):
    if source_name:
        update_source()

def script_unload():
    global running
    running = False
