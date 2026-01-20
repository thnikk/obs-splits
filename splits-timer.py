#!/usr/bin/env python3
"""
OBS Plugin for Speedrun Splits.
Requires: pip install evdev
Interfaces via BTN_MODE (316). Single press: Start/Split. Hold: Reset.
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
from datetime import datetime


class SplitsTimer:
    """Manages the speedrun timer state and logic."""

    def __init__(self):
        self.current_split_index = -1
        self.start_time = 0
        self.split_times = []
        self.timer_running = False
        self.comparison_pb_segments = {}
        self.comparison_best_segments = {}
        self.comparison_pb_total = None

    def start(self, split_names, segment_history):
        """Initialize and start a new run."""
        self.comparison_pb_segments = {}
        self.comparison_best_segments = {}

        # Find PB run
        pb_run = None
        min_total = None
        for run_data in segment_history.values():
            run_total = sum(run_data.values())
            if min_total is None or run_total < min_total:
                min_total = run_total
                pb_run = run_data

        self.comparison_pb_total = min_total

        for i, name in enumerate(split_names):
            # Best segment ever
            best_seg = self._get_best_segment(i, split_names,
                                              segment_history)
            if best_seg is not None:
                self.comparison_best_segments[name] = best_seg

            # PB run segment
            if pb_run and name in pb_run:
                self.comparison_pb_segments[name] = pb_run[name]

        self.start_time = time.time()
        self.timer_running = True
        self.current_split_index = 0
        self.split_times = []

    def split(self, split_names):
        """Record a split time."""
        if not self.timer_running:
            return False

        elapsed = time.time() - self.start_time
        self.split_times.append(elapsed)

        if self.current_split_index >= len(split_names) - 1:
            self.timer_running = False
            return True
        else:
            self.current_split_index += 1
            return False

    def reset(self):
        """Reset the timer."""
        self.current_split_index = -1
        self.timer_running = False
        self.split_times = []

    def get_current_elapsed(self):
        """Get current total elapsed time."""
        if self.timer_running:
            return time.time() - self.start_time
        elif self.split_times:
            return self.split_times[-1]
        return 0

    @staticmethod
    def _get_best_segment(index, split_names, segment_history):
        """Get the best time for a specific segment."""
        if index < 0 or index >= len(split_names):
            return None
        name = split_names[index]
        best_times = []
        for run_data in segment_history.values():
            if name in run_data:
                best_times.append(run_data[name])
        return min(best_times) if best_times else None


class SplitsData:
    """Manages splits data and history."""

    def __init__(self):
        self.splits_file_path = ""
        self.history_file_path = ""
        self.game_name = ""
        self.category_name = ""
        self.game_image_path = ""
        self.split_names = []
        self.full_history = {}
        self.segment_history = {}

    def load_splits(self, splits_file_path):
        """Load splits from JSON file."""
        self.splits_file_path = splits_file_path

        if not splits_file_path or not os.path.exists(splits_file_path):
            return False

        try:
            with open(splits_file_path, 'r') as f:
                data = json.load(f)
        except Exception as e:
            self._log(f"Error reading JSON: {e}")
            return False

        if not data:
            return False

        try:
            if self.game_name in data:
                game_data = data[self.game_name]
                self.game_image_path = game_data.get("image", "")
                categories = game_data.get("categories", {})

                if self.category_name in categories:
                    self.split_names = categories[self.category_name]
                elif categories:
                    self.category_name = list(categories.keys())[0]
                    self.split_names = categories[self.category_name]
            else:
                self.game_name = list(data.keys())[0]
                game_data = data[self.game_name]
                self.game_image_path = game_data.get("image", "")
                categories = game_data.get("categories", {})
                if categories:
                    self.category_name = list(categories.keys())[0]
                    self.split_names = categories[self.category_name]

            self.history_file_path = splits_file_path.replace(
                ".json", "_history.json")
            self._load_history()
            return True
        except Exception as e:
            self._log(f"Error loading splits: {e}")
            return False

    def _load_history(self):
        """Load run history from file."""
        self.full_history = {}
        if os.path.exists(self.history_file_path):
            with open(self.history_file_path, 'r') as f:
                try:
                    self.full_history = json.load(f)
                except Exception as e:
                    self._log(f"Error parsing history JSON: {e}")
                    self.full_history = {}

        # Migration: Check old flat format
        is_old_format = False
        for k in self.full_history.keys():
            if re.match(r'\d{4}-\d{2}-\d{2}', str(k)):
                is_old_format = True
                break

        if is_old_format:
            self._log("Migrating old flat history to nested format...")
            self.full_history = {
                self.game_name: {self.category_name: self.full_history}
            }
            self._save_history()

        # Set segment_history to current category's data
        if self.game_name not in self.full_history:
            self.full_history[self.game_name] = {}
        if self.category_name not in self.full_history[self.game_name]:
            self.full_history[self.game_name][self.category_name] = {}

        self.segment_history = (
            self.full_history[self.game_name][self.category_name]
        )

    def _save_history(self):
        """Save run history to file."""
        if not self.history_file_path:
            return
        try:
            with open(self.history_file_path, 'w') as f:
                json.dump(self.full_history, f, indent='\t')
        except Exception as e:
            self._log(f"Error saving history: {e}")

    def save_run(self, split_times):
        """Save completed run to history."""
        if len(split_times) != len(self.split_names):
            return

        run_key = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run_data = {}

        for i, name in enumerate(self.split_names):
            prev_total = split_times[i - 1] if i > 0 else 0
            segment_time = split_times[i] - prev_total
            run_data[name] = round(segment_time, 2)

        self.segment_history[run_key] = run_data
        self._save_history()

    def get_splits_data_raw(self):
        """Get raw JSON data for property population."""
        if not self.splits_file_path or not os.path.exists(
                self.splits_file_path):
            return None
        try:
            with open(self.splits_file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self._log(f"Error reading JSON: {e}")
            return None

    @staticmethod
    def _log(message):
        try:
            obs.script_log(obs.LOG_INFO, f"[Splits] {message}")
        except:
            pass


class InputMonitor:
    """Monitors gamepad input for split/reset commands."""

    def __init__(self, on_split, on_reset):
        self.on_split = on_split
        self.on_reset = on_reset
        self.running = True
        self.thread = None
        self.gamepad = None
        self.device_blacklist = ""
        self.device_filter = ""
        self.input_code = 316
        self.last_press_time = 0
        self.hold_threshold = 1.0
        self.is_held = False
        self.reset_triggered = False
        self.debug_status = "Initializing..."

    def start(self):
        """Start the input monitoring thread."""
        if self.thread is None or not self.thread.is_alive():
            self.running = True
            self.thread = threading.Thread(target=self._monitor_loop,
                                           daemon=True)
            self.thread.start()

    def stop(self):
        """Stop the input monitoring thread."""
        self.running = False

    def _monitor_loop(self):
        """Main input monitoring loop."""
        self._log("Input monitor thread started", obs.LOG_INFO)
        self.debug_status = "Monitor Started"

        try:
            while self.running:
                if self.gamepad is None:
                    self._search_for_gamepad()
                    if not self.gamepad:
                        time.sleep(1)
                        continue

                try:
                    self._process_input()
                except (OSError, Exception) as e:
                    if self.gamepad:
                        self._log(f"Controller disconnected: "
                                  f"{self.gamepad.name} - Error: {e}",
                                  obs.LOG_INFO)
                        try:
                            self.gamepad.close()
                        except:
                            pass
                    self.gamepad = None
                    self.is_held = False
                    self.reset_triggered = False
                    self.debug_status = "Disconnected"
        except Exception as e:
            self._log(f"Input monitor fatal error: {e}", obs.LOG_INFO)
            self.debug_status = f"Fatal Error: {e}"
        finally:
            self._log("Input monitor thread stopped", obs.LOG_INFO)
            self.debug_status = "Thread Stopped"

    def _search_for_gamepad(self):
        """Search for a compatible gamepad."""
        self.debug_status = "Searching..."
        blacklist_terms = [term.strip().lower() for term in
                           self.device_blacklist.split(",") if
                           term.strip()]
        filter_term = self.device_filter.strip().lower()
        all_paths = list_devices()

        if not all_paths:
            self.debug_status = "No devices found"
            return

        self._log(f"Searching for controller among {len(all_paths)} "
                  f"devices...")

        for path in all_paths:
            try:
                dev = InputDevice(path)
            except Exception:
                continue

            # Check blacklist
            dev_name = dev.name.lower()
            if any(term in dev_name for term in blacklist_terms):
                self._log(f"Skipping blacklisted device: {dev.name} "
                          f"({path})")
                dev.close()
                continue

            # Check filter
            if filter_term and filter_term not in dev_name:
                self._log(f"Skipping device (doesn't match filter): "
                          f"{dev.name} ({path})")
                dev.close()
                continue

            is_match = False
            # Check capabilities
            try:
                caps = dev.capabilities(verbose=False)
                if ecodes.EV_KEY in caps:
                    key_caps = caps[ecodes.EV_KEY]
                    # Require BOTH BTN_GAMEPAD and input_code
                    if (ecodes.BTN_GAMEPAD in key_caps and
                            self.input_code in key_caps):
                        is_match = True
            except Exception as e:
                self._log(f"Error checking capabilities for "
                          f"{dev.name}: {e}")

            if is_match:
                self.gamepad = dev
                self._log(f"Controller connected: {dev.name} "
                          f"({dev.path})", obs.LOG_INFO)
                self.debug_status = f"Connected: {dev.name}"
                break
            else:
                dev.close()

    def _process_input(self):
        """Process input events from gamepad."""
        r, w, x = select([self.gamepad.fd], [], [], 0.1)

        if self.is_held and not self.reset_triggered:
            if (time.time() - self.last_press_time) > self.hold_threshold:
                self.on_reset()
                self.reset_triggered = True

        if r:
            self.debug_status = f"Active: {self.gamepad.name}"
            for event in self.gamepad.read():
                if (event.type == ecodes.EV_KEY and
                        event.code == self.input_code):
                    if event.value == 1:
                        self.last_press_time = time.time()
                        self.is_held = True
                        self.reset_triggered = False
                    elif event.value == 0:
                        self.is_held = False
                        if not self.reset_triggered:
                            self.on_split()
                        self.reset_triggered = False

    @staticmethod
    def _log(message, level=None):
        if level is None:
            level = obs.LOG_INFO
        try:
            obs.script_log(level, f"[Splits] {message}")
        except:
            pass


class SVGRenderer:
    """Renders the splits display as SVG."""

    def __init__(self):
        self.bg_color = "#1e1e1e"
        self.bg_opacity = 100
        self.corner_radius = 10
        self.font_scale = 1.0
        self.line_spacing = 30
        self.show_ms = True
        self.show_best_segment_time = False
        self.show_deltas = True
        self.comparison_type = "pb"
        self.delta_type = "cumulative"
        self.use_dynamic_height = True
        self.height_setting = 600
        self.svg_width = 400
        self.text_color = "#ffffff"
        self.highlight_color = "#4c91f0"
        self.gold_color = "#ffda44"
        self.green_color = "#44ff44"
        self.red_color = "#ff4444"
        self.active_segment_bg = "#2b303b"
        self.separator_color = "#444444"
        self.normal_font = "Nunito"
        self.mono_font = "Courier New"

    def render(self, data, timer):
        """Render the splits display."""
        hex_color = self.bg_color.lstrip('#')
        rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        rgba_str = (f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, "
                    f"{self.bg_opacity / 100})")

        current_total_elapsed = timer.get_current_elapsed()

        # Height Logic
        header_height = 90
        splits_height = len(data.split_names) * self.line_spacing
        footer_height = 100
        content_height = header_height + splits_height + footer_height

        total_svg_height = self.height_setting

        if self.use_dynamic_height:
            content_start_y = max(0, total_svg_height - content_height)
            render_height = min(content_height, total_svg_height)
        else:
            content_start_y = 0
            render_height = total_svg_height

        svg = [
            f'<svg width="{self.svg_width}" '
            f'height="{total_svg_height}" '
            f'xmlns="http://www.w3.org/2000/svg">',
            f'<rect x="0" y="{content_start_y}" width="100%" '
            f'height="{render_height}" fill="{rgba_str}" '
            f'rx="{self.corner_radius}"/>'
        ]

        # Header
        text_x_start = 20
        image_uri = self._get_image_data_uri(data.game_image_path)
        if image_uri:
            img_size = 50 * self.font_scale
            svg.append(
                f'<image href="{image_uri}" x="20" '
                f'y="{content_start_y + 20}" width="{img_size}" '
                f'height="{img_size}" />')
            text_x_start = 30 + img_size

        svg.extend([
            f'<text x="{text_x_start}" y="{content_start_y + 40}" '
            f'fill="{self.highlight_color}" '
            f'font-family="{self.normal_font}" '
            f'font-size="{20 * self.font_scale}" font-weight="bold">'
            f'{data.game_name}</text>',
            f'<text x="{text_x_start}" y="{content_start_y + 65}" '
            f'fill="{self.text_color}" '
            f'font-family="{self.normal_font}" '
            f'font-size="{14 * self.font_scale}">'
            f'{data.category_name}</text>',
            f'<line x1="20" y1="{content_start_y + 80}" x2="380" '
            f'y2="{content_start_y + 80}" '
            f'stroke="{self.separator_color}" stroke-width="1"/>'
        ])

        # Splits
        y_offset = content_start_y + 110
        seg_decimals = 2 if self.show_ms else 0

        # Calculate PB and SoB
        if timer.timer_running or timer.current_split_index >= 0:
            pb_total = timer.comparison_pb_total
            sob_total = (sum(timer.comparison_best_segments.values()) if
                         timer.comparison_best_segments else 0)
        else:
            sob_total = 0
            for i in range(len(data.split_names)):
                best_time = timer._get_best_segment(
                    i, data.split_names, data.segment_history)
                if best_time is not None:
                    sob_total += best_time

            pb_total = None
            for run_data in data.segment_history.values():
                run_total = sum(run_data.values())
                if pb_total is None or run_total < pb_total:
                    pb_total = run_total

        # Color final timer relative to PB
        final_timer_color = self.text_color
        if pb_total is not None and pb_total > 0:
            final_timer_color = (self.green_color if
                                 current_total_elapsed < pb_total else
                                 self.red_color)

        for i, name in enumerate(data.split_names):
            time_str = ""
            delta_str = ""
            segment_time_color = self.text_color
            delta_time_color = self.text_color

            prev_total = (timer.split_times[i - 1] if
                          (i > 0 and len(timer.split_times) >= i) else 0)

            if i == timer.current_split_index:
                font_size = 16 * self.font_scale
                text_center_offset = font_size * 0.35
                rect_y = (y_offset - text_center_offset -
                          self.line_spacing / 2)
                svg.append(
                    f'<rect x="5" y="{rect_y}" width="390" '
                    f'height="{self.line_spacing}" rx="8" '
                    f'fill="{self.active_segment_bg}" opacity="0.9"/>')

            if i < len(timer.split_times):
                actual_seg = timer.split_times[i] - prev_total
                actual_cumulative = timer.split_times[i]

                # Use snapshots for comparison if available
                if (timer.timer_running or
                        timer.current_split_index >= 0):
                    comp_best = timer.comparison_best_segments.get(name)
                    comp_pb = timer.comparison_pb_segments.get(name)
                else:
                    comp_best = timer._get_best_segment(
                        i, data.split_names, data.segment_history)
                    comp_pb = None

                if self.show_best_segment_time:
                    time_str = self._get_comparison_time(
                        i, data, timer, seg_decimals)
                    segment_time_color = self.text_color
                else:
                    time_str = self._format_time(
                        actual_seg, decimal_places=seg_decimals)

                if self.show_deltas:
                    # Calculate cumulative comparison time for delta
                    if self.comparison_type == "sob":
                        # Sum of best segments up to this point
                        comp_cumulative_times = [
                            timer.comparison_best_segments.get(
                                data.split_names[j])
                            for j in range(i + 1)
                        ]
                        comp_cumulative_times = [t for t in
                                                 comp_cumulative_times if
                                                 t is not None]
                        comp_cumulative = (sum(comp_cumulative_times) if
                                           comp_cumulative_times else None)
                    else:
                        # PB segments up to this point
                        comp_cumulative_times = [
                            timer.comparison_pb_segments.get(
                                data.split_names[j])
                            for j in range(i + 1)
                        ]
                        comp_cumulative_times = [t for t in
                                                 comp_cumulative_times if
                                                 t is not None]
                        comp_cumulative = (sum(comp_cumulative_times) if
                                           comp_cumulative_times else None)

                    if comp_cumulative is not None:
                        if self.delta_type == "segment" and comp_best is not None:
                            # Segment delta
                            delta = actual_seg - comp_best
                        else:
                            # Cumulative delta
                            delta = actual_cumulative - comp_cumulative
                        delta_str = self._format_time(
                            delta, show_plus=True, decimal_places=1,
                            strip_leading_zero=True, delta_format=True)

                        # Check if this segment was gold
                        segment_was_gold = (comp_best is not None and
                                            actual_seg <= comp_best + 0.001)

                        if segment_was_gold and self.delta_type == "segment":
                            delta_time_color = self.gold_color
                        else:
                            # Green if ahead, red if behind
                            delta_time_color = (self.green_color if delta < 0
                                                else self.red_color)
            elif i == timer.current_split_index:
                best_seg = timer._get_best_segment(
                    i, data.split_names, data.segment_history)

                # Live Delta Implementation
                if timer.timer_running and self.show_deltas:
                    live_seg_duration = current_total_elapsed - prev_total

                    if self.delta_type == "segment" and best_seg is not None:
                        # Segment delta
                        live_delta = live_seg_duration - best_seg
                    else:
                        # Cumulative delta
                        # Calculate cumulative comparison time up to current split
                        comp_cumulative_times = []
                        for j in range(i + 1):
                            if self.comparison_type == "sob":
                                comp_time = timer.comparison_best_segments.get(
                                    data.split_names[j])
                            else:
                                comp_time = timer.comparison_pb_segments.get(
                                    data.split_names[j])
                            if comp_time is not None:
                                comp_cumulative_times.append(comp_time)
                        comp_cumulative = (sum(comp_cumulative_times) if
                                          comp_cumulative_times else None)

                        if comp_cumulative is not None:
                            live_delta = current_total_elapsed - comp_cumulative
                        else:
                            live_delta = None

                    if live_delta is not None and live_delta > -10.0:
                        delta_str = self._format_time(
                            live_delta, show_plus=True, decimal_places=1,
                            strip_leading_zero=True, delta_format=True)
                        # Green if ahead, red if behind
                        delta_time_color = (self.green_color if
                                            live_delta < 0 else
                                            self.red_color)

                if self.show_best_segment_time:
                    time_str = self._get_comparison_time(
                        i, data, timer, seg_decimals)
                    segment_time_color = self.text_color
                else:
                    segment_time_color = self.text_color
                    time_str = self._format_time(
                        current_total_elapsed - prev_total,
                        decimal_places=seg_decimals)
            else:
                if self.show_best_segment_time:
                    time_str = self._get_comparison_time(
                        i, data, timer, seg_decimals)
                    segment_time_color = self.text_color
                else:
                    # Show 00:00.xx for normal mode when no data
                    time_str = "00:00.00" if seg_decimals == 2 else "00:00"

            svg.append(
                f'<text x="15" y="{y_offset}" '
                f'fill="{self.text_color}" '
                f'font-family="{self.normal_font}" '
                f'font-size="{16 * self.font_scale}">{name}</text>')
            if delta_str:
                svg.append(
                    f'<text x="310" y="{y_offset}" '
                    f'fill="{delta_time_color}" '
                    f'font-family="{self.mono_font}" '
                    f'font-size="{13 * self.font_scale}" '
                    f'text-anchor="end" opacity="0.9">{delta_str}</text>')
            svg.append(
                f'<text x="385" y="{y_offset}" '
                f'fill="{segment_time_color}" '
                f'font-family="{self.mono_font}" '
                f'font-size="{16 * self.font_scale}" text-anchor="end">'
                f'{time_str}</text>')
            y_offset += self.line_spacing

        pb_str = (self._format_time(pb_total) if pb_total is not None
                  and pb_total > 0 else "00:00.00")
        sob_str = (self._format_time(sob_total) if sob_total > 0
                   else "00:00.00")

        # Footer
        footer_y = content_start_y + render_height
        svg.append(
            f'<text x="20" y="{footer_y - 45}" '
            f'fill="{self.text_color}" '
            f'font-family="{self.normal_font}" '
            f'font-size="{12 * self.font_scale}" opacity="0.7">PB: '
            f'<tspan font-family="{self.mono_font}">{pb_str}</tspan>'
            f'</text>')
        svg.append(
            f'<text x="20" y="{footer_y - 25}" '
            f'fill="{self.text_color}" '
            f'font-family="{self.normal_font}" '
            f'font-size="{12 * self.font_scale}" opacity="0.7">SoB: '
            f'<tspan font-family="{self.mono_font}">{sob_str}</tspan>'
            f'</text>')

        svg.append(
            f'<text x="380" y="{footer_y - 30}" '
            f'fill="{final_timer_color}" '
            f'font-family="{self.mono_font}" '
            f'font-size="{48 * self.font_scale}" font-weight="bold" '
            f'text-anchor="end">'
            f'{self._format_time(current_total_elapsed)}</text>')

        svg.append('</svg>')
        return "".join(svg)

    def _get_comparison_time(self, index, data, timer, seg_decimals):
        """Get comparison time based on current mode."""
        if self.comparison_type == "sob":
            best_times = [
                timer._get_best_segment(j, data.split_names,
                                        data.segment_history)
                for j in range(index + 1)
            ]
            best_times = [t for t in best_times if t is not None]
            if not best_times:
                if seg_decimals == 2:
                    return "00:00.00"
                elif seg_decimals == 0:
                    return "00:00"
                else:
                    return "00:00.0"
            cumulative_best = sum(best_times)
            return self._format_time(cumulative_best,
                                     decimal_places=seg_decimals)
        else:
            # Show cumulative PB
            # Before run starts, use current history
            if not timer.timer_running and timer.current_split_index < 0:
                # Calculate from current segment_history
                pb_run = None
                min_total = None
                for run_data in data.segment_history.values():
                    run_total = sum(run_data.values())
                    if min_total is None or run_total < min_total:
                        min_total = run_total
                        pb_run = run_data

                if pb_run:
                    pb_times = [pb_run.get(data.split_names[j])
                                for j in range(index + 1)]
                    pb_times = [t for t in pb_times if t is not None]
                    if pb_times:
                        cumulative_pb = sum(pb_times)
                        return self._format_time(
                            cumulative_pb,
                            decimal_places=seg_decimals)
            elif timer.comparison_pb_segments:
                # During/after run, use snapshot
                pb_times = [
                    timer.comparison_pb_segments.get(data.split_names[j])
                    for j in range(index + 1)
                ]
                pb_times = [t for t in pb_times if t is not None]
                if pb_times:
                    cumulative_pb = sum(pb_times)
                    return self._format_time(cumulative_pb,
                                             decimal_places=seg_decimals)

            if seg_decimals == 2:
                return "00:00.00"
            elif seg_decimals == 0:
                return "00:00"
            else:
                return "00:00.0"

    @staticmethod
    def _format_time(seconds, show_plus=False, decimal_places=2,
                     strip_leading_zero=False, delta_format=False):
        """Format seconds into MM:SS.h or HH:MM:SS.h."""
        if seconds == 0 and not show_plus:
            if decimal_places == 1:
                return "00:00.0"
            elif decimal_places == 0:
                return "00:00"
            else:
                return "00:00.00"

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

        if strip_leading_zero:
            if delta_format:
                # For delta format, strip leading zeros from minutes
                if mins > 0 and hrs == 0:
                    time_str = re.sub(r'^0', '', time_str)
                # Also strip leading zero from seconds if no minutes
                elif mins == 0:
                    time_str = re.sub(r'^0', '', time_str)
            elif not delta_format:
                time_str = re.sub(r'^0', '', time_str)

        return f"{prefix}{time_str}"

    @staticmethod
    def _get_image_data_uri(path):
        """Convert local image to base64 data URI for SVG embedding."""
        if not path or not os.path.exists(path):
            return None
        try:
            ext = os.path.splitext(path)[1].lower().strip(".")
            mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
            with open(path, "rb") as img_file:
                b64_string = base64.b64encode(
                    img_file.read()).decode("utf-8")
            return f"data:{mime};base64,{b64_string}"
        except Exception:
            return None


class SplitsPlugin:
    """Main plugin class."""

    def __init__(self):
        self.data = SplitsData()
        self.timer = SplitsTimer()
        self.renderer = SVGRenderer()
        self.input_monitor = InputMonitor(self._on_split, self._on_reset)
        self.source_name = ""

    def _on_split(self):
        """Handle split input."""
        if not self.timer.timer_running and self.timer.current_split_index == -1:
            self.timer.start(self.data.split_names,
                             self.data.segment_history)
        elif self.timer.timer_running:
            is_finished = self.timer.split(self.data.split_names)
            if is_finished:
                self.data.save_run(self.timer.split_times)

    def _on_reset(self):
        """Handle reset input."""
        self.timer.reset()

    def update_source(self):
        """Update the OBS source with new SVG content."""
        if not self.source_name:
            return

        source = obs.obs_get_source_by_name(self.source_name)
        if source is not None:
            svg_content = self.renderer.render(self.data, self.timer)
            svg_path = "/tmp/obs_splits.svg"
            try:
                with open(svg_path, 'w') as f:
                    f.write(svg_content)
                settings = obs.obs_data_create()
                obs.obs_data_set_string(settings, "file", svg_path)
                obs.obs_source_update(source, settings)
                obs.obs_data_release(settings)
            except Exception as e:
                self._log(f"Error updating SVG: {e}")
            obs.obs_source_release(source)

    @staticmethod
    def _log(message):
        try:
            obs.script_log(obs.LOG_INFO, f"[Splits] {message}")
        except:
            pass


# Global plugin instance
plugin = SplitsPlugin()


@staticmethod
def int_to_hex_color(color_int):
    """Convert OBS int color (BGR) to hex color string (#RRGGBB)."""
    hex_color = "#{:06x}".format(color_int & 0xFFFFFF)
    return "#" + hex_color[5:7] + hex_color[3:5] + hex_color[1:3]


def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "input_code", 316)
    obs.obs_data_set_default_string(settings, "device_blacklist", "ydotool")
    obs.obs_data_set_default_bool(settings, "show_ms", True)
    obs.obs_data_set_default_bool(settings, "show_best_segment_time",
                                  True)
    obs.obs_data_set_default_bool(settings, "show_deltas", True)
    obs.obs_data_set_default_string(settings, "comparison_type", "pb")
    obs.obs_data_set_default_string(settings, "delta_type", "cumulative")
    obs.obs_data_set_default_bool(settings, "use_dynamic_height", True)
    obs.obs_data_set_default_int(settings, "height_setting", 800)
    obs.obs_data_set_default_int(settings, "bg_opacity", 80)
    obs.obs_data_set_default_int(settings, "corner_radius", 10)

    # Set default fonts
    # normal_font_data = obs.obs_data_create()
    # obs.obs_data_set_string(normal_font_data, "face", "Nunito Regular")
    # obs.obs_data_set_default_obj(settings, "normal_font_select", normal_font_data)
    # obs.obs_data_release(normal_font_data)

    # mono_font_data = obs.obs_data_create()
    # obs.obs_data_set_string(mono_font_data, "face", "Nunito Regular")
    # obs.obs_data_set_default_obj(settings, "mono_font_select", mono_font_data)
    # obs.obs_data_release(mono_font_data)

    obs.obs_data_set_default_double(settings, "font_scale", 1.0)
    obs.obs_data_set_default_int(settings, "line_spacing", 40)
    obs.obs_data_set_default_int(settings, "bg_color", 2498332)
    obs.obs_data_set_default_int(settings, "text_color", 15326936)
    obs.obs_data_set_default_int(settings, "highlight_color", 12689793)
    obs.obs_data_set_default_int(settings, "gold_color", 9161707)
    obs.obs_data_set_default_int(settings, "green_color", 9223843)
    obs.obs_data_set_default_int(settings, "red_color", 6971839)
    obs.obs_data_set_default_int(settings, "active_segment_bg", 6179907)
    obs.obs_data_set_default_int(settings, "separator_color", 6968908)


