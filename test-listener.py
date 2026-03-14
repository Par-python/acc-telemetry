import sys
import socket
import struct
from collections import deque
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QLineEdit,
    QTabWidget, QFileDialog, QMessageBox, QSplitter, QScrollArea,
    QFrame, QGridLayout, QSizePolicy,
)
from PyQt6.QtCore import QTimer, Qt, QRectF, QPointF
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush, QRadialGradient
from abc import ABC, abstractmethod
import threading
import math
import time
import random

import json
from pathlib import Path

import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# ---------------------------------------------------------------------------
# COLOR CONSTANTS
# ---------------------------------------------------------------------------
BG      = '#0b0b0b'
BG1     = '#111111'
BG2     = '#181818'
BG3     = '#222222'
BORDER  = '#2a2a2a'
BORDER2 = '#383838'
TXT     = '#c8c8c8'
TXT2    = '#6a6a6a'
WHITE   = '#f2f2f2'

C_SPEED    = '#00d4ff'
C_THROTTLE = '#00e87a'
C_BRAKE    = '#ff3232'
C_RPM      = '#ffc200'
C_GEAR     = '#e0e0e0'
C_STEER    = '#cc77ff'
C_ABS      = '#ff7f00'
C_TC       = '#ffe000'
C_DELTA    = '#4499ff'
C_REF      = '#e74c3c'

# ---------------------------------------------------------------------------
# FONT HELPERS
# ---------------------------------------------------------------------------

def mono(size: int, bold: bool = False) -> QFont:
    f = QFont('Consolas', size)
    f.setBold(bold)
    return f


def sans(size: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSize(size)
    f.setBold(bold)
    return f


def h_line() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f'color: {BORDER2}; background: {BORDER2};')
    line.setFixedHeight(1)
    return line


# ---------------------------------------------------------------------------
# APP-WIDE QSS
# ---------------------------------------------------------------------------
APP_STYLE = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TXT};
    font-size: 11px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER2};
    background: {BG};
}}

QTabBar::tab {{
    background: {BG2};
    color: {TXT2};
    padding: 7px 18px;
    border: none;
    border-right: 1px solid {BORDER};
    font-size: 11px;
    letter-spacing: 0.3px;
}}

QTabBar::tab:selected {{
    background: {BG2};
    color: {WHITE};
    border-top: 2px solid {C_SPEED};
}}

QTabBar::tab:hover:!selected {{
    background: {BG3};
    color: {TXT};
}}

QComboBox, QLineEdit {{
    background: {BG3};
    color: {TXT};
    border: 1px solid {BORDER2};
    border-radius: 3px;
    padding: 4px 8px;
    selection-background-color: {BG3};
}}

QComboBox::drop-down {{
    border: none;
    padding-right: 4px;
}}

QPushButton {{
    background: {BG3};
    color: {TXT};
    border: 1px solid {BORDER2};
    border-radius: 3px;
    padding: 5px 12px;
    font-size: 10px;
    letter-spacing: 0.5px;
}}

QPushButton:hover {{
    background: #2d2d2d;
    border-color: #4a4a4a;
    color: {WHITE};
}}

QPushButton:pressed {{
    background: #1e1e1e;
}}

QScrollBar:vertical {{
    background: {BG1};
    width: 8px;
    border: none;
    border-radius: 4px;
}}

