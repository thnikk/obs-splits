# OBS Splits Timer

A high-performance, customizable speedrun splits plugin for OBS Studio on Linux. This plugin renders splits as an SVG file and monitors controller/keyboard input directly via `evdev` for low-latency splitting.

## Features

- **SVG Rendering:** High-quality, scalable graphics that look great at any size.
- **Direct Input Monitoring:** Uses `evdev` to listen for hardware events (default: `KEY_RECORD` / 167), bypassing standard OS input focus issues.
- **Dynamic History:** Automatically tracks Personal Bests (PB) and Sum of Best (SoB) segments.
- **Visual Customization:**
  - Adjustable colors for background, text, highlights, gold segments, and ahead/behind deltas.
  - Font scaling and custom font support.
  - Optional millisecond display.
  - Dynamic height adjustment based on the number of splits.
- **Multi-Game Support:** Manage multiple games and categories in a single JSON file.
- **Live Deltas:** Real-time feedback on your current pace compared to your best times.

## Requirements

- **Linux OS** (due to `evdev` dependency)
- **Python 3**
- **evdev** Python library: `pip install evdev`
- **OBS Studio** with Python scripting support

## Installation

1.  **Install dependencies:**
    ```bash
    python-evdev
    ```
    Install this with your distro's package manager.
2.  **Permissions:** Since this plugin reads directly from `/dev/input/`, your user might need to be in the `input` group:
    ```bash
    sudo usermod -a -G input $USER
    ```
    *(Note: You may need to log out and back in for this to take effect.)*
3.  **Add to OBS:**
    - Open OBS Studio.
    - Go to **Tools** -> **Scripts**.
    - Click the **+** button and select `splits-timer.py`.

## Configuration

### 1. Splits JSON File
Create a JSON file (e.g., `splits.json`) to define your games and categories:

```json
{
  "Game Name": {
    "image": "/path/to/game-icon.png",
    "categories": {
      "Any%": [
        "First Split",
        "Second Split",
        "Final Split"
      ]
    }
  }
}
```

### 2. OBS Source Setup
1.  Add an **Image** source to your scene.
2.  Enable the "Unload image when not showing" option (optional but recommended).
3.  In the Script settings, select this Image source under **Image Source**.
4.  The plugin will now write an SVG to `/tmp/obs_splits.svg` and update the Image source automatically.

### 3. Controls
- **Split / Start:** Press the configured key (Default: `KEY_RECORD` / 167).
- **Reset:** Hold the configured key for 1 second.

## Customization Options

- **Comparison Type:** Toggle between "Personal Best" and "Sum of Best".
- **Delta Type:** Toggle between "Cumulative" deltas (total run time) and "Segment" deltas (individual split time).
- **Fonts:** Set specific fonts for normal text and monospace timers.
- **Colors:** Fully themeable interface to match your stream layout.

## Data Storage

- **Splits:** Stored in your provided JSON file.
- **History:** Automatically saved to `[splits_filename]_history.json` in the same directory. This file tracks every completed run.

## Troubleshooting

- **Input not working:** Ensure the correct `Input Event Code` is set and that your user has permissions to read from `/dev/input/`. Check the OBS Script Log (**Tools** -> **Scripts** -> **Log**) for connection status messages.
- **Fonts not rendering:** Ensure the font names entered in settings match the names installed on your system exactly.