def script_description():
    return ("SVG Speedrun Splits Display.\nPress KEY_RECORD to split, "
            "hold for 1s to reset.\nRequires 'evdev' and system fonts.")


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_path(
        props, "splits_file", "Splits JSON File", obs.OBS_PATH_FILE,
        "*.json", None)

    g_list = obs.obs_properties_add_list(
        props, "game_select", "Game", obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING)
    c_list = obs.obs_properties_add_list(
        props, "category_select", "Category", obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING)

    obs.obs_properties_add_color(props, "bg_color", "Background Color")
    obs.obs_properties_add_int(props, "bg_opacity", "Opacity (%)",
                               0, 100, 1)
    obs.obs_properties_add_int(
        props, "corner_radius", "Corner Radius", 0, 100, 1)
    obs.obs_properties_add_color(props, "text_color", "Text Color")
    obs.obs_properties_add_color(props, "highlight_color",
                                 "Highlight Color")
    obs.obs_properties_add_color(props, "gold_color",
                                 "Gold Segment Color")
    obs.obs_properties_add_color(props, "green_color",
                                 "Ahead/Green Color")
    obs.obs_properties_add_color(props, "red_color", "Behind/Red Color")
    obs.obs_properties_add_color(props, "active_segment_bg",
                                 "Active Segment Background")
    obs.obs_properties_add_color(props, "separator_color",
                                 "Separator Line Color")

    # Font Selection Menus
    obs.obs_properties_add_font(props, "normal_font_select",
                                "Normal Font")
    obs.obs_properties_add_font(props, "mono_font_select",
                                 "Time Font")

    obs.obs_properties_add_float(
        props, "font_scale", "Font Scale", 0.1, 5.0, 0.1)
    obs.obs_properties_add_int(
        props, "line_spacing", "Line Spacing (px)", 10, 100, 1)
    obs.obs_properties_add_bool(
        props, "show_ms", "Show Milliseconds in Segments")
    obs.obs_properties_add_bool(
        props, "show_best_segment_time", "Comparison Mode")
    obs.obs_properties_add_bool(
        props, "show_deltas", "Show Deltas")

    # Comparison type dropdown
    comp_type_list = obs.obs_properties_add_list(
        props, "comparison_type", "Comparison Type",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    obs.obs_property_list_add_string(comp_type_list, "Personal Best",
                                     "pb")
    obs.obs_property_list_add_string(comp_type_list, "Sum of Best",
                                     "sob")

    # Delta type dropdown
    delta_type_list = obs.obs_properties_add_list(
        props, "delta_type", "Delta Type",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    obs.obs_property_list_add_string(delta_type_list, "Cumulative",
                                     "cumulative")
    obs.obs_property_list_add_string(delta_type_list, "Segment",
                                     "segment")

    obs.obs_properties_add_bool(
        props, "use_dynamic_height", "Fit Height to Categories")
    obs.obs_properties_add_int(
        props, "height_setting", "Height", 100, 2000, 10)

    obs.obs_properties_add_text(props, "device_blacklist",
                                "Device Blacklist (comma-separated)",
                                obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(props, "device_filter",
                                "Device Filter (substring)",
                                obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(props, "input_code",
                               "Input Event Code", 0, 1000, 1)

    data = plugin.data.get_splits_data_raw()

    if data:
        for g in data.keys():
            obs.obs_property_list_add_string(g_list, g, g)
        current_g = list(data.keys())[0]
        categories = data[current_g].get("categories", {})
        for c in categories.keys():
            obs.obs_property_list_add_string(c_list, c, c)

    p = obs.obs_properties_add_list(
        props, "source", "Image Source", obs.OBS_COMBO_TYPE_EDITABLE,
        obs.OBS_COMBO_FORMAT_STRING)
    sources = obs.obs_enum_sources()
    if sources:
        for s in sources:
            if obs.obs_source_get_unversioned_id(s) == "image_source":
                name = obs.obs_source_get_name(s)
                obs.obs_property_list_add_string(p, name, name)
        obs.source_list_release(sources)
    return props


def script_update(settings):
    plugin.source_name = obs.obs_data_get_string(settings, "source")
    splits_file_path = obs.obs_data_get_string(settings, "splits_file")
    plugin.data.game_name = obs.obs_data_get_string(settings,
                                                    "game_select")
    plugin.data.category_name = obs.obs_data_get_string(settings,
                                                        "category_select")
    plugin.renderer.show_ms = obs.obs_data_get_bool(settings, "show_ms")
    plugin.renderer.show_best_segment_time = obs.obs_data_get_bool(
        settings, "show_best_segment_time")
    plugin.renderer.show_deltas = obs.obs_data_get_bool(settings, "show_deltas")
    plugin.renderer.comparison_type = obs.obs_data_get_string(
        settings, "comparison_type")
    plugin.renderer.delta_type = obs.obs_data_get_string(
        settings, "delta_type")
    plugin.input_monitor.device_blacklist = obs.obs_data_get_string(
        settings, "device_blacklist")
    plugin.input_monitor.device_filter = obs.obs_data_get_string(
        settings, "device_filter")
    plugin.input_monitor.input_code = obs.obs_data_get_int(settings,
                                                           "input_code")

    # Update font families
    n_font_data = obs.obs_data_get_obj(settings, "normal_font_select")
    if n_font_data:
        plugin.renderer.normal_font = obs.obs_data_get_string(n_font_data,
                                                              "face")
        obs.obs_data_release(n_font_data)

    m_font_data = obs.obs_data_get_obj(settings, "mono_font_select")
    if m_font_data:
        plugin.renderer.mono_font = obs.obs_data_get_string(m_font_data,
                                                            "face")
        obs.obs_data_release(m_font_data)

    plugin.renderer.use_dynamic_height = obs.obs_data_get_bool(
        settings, "use_dynamic_height")
    plugin.renderer.height_setting = obs.obs_data_get_int(settings,
                                                          "height_setting")

    plugin.renderer.font_scale = obs.obs_data_get_double(settings,
                                                         "font_scale")
    if plugin.renderer.font_scale <= 0:
        plugin.renderer.font_scale = 1.0

    plugin.renderer.line_spacing = obs.obs_data_get_int(settings,
                                                        "line_spacing")
    if plugin.renderer.line_spacing <= 0:
        plugin.renderer.line_spacing = 30

    bg_color_int = obs.obs_data_get_int(settings, "bg_color")
    plugin.renderer.bg_color = int_to_hex_color(bg_color_int)

    plugin.renderer.text_color = int_to_hex_color(
        obs.obs_data_get_int(settings, "text_color"))
    plugin.renderer.highlight_color = int_to_hex_color(
        obs.obs_data_get_int(settings, "highlight_color"))
    plugin.renderer.gold_color = int_to_hex_color(
        obs.obs_data_get_int(settings, "gold_color"))
    plugin.renderer.green_color = int_to_hex_color(
        obs.obs_data_get_int(settings, "green_color"))
    plugin.renderer.red_color = int_to_hex_color(
        obs.obs_data_get_int(settings, "red_color"))
    plugin.renderer.active_segment_bg = int_to_hex_color(
        obs.obs_data_get_int(settings, "active_segment_bg"))
    plugin.renderer.separator_color = int_to_hex_color(
        obs.obs_data_get_int(settings, "separator_color"))

    plugin.renderer.bg_opacity = obs.obs_data_get_int(settings,
                                                      "bg_opacity")
    plugin.renderer.corner_radius = obs.obs_data_get_int(settings,
                                                         "corner_radius")

    plugin.data.load_splits(splits_file_path)
    plugin.input_monitor.start()


def script_tick(seconds):
    if plugin.source_name:
        plugin.update_source()
    plugin.input_monitor.start()


def script_unload():
    plugin.input_monitor.stop()