QScrollBar::handle:vertical {{
    background: {BG3};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}

QScrollBar:horizontal {{
    background: {BG1};
    height: 8px;
    border: none;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal {{
    background: {BG3};
    border-radius: 4px;
    min-width: 20px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 10px;
    color: {TXT2};
    font-size: 10px;
    letter-spacing: 0.3px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}

QLabel {{
    background: transparent;
    color: {TXT};
}}

QSplitter::handle {{
    background: {BORDER};
}}
"""

# ---------------------------------------------------------------------------
# TELEMETRY READERS
# ---------------------------------------------------------------------------

class TelemetryReader(ABC):
    @abstractmethod
    def read(self):
        pass

    @abstractmethod
    def is_connected(self):
        pass


class ACUDPReader(TelemetryReader):
    def __init__(self, host='127.0.0.1', port=9996):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self.latest_data = None
        self.handshake_sent = False
        self.running = False
        self.listener_thread = None

        self.sim_lap_start_ms = None
        self.sim_target_lap_ms = None
        self.sim_lap_count = 0
        self.sim_last_lap_ms = 0

    def connect(self):
        try:
            if self.socket:
                self.socket.close()
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.settimeout(1.0)
            identifier = 1
            version = 1
            operation_id = 0
            handshake = struct.pack('<iii', identifier, version, operation_id)
            self.socket.sendto(handshake, (self.host, self.port))
            try:
                data, addr = self.socket.recvfrom(2048)
                if data:
                    self.connected = True
                    self.handshake_sent = True
                    subscribe = struct.pack('<iii', identifier, version, 1)
                    self.socket.sendto(subscribe, (self.host, self.port))
                    self.running = True
                    self.listener_thread = threading.Thread(target=self._listen, daemon=True)
                    self.listener_thread.start()
                    return True
            except socket.timeout:
                pass
        except Exception as e:
            print(f"AC UDP connection error: {e}")
        return False

    def _listen(self):
        while self.running:
            try:
                data, addr = self.socket.recvfrom(2048)
                if data and len(data) > 4:
                    packet_id = struct.unpack('<i', data[0:4])[0]
                    if packet_id == 2:
                        self.latest_data = self._parse_car_info(data)
            except socket.timeout:
                continue
            except Exception:
                break

    def _parse_car_info(self, data):
        try:
            offset = 4
            speed_kmh = struct.unpack('<f', data[offset:offset+4])[0]
            # World position at bytes 8-20 (x, y, z floats per AC UDP spec)
            world_x = struct.unpack('<f', data[8:12])[0]  if len(data) >= 20 else 0.0
            world_z = struct.unpack('<f', data[16:20])[0] if len(data) >= 20 else 0.0
            offset += 8
            rpm = struct.unpack('<f', data[offset+16:offset+20])[0]
            gear = struct.unpack('<i', data[offset+20:offset+24])[0]
            throttle = brake = steer_angle = abs_val = tc_val = 0.0
            if len(data) >= 56:
                throttle = struct.unpack('<f', data[36:40])[0]
                brake = struct.unpack('<f', data[40:44])[0]
                steer_angle = struct.unpack('<f', data[44:48])[0]
                abs_val = struct.unpack('<f', data[48:52])[0]
                tc_val = struct.unpack('<f', data[52:56])[0]

            now_ms = int(time.time() * 1000)
            if self.sim_lap_start_ms is None:
                self.sim_lap_start_ms = now_ms
                self.sim_target_lap_ms = random.randint(81000, 99000)

            elapsed_ms = now_ms - self.sim_lap_start_ms
            if elapsed_ms >= self.sim_target_lap_ms:
                self.sim_last_lap_ms = elapsed_ms
                self.sim_lap_count += 1
                self.sim_lap_start_ms = now_ms
                self.sim_target_lap_ms = random.randint(81000, 99000)
                elapsed_ms = 0

            return {
                'speed': speed_kmh,
                'rpm': rpm,
                'max_rpm': 8000,
                'gear': gear,
                'throttle': throttle,
                'brake': brake,
                'steer_angle': steer_angle,
                'abs': abs_val,
                'tc': tc_val,
                'fuel': 0,
                'max_fuel': 100,
                'lap_time': self.sim_last_lap_ms / 1000.0 if self.sim_lap_count > 0 else 0,
                'position': 0,
                'car_name': 'Simulated Car',
                'track_name': 'Monza (Simulated)',
                'lap_count': self.sim_lap_count,
                'current_time': elapsed_ms,
                'world_x': world_x,
                'world_z': world_z,
            }
        except Exception:
            return None

    def read(self):
        if not self.connected:
            if not self.connect():
                return None
        return self.latest_data

    def is_connected(self):
        return self.connected and self.latest_data is not None

    def disconnect(self):
        self.running = False
        if self.socket:
            self.socket.close()


class ACCReader(TelemetryReader):
    """Assetto Corsa Competizione via pyaccsharedmemory (Windows shared memory)."""

    def __init__(self):
        try:
            from pyaccsharedmemory import accSharedMemory
            self.asm = accSharedMemory()
            self.available = True
        except Exception as e:
            print(f"ACC Reader initialization failed: {e}")
            print("Install with: pip install pyaccsharedmemory")
            self.available = False

    def read(self):
        if not self.available:
            return None
        try:
            sm = self.asm.read_shared_memory()
            if sm is None:
                return None
            return {
                'speed': sm.Physics.speed_kmh,
                'rpm': sm.Physics.rpm,
                'max_rpm': sm.Static.max_rpm,
                'gear': sm.Physics.gear - 1,
                'throttle': sm.Physics.gas * 100,
                'brake': sm.Physics.brake * 100,
                'steer_angle': sm.Physics.steer_angle,
                'abs': sm.Physics.abs,
                'tc': sm.Physics.tc,
                'fuel': sm.Physics.fuel,
                'max_fuel': sm.Static.max_fuel,
                'lap_time': sm.Graphics.last_time / 1000,
                'position': sm.Graphics.position,
                'car_name': sm.Static.car_model,
                'track_name': sm.Static.track,
                'lap_count': sm.Graphics.completed_lap,
                'current_time': sm.Graphics.current_time,
                'lap_dist_pct': sm.Graphics.normalized_car_position,
                'world_x': sm.Graphics.car_coordinates[0].x,
                'world_z': sm.Graphics.car_coordinates[0].z,
            }
        except Exception as e:
            print(f"ACC read error: {e}")
            return None

    def is_connected(self):
        if not self.available:
            return False
        try:
            sm = self.asm.read_shared_memory()
            return sm is not None
        except Exception:
            return False


class IRacingReader(TelemetryReader):
    """iRacing telemetry via irsdk shared memory (Windows only).
    Install: pip install irsdk
    """

    def __init__(self):
        self.ir = None
        self.available = False
        try:
            import irsdk  # type: ignore[import]  – Windows-only, optional dep
            self.ir = irsdk.IRSDK()
            self.ir.startup()
            self.available = True
        except Exception as e:
            print(f"iRacing SDK init failed (Windows + iRacing required): {e}")

    def read(self):
        if not self.available or self.ir is None:
            return None
        try:
            if not (self.ir.is_initialized and self.ir.is_connected):
                return None
            self.ir.freeze_var_buffer_latest()

            speed_ms = self.ir['Speed'] or 0.0
            rpm      = self.ir['RPM']   or 0.0
            gear_raw = self.ir['Gear']  or 0       # -1=R, 0=N, 1+=drive
            throttle = (self.ir['Throttle'] or 0.0) * 100.0
            brake    = (self.ir['Brake']    or 0.0) * 100.0
            steer    = self.ir['SteeringWheelAngle'] or 0.0
            fuel     = self.ir['FuelLevel']    or 0.0
            fuel_pct = self.ir['FuelLevelPct'] or 0.0
            lap      = self.ir['Lap']              or 0
            cur_s    = self.ir['LapCurrentLapTime'] or 0.0
            last_lap = self.ir['LapLastLapTime']    or 0.0
            lap_pct  = self.ir['LapDistPct']        or 0.0

            try:
                position = int(self.ir['PlayerCarPosition'] or 0)
            except Exception:
                position = 0

            max_rpm = 10000.0
            try:
                max_rpm = float(self.ir['DriverInfo']['DriverCarRedLine'] or 10000)
            except Exception:
                pass

            max_fuel = (fuel / fuel_pct) if fuel_pct > 0.001 else 100.0

            car_name = 'iRacing Car'
            try:
                idx = self.ir['PlayerCarIdx'] or 0
                car_name = self.ir['DriverInfo']['Drivers'][idx]['CarScreenName']
            except Exception:
                pass

            track_name = 'iRacing Track'
            try:
                track_name = self.ir['WeekendInfo']['TrackName']
            except Exception:
                pass

            # Normalise to app convention: 0=R, 1=N, 2+=1st,2nd,...
            if gear_raw < 0:
                gear = 0
            elif gear_raw == 0:
                gear = 1
            else:
                gear = gear_raw + 1

            # World position for track recording
            world_x = world_z = 0.0
            try:
                player_idx = int(self.ir['PlayerCarIdx'] or 0)
                car_x = self.ir['CarIdxX']
                car_z = self.ir['CarIdxZ']
                if car_x and car_z:
                    world_x = float(car_x[player_idx])
                    world_z = float(car_z[player_idx])
            except Exception:
                pass

            return {
                'speed':        speed_ms * 3.6,
                'rpm':          rpm,
                'max_rpm':      max_rpm,
                'gear':         gear,
                'throttle':     throttle,
                'brake':        brake,
                'steer_angle':  steer,
                'abs':          0.0,
                'tc':           0.0,
                'fuel':         fuel,
                'max_fuel':     max_fuel,
                'lap_time':     last_lap,
                'position':     position,
                'car_name':     car_name,
                'track_name':   track_name,
                'lap_count':    lap,
                'current_time': cur_s * 1000.0,   # → ms
                'lap_dist_pct': lap_pct,
                'world_x':      world_x,
                'world_z':      world_z,
            }
        except Exception as e:
            print(f"iRacing read error: {e}")
            return None

    def is_connected(self):
        if not self.available or self.ir is None:
            return False
        try:
            return bool(self.ir.is_initialized and self.ir.is_connected)
        except Exception:
            return False

    def shutdown(self):
        if self.ir:
            try:
                self.ir.shutdown()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# TRACK RECORDER  –  drive a lap to capture world coordinates → saved JSON
# ---------------------------------------------------------------------------

class TrackRecorder:
    """Samples world position during a lap and saves a normalized track JSON."""

    N_OUT = 250          # waypoints to write to the JSON file
    MIN_SAMPLES = 50     # minimum samples before a save is accepted

    def __init__(self):
        self.recording = False
        self._samples: list[tuple[float, float, float]] = []   # (pct, x, z)
        self._last_pct = -1.0

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def start(self):
        self.recording = True
        self._samples = []
        self._last_pct = -1.0

    def stop(self):
        self.recording = False

    def feed(self, lap_dist_pct: float, world_x: float, world_z: float):
        if not self.recording:
            return
        if world_x == 0.0 and world_z == 0.0:
            return
        # Skip backward jumps larger than 0.5 (lap boundary crossing)
        if self._last_pct >= 0 and (lap_dist_pct - self._last_pct) < -0.5:
            return
        # Deduplicate: only record if we've moved at least 0.001 along the lap
        if abs(lap_dist_pct - self._last_pct) < 0.001:
            return
        self._samples.append((lap_dist_pct, world_x, world_z))
        self._last_pct = lap_dist_pct

    def save(self, track_name: str, length_m: int) -> str | None:
        """Normalize and save to tracks/{key}.json. Returns the path on success."""
        if len(self._samples) < self.MIN_SAMPLES:
            return None

        # Sort by lap fraction
        s = sorted(self._samples, key=lambda t: t[0])
        xs = [p[1] for p in s]
        zs = [p[2] for p in s]

        min_x, max_x = min(xs), max(xs)
        min_z, max_z = min(zs), max(zs)
        span = max(max_x - min_x, max_z - min_z)
        if span == 0:
            return None

        PAD = 0.06
        scale = (1.0 - 2 * PAD) / span
        nx = [(x - min_x) * scale + PAD for x in xs]
        nz = [(z - min_z) * scale + PAD for z in zs]

        # Downsample to N_OUT evenly-spaced points
        n = len(nx)
        indices = [int(round(i * (n - 1) / (self.N_OUT - 1))) for i in range(self.N_OUT)]
        pts = [[round(nx[i], 4), round(nz[i], 4)] for i in indices]

        # Derive a filesystem-safe key from the track name
        import re
        track_key = re.sub(r'[^a-z0-9_]', '_', track_name.lower()).strip('_')
        track_key = re.sub(r'_+', '_', track_key)

        data = {
            'name': track_name,
            'track_key': track_key,
            'length_m': length_m,
            'pts': pts,
            'turns': [],
        }

        out_dir = Path(__file__).parent / 'tracks'
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'{track_key}.json'
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        return str(path)


def _load_saved_tracks():
    """Load any JSON files from the tracks/ directory into TRACKS and TRACK_NAME_MAP."""
    tracks_dir = Path(__file__).parent / 'tracks'
    if not tracks_dir.exists():
        return
    for json_file in sorted(tracks_dir.glob('*.json')):
        try:
            with open(json_file) as f:
                td = json.load(f)
            key = td['track_key']
            TRACKS[key] = {
                'name': td['name'],
                'pts': [tuple(p) for p in td['pts']],
                'turns': [tuple(t) for t in td.get('turns', [])],
                'length_m': td['length_m'],
            }
            TRACK_NAME_MAP[key] = key
        except Exception as e:
            print(f'Failed to load saved track {json_file.name}: {e}')


# ---------------------------------------------------------------------------
# TRACK DATA  –  normalized waypoints + turn metadata
# Track registry: starts empty, populated by _load_saved_tracks() from tracks/*.json
TRACKS: dict = {}

# Substring → track key map: populated by _load_saved_tracks() alongside TRACKS
TRACK_NAME_MAP: dict[str, str] = {}

# Load any previously recorded tracks (tracks/*.json)
_load_saved_tracks()

# Fallback length used by graph x-axis before a real track length is known
MONZA_LENGTH_M: int = 5000

# No default track – widget starts empty and builds live
DEFAULT_TRACK: str | None = None

# Number of distance-buckets used to store per-position telemetry
N_TRACK_SEG = 220


# ---------------------------------------------------------------------------
# CUSTOM WIDGETS
# ---------------------------------------------------------------------------

class RevBar(QWidget):
    """Custom RPM rev-bar drawn with QPainter. No QProgressBar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0
        self.maximum = 8000
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, rpm: float, max_rpm: float = 8000):
        self.value = max(0, rpm)
        self.maximum = max(1, max_rpm)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        # Background track
        painter.fillRect(0, 0, w, h, QColor(BG3))

        ratio = min(1.0, self.value / self.maximum)

        # Zone boundaries as fractions of max_rpm
        z1 = 0.70
        z2 = 0.90

        # Draw filled bar in zones
        fill_w = int(w * ratio)

        def zone_rect(start_frac, end_frac, fill_color):
            x0 = int(w * start_frac)
            x1 = int(w * end_frac)
            if fill_w <= x0:
                return
            painter.fillRect(x0, 0, min(fill_w, x1) - x0, h, QColor(fill_color))

        zone_rect(0,   z1,  '#1e7a1e')
        zone_rect(z1,  z2,  '#8a6200')
        zone_rect(z2,  1.0, '#9a1f1f')

        # Redline tick at 90%
        tick_x = int(w * z2)
        painter.setPen(QPen(QColor(WHITE), 1))
        painter.drawLine(tick_x, 0, tick_x, h)

        # Border
        painter.setPen(QPen(QColor(BORDER2), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        painter.end()


class PedalBar(QWidget):
    """Vertical pedal bar drawn with QPainter."""

    def __init__(self, color: str, label: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.label = label
        self.value = 0.0
        self.setFixedWidth(32)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_value(self, pct: float):
        self.value = max(0.0, min(100.0, pct))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        text_h = 16

        bar_h = h - text_h
        ratio = self.value / 100.0
        fill_h = int(bar_h * ratio)

        # Track
        painter.fillRect(0, text_h, w, bar_h, QColor(BG3))

        # Fill from bottom
        painter.fillRect(0, text_h + bar_h - fill_h, w, fill_h, QColor(self.color))

        # Border
        painter.setPen(QPen(QColor(BORDER2), 1))
        painter.drawRect(0, text_h, w - 1, bar_h - 1)

        # Value text at top
        painter.setFont(mono(7))
        painter.setPen(QColor(TXT2))
        painter.drawText(QRectF(0, 0, w, text_h), Qt.AlignmentFlag.AlignCenter, f"{int(self.value)}")

        painter.end()


class ValueDisplay(QWidget):
    """Small card: colored dot + channel name + large value."""

    def __init__(self, channel_color: str, channel_name: str,
                 value_font_size: int = 22, unit: str = '', parent=None):
        super().__init__(parent)
        self.channel_color = channel_color
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        dot = QLabel('●')
        dot.setFont(sans(8))
        dot.setStyleSheet(f'color: {channel_color};')
        dot.setFixedWidth(12)
        layout.addWidget(dot)

        info_col = QVBoxLayout()
        info_col.setSpacing(1)
        name_lbl = QLabel(channel_name.upper())
        name_lbl.setFont(sans(8))
        name_lbl.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        info_col.addWidget(name_lbl)

        val_row = QHBoxLayout()
        val_row.setSpacing(4)
        self.value_label = QLabel('--')
        self.value_label.setFont(mono(value_font_size, bold=True))
        self.value_label.setStyleSheet(f'color: {WHITE};')
        val_row.addWidget(self.value_label)

        if unit:
            unit_lbl = QLabel(unit)
            unit_lbl.setFont(sans(9))
            unit_lbl.setStyleSheet(f'color: {TXT2};')
            unit_lbl.setAlignment(Qt.AlignmentFlag.AlignBottom)
            val_row.addWidget(unit_lbl)
        val_row.addStretch()

        info_col.addLayout(val_row)
        layout.addLayout(info_col)
        layout.addStretch()

    def set_value(self, text: str):
        self.value_label.setText(text)


class SteeringWidget(QWidget):
    """Steering wheel visualization."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0.0
        self.setMinimumSize(140, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_angle(self, angle: float):
        self.angle = angle
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() // 2
        cy = self.height() // 2
        text_h = 18
        radius = min(cx, cy - text_h // 2) - 6

        angle_deg = math.degrees(self.angle)
        abs_deg = abs(angle_deg)

        if abs_deg < 90:
            indicator_color = QColor(C_THROTTLE)
        elif abs_deg < 180:
            indicator_color = QColor(C_TC)
        else:
            indicator_color = QColor(C_BRAKE)

        # Outer ring fill
        painter.setPen(QPen(QColor(BORDER2), 2))
        painter.setBrush(QBrush(QColor(BG3)))
        painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        # Indicator arc (sweep from 0 to angle)
        arc_pen = QPen(indicator_color, 4)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arc_rect = QRectF(cx - radius + 5, cy - radius + 5,
                          (radius - 5) * 2, (radius - 5) * 2)
        start_angle = 90 * 16  # Qt: 90deg = top, angles in 1/16th degree
        span_angle = int(-angle_deg * 16)
        painter.drawArc(arc_rect, start_angle, span_angle)

        # 3 spokes rotated by steering angle
        spoke_pen = QPen(QColor(TXT2), 2)
        spoke_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(spoke_pen)
        spoke_len = radius - 8
        for offset_deg in [0, 120, 240]:
            rad = math.radians(offset_deg) + self.angle
            ex = cx + spoke_len * math.sin(rad)
            ey = cy - spoke_len * math.cos(rad)
            painter.drawLine(cx, cy, int(ex), int(ey))

        # Hub
        hub_r = 6
        painter.setPen(QPen(QColor(BORDER2), 1))
        painter.setBrush(QBrush(QColor(BG2)))
        painter.drawEllipse(cx - hub_r, cy - hub_r, hub_r * 2, hub_r * 2)

        # Angle text below
        painter.setPen(QColor(TXT2))
        painter.setFont(mono(9))
        text_rect = QRectF(0, self.height() - text_h, self.width(), text_h)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter,
                         f"{angle_deg:.1f}°")

        painter.end()


class TrackMapWidget(QWidget):
    """
    MoTeC-style live track map for Monza GP.

    The track outline is drawn from MONZA_NORM waypoints.  Each segment is
    coloured according to the throttle / brake value recorded the last time
    the car passed that part of the circuit:

        brake  > 15 %  →  red   (intensity scales with brake %)
        throttle > 80% →  bright green
        throttle > 30% →  yellow-green gradient
        otherwise      →  dim gray  (coasting / lift)

    Turn labels, sector markers, and a glowing car-position dot complete the
    picture.  All painting is pure QPainter – no image files required.
    """

    PAD   = 28   # canvas padding in px
    W_OUT = 22   # outer track-surface stroke width  (thicker = bolder look)
    W_IN  =  8   # inner data-colour stroke width

    # Minimum live buckets before drawing anything
    MIN_DRAW = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(440, 370)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.car_progress  = 0.0
        self._throttle_map = [0.0] * N_TRACK_SEG
        self._brake_map    = [0.0] * N_TRACK_SEG

        # Live world-coordinate accumulation (bucket_index → (world_x, world_z))
        self._world_buckets: dict[int, tuple[float, float]] = {}
        self._raw_min_x = self._raw_max_x = 0.0
        self._raw_min_z = self._raw_max_z = 0.0
        self._bounds_set = False

        # Normalized display points (0-1) sorted by lap fraction
        self._norm:       list[tuple[float, float]] = []
        self._turns:      list = []
        self._track_name: str  = ''

        # Pixel-coord cache – rebuilt on resize or norm change
        self._pts:     list[tuple[float, float]] = []
        self._last_sz: tuple[int, int]            = (0, 0)

    # ------------------------------------------------------------------ API

    def set_track(self, key: str):
        """Load a saved-JSON track if available; otherwise reset to live-build mode."""
        td  = TRACKS.get(key, {})
        pts = td.get('pts', [])
        if pts:
            self._norm       = [tuple(p) for p in pts]
            self._turns      = list(td.get('turns', []))
            self._track_name = td.get('name', key)
            # Clear live data – we're showing a saved layout
            self._world_buckets = {}
            self._bounds_set    = False
        else:
            # Unknown / new track – start empty
            self._norm       = []
            self._turns      = []
            self._track_name = td.get('name', key.replace('_', ' ').title()) if td else ''
        self._pts     = []
        self._last_sz = (0, 0)
        self.reset()

    def reset_track(self, display_name: str = ''):
        """Clear accumulated shape and throttle/brake data (new session / track switch)."""
        self._world_buckets = {}
        self._raw_min_x = self._raw_max_x = 0.0
        self._raw_min_z = self._raw_max_z = 0.0
        self._bounds_set = False
        self._norm       = []
        self._turns      = []
        self._track_name = display_name
        self._pts        = []
        self._last_sz    = (0, 0)
        self.reset()

    def feed_world_pos(self, pct: float, world_x: float, world_z: float):
        """Add a live world-coord sample. Rebuilds the display when new ground is covered."""
        if world_x == 0.0 and world_z == 0.0:
            return
        bucket     = int(pct * N_TRACK_SEG) % N_TRACK_SEG
        is_new     = bucket not in self._world_buckets
        self._world_buckets[bucket] = (world_x, world_z)

        # Update bounding box
        bounds_changed = False
        if not self._bounds_set:
            self._raw_min_x = self._raw_max_x = world_x
            self._raw_min_z = self._raw_max_z = world_z
            self._bounds_set = True
            bounds_changed = True
        else:
            if world_x < self._raw_min_x: self._raw_min_x = world_x; bounds_changed = True
            if world_x > self._raw_max_x: self._raw_max_x = world_x; bounds_changed = True
            if world_z < self._raw_min_z: self._raw_min_z = world_z; bounds_changed = True
            if world_z > self._raw_max_z: self._raw_max_z = world_z; bounds_changed = True

        if (is_new or bounds_changed) and len(self._world_buckets) >= self.MIN_DRAW:
            self._recompute_norm()

    def _recompute_norm(self):
        """Re-normalize all accumulated world coords to centered 0-1 space."""
        if not self._world_buckets or not self._bounds_set:
            return
        span_x = self._raw_max_x - self._raw_min_x
        span_z = self._raw_max_z - self._raw_min_z
        span   = max(span_x, span_z)
        if span < 1.0:        # less than 1 m – skip
            return

        scale    = 0.90 / span                       # 5 % margin each side
        offset_x = (1.0 - span_x * scale) / 2.0     # center shorter axis
        offset_z = (1.0 - span_z * scale) / 2.0

        self._norm = [
            (round((x - self._raw_min_x) * scale + offset_x, 4),
             round((z - self._raw_min_z) * scale + offset_z, 4))
            for _, (x, z) in sorted(self._world_buckets.items())
        ]
        self._pts     = []       # invalidate pixel cache
        self._last_sz = (0, 0)
        self.update()

    def update_telemetry(self, lap_progress: float, throttle: float, brake: float):
        self.car_progress = max(0.0, min(1.0, lap_progress))
        bucket = int(lap_progress * N_TRACK_SEG) % N_TRACK_SEG
        self._throttle_map[bucket] = throttle
        self._brake_map[bucket]    = brake
        self.update()

    def reset(self):
        self.car_progress  = 0.0
        self._throttle_map = [0.0] * N_TRACK_SEG
        self._brake_map    = [0.0] * N_TRACK_SEG
        self.update()

    # ----------------------------------------------------------- scaled pts
    def _get_pts(self) -> list[tuple[float, float]]:
        sz = (self.width(), self.height())
        if sz == self._last_sz and self._pts:
            return self._pts
        w, h = sz
        pad  = self.PAD
        self._pts = [
            (pad + x * (w - 2 * pad),
             pad + y * (h - 2 * pad))
            for x, y in self._norm
        ]
        self._last_sz = sz
        return self._pts

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(BG))

        pts = self._get_pts()
        n   = len(pts)

        # ── Empty / building state ─────────────────────────────────────────
        if n < 2:
            painter.setPen(QColor('#333333'))
            painter.setFont(sans(9))
            filled = len(self._world_buckets)
            if filled > 0:
                pct_done = int(filled / N_TRACK_SEG * 100)
                msg = f'Building track map…  {pct_done}%  ({filled} / {N_TRACK_SEG} segments)'
            else:
                msg = 'Drive a lap to build the track map'
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)
            painter.end()
            return

        cap  = Qt.PenCapStyle.RoundCap
        join = Qt.PenJoinStyle.RoundJoin

        # ── Pass 1: wide dark track surface ──────────────────────────────
        surface_pen = QPen(QColor('#1e1e1e'), self.W_OUT, Qt.PenStyle.SolidLine, cap, join)
        painter.setPen(surface_pen)
        for i in range(n):
            p1 = QPointF(*pts[i])
            p2 = QPointF(*pts[(i + 1) % n])
            painter.drawLine(p1, p2)

        # Thin edge highlight to define track boundary
        edge_pen = QPen(QColor('#303030'), self.W_OUT + 2, Qt.PenStyle.SolidLine, cap, join)
        painter.setPen(edge_pen)
        for i in range(n):
            p1 = QPointF(*pts[i])
            p2 = QPointF(*pts[(i + 1) % n])
            painter.drawLine(p1, p2)
        # Redraw surface on top to keep it cleaner
        painter.setPen(surface_pen)
        for i in range(n):
            p1 = QPointF(*pts[i])
            p2 = QPointF(*pts[(i + 1) % n])
            painter.drawLine(p1, p2)

        # ── Pass 2: colour-coded channel data ────────────────────────────
        for i in range(n):
            frac   = i / n
            bucket = int(frac * N_TRACK_SEG) % N_TRACK_SEG
            thr    = self._throttle_map[bucket]
            brk    = self._brake_map[bucket]

            if brk > 15:
                t   = min(1.0, brk / 100.0)
                col = QColor(int(180 + 75 * t), int(40 * (1 - t)), int(40 * (1 - t)))
            elif thr > 80:
                col = QColor(0, 232, 120)
            elif thr > 30:
                t   = (thr - 30) / 50.0
                col = QColor(int(220 * (1 - t)), int(180 + 52 * t), 40)
            else:
                col = QColor(70, 70, 70)

            p1 = QPointF(*pts[i])
            p2 = QPointF(*pts[(i + 1) % n])
            painter.setPen(QPen(col, self.W_IN, Qt.PenStyle.SolidLine, cap, join))
            painter.drawLine(p1, p2)

        # ── S/F line  (checkerboard-style double bar) ─────────────────────
        sx, sy = pts[0]
        for i, col_hex in enumerate(['#ffffff', '#000000', '#ffffff']):
            painter.setPen(QPen(QColor(col_hex), 2))
            off = (i - 1) * 4
            painter.drawLine(QPointF(sx + off, sy - 10), QPointF(sx + off, sy + 10))
        sf_font = QFont()
        sf_font.setPointSize(6)
        sf_font.setBold(True)
        painter.setFont(sf_font)
        painter.setPen(QColor('#cccccc'))
        painter.drawText(int(sx + 8), int(sy - 4), 'S/F')

        # ── Track name ────────────────────────────────────────────────────
        name_font = QFont()
        name_font.setPointSize(7)
        painter.setFont(name_font)
        painter.setPen(QColor('#444444'))
        painter.drawText(self.PAD, self.PAD - 6, self._track_name)

        # ── Turn circles  (reference-image style: circle + number + name) ─
        num_font = QFont()
        num_font.setPointSize(6)
        num_font.setBold(True)
        name_font2 = QFont()
        name_font2.setPointSize(6)
        CR = 8   # circle radius in px

        for frac, lbl, tname, ox, oy in self._turns:
            idx = int(frac * n) % n
            lx, ly = pts[idx]
            cp2 = QPointF(lx + ox, ly + oy)

            # Filled circle
            painter.setPen(QPen(QColor('#c0c0c0'), 1.2))
            painter.setBrush(QBrush(QColor('#1a1a1a')))
            painter.drawEllipse(cp2, CR, CR)

            # Number text centred inside circle
            painter.setFont(num_font)
            painter.setPen(QColor('#e8e8e8'))
            r = QRectF(cp2.x() - CR, cp2.y() - CR, CR * 2, CR * 2)
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, lbl)

            # Corner name in dim text beside the circle
            if tname:
                painter.setFont(name_font2)
                painter.setPen(QColor('#555555'))
                # Place name below/above depending on oy sign
                ny = int(cp2.y() + (CR + 9 if oy >= 0 else -CR - 3))
                painter.drawText(int(cp2.x() - 20), ny, tname)

        # ── Car position dot (glowing red) ───────────────────────────────
        car_idx = int(self.car_progress * n) % n
        cx, cy  = pts[car_idx]
        cp      = QPointF(cx, cy)

        grad = QRadialGradient(cp, 14)
        grad.setColorAt(0.0, QColor(255, 60,  60, 210))
        grad.setColorAt(0.5, QColor(255, 60,  60,  80))
        grad.setColorAt(1.0, QColor(255, 60,  60,   0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(cp, 14, 14)

        painter.setBrush(QBrush(QColor('#ff3c3c')))
        painter.setPen(QPen(QColor('#ffffff'), 1.5))
        painter.drawEllipse(cp, 5, 5)

        painter.end()


# ---------------------------------------------------------------------------
# GRAPH WIDGETS
# ---------------------------------------------------------------------------

def _style_ax(ax, fig, ylabel: str = '', ylim=None):
    """Apply consistent MoTeC-inspired dark styling to an axes object."""
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG1)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#303030')
    ax.spines['bottom'].set_color('#303030')
    ax.tick_params(colors=TXT2, labelsize=7, length=3)
    ax.grid(True, color='#1c1c1c', linewidth=0.8, linestyle='-', axis='y')
    if ylabel:
        ax.set_ylabel(ylabel, color=TXT2, fontsize=8)
    if ylim:
        ax.set_ylim(ylim)
    fig.tight_layout(pad=0.4)


class ChannelGraph(FigureCanvas):
    """Single-channel live telemetry graph."""

    def __init__(self, color: str, ylabel: str, ylim=(0, 100), parent=None):
        self.fig = Figure(figsize=(8, 1.8), facecolor=BG)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        _style_ax(self.ax, self.fig, ylabel=ylabel, ylim=ylim)
        self.data = []
        self.line, = self.ax.plot([], [], color=color, linewidth=1.4)

    def update_data(self, value: float):
        self.data.append(value)
        x = range(len(self.data))
        self.line.set_data(x, self.data)
        self.ax.set_xlim(0, max(1, len(self.data)))
        self.draw_idle()

    def clear(self):
        self.data.clear()
        self.line.set_data([], [])
        self.ax.set_xlim(0, 1)
        self.draw_idle()


class MultiChannelGraph(FigureCanvas):
    """Two-channel live telemetry graph."""

    def __init__(self, color1: str, color2: str, ylabel: str,
                 label1: str, label2: str, ylim=(0, 100), parent=None):
        self.fig = Figure(figsize=(8, 1.8), facecolor=BG)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        _style_ax(self.ax, self.fig, ylabel=ylabel, ylim=ylim)
        self.data1, self.data2 = [], []
        self.line1, = self.ax.plot([], [], color=color1, linewidth=1.4, label=label1)
        self.line2, = self.ax.plot([], [], color=color2, linewidth=1.4, label=label2)
        self.ax.legend(fontsize=7, framealpha=0, loc='upper right',
                       labelcolor=TXT2)

    def update_data(self, v1: float, v2: float):
        self.data1.append(v1)
        self.data2.append(v2)
        x = range(len(self.data1))
        self.line1.set_data(x, self.data1)
        self.line2.set_data(x, self.data2)
        self.ax.set_xlim(0, max(1, len(self.data1)))
        self.draw_idle()

    def clear(self):
        self.data1.clear()
        self.data2.clear()
        self.line1.set_data([], [])
        self.line2.set_data([], [])
        self.ax.set_xlim(0, 1)
        self.draw_idle()


class AnalysisTelemetryGraph(FigureCanvas):
    """Distance-based single channel graph for lap analysis."""

    def __init__(self, ylabel: str, color: str = C_SPEED, ylim=(0, 100), parent=None):
        self.fig = Figure(figsize=(4, 1.2), facecolor=BG)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        _style_ax(self.ax, self.fig, ylabel=ylabel, ylim=ylim)
        self.distances, self.values = [], []
        self.line, = self.ax.plot([], [], color=color, linewidth=1.2)
        self.vline = self.ax.axvline(0, color=WHITE, linewidth=0.8, alpha=0.5)

    def update_data(self, distance_m: float, value: float):
        self.distances.append(distance_m)
        self.values.append(value)
        self.line.set_data(self.distances, self.values)
        self.ax.set_xlim(0, max(MONZA_LENGTH_M, distance_m))
        self.vline.set_xdata([distance_m])
        self.draw_idle()

    def clear(self):
        self.distances.clear()
        self.values.clear()
        self.line.set_data([], [])
        self.vline.set_xdata([0])
        self.ax.set_xlim(0, MONZA_LENGTH_M)
        self.draw_idle()


class AnalysisMultiLineGraph(FigureCanvas):
    """Distance-based two-channel graph for lap analysis."""

    def __init__(self, ylabel: str, label1: str, label2: str,
                 color1: str = C_THROTTLE, color2: str = C_BRAKE,
                 ylim=(0, 100), parent=None):
        self.fig = Figure(figsize=(4, 1.2), facecolor=BG)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        _style_ax(self.ax, self.fig, ylabel=ylabel, ylim=ylim)
        self.distances, self.v1, self.v2 = [], [], []
        self.line1, = self.ax.plot([], [], color=color1, linewidth=1.2, label=label1)
        self.line2, = self.ax.plot([], [], color=color2, linewidth=1.2, label=label2)
        self.ax.legend(fontsize=6, framealpha=0, loc='upper right', labelcolor=TXT2)
        self.vline = self.ax.axvline(0, color=WHITE, linewidth=0.8, alpha=0.5)

    def update_data(self, distance_m: float, val1: float, val2: float):
        self.distances.append(distance_m)
        self.v1.append(val1)
        self.v2.append(val2)
        self.line1.set_data(self.distances, self.v1)
        self.line2.set_data(self.distances, self.v2)
        self.ax.set_xlim(0, max(MONZA_LENGTH_M, distance_m))
        self.vline.set_xdata([distance_m])
        self.draw_idle()

    def clear(self):
        self.distances.clear()
        self.v1.clear()
        self.v2.clear()
        self.line1.set_data([], [])
        self.line2.set_data([], [])
        self.vline.set_xdata([0])
        self.ax.set_xlim(0, MONZA_LENGTH_M)
        self.draw_idle()


class TimeDeltaGraph(FigureCanvas):
    """Time delta vs distance with fill bands."""

    def __init__(self, parent=None):
        self.fig = Figure(figsize=(10, 1.8), facecolor=BG)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        _style_ax(self.ax, self.fig, ylabel='Delta (s)')
        self.ax.axhline(0, color=C_REF, linewidth=1, alpha=0.8)
        self.distances, self.deltas = [], []
        self.current_dist = 0
        self.line, = self.ax.plot([], [], color=C_DELTA, linewidth=1.4)
        self.vline = self.ax.axvline(0, color=WHITE, linewidth=0.8, alpha=0.5)
        self._fill_pos = None
        self._fill_neg = None

    def update_data(self, distances, deltas, current_distance_m: float):
        self.distances = list(distances) if distances else []
        self.deltas = list(deltas) if deltas else []
        self.current_dist = current_distance_m

        if self._fill_pos:
            self._fill_pos.remove()
            self._fill_pos = None
        if self._fill_neg:
            self._fill_neg.remove()
            self._fill_neg = None

        if self.distances and self.deltas:
            self.line.set_data(self.distances, self.deltas)
            self.ax.set_xlim(0, max(MONZA_LENGTH_M, max(self.distances)))
            mn = min(-0.2, min(self.deltas) - 0.02)
            mx = max(0.2, max(self.deltas) + 0.02)
            self.ax.set_ylim(mn, mx)
            try:
                import numpy as np  # type: ignore[import-untyped]
                d = np.array(self.distances)
                v = np.array(self.deltas)
                self._fill_pos = self.ax.fill_between(d, 0, v, where=(v > 0),
                                                       color=C_REF, alpha=0.12)
                self._fill_neg = self.ax.fill_between(d, 0, v, where=(v <= 0),
                                                       color=C_DELTA, alpha=0.12)
            except ImportError:
                pass

        self.vline.set_xdata([current_distance_m])
        self.draw_idle()

    def clear(self):
        if self._fill_pos:
            self._fill_pos.remove()
            self._fill_pos = None
        if self._fill_neg:
            self._fill_neg.remove()
            self._fill_neg = None
        self.distances.clear()
        self.deltas.clear()
        self.line.set_data([], [])
        self.vline.set_xdata([0])
        self.ax.set_xlim(0, MONZA_LENGTH_M)
        self.ax.set_ylim(-0.2, 0.2)
        self.draw_idle()


# ---------------------------------------------------------------------------
# SECTOR TIMES PANEL
# ---------------------------------------------------------------------------

class SectorTimesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(230)
        self.setStyleSheet(f'background: {BG2};')
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        laps_header = QLabel('LAP TIMES')
        laps_header.setFont(sans(8))
        laps_header.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        layout.addWidget(laps_header)

        self.lap1_label = QLabel('01:41.475')
        self.lap1_label.setFont(mono(13, bold=True))
        self.lap1_label.setStyleSheet(f'color: {C_REF};')

        self.lap2_label = QLabel('01:41.510  +0.035s')
        self.lap2_label.setFont(mono(11))
        self.lap2_label.setStyleSheet(f'color: {C_DELTA};')

        lap_card = QFrame()
        lap_card.setStyleSheet(f'''
            QFrame {{
                background: {BG3};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 6px;
            }}
        ''')
        lap_card_layout = QVBoxLayout(lap_card)
        lap_card_layout.setSpacing(4)
        lap_card_layout.addWidget(self.lap1_label)
        lap_card_layout.addWidget(self.lap2_label)
        layout.addWidget(lap_card)

        layout.addWidget(h_line())

        sectors_header = QLabel('SECTOR GAPS')
        sectors_header.setFont(sans(8))
        sectors_header.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        layout.addWidget(sectors_header)

        grid = QGridLayout()
        grid.setSpacing(4)

        col_headers = ['SECTOR', 'LAP 1', 'Δ']
        for col, txt in enumerate(col_headers):
            lbl = QLabel(txt)
            lbl.setFont(sans(8))
            lbl.setStyleSheet(f'color: {TXT2}; letter-spacing: 0.5px;')
            grid.addWidget(lbl, 0, col)

        self.gap_labels = {}
        self.sector_time_labels = {}
        for row, s in enumerate(['S1', 'S2', 'S3', 'S4', 'S5'], 1):
            s_lbl = QLabel(s)
            s_lbl.setFont(mono(9, bold=True))
            s_lbl.setStyleSheet(f'color: {TXT2};')
            grid.addWidget(s_lbl, row, 0)

            st_lbl = QLabel('20.706')
            st_lbl.setFont(mono(10))
            st_lbl.setStyleSheet(f'color: {TXT};')
            self.sector_time_labels[s] = st_lbl
            grid.addWidget(st_lbl, row, 1)

            gap_lbl = QLabel('--')
            gap_lbl.setFont(mono(10, bold=True))
            self.gap_labels[s] = gap_lbl
            grid.addWidget(gap_lbl, row, 2)

        gaps_frame = QFrame()
        gaps_frame.setStyleSheet(f'''
            QFrame {{
                background: {BG3};
                border: 1px solid {BORDER};
                border-radius: 4px;
            }}
        ''')
        gaps_frame.setLayout(grid)
        layout.addWidget(gaps_frame)
        layout.addStretch()

    def update_laps(self, lap1_time_s: float, lap2_time_s: float, gap_s: float):
        def fmt(t):
            m = int(t // 60)
            s = t % 60
            return f'{m:02d}:{s:06.3f}'

        self.lap1_label.setText(fmt(lap1_time_s))
        sign = '+' if gap_s >= 0 else ''
        self.lap2_label.setText(f'{fmt(lap2_time_s)}  {sign}{gap_s:.3f}s')
        self._update_gaps(lap1_time_s, lap2_time_s)

    def _update_gaps(self, t1: float, t2: float):
        base = t1 / 5
        times = [base * 1.1, base * 0.9, base * 1.0, base * 1.05, base * 0.95]
        deltas = [
            (t2 - t1) * 0.20,
            (t2 - t1) * 0.40,
            (t2 - t1) * -0.30,
            (t2 - t1) * 0.20,
            (t2 - t1) * -0.20,
        ]
        for s, sec_t, delta in zip(['S1', 'S2', 'S3', 'S4', 'S5'], times, deltas):
            self.sector_time_labels[s].setText(f'{sec_t:.3f}')
            lbl = self.gap_labels[s]
            sign = '+' if delta >= 0 else ''
            lbl.setText(f'{sign}{delta:.3f}s')
            if delta > 0:
                lbl.setStyleSheet(f'color: {C_REF};')
            else:
                lbl.setStyleSheet(f'color: {C_THROTTLE};')


# ---------------------------------------------------------------------------
# CHANNEL HEADER LABEL (for Graphs tab)
# ---------------------------------------------------------------------------

def _channel_header(color: str, name: str, unit: str = '') -> QLabel:
    """Small colored-square + channel name header for graph sections."""
    txt = f'■  {name}'
    if unit:
        txt += f'  ·  {unit}'
    lbl = QLabel(txt)
    lbl.setFont(sans(9))
    lbl.setStyleSheet(f'color: {color}; letter-spacing: 0.8px; padding-top: 6px;')
    return lbl


# ---------------------------------------------------------------------------
# MAIN APPLICATION
# ---------------------------------------------------------------------------

class TelemetryApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AC / ACC Telemetry')
        self.setGeometry(100, 100, 1640, 980)

        self.ac_reader  = None
        self.acc_reader = ACCReader()
        self.ir_reader  = IRacingReader()
        self.current_reader = None
        self.auto_detect = True

        self.last_lap_time = 0
        self.current_lap_count = 0

        self.session_laps = []
        self._reset_current_lap_data()

        # Track selection (None = auto-detect from telemetry data)
        self._active_track_key: str | None = None
        self._auto_track = True

        # Track recorder
        self.recorder = TrackRecorder()

        self._init_ui()

        self.timer = QTimer()
        self.timer.timeout.connect(self._update_telemetry)
        self.timer.start(50)

    # ------------------------------------------------------------------
    # UI CONSTRUCTION
    # ------------------------------------------------------------------

    def _init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Top connection strip
        root_layout.addWidget(self._build_connection_strip())
        root_layout.addWidget(h_line())

        # Tabs
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)

        self.tabs.addTab(self._build_dashboard_tab(), 'DASHBOARD')
        self.tabs.addTab(self._build_graphs_tab(), 'TELEMETRY GRAPHS')
        self.tabs.addTab(self._build_analysis_tab(), 'LAP ANALYSIS')

        self._set_graph_title_suffix('Lap 1')

    def _build_connection_strip(self) -> QWidget:
        strip = QWidget()
        strip.setFixedHeight(38)
        strip.setStyleSheet(f'background: {BG2}; border-bottom: 1px solid {BORDER2};')
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(18)

        # Status indicator
        self.connection_dot = QLabel('●')
        self.connection_dot.setFont(sans(10))
        self.connection_dot.setStyleSheet('color: #444;')

        self.connection_label = QLabel('DISCONNECTED')
        self.connection_label.setFont(sans(9))
        self.connection_label.setStyleSheet(f'color: {TXT2}; letter-spacing: 0.5px;')

        layout.addWidget(self.connection_dot)
        layout.addWidget(self.connection_label)
        layout.addWidget(_vsep())

        # Game selector
        game_lbl = QLabel('SOURCE')
        game_lbl.setFont(sans(8))
        game_lbl.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        self.game_combo = QComboBox()
        self.game_combo.addItems([
            'Auto-Detect', 'ACC (Shared Memory)', 'AC (UDP)', 'iRacing (SDK)',
        ])
        self.game_combo.setFixedWidth(170)
        self.game_combo.currentTextChanged.connect(self._on_game_changed)
        layout.addWidget(game_lbl)
        layout.addWidget(self.game_combo)

        layout.addWidget(_vsep())

        # Track selector
        track_lbl = QLabel('TRACK')
        track_lbl.setFont(sans(8))
        track_lbl.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        self.track_combo = QComboBox()
        self.track_combo.addItem('Auto-Detect', userData=None)
        for key, td in TRACKS.items():
            self.track_combo.addItem(td['name'], userData=key)
        self.track_combo.setFixedWidth(155)
        self.track_combo.currentIndexChanged.connect(self._on_track_changed)
        layout.addWidget(track_lbl)
        layout.addWidget(self.track_combo)

        layout.addWidget(_vsep())

        # UDP settings
        host_lbl = QLabel('HOST')
        host_lbl.setFont(sans(8))
        host_lbl.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        self.udp_host = QLineEdit('127.0.0.1')
        self.udp_host.setFixedWidth(110)
        port_lbl = QLabel('PORT')
        port_lbl.setFont(sans(8))
        port_lbl.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        self.udp_port = QLineEdit('9996')
        self.udp_port.setFixedWidth(55)
        layout.addWidget(host_lbl)
        layout.addWidget(self.udp_host)
        layout.addWidget(port_lbl)
        layout.addWidget(self.udp_port)

        layout.addWidget(_vsep())

        # Track recorder
        self.rec_btn = QPushButton('⏺  REC')
        self.rec_btn.setFixedSize(72, 22)
        self.rec_btn.setCheckable(True)
        self.rec_btn.setStyleSheet(
            f'QPushButton {{ background: {BG3}; color: {TXT2}; border: 1px solid {BORDER2};'
            f' border-radius: 3px; font-size: 10px; padding: 0 6px; }}'
            f'QPushButton:checked {{ background: #5a0000; color: {C_BRAKE};'
            f' border-color: {C_BRAKE}; }}'
        )
        self.rec_btn.toggled.connect(self._on_rec_toggled)
        self.rec_label = QLabel('')
        self.rec_label.setFont(sans(8))
        self.rec_label.setStyleSheet(f'color: {TXT2};')
        layout.addWidget(self.rec_btn)
        layout.addWidget(self.rec_label)

        layout.addStretch()

        # Car / Track / Lap info
        self.car_label = QLabel('—')
        self.car_label.setFont(mono(10))
        self.car_label.setStyleSheet(f'color: {TXT};')
        self.track_label = QLabel('—')
        self.track_label.setFont(mono(10))
        self.track_label.setStyleSheet(f'color: {TXT};')
        self.header_lap_label = QLabel('LAP —')
        self.header_lap_label.setFont(mono(10, bold=True))
        self.header_lap_label.setStyleSheet(f'color: {C_SPEED};')

        layout.addWidget(self.car_label)
        layout.addWidget(_vsep())
        layout.addWidget(self.track_label)
        layout.addWidget(_vsep())
        layout.addWidget(self.header_lap_label)

        return strip

    def _build_dashboard_tab(self) -> QWidget:
        tab = QWidget()
        main = QVBoxLayout(tab)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(8)

        # ── Row 1: Speed column | Right column ──────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        # LEFT: Speed + Gear
        left_col = QVBoxLayout()
        left_col.setSpacing(4)

        speed_card = QFrame()
        speed_card.setStyleSheet(f'background: {BG2}; border: 1px solid {BORDER}; border-radius: 4px;')
        speed_card_layout = QVBoxLayout(speed_card)
        speed_card_layout.setContentsMargins(14, 10, 14, 10)
        speed_card_layout.setSpacing(2)

        self.speed_value = ValueDisplay(C_SPEED, 'Speed', value_font_size=48, unit='km/h')
        speed_card_layout.addWidget(self.speed_value)

        self.gear_value = ValueDisplay(C_GEAR, 'Gear', value_font_size=36)
        speed_card_layout.addWidget(self.gear_value)

        left_col.addWidget(speed_card)
        row1.addLayout(left_col, stretch=0)

        # RIGHT: RPM bar + pedals
        right_col = QVBoxLayout()
        right_col.setSpacing(8)

        # RPM section
        rpm_card = QFrame()
        rpm_card.setStyleSheet(f'background: {BG2}; border: 1px solid {BORDER}; border-radius: 4px;')
        rpm_card_layout = QVBoxLayout(rpm_card)
        rpm_card_layout.setContentsMargins(12, 8, 12, 8)
        rpm_card_layout.setSpacing(4)

        rpm_header = QHBoxLayout()
        rpm_name = QLabel('RPM')
        rpm_name.setFont(sans(8))
        rpm_name.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        self.rpm_numbers = QLabel('0 / 8000')
        self.rpm_numbers.setFont(mono(10))
        self.rpm_numbers.setStyleSheet(f'color: {TXT2};')
        rpm_header.addWidget(rpm_name)
        rpm_header.addStretch()
        rpm_header.addWidget(self.rpm_numbers)
        rpm_card_layout.addLayout(rpm_header)

        self.rev_bar = RevBar()
        rpm_card_layout.addWidget(self.rev_bar)
        right_col.addWidget(rpm_card)

        # Pedals + ABS/TC row
        pedals_row = QHBoxLayout()
        pedals_row.setSpacing(10)

        # Pedal bars
        pedals_card = QFrame()
        pedals_card.setStyleSheet(f'background: {BG2}; border: 1px solid {BORDER}; border-radius: 4px;')
        pedals_card_layout = QHBoxLayout(pedals_card)
        pedals_card_layout.setContentsMargins(12, 8, 12, 8)
        pedals_card_layout.setSpacing(14)

        # Throttle
        thr_col = QVBoxLayout()
        thr_col.setSpacing(3)
        thr_name = QLabel('THROTTLE')
        thr_name.setFont(sans(8))
        thr_name.setStyleSheet(f'color: {C_THROTTLE}; letter-spacing: 0.5px;')
        thr_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.throttle_bar = PedalBar(C_THROTTLE, 'THR')
        thr_col.addWidget(thr_name)
        thr_col.addWidget(self.throttle_bar)
        pedals_card_layout.addLayout(thr_col)

        # Brake
        brk_col = QVBoxLayout()
        brk_col.setSpacing(3)
        brk_name = QLabel('BRAKE')
        brk_name.setFont(sans(8))
        brk_name.setStyleSheet(f'color: {C_BRAKE}; letter-spacing: 0.5px;')
        brk_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brake_bar = PedalBar(C_BRAKE, 'BRK')
        brk_col.addWidget(brk_name)
        brk_col.addWidget(self.brake_bar)
        pedals_card_layout.addLayout(brk_col)

        pedals_row.addWidget(pedals_card)

        # ABS / TC badges
        aids_card = QFrame()
        aids_card.setStyleSheet(f'background: {BG2}; border: 1px solid {BORDER}; border-radius: 4px;')
        aids_layout = QVBoxLayout(aids_card)
        aids_layout.setContentsMargins(12, 8, 12, 8)
        aids_layout.setSpacing(8)

        aids_title = QLabel('DRIVER AIDS')
        aids_title.setFont(sans(8))
        aids_title.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        aids_layout.addWidget(aids_title)

        self.abs_badge = _AidBadge('ABS')
        self.tc_badge = _AidBadge('TC')
        aids_layout.addWidget(self.abs_badge)
        aids_layout.addWidget(self.tc_badge)
        aids_layout.addStretch()
        pedals_row.addWidget(aids_card)

        right_col.addLayout(pedals_row)
        row1.addLayout(right_col, stretch=1)
        main.addLayout(row1)

        # ── Row 2: Steering | Fuel | Position | Lap time ────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        steering_card = QFrame()
        steering_card.setStyleSheet(f'background: {BG2}; border: 1px solid {BORDER}; border-radius: 4px;')
        steering_card_layout = QVBoxLayout(steering_card)
        steering_card_layout.setContentsMargins(8, 8, 8, 8)
        steer_title = QLabel('STEERING')
        steer_title.setFont(sans(8))
        steer_title.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px;')
        steering_card_layout.addWidget(steer_title)
        self.steering_widget = SteeringWidget()
        steering_card_layout.addWidget(self.steering_widget)
        row2.addWidget(steering_card)

        # Fuel / Position / Lap time small cards
        info_row = QVBoxLayout()
        self.fuel_display = ValueDisplay(C_RPM, 'Fuel', value_font_size=16, unit='L')
        self.position_display = ValueDisplay(C_GEAR, 'Position', value_font_size=16)
        self.laptime_display = ValueDisplay(C_REF, 'Last Lap', value_font_size=14)

        for d in [self.fuel_display, self.position_display, self.laptime_display]:
            card = QFrame()
            card.setStyleSheet(f'background: {BG2}; border: 1px solid {BORDER}; border-radius: 4px;')
            cl = QVBoxLayout(card)
            cl.setContentsMargins(4, 4, 4, 4)
            cl.addWidget(d)
            info_row.addWidget(card)

        row2.addLayout(info_row)
        row2.addStretch()
        main.addLayout(row2)
        main.addStretch()

        return tab

    def _build_graphs_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        # Export buttons — right-aligned
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.export_last_lap_button = QPushButton('EXPORT LAP')
        self.export_last_lap_button.clicked.connect(self.export_last_lap_graphs)
        self.export_session_button = QPushButton('EXPORT SESSION')
        self.export_session_button.clicked.connect(self.export_session_graphs)
        btn_row.addWidget(self.export_last_lap_button)
        btn_row.addWidget(self.export_session_button)
        outer.addLayout(btn_row)

        # Scroll area for graphs
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')
        container = QWidget()
        container.setStyleSheet(f'background: {BG};')
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(2)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        self.speed_graph_title = _channel_header(C_SPEED, 'SPEED', 'km/h')
        vbox.addWidget(self.speed_graph_title)
        self.speed_graph = ChannelGraph(C_SPEED, 'km/h', ylim=(0, 300))
        vbox.addWidget(self.speed_graph)
        vbox.addWidget(h_line())

        self.pedals_graph_title = _channel_header(C_THROTTLE, 'THROTTLE & BRAKE', '%')
        vbox.addWidget(self.pedals_graph_title)
        self.pedals_graph = MultiChannelGraph(
            C_THROTTLE, C_BRAKE, '%', 'Throttle', 'Brake', ylim=(0, 100))
        vbox.addWidget(self.pedals_graph)
        vbox.addWidget(h_line())

        self.steering_graph_title = _channel_header(C_STEER, 'STEERING', '°')
        vbox.addWidget(self.steering_graph_title)
        self.steering_graph = ChannelGraph(C_STEER, '°', ylim=(-540, 540))
        vbox.addWidget(self.steering_graph)
        vbox.addWidget(h_line())

        self.rpm_graph_title = _channel_header(C_RPM, 'RPM', 'rpm')
        vbox.addWidget(self.rpm_graph_title)
        self.rpm_graph = ChannelGraph(C_RPM, 'rpm', ylim=(0, 10000))
        vbox.addWidget(self.rpm_graph)
        vbox.addWidget(h_line())

        self.gear_graph_title = _channel_header(C_GEAR, 'GEAR', '')
        vbox.addWidget(self.gear_graph_title)
        self.gear_graph = ChannelGraph(C_GEAR, 'gear', ylim=(-1, 8))
        vbox.addWidget(self.gear_graph)
        vbox.addWidget(h_line())

        self.aids_graph_title = _channel_header(C_ABS, 'ABS / TC', '')
        vbox.addWidget(self.aids_graph_title)
        self.aids_graph = MultiChannelGraph(
            C_ABS, C_TC, 'activity', 'ABS', 'TC', ylim=(0, 10))
        vbox.addWidget(self.aids_graph)

        return tab

    def _build_analysis_tab(self) -> QWidget:
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: sector times panel
        self.sector_panel = SectorTimesPanel()
        splitter.addWidget(self.sector_panel)

        # Center: track map
        self.track_map = TrackMapWidget()
        self.track_map.setMinimumWidth(420)
        splitter.addWidget(self.track_map)

        # Right: analysis telemetry graphs in a scroll area
        right_container = QWidget()
        right_container.setStyleSheet(f'background: {BG};')
        right_vbox = QVBoxLayout(right_container)
        right_vbox.setContentsMargins(4, 4, 4, 4)
        right_vbox.setSpacing(2)

        self.ana_speed = AnalysisTelemetryGraph('Speed km/h', color=C_SPEED, ylim=(0, 320))
        self.ana_throttle_brake = AnalysisMultiLineGraph(
            '%', 'Throttle', 'Brake', color1=C_THROTTLE, color2=C_BRAKE, ylim=(0, 100))
        self.ana_gear = AnalysisTelemetryGraph('Gear', color=C_GEAR, ylim=(-1, 8))
        self.ana_rpm = AnalysisTelemetryGraph('RPM', color=C_RPM, ylim=(0, 10000))
        self.ana_steer = AnalysisTelemetryGraph('Steer °', color=C_STEER, ylim=(-540, 540))

        for label, graph in [
            (('SPEED', C_SPEED, 'km/h'), self.ana_speed),
            (('THROTTLE & BRAKE', C_THROTTLE, '%'), self.ana_throttle_brake),
            (('GEAR', C_GEAR, ''), self.ana_gear),
            (('RPM', C_RPM, 'rpm'), self.ana_rpm),
            (('STEERING', C_STEER, '°'), self.ana_steer),
        ]:
            right_vbox.addWidget(_channel_header(label[1], label[0], label[2]))
            right_vbox.addWidget(graph)
            right_vbox.addWidget(h_line())

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right_container)
        right_scroll.setMinimumWidth(280)
        right_scroll.setMaximumWidth(340)
        splitter.addWidget(right_scroll)
        splitter.setSizes([230, 500, 310])
        main_layout.addWidget(splitter, stretch=3)

        # Bottom: time delta graph
        delta_header = QLabel('TIME DELTA')
        delta_header.setFont(sans(8))
        delta_header.setStyleSheet(f'color: {TXT2}; letter-spacing: 1px; padding-top: 4px;')
        main_layout.addWidget(delta_header)

        self.time_delta_graph = TimeDeltaGraph()
        self.time_delta_graph.setMinimumHeight(130)
        main_layout.addWidget(self.time_delta_graph, stretch=1)

        # Sector marker strip
        sector_strip = QHBoxLayout()
        sector_strip.setSpacing(2)
        sector_colors = [C_SPEED, C_THROTTLE, C_RPM, C_STEER, C_BRAKE]
        for s, c in zip(['S1', 'S2', 'S3', 'S4', 'S5'], sector_colors):
            lbl = QLabel(s)
            lbl.setFont(mono(8, bold=True))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f'background: {BG3}; color: {c}; border: 1px solid {BORDER}; '
                f'padding: 2px 8px; border-radius: 2px;'
            )
            sector_strip.addWidget(lbl)
        sector_strip.addStretch()
        main_layout.addLayout(sector_strip)

        return tab

    # ------------------------------------------------------------------
    # DATA MANAGEMENT
    # ------------------------------------------------------------------

    def _reset_current_lap_data(self):
        self.current_lap_data = {
            'time_ms': [],
            'speed': [],
            'throttle': [],
            'brake': [],
            'steer_deg': [],
            'rpm': [],
            'gear': [],
            'abs': [],
            'tc': [],
        }

    def _store_completed_lap(self):
        if self.current_lap_data.get('speed'):
            self.session_laps.append({
                'lap_number': self.current_lap_count,
                'data': {k: list(v) for k, v in self.current_lap_data.items()},
            })

    def _set_graph_title_suffix(self, suffix: str):
        # Lap info is shown in the connection strip header label.
        # Graph channel headers stay clean (no per-lap suffix in the label text).
        _ = suffix  # acknowledged, intentionally unused here

    # ------------------------------------------------------------------
    # GAME SELECTION / AUTO-DETECT
    # ------------------------------------------------------------------

    def _on_game_changed(self, game: str):
        self.auto_detect = False
        if game == 'Auto-Detect':
            self.auto_detect = True
            self.current_reader = None
        elif game == 'ACC (Shared Memory)':
            self.current_reader = self.acc_reader
        elif game == 'iRacing (SDK)':
            self.current_reader = self.ir_reader
        else:  # 'AC (UDP)'
            if self.ac_reader:
                self.ac_reader.disconnect()
            self.ac_reader = ACUDPReader(self.udp_host.text(), int(self.udp_port.text()))
            self.current_reader = self.ac_reader

    def _detect_game(self):
        """Priority: ACC → iRacing → AC UDP."""
        if self.acc_reader.is_connected():
            return self.acc_reader
        if self.ir_reader.is_connected():
            return self.ir_reader
        if not self.ac_reader:
            self.ac_reader = ACUDPReader(self.udp_host.text(), int(self.udp_port.text()))
        if self.ac_reader.is_connected():
            return self.ac_reader
        return None

    # ------------------------------------------------------------------
    # TRACK SELECTION / AUTO-DETECT
    # ------------------------------------------------------------------

    def _on_track_changed(self, index: int):
        key = self.track_combo.itemData(index)   # None = Auto-Detect
        self._auto_track = (key is None)
        if key and key in TRACKS:
            self._apply_track(key)

    def _apply_track(self, key: str):
        global MONZA_LENGTH_M
        self._active_track_key = key
        if key in TRACKS:
            self.track_map.set_track(key)
            MONZA_LENGTH_M = TRACKS[key].get('length_m', MONZA_LENGTH_M)
        else:
            # New unknown track – reset to live-build mode
            display = key.replace('_', ' ').title()
            self.track_map.reset_track(display_name=display)

    def _auto_detect_track(self, track_name: str):
        if not self._auto_track:
            return
        import re
        # Derive a stable key from whatever string the game reports
        key = re.sub(r'[^a-z0-9_]', '_', track_name.lower()).strip('_')
        key = re.sub(r'_+', '_', key)
        # Also check TRACK_NAME_MAP for any manual overrides
        name_lc = track_name.lower()
        for substr, mapped in TRACK_NAME_MAP.items():
            if substr in name_lc:
                key = mapped
                break
        if key != self._active_track_key:
            self._apply_track(key)

    # ------------------------------------------------------------------
    # TRACK RECORDING
    # ------------------------------------------------------------------

    def _on_rec_toggled(self, checked: bool):
        if checked:
            self.recorder.start()
            self.rec_label.setText('0 pts')
            self.rec_label.setStyleSheet(f'color: {C_BRAKE};')
        else:
            self.recorder.stop()
            self._finish_recording()

    def _finish_recording(self):
        data = self.current_reader.read() if self.current_reader else None
        track_name = data['track_name'] if data else 'Unknown Track'
        length_m = TRACKS.get(self._active_track_key or '', {}).get('length_m', MONZA_LENGTH_M)

        path = self.recorder.save(track_name, length_m)
        if path:
            _load_saved_tracks()
            self._reload_track_combo()
            self.rec_label.setText(f'Saved: {Path(path).stem}')
            self.rec_label.setStyleSheet(f'color: {C_THROTTLE};')
        else:
            n = self.recorder.sample_count
            self.rec_label.setText(f'Too few pts ({n})')
            self.rec_label.setStyleSheet(f'color: {C_ABS};')

    def _reload_track_combo(self):
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        self.track_combo.addItem('Auto-Detect', userData=None)
        for key, td in TRACKS.items():
            self.track_combo.addItem(td['name'], userData=key)
        self.track_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # TELEMETRY UPDATE LOOP
    # ------------------------------------------------------------------

    def _update_telemetry(self):
        if self.auto_detect:
            self.current_reader = self._detect_game()

        if self.current_reader is None:
            self.connection_dot.setStyleSheet('color: #444;')
            self.connection_label.setText('DISCONNECTED')
            self.connection_label.setStyleSheet(f'color: {TXT2}; letter-spacing: 0.5px;')
            self._reset_display()
            return

        data = self.current_reader.read()
        if data is None:
            self.connection_dot.setStyleSheet('color: #8a4a00;')
            self.connection_label.setText('CONNECTION LOST')
            self.connection_label.setStyleSheet(f'color: {C_ABS}; letter-spacing: 0.5px;')
            return

        # Lap change detection
        current_lap = data.get('lap_count', 0)
        current_time = data.get('current_time', 0)
        lap_changed = (
            current_lap > self.current_lap_count
            or (current_lap == 0 and self.current_lap_count > 0)
            or (current_time < 5000 and self.last_lap_time > 5000)
        )

        if lap_changed:
            self._store_completed_lap()
            self._reset_graphs()
            self._reset_analysis_graphs()
            self._reset_current_lap_data()
            display_lap = current_lap if current_lap > 0 else 1
            self.header_lap_label.setText(f'LAP {display_lap}')
            # Auto-save recording on lap completion
            if self.recorder.recording and self.recorder.sample_count >= TrackRecorder.MIN_SAMPLES:
                self.rec_btn.setChecked(False)  # triggers _on_rec_toggled → _finish_recording

        self.current_lap_count = current_lap
        self.last_lap_time = current_time

        if isinstance(self.current_reader, ACUDPReader):
            game_type = 'AC'
        elif isinstance(self.current_reader, IRacingReader):
            game_type = 'iRacing'
        else:
            game_type = 'ACC'
        self.connection_dot.setStyleSheet(f'color: {C_THROTTLE};')
        self.connection_label.setText(f'CONNECTED  ·  {game_type}')
        self.connection_label.setStyleSheet(f'color: {TXT}; letter-spacing: 0.5px;')

        # Gear text  (all readers normalise to: 0=R, 1=N, 2+=1st,2nd,…)
        gear = data['gear']
        if gear == 0:
            gear_text = 'R'
        elif gear == 1:
            gear_text = 'N'
        else:
            gear_text = str(gear - 1)  # 2→1st, 3→2nd, …

        # ── Dashboard updates ────────────────────────────────────────────
        self.speed_value.set_value(f"{int(data['speed'])}")
        self.gear_value.set_value(gear_text)

        rpm = data['rpm']
        max_rpm = data['max_rpm']
        self.rev_bar.set_value(rpm, max_rpm)
        self.rpm_numbers.setText(f"{int(rpm):,} / {int(max_rpm):,}")

        self.throttle_bar.set_value(data['throttle'])
        self.brake_bar.set_value(data['brake'])

        self.steering_widget.set_angle(data['steer_angle'])

        self.abs_badge.set_active(data['abs'] > 0, f"{data['abs']:.1f}")
        self.tc_badge.set_active(data['tc'] > 0, f"{data['tc']:.1f}")

        self.car_label.setText(data['car_name'])
        self.track_label.setText(data['track_name'])
        self._auto_detect_track(data['track_name'])

        fuel = data['fuel']
        self.fuel_display.set_value(f"{fuel:.1f}")

        self.position_display.set_value(str(data['position']))

        if data['lap_time'] > 0:
            lt = data['lap_time']
            m = int(lt // 60)
            s = lt % 60
            self.laptime_display.set_value(f'{m}:{s:06.3f}')

        # ── Graph updates ────────────────────────────────────────────────
        steer_deg = math.degrees(data['steer_angle'])
        self.speed_graph.update_data(data['speed'])
        self.pedals_graph.update_data(data['throttle'], data['brake'])
        self.steering_graph.update_data(steer_deg)
        self.rpm_graph.update_data(rpm)
        gear_int = gear if isinstance(gear, int) else 0
        self.gear_graph.update_data(gear_int)
        self.aids_graph.update_data(data['abs'], data['tc'])

        # ── Lap Analysis updates ─────────────────────────────────────────
        # iRacing provides exact lap fraction; other sims estimate from time.
        lap_dur_ms = 90000
        if 'lap_dist_pct' in data and data['lap_dist_pct'] > 0:
            lap_progress = float(data['lap_dist_pct'])
        else:
            lap_progress = min(1.0, current_time / lap_dur_ms) if lap_dur_ms > 0 else 0
        _track_length_m = TRACKS.get(self._active_track_key or '', {}).get('length_m', MONZA_LENGTH_M)
        distance_m = lap_progress * _track_length_m
        self.track_map.update_telemetry(lap_progress, data['throttle'], data['brake'])
        self.track_map.feed_world_pos(
            lap_progress,
            data.get('world_x', 0.0),
            data.get('world_z', 0.0),
        )

        # Feed recorder
        if self.recorder.recording:
            self.recorder.feed(
                lap_progress,
                data.get('world_x', 0.0),
                data.get('world_z', 0.0),
            )
            self.rec_label.setText(f'{self.recorder.sample_count} pts')

        ref_lap_s = 101.475
        gap_s = 0.035 + 0.02 * math.sin(time.time() * 0.5)
        self.sector_panel.update_laps(ref_lap_s, ref_lap_s + gap_s, gap_s)

        self.ana_speed.update_data(distance_m, data['speed'])
        self.ana_throttle_brake.update_data(distance_m, data['throttle'], data['brake'])
        self.ana_gear.update_data(distance_m, gear_int)
        self.ana_rpm.update_data(distance_m, rpm)
        self.ana_steer.update_data(distance_m, steer_deg)

        n = len(self.current_lap_data.get('time_ms', []))
        dists = [(self.current_lap_data['time_ms'][i] / lap_dur_ms) * _track_length_m
                 for i in range(n)] if n else []
        deltas = [0.1 * math.sin(d / 500) for d in dists] if dists else []
        self.time_delta_graph.update_data(dists, deltas, distance_m)

        # ── Store raw lap data ───────────────────────────────────────────
        self.current_lap_data['time_ms'].append(current_time)
        self.current_lap_data['speed'].append(data['speed'])
        self.current_lap_data['throttle'].append(data['throttle'])
        self.current_lap_data['brake'].append(data['brake'])
        self.current_lap_data['steer_deg'].append(steer_deg)
        self.current_lap_data['rpm'].append(rpm)
        self.current_lap_data['gear'].append(gear_int)
        self.current_lap_data['abs'].append(data['abs'])
        self.current_lap_data['tc'].append(data['tc'])

    # ------------------------------------------------------------------
    # GRAPH RESET
    # ------------------------------------------------------------------

    def _reset_graphs(self):
        self.speed_graph.clear()
        self.pedals_graph.clear()
        self.steering_graph.clear()
        self.rpm_graph.clear()
        self.gear_graph.clear()
        self.aids_graph.clear()

    def _reset_analysis_graphs(self):
        self.ana_speed.clear()
        self.ana_throttle_brake.clear()
        self.ana_gear.clear()
        self.ana_rpm.clear()
        self.ana_steer.clear()
        self.time_delta_graph.clear()

    # ------------------------------------------------------------------
    # EXPORT
    # ------------------------------------------------------------------

    def _get_last_lap_data(self):
        if self.session_laps:
            return self.session_laps[-1]['data']
        return self.current_lap_data

    def _get_session_data(self):
        combined = {k: [] for k in self.current_lap_data}
        for lap in self.session_laps:
            for key in combined:
                combined[key].extend(lap['data'].get(key, []))
        for key in combined:
            combined[key].extend(self.current_lap_data.get(key, []))
        return combined

    def _export_graphs(self, data_dict: dict, dialog_title: str, default_filename: str):
        if not data_dict.get('speed'):
            QMessageBox.information(self, 'Export', 'No telemetry data available to export yet.')
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, dialog_title, default_filename, 'PNG Image (*.png);;All Files (*)')
        if not file_path:
            return

        time_ms = data_dict.get('time_ms', [])
        if time_ms:
            start = time_ms[0]
            x_values = [(t - start) / 1000.0 for t in time_ms]
            x_label = 'Time (s)'
        else:
            x_values = list(range(len(data_dict['speed'])))
            x_label = 'Samples'

        export_fig = Figure(figsize=(12, 9), facecolor=BG)
        axs = export_fig.subplots(3, 2, sharex=True)
        axs = axs.flatten()

        def style_export_ax(ax, title):
            ax.set_facecolor(BG1)
            ax.set_title(title, color=TXT2, fontsize=10, pad=4)
            ax.tick_params(colors=TXT2, labelsize=7)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#303030')
            ax.spines['bottom'].set_color('#303030')
            ax.grid(True, color='#1c1c1c', linewidth=0.8, linestyle='-', axis='y')

        style_export_ax(axs[0], 'Speed')
        axs[0].plot(x_values, data_dict['speed'], color=C_SPEED, linewidth=1.0)
        axs[0].set_ylabel('km/h', color=TXT2, fontsize=8)

        style_export_ax(axs[1], 'Throttle & Brake')
        axs[1].plot(x_values, data_dict['throttle'], color=C_THROTTLE, linewidth=1.0, label='Throttle')
        axs[1].plot(x_values, data_dict['brake'], color=C_BRAKE, linewidth=1.0, label='Brake')
        axs[1].set_ylabel('%', color=TXT2, fontsize=8)
        axs[1].legend(loc='upper right', fontsize=7, framealpha=0, labelcolor=TXT2)

        style_export_ax(axs[2], 'Steering Angle')
        axs[2].plot(x_values, data_dict['steer_deg'], color=C_STEER, linewidth=1.0)
        axs[2].set_ylabel('°', color=TXT2, fontsize=8)

        style_export_ax(axs[3], 'RPM')
        axs[3].plot(x_values, data_dict['rpm'], color=C_RPM, linewidth=1.0)
        axs[3].set_ylabel('rpm', color=TXT2, fontsize=8)

        style_export_ax(axs[4], 'Gear')
        axs[4].step(x_values, data_dict['gear'], color=C_GEAR, linewidth=1.0, where='post')
        axs[4].set_ylabel('gear', color=TXT2, fontsize=8)

        style_export_ax(axs[5], 'ABS & TC Activity')
        axs[5].plot(x_values, data_dict['abs'], color=C_ABS, linewidth=1.0, label='ABS')
        axs[5].plot(x_values, data_dict['tc'], color=C_TC, linewidth=1.0, label='TC')
        axs[5].set_ylabel('activity', color=TXT2, fontsize=8)
        axs[5].legend(loc='upper right', fontsize=7, framealpha=0, labelcolor=TXT2)

        for ax in axs[4:]:
            ax.set_xlabel(x_label, color=TXT2, fontsize=8)

        export_fig.tight_layout(pad=0.5)
        export_fig.savefig(file_path, dpi=150, facecolor=BG)
        QMessageBox.information(self, 'Export', f'Graphs saved to:\n{file_path}')

    def export_last_lap_graphs(self):
        self._export_graphs(self._get_last_lap_data(), 'Save Last Lap Graphs', 'last_lap.png')

    def export_session_graphs(self):
        self._export_graphs(self._get_session_data(), 'Save Full Session Graphs', 'session.png')

    # ------------------------------------------------------------------
    # DISPLAY RESET
    # ------------------------------------------------------------------

    def _reset_display(self):
        self.speed_value.set_value('0')
        self.gear_value.set_value('N')
        self.rev_bar.set_value(0, 8000)
        self.rpm_numbers.setText('0 / 8000')
        self.throttle_bar.set_value(0)
        self.brake_bar.set_value(0)
        self.steering_widget.set_angle(0)
        self.abs_badge.set_active(False)
        self.tc_badge.set_active(False)
        self.car_label.setText('—')
        self.track_label.setText('—')
        self.fuel_display.set_value('—')
        self.position_display.set_value('—')
        self.laptime_display.set_value('—')
        self._reset_analysis_graphs()
        self.track_map.reset()


# ---------------------------------------------------------------------------
# SMALL HELPER WIDGETS
# ---------------------------------------------------------------------------

class _AidBadge(QWidget):
    """Rectangular indicator badge for ABS / TC status."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        self.dot = QLabel('●')
        self.dot.setFont(sans(9))
        layout.addWidget(self.dot)

        self.name_lbl = QLabel(name)
        self.name_lbl.setFont(mono(9, bold=True))
        layout.addWidget(self.name_lbl)

        self.val_lbl = QLabel('OFF')
        self.val_lbl.setFont(mono(9))
        layout.addWidget(self.val_lbl)
        layout.addStretch()

        self.setStyleSheet(
            f'background: {BG3}; border: 1px solid {BORDER}; border-radius: 3px;'
        )
        self._set_inactive()

    def _set_inactive(self):
        self.dot.setStyleSheet(f'color: {TXT2};')
        self.name_lbl.setStyleSheet(f'color: {TXT2};')
        self.val_lbl.setText('OFF')
        self.val_lbl.setStyleSheet(f'color: {TXT2};')
        self.setStyleSheet(f'background: {BG3}; border: 1px solid {BORDER}; border-radius: 3px;')

    def _set_active_style(self, val_text: str):
        self.dot.setStyleSheet(f'color: {C_RPM};')
        self.name_lbl.setStyleSheet(f'color: {C_RPM};')
        self.val_lbl.setText(val_text)
        self.val_lbl.setStyleSheet(f'color: {C_RPM};')
        self.setStyleSheet(
            f'background: #2a1e00; border: 1px solid #4a3200; border-radius: 3px;'
        )

    def set_active(self, active: bool, val_text: str = ''):
        if active:
            self._set_active_style(val_text)
        else:
            self._set_inactive()


def _vsep() -> QFrame:
    """Vertical separator line for the header strip."""
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setStyleSheet(f'color: {BORDER2}; background: {BORDER2};')
    sep.setFixedWidth(1)
    return sep


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    window = TelemetryApp()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
