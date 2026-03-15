# ACC Telemetry

A MoTeC-inspired real-time telemetry dashboard for racing simulators. Connects directly to your sim and displays live car data, lap analysis, and a self-building track map — no external tools required.

![Dashboard](https://placeholder)

---

## Supported Games

| Game                       | Connection Method | Notes         |
| -------------------------- | ----------------- | ------------- |
| Assetto Corsa Competizione | Shared Memory     | Windows only  |
| Assetto Corsa              | UDP (port 9996)   | Windows / Mac |
| iRacing                    | iRacing SDK       | Windows only  |

The app auto-detects whichever sim is running. You can also manually pin a source from the **SOURCE** dropdown.

---

## Installation

**Requirements:** Python 3.11+

```bash
pip install PyQt6 matplotlib pyaccsharedmemory irsdk
```

Run the app:

```bash
python test-listener.py
```

---

## Features

### Dashboard Tab

Live car data updated at 20 Hz:

- **Speed** — large numeric readout in km/h
- **Gear** — R / N / 1–8
- **RPM** — rev bar with numeric display
- **Throttle & Brake** — vertical input bars
- **Steering** — visual wheel widget with angle readout
- **ABS / TC** — activity badges that light up when active
- **Fuel** — remaining fuel in litres
- **Position** — race position
- **Last Lap Time** — updated on every lap crossing

### Telemetry Graphs Tab

Rolling time-series graphs for the current lap:

- Speed, Throttle/Brake, Steering, RPM, Gear, ABS/TC

### Lap Analysis Tab

The main analytical view, split into three panels:

**Left — Sector Panel**

- Running current lap timer starting from `0:00.000` on every lap, valid or not
- Reference lap time (last completed lap)
- S1 / S2 / S3 sector times with delta vs reference (green = faster, red = slower)

**Center — Live Track Map**

- Builds automatically from real world coordinates as you drive — no preset layouts
- Recording starts after the outlap is complete (skips pit-exit artifacts)
- Pauses recording during invalid laps (track cuts, penalties) to keep the shape clean
- Color-coded track surface: green = full throttle, yellow-green = partial throttle, red = braking, gray = coasting
- Colors are smoothed with a gradient kernel so zones blend naturally
- Car position dot appears after the first full lap of data, animated at 60 fps
- Saved to `tracks/<track_name>.json` after a complete lap — loads instantly on next launch

**Right — Lap Analysis Graphs**
Distance-based (not time-based) graphs for the current lap:

- Speed, Throttle/Brake, Gear, RPM, Steering

**Bottom — Time Delta**

- Delta vs last completed lap, plotted against track distance
- Fill above zero = slower (red), below zero = faster (blue)
- Empty until a reference lap exists

---

## Track Map Recording

The track map is generated from real car coordinates — no hand-drawn layouts. Here is how it works:

1. **Load into any session** on a track you want to map
2. **Complete the outlap** — the app skips this lap to avoid pit-exit noise
3. **Drive a flying lap** — the map builds in real time, showing progress as a percentage
4. **Cross the finish line** — the map is saved automatically to `tracks/<track_name>.json`
5. **Next session** — the saved map loads instantly at startup

You can also hit **⏺ REC** in the top bar to manually start and stop a recording at any point.

Saved JSON files live in `tracks/` next to the script. Each file contains the normalized waypoints and an empty `turns` array you can populate manually with corner names if you want labels on the map.

---

## Connection Strip

The bar at the top of the window gives you control over everything:

| Control                        | Description                                   |
| ------------------------------ | --------------------------------------------- |
| **● CONNECTED / DISCONNECTED** | Live connection status and active sim         |
| **SOURCE**                     | Auto-Detect, ACC, AC (UDP), or iRacing        |
| **TRACK**                      | Auto-Detect or any saved track JSON           |
| **HOST / PORT**                | UDP address for AC (default 127.0.0.1 : 9996) |
| **⏺ REC**                      | Manually record a track lap                   |
| **Car / Track / Lap**          | Live session info                             |

---

## Planned Features

- [ ] **Lap comparison overlay** — plot any two saved laps on the same graphs
- [x] **Tyre temperatures & wear** — core temp, pressure, brake temp per corner (ACC); tyre temp on iRacing
- [x] **Fuel strategy calculator** — target laps remaining based on current consumption
- [ ] **Corner labels on track map** — auto-detected from curvature, manually editable in JSON
- [x] **Export to CSV** — dump full lap data for external analysis
- [x] **Session summary screen** — lap table with times, sectors, and validity flags
- [ ] **Multi-class support for iRacing** — filter by car class in race sessions
- [ ] **Standalone executable** — PyInstaller build so Python is not required

---

## Project Structure

```
acc-telemetry/
├── test-listener.py      # Main application
├── tracks/               # Auto-generated track JSON files (created on first use)
└── README.md
```

---

## Notes

- ACC and iRacing shared memory only works on **Windows** while the sim is running
- AC UDP works on any OS as long as the game is broadcasting on the configured port
- The app gracefully degrades if a sim library is not installed — the source just stays disconnected
