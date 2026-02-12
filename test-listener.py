import sys
import os
import socket
import struct
from collections import deque
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QProgressBar, QComboBox, QPushButton,
                             QLineEdit, QFormLayout, QGroupBox, QTabWidget, QFileDialog,
                             QMessageBox, QSplitter, QScrollArea, QFrame, QGridLayout)
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal, QRectF
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush, QPixmap
from abc import ABC, abstractmethod
import threading
import math
import time
import random

# Install these libraries:
# pip install PyQt6 pyaccsharedmemory matplotlib
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class TelemetryReader(ABC):
    """Abstract base class for telemetry readers"""
    
    @abstractmethod
    def read(self):
        """Read telemetry data from the game"""
        pass
    
    @abstractmethod
    def is_connected(self):
        """Check if game is running and accessible"""
        pass


class ACUDPReader(TelemetryReader):
    """Assetto Corsa telemetry reader using UDP"""
    
    def __init__(self, host='127.0.0.1', port=9996):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self.latest_data = None
        self.handshake_sent = False
        self.running = False
        self.listener_thread = None
        
        # Simple simulated lap state so the UDP-based
        # simulator can drive lap timing similar to Monza.
        self.sim_lap_start_ms = None
        self.sim_target_lap_ms = None
        self.sim_lap_count = 0
        self.sim_last_lap_ms = 0
        
    def connect(self):
        """Connect to AC UDP server"""
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
        """Listen for UDP packets in background thread"""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(2048)
                if data and len(data) > 4:
                    packet_id = struct.unpack('<i', data[0:4])[0]
                    
                    if packet_id == 2:
                        self.latest_data = self._parse_car_info(data)
                        
            except socket.timeout:
                continue
            except Exception as e:
                break
    
    def _parse_car_info(self, data):
        """Parse RT_CAR_INFO UDP packet"""
        try:
            offset = 4
            
            speed_kmh = struct.unpack('<f', data[offset:offset+4])[0]
            offset += 8
            
            rpm = struct.unpack('<f', data[offset+16:offset+20])[0]
            gear = struct.unpack('<i', data[offset+20:offset+24])[0]
            
            # Optional: throttle, brake, steer_angle, abs, tc (if packet is long enough)
            throttle = brake = steer_angle = abs_val = tc_val = 0.0
            if len(data) >= 56:
                throttle = struct.unpack('<f', data[36:40])[0]
                brake = struct.unpack('<f', data[40:44])[0]
                steer_angle = struct.unpack('<f', data[44:48])[0]
                abs_val = struct.unpack('<f', data[48:52])[0]
                tc_val = struct.unpack('<f', data[52:56])[0]
            
            # --- Simulated Monza-style lap timing for UDP source ---
            # We don't get lap info over this simplified UDP format,
            # so we synthesize it here to:
            #   - have laps between 1:21 and 1:39 (Monza-ish)
            #   - drive the per-lap graph reset & exports
            now_ms = int(time.time() * 1000)
            if self.sim_lap_start_ms is None:
                self.sim_lap_start_ms = now_ms
                # Random lap length between 81s and 99s
                self.sim_target_lap_ms = random.randint(81000, 99000)
            
            elapsed_ms = now_ms - self.sim_lap_start_ms
            
            if elapsed_ms >= self.sim_target_lap_ms:
                # Lap finished
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
                # Last completed lap time in seconds
                'lap_time': self.sim_last_lap_ms / 1000.0 if self.sim_lap_count > 0 else 0,
                'position': 0,
                'car_name': 'Simulated Car',
                'track_name': 'Monza (Simulated)',
                # Completed laps so far
                'lap_count': self.sim_lap_count,
                # Current lap elapsed time in ms (used for x-axis / lap reset)
                'current_time': elapsed_ms
            }
        except Exception as e:
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
    """Assetto Corsa Competizione telemetry reader using shared memory"""
    
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
                'current_time': sm.Graphics.current_time
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
        except Exception as e:
            return False


class TelemetryGraph(FigureCanvas):
    """Custom matplotlib graph widget for a single value over time.
    
    - X-axis always uses full width of the canvas.
    - Title is not drawn on the axes; it is shown via a Qt label above the graph.
    """
    
    def __init__(self, title, ylabel, max_points=None, ylim=(0, 100)):
        self.fig = Figure(figsize=(8, 2), facecolor='#2b2b2b')
        super().__init__(self.fig)
        
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        
        self.ax.set_ylabel(ylabel, color='white', fontsize=8)
        self.ax.tick_params(colors='white', labelsize=7)
        self.ax.grid(True, alpha=0.2, color='gray')
        self.ax.set_ylim(ylim)
        
        self.max_points = max_points  # None means unlimited (per lap)
        self.data = []
        self.line, = self.ax.plot([], [], 'cyan', linewidth=1.5)
        
        # No matplotlib title here; handled by external Qt label
        self.fig.tight_layout()
    
    def update_data(self, value):
        """Add new data point and redraw."""
        if self.max_points is not None and len(self.data) >= self.max_points:
            # Keep only the most recent points if a limit is set
            self.data.pop(0)
        self.data.append(value)
        
        x = range(len(self.data))
        self.line.set_data(x, self.data)
        
        # Always use full width for current data
        self.ax.set_xlim(0, max(1, len(self.data)))
        
        self.draw_idle()
    
    def clear(self):
        """Clear all data."""
        self.data.clear()
        self.line.set_data([], [])
        self.ax.set_xlim(0, 1)
        self.draw_idle()


class MultiLineGraph(FigureCanvas):
    """Graph widget for multiple lines (throttle/brake or ABS/TC).
    
    - X-axis always uses full width of the canvas.
    - Title is not drawn on the axes; it is shown via a Qt label above the graph.
    """
    
    def __init__(self, title, ylabel, max_points=None, line1_label='Line 1', line2_label='Line 2',
                 line1_color='lime', line2_color='red'):
        self.fig = Figure(figsize=(8, 2), facecolor='#2b2b2b')
        super().__init__(self.fig)
        
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        
        self.ax.set_ylabel(ylabel, color='white', fontsize=8)
        self.ax.tick_params(colors='white', labelsize=7)
        self.ax.grid(True, alpha=0.2, color='gray')
        self.ax.set_ylim(0, 100)
        
        self.max_points = max_points  # None means unlimited (per lap)
        self.line1_data = []
        self.line2_data = []
        
        self.line1, = self.ax.plot([], [], line1_color, linewidth=1.5, label=line1_label)
        self.line2, = self.ax.plot([], [], line2_color, linewidth=1.5, label=line2_label)
        self.ax.legend(loc='upper right', fontsize=7, facecolor='#2b2b2b', edgecolor='white')
        
        # No matplotlib title here; handled by external Qt label
        self.fig.tight_layout()
    
    def update_data(self, value1, value2):
        """Add new data points."""
        if self.max_points is not None and len(self.line1_data) >= self.max_points:
            # Keep only the most recent points if a limit is set
            self.line1_data.pop(0)
            self.line2_data.pop(0)
        
        self.line1_data.append(value1)
        self.line2_data.append(value2)
        
        x = range(len(self.line1_data))
        self.line1.set_data(x, self.line1_data)
        self.line2.set_data(x, self.line2_data)
        
        # Always use full width for current data
        self.ax.set_xlim(0, max(1, len(self.line1_data)))
        
        self.draw_idle()
    
    def clear(self):
        """Clear all data."""
        self.line1_data.clear()
        self.line2_data.clear()
        self.line1.set_data([], [])
        self.line2.set_data([], [])
        self.ax.set_xlim(0, 1)
        self.draw_idle()


class SteeringWidget(QWidget):
    """Custom widget to display steering wheel rotation"""
    
    def __init__(self):
        super().__init__()
        self.angle = 0  # Current steering angle
        self.setMinimumSize(200, 200)
    
    def set_angle(self, angle):
        """Set steering angle in radians"""
        self.angle = angle
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Calculate center
        center_x = self.width() // 2
        center_y = self.height() // 2
        radius = min(center_x, center_y) - 10
        
        # Draw outer circle (steering wheel)
        painter.setPen(QPen(QColor(100, 100, 100), 3))
        painter.setBrush(QColor(50, 50, 50))
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
        
        # Draw center dot
        painter.setBrush(QColor(200, 200, 200))
        painter.drawEllipse(center_x - 5, center_y - 5, 10, 10)
        
        # Draw steering indicator line
        angle_deg = math.degrees(self.angle)
        line_length = radius - 10
        end_x = center_x + line_length * math.sin(self.angle)
        end_y = center_y - line_length * math.cos(self.angle)
        
        # Color based on angle
        if abs(angle_deg) < 90:
            color = QColor(0, 255, 0)  # Green
        elif abs(angle_deg) < 180:
            color = QColor(255, 255, 0)  # Yellow
        else:
            color = QColor(255, 0, 0)  # Red
        
        painter.setPen(QPen(color, 4))
        painter.drawLine(center_x, center_y, int(end_x), int(end_y))
        
        # Draw angle text
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                        f"{angle_deg:.1f}°")


# --- Monza approximate centerline (pixel coords for 900x675 image) for car position ---
def _monza_centerline_points():
    """Rough centerline path for Monza_900x.png (900x675). progress 0..1 maps along the track."""
    points = []
    n = 120
    for i in range(n + 1):
        t = i / n
        ang = t * 2 * math.pi
        x = 450 + 380 * math.cos(ang)
        y = 337 + 280 * math.sin(ang)  # center 337 for 675 height
        points.append((x, y))
    return points


MONZA_CENTERLINE = _monza_centerline_points()
MONZA_LENGTH_M = 5793  # Actual Monza GP length


class TrackMapWidget(QWidget):
    """Displays track map with live car position dot."""

    def __init__(self, track_image_path=None):
        super().__init__()
        self.setMinimumSize(400, 300)
        self.car_progress = 0.0  # 0..1 along lap
        self._pixmap = None
        self._track_path = track_image_path
        if track_image_path and os.path.isfile(track_image_path):
            self._pixmap = QPixmap(track_image_path)

    def set_track(self, path):
        if path and os.path.isfile(path):
            self._track_path = path
            self._pixmap = QPixmap(path)
            self.update()

    def set_car_progress(self, progress):
        """progress: 0..1 along lap (from distance or time)"""
        self.car_progress = max(0, min(1, progress))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            img_w, img_h = scaled.width(), scaled.height()
            ox = (rect.width() - img_w) // 2
            oy = (rect.height() - img_h) // 2
            painter.drawPixmap(ox, oy, scaled)
            # Map track coords (900x675) to drawn pixmap
            scale_x = img_w / 900.0
            scale_y = img_h / 675.0
            scale = min(scale_x, scale_y)
            idx = int(self.car_progress * (len(MONZA_CENTERLINE) - 1))
            cx, cy = MONZA_CENTERLINE[idx]
            px = ox + cx * scale_x
            py = oy + cy * scale_y
        else:
            painter.fillRect(rect, QColor(30, 30, 30))
            px = rect.center().x() + 100 * math.cos(self.car_progress * 2 * math.pi)
            py = rect.center().y() + 80 * math.sin(self.car_progress * 2 * math.pi)
        painter.setPen(QPen(QColor(255, 50, 50), 2))
        painter.setBrush(QBrush(QColor(255, 50, 50)))
        r = 8
        painter.drawEllipse(int(px - r), int(py - r), r * 2, r * 2)


class SectorTimesPanel(QWidget):
    """Left panel: lap times and sector gaps."""

    def __init__(self):
        super().__init__()
        self.setMaximumWidth(220)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.lap1_label = QLabel("01:41.475")
        self.lap1_label.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 14px;")
        self.lap2_label = QLabel("01:41.510 (+0.035s)")
        self.lap2_label.setStyleSheet("color: #3498db; font-weight: bold; font-size: 14px;")
        lap_box = QFrame()
        lap_box.setStyleSheet("QFrame { background-color: #2b2b2b; border-radius: 4px; padding: 8px; }")
        lap_layout = QVBoxLayout(lap_box)
        lap_layout.addWidget(QLabel("Laps:", styleSheet="color: #888; font-size: 11px;"))
        lap_layout.addWidget(self.lap1_label)
        lap_layout.addWidget(self.lap2_label)
        layout.addWidget(lap_box)
        gaps_header = QLabel("Gaps:")
        gaps_header.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(gaps_header)
        self.gap_labels = {}
        self.sector_time_labels = {}
        grid = QGridLayout()
        grid.addWidget(QLabel("Sector", styleSheet="color: #888;"), 0, 0)
        grid.addWidget(QLabel("Lap 1", styleSheet="color: #888;"), 0, 1)
        grid.addWidget(QLabel("Δ Lap 2", styleSheet="color: #888;"), 0, 2)
        for row, s in enumerate(["S1", "S2", "S3", "S4", "S5"], 1):
            grid.addWidget(QLabel(s), row, 0)
            st_lbl = QLabel("20.706")
            self.sector_time_labels[s] = st_lbl
            grid.addWidget(st_lbl, row, 1)
            gap_lbl = QLabel("--")
            self.gap_labels[s] = gap_lbl
            grid.addWidget(gap_lbl, row, 2)
        gaps_frame = QFrame()
        gaps_frame.setStyleSheet("QFrame { background-color: #2b2b2b; border-radius: 4px; padding: 8px; }")
        gaps_frame.setLayout(grid)
        layout.addWidget(gaps_frame)
        layout.addStretch()

    def update_laps(self, lap1_time_s, lap2_time_s, gap_s):
        """Update displayed lap times and gap."""
        def fmt(t):
            m = int(t // 60)
            s = t % 60
            return f"{m:02d}:{s:06.3f}"
        self.lap1_label.setText(fmt(lap1_time_s))
        sign = "+" if gap_s >= 0 else ""
        self.lap2_label.setText(f"{fmt(lap2_time_s)} ({sign}{gap_s:.3f}s)")
        self._update_gaps(lap1_time_s, lap2_time_s)

    def _update_gaps(self, t1, t2):
        """Simulate sector gaps (5 equal sectors)."""
        base = t1 / 5
        times = [base * 1.1, base * 0.9, base * 1.0, base * 1.05, base * 0.95]
        d1, d2, d3, d4, d5 = (t2 - t1) * 0.2, (t2 - t1) * 0.4, (t2 - t1) * -0.3, (t2 - t1) * 0.2, (t2 - t1) * -0.2
        for s, sec_t, delta in [("S1", times[0], d1), ("S2", times[1], d2), ("S3", times[2], d3), ("S4", times[3], d4), ("S5", times[4], d5)]:
            self.sector_time_labels[s].setText(f"{sec_t:.3f}")
            lbl = self.gap_labels[s]
            sign = "+" if delta >= 0 else ""
            lbl.setText(f"{sign}{delta:.3f}s")
            lbl.setStyleSheet("background-color: #e74c3c22; color: #e74c3c;" if delta > 0 else "background-color: #27ae6022; color: #27ae60;")


class TimeDeltaGraph(FigureCanvas):
    """Bottom panel: time delta vs distance (gap to reference lap)."""

    def __init__(self):
        self.fig = Figure(figsize=(10, 1.8), facecolor='#2b2b2b')
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        self.ax.set_ylabel("Time delta", color='white', fontsize=9)
        self.ax.tick_params(colors='white', labelsize=7)
        self.ax.axhline(0, color='#e74c3c', linewidth=1)
        self.ax.grid(True, alpha=0.2, color='gray')
        self.distances = []
        self.deltas = []
        self.current_dist = 0
        self.line, = self.ax.plot([], [], '#3498db', linewidth=1.5)
        self.vline = self.ax.axvline(0, color='white', linewidth=1, alpha=0.7)
        self.fig.tight_layout()

    def update_data(self, distances, deltas, current_distance_m):
        self.distances = list(distances) if distances else []
        self.deltas = list(deltas) if deltas else []
        self.current_dist = current_distance_m
        if self.distances and self.deltas:
            self.line.set_data(self.distances, self.deltas)
            self.ax.set_xlim(0, max(MONZA_LENGTH_M, max(self.distances)))
            mn = min(-0.2, min(self.deltas) - 0.02)
            mx = max(0.2, max(self.deltas) + 0.02)
            self.ax.set_ylim(mn, mx)
        self.vline.set_xdata([current_distance_m])
        self.draw_idle()

    def clear(self):
        self.distances = []
        self.deltas = []
        self.line.set_data([], [])
        self.vline.set_xdata([0])
        self.ax.set_xlim(0, MONZA_LENGTH_M)
        self.ax.set_ylim(-0.2, 0.2)
        self.draw_idle()


class AnalysisTelemetryGraph(FigureCanvas):
    """Telemetry graph with distance (m) on X-axis for lap analysis."""

    def __init__(self, ylabel, ylim=(0, 100)):
        self.fig = Figure(figsize=(4, 1.2), facecolor='#2b2b2b')
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        self.ax.set_ylabel(ylabel, color='white', fontsize=8)
        self.ax.tick_params(colors='white', labelsize=6)
        self.ax.set_ylim(ylim)
        self.ax.grid(True, alpha=0.2, color='gray')
        self.distances = []
        self.values = []
        self.line, = self.ax.plot([], [], 'cyan', linewidth=1.2)
        self.vline = self.ax.axvline(0, color='white', linewidth=0.8, alpha=0.6)
        self.fig.tight_layout()

    def update_data(self, distance_m, value):
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
    """Multi-line graph with distance on X-axis."""

    def __init__(self, ylabel, line1_label, line2_label, line1_color='lime', line2_color='red'):
        self.fig = Figure(figsize=(4, 1.2), facecolor='#2b2b2b')
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        self.ax.set_ylabel(ylabel, color='white', fontsize=8)
        self.ax.tick_params(colors='white', labelsize=6)
        self.ax.set_ylim(0, 100)
        self.ax.grid(True, alpha=0.2, color='gray')
        self.distances = []
        self.v1, self.v2 = [], []
        self.line1, = self.ax.plot([], [], line1_color, linewidth=1.2, label=line1_label)
        self.line2, = self.ax.plot([], [], line2_color, linewidth=1.2, label=line2_label)
        self.ax.legend(loc='upper right', fontsize=6)
        self.vline = self.ax.axvline(0, color='white', linewidth=0.8, alpha=0.6)
        self.fig.tight_layout()

    def update_data(self, distance_m, val1, val2):
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


class TelemetryApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AC/ACC Telemetry Dashboard")
        self.setGeometry(100, 100, 1600, 950)
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #1a1a1a; }
            QGroupBox { color: #ccc; border: 1px solid #444; border-radius: 4px; margin-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QTabWidget::pane { border: 1px solid #333; background: #1e1e1e; }
            QTabBar::tab { background: #2b2b2b; color: #ccc; padding: 8px 16px; }
            QTabBar::tab:selected { background: #333; color: white; }
            QLabel { color: #ddd; }
            QComboBox, QLineEdit { background: #2b2b2b; color: white; border: 1px solid #555; padding: 4px; }
            QPushButton { background: #333; color: white; border: 1px solid #555; padding: 6px 12px; }
            QPushButton:hover { background: #444; }
            QScrollArea { border: none; background: transparent; }
        """)
        
        # Initialize readers
        self.ac_reader = None
        self.acc_reader = ACCReader()
        self.current_reader = None
        self.auto_detect = True
        
        # Track lap changes for graph reset
        self.last_lap_time = 0
        self.current_lap_count = 0
        
        # Store time-series data per lap and for the whole session
        self.session_laps = []  # List[{'lap_number': int, 'data': dict}]
        self.current_lap_start_time = 0
        self._reset_current_lap_data()
        
        # Setup UI
        self.init_ui()
        
        # Setup update timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_telemetry)
        self.timer.start(50)  # Update every 50ms (20Hz)
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Connection settings
        settings_group = QGroupBox("Connection Settings")
        settings_layout = QFormLayout()
        
        game_layout = QHBoxLayout()
        self.game_combo = QComboBox()
        self.game_combo.addItems(['Auto-Detect', 'AC (UDP)', 'ACC (Shared Memory)'])
        self.game_combo.currentTextChanged.connect(self.on_game_changed)
        game_layout.addWidget(self.game_combo)
        
        self.connection_label = QLabel("⚫ Disconnected")
        self.connection_label.setStyleSheet("color: red;")
        game_layout.addWidget(self.connection_label)
        settings_layout.addRow("Game:", game_layout)
        
        udp_layout = QHBoxLayout()
        self.udp_host = QLineEdit("127.0.0.1")
        self.udp_host.setMaximumWidth(150)
        self.udp_port = QLineEdit("9996")
        self.udp_port.setMaximumWidth(80)
        udp_layout.addWidget(QLabel("Host:"))
        udp_layout.addWidget(self.udp_host)
        udp_layout.addWidget(QLabel("Port:"))
        udp_layout.addWidget(self.udp_port)
        udp_layout.addStretch()
        settings_layout.addRow("AC UDP:", udp_layout)
        
        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)
        
        # Create tabs
        tabs = QTabWidget()
        
        # Dashboard Tab
        dashboard_tab = QWidget()
        dashboard_layout = QVBoxLayout(dashboard_tab)
        
        # Car and Track info
        info_layout = QHBoxLayout()
        self.car_label = QLabel("Car: --")
        self.track_label = QLabel("Track: --")
        info_layout.addWidget(self.car_label)
        info_layout.addWidget(self.track_label)
        dashboard_layout.addLayout(info_layout)
        
        # Main display row (Speed, Gear, Steering)
        main_display = QHBoxLayout()
        
        # Speed
        speed_layout = QVBoxLayout()
        self.speed_label = QLabel("0")
        self.speed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        speed_font = QFont()
        speed_font.setPointSize(48)
        speed_font.setBold(True)
        self.speed_label.setFont(speed_font)
        speed_layout.addWidget(self.speed_label)
        speed_layout.addWidget(QLabel("km/h", alignment=Qt.AlignmentFlag.AlignCenter))
        main_display.addLayout(speed_layout)
        
        # Gear
        gear_layout = QVBoxLayout()
        self.gear_label = QLabel("N")
        self.gear_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gear_font = QFont()
        gear_font.setPointSize(36)
        gear_font.setBold(True)
        self.gear_label.setFont(gear_font)
        gear_layout.addWidget(self.gear_label)
        gear_layout.addWidget(QLabel("Gear", alignment=Qt.AlignmentFlag.AlignCenter))
        main_display.addLayout(gear_layout)
        
        # Steering wheel
        steering_layout = QVBoxLayout()
        self.steering_widget = SteeringWidget()
        steering_layout.addWidget(self.steering_widget)
        steering_layout.addWidget(QLabel("Steering", alignment=Qt.AlignmentFlag.AlignCenter))
        main_display.addLayout(steering_layout)
        
        dashboard_layout.addLayout(main_display)
        
        # RPM bar
        rpm_layout = QVBoxLayout()
        rpm_layout.addWidget(QLabel("RPM"))
        self.rpm_bar = QProgressBar()
        self.rpm_bar.setTextVisible(True)
        self.rpm_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid grey;
                border-radius: 5px;
                text-align: center;
                height: 30px;
            }
            QProgressBar::chunk {
                background-color: #05B8CC;
            }
        """)
        rpm_layout.addWidget(self.rpm_bar)
        dashboard_layout.addLayout(rpm_layout)
        
        # Pedals
        pedals_layout = QHBoxLayout()
        
        throttle_layout = QVBoxLayout()
        throttle_layout.addWidget(QLabel("Throttle"))
        self.throttle_bar = QProgressBar()
        self.throttle_bar.setOrientation(Qt.Orientation.Vertical)
        self.throttle_bar.setMinimumHeight(150)
        self.throttle_bar.setStyleSheet("QProgressBar::chunk { background-color: green; }")
        throttle_layout.addWidget(self.throttle_bar)
        pedals_layout.addLayout(throttle_layout)
        
        brake_layout = QVBoxLayout()
        brake_layout.addWidget(QLabel("Brake"))
        self.brake_bar = QProgressBar()
        self.brake_bar.setOrientation(Qt.Orientation.Vertical)
        self.brake_bar.setMinimumHeight(150)
        self.brake_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        brake_layout.addWidget(self.brake_bar)
        pedals_layout.addLayout(brake_layout)
        
        # ABS/TC indicators
        aids_layout = QVBoxLayout()
        aids_layout.addWidget(QLabel("Driver Aids"))
        self.abs_label = QLabel("ABS: OFF")
        self.abs_label.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        self.tc_label = QLabel("TC: OFF")
        self.tc_label.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        aids_layout.addWidget(self.abs_label)
        aids_layout.addWidget(self.tc_label)
        aids_layout.addStretch()
        pedals_layout.addLayout(aids_layout)
        
        pedals_layout.addStretch()
        dashboard_layout.addLayout(pedals_layout)
        
        # Bottom info
        bottom_layout = QHBoxLayout()
        self.fuel_label = QLabel("Fuel: --")
        self.position_label = QLabel("Position: --")
        self.lap_time_label = QLabel("Last Lap: --")
        bottom_layout.addWidget(self.fuel_label)
        bottom_layout.addWidget(self.position_label)
        bottom_layout.addWidget(self.lap_time_label)
        dashboard_layout.addLayout(bottom_layout)
        
        tabs.addTab(dashboard_tab, "Dashboard")
        
        # Graphs Tab
        graphs_tab = QWidget()
        graphs_layout = QVBoxLayout(graphs_tab)
        
        # Export controls
        graphs_controls_layout = QHBoxLayout()
        self.export_last_lap_button = QPushButton("Save Last Lap Graphs")
        self.export_last_lap_button.clicked.connect(self.export_last_lap_graphs)
        graphs_controls_layout.addWidget(self.export_last_lap_button)
        
        self.export_session_button = QPushButton("Save Full Session Graphs")
        self.export_session_button.clicked.connect(self.export_session_graphs)
        graphs_controls_layout.addWidget(self.export_session_button)
        graphs_controls_layout.addStretch()
        
        graphs_layout.addLayout(graphs_controls_layout)
        
        # Speed graph
        self.speed_graph_title = QLabel()
        self.speed_graph_title.setStyleSheet("color: white; font-weight: bold;")
        graphs_layout.addWidget(self.speed_graph_title)
        self.speed_graph = TelemetryGraph("Speed", "km/h", ylim=(0, 300))
        graphs_layout.addWidget(self.speed_graph)
        
        # Throttle/Brake graph
        self.pedals_graph_title = QLabel()
        self.pedals_graph_title.setStyleSheet("color: white; font-weight: bold;")
        graphs_layout.addWidget(self.pedals_graph_title)
        self.pedals_graph = MultiLineGraph("Throttle & Brake", "%", 
                                          line1_label='Throttle', line2_label='Brake',
                                          line1_color='lime', line2_color='red')
        graphs_layout.addWidget(self.pedals_graph)
        
        # Steering graph
        self.steering_graph_title = QLabel()
        self.steering_graph_title.setStyleSheet("color: white; font-weight: bold;")
        graphs_layout.addWidget(self.steering_graph_title)
        self.steering_graph = TelemetryGraph("Steering Angle", "degrees", ylim=(-540, 540))
        graphs_layout.addWidget(self.steering_graph)
        
        # RPM graph
        self.rpm_graph_title = QLabel()
        self.rpm_graph_title.setStyleSheet("color: white; font-weight: bold;")
        graphs_layout.addWidget(self.rpm_graph_title)
        self.rpm_graph = TelemetryGraph("RPM", "RPM", ylim=(0, 10000))
        graphs_layout.addWidget(self.rpm_graph)
        
        # Gear graph
        self.gear_graph_title = QLabel()
        self.gear_graph_title.setStyleSheet("color: white; font-weight: bold;")
        graphs_layout.addWidget(self.gear_graph_title)
        self.gear_graph = TelemetryGraph("Gear", "Gear", ylim=(-1, 8))
        graphs_layout.addWidget(self.gear_graph)
        
        # ABS/TC graph
        self.aids_graph_title = QLabel()
        self.aids_graph_title.setStyleSheet("color: white; font-weight: bold;")
        graphs_layout.addWidget(self.aids_graph_title)
        self.aids_graph = MultiLineGraph("ABS & TC Activity", "Intensity",
                                        line1_label='ABS', line2_label='TC',
                                        line1_color='orange', line2_color='yellow')
        graphs_layout.addWidget(self.aids_graph)
        
        # Initial titles (will be updated as laps change)
        self._set_graph_title_suffix("Lap 1")
        
        tabs.addTab(graphs_tab, "Telemetry Graphs")
        
        # --- Lap Analysis tab: sector times (left), track map (center), telemetry (right), time delta (bottom) ---
        analysis_tab = QWidget()
        analysis_main = QVBoxLayout(analysis_tab)
        
        # Resolve track image path
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        _monza_path = os.path.join(_script_dir, "tracks", "Monza_900x.png")
        
        # Left | Center | Right split
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.sector_panel = SectorTimesPanel()
        splitter.addWidget(self.sector_panel)
        
        self.track_map = TrackMapWidget(_monza_path)
        self.track_map.setMinimumWidth(450)
        splitter.addWidget(self.track_map)
        
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(4)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right_panel)
        right_scroll.setMinimumWidth(280)
        right_scroll.setMaximumWidth(320)
        
        self.ana_speed = AnalysisTelemetryGraph("Speed (km/h)", ylim=(0, 320))
        self.ana_throttle = AnalysisTelemetryGraph("Throttle (%)", ylim=(0, 100))
        self.ana_brake = AnalysisTelemetryGraph("Brake (%)", ylim=(0, 100))
        self.ana_gear = AnalysisTelemetryGraph("Gear", ylim=(-1, 8))
        self.ana_rpm = AnalysisTelemetryGraph("RPM", ylim=(0, 10000))
        self.ana_steer = AnalysisTelemetryGraph("Steer (°)", ylim=(-540, 540))
        
        for g in [self.ana_speed, self.ana_throttle, self.ana_brake, self.ana_gear, self.ana_rpm, self.ana_steer]:
            right_layout.addWidget(g)
        
        splitter.addWidget(right_scroll)
        splitter.setSizes([220, 500, 300])
        
        analysis_main.addWidget(splitter)
        
        # Bottom: time delta graph + sector markers
        analysis_main.addWidget(QLabel("Time delta", styleSheet="color: #888; font-size: 11px;"))
        self.time_delta_graph = TimeDeltaGraph()
        self.time_delta_graph.setMinimumHeight(120)
        analysis_main.addWidget(self.time_delta_graph)
        sector_bar = QHBoxLayout()
        for s in ["S1", "S2", "S3", "S4", "S5"]:
            sector_bar.addWidget(QLabel(s, styleSheet="background-color: #333; padding: 4px 12px; color: #888; font-size: 10px;"))
        analysis_main.addLayout(sector_bar)
        
        tabs.addTab(analysis_tab, "Lap Analysis")
        
        main_layout.addWidget(tabs)
    
    def _reset_current_lap_data(self):
        """Initialize/clear containers for the current lap's raw data."""
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
        """Store the just-finished lap into the session history."""
        # Only store if we actually have data points
        if self.current_lap_data.get('speed'):
            self.session_laps.append({
                'lap_number': self.current_lap_count,
                'data': {k: list(v) for k, v in self.current_lap_data.items()},
            })
    
    def _set_graph_title_suffix(self, suffix: str):
        """Apply a dynamic suffix (e.g. 'Lap 3') to all graph labels above the canvases."""
        self.speed_graph_title.setText(f"Speed - {suffix}")
        self.pedals_graph_title.setText(f"Throttle & Brake - {suffix}")
        self.steering_graph_title.setText(f"Steering Angle - {suffix}")
        self.rpm_graph_title.setText(f"RPM - {suffix}")
        self.gear_graph_title.setText(f"Gear - {suffix}")
        self.aids_graph_title.setText(f"ABS & TC Activity - {suffix}")
    
    def on_game_changed(self, game):
        if game == 'Auto-Detect':
            self.auto_detect = True
            self.current_reader = None
        elif game == 'AC (UDP)':
            self.auto_detect = False
            if self.ac_reader:
                self.ac_reader.disconnect()
            self.ac_reader = ACUDPReader(self.udp_host.text(), int(self.udp_port.text()))
            self.current_reader = self.ac_reader
        else:
            self.auto_detect = False
            self.current_reader = self.acc_reader
    
    def detect_game(self):
        """Auto-detect which game is running"""
        if self.acc_reader.is_connected():
            return self.acc_reader
        
        if not self.ac_reader:
            self.ac_reader = ACUDPReader(self.udp_host.text(), int(self.udp_port.text()))
        
        if self.ac_reader.is_connected():
            return self.ac_reader
            
        return None
    
    def update_telemetry(self):
        if self.auto_detect:
            self.current_reader = self.detect_game()
        
        if self.current_reader is None:
            self.connection_label.setText("⚫ Disconnected")
            self.connection_label.setStyleSheet("color: red;")
            self.reset_display()
            return
        
        data = self.current_reader.read()
        if data is None:
            self.connection_label.setText("⚫ Connection Lost")
            self.connection_label.setStyleSheet("color: orange;")
            return
        
        # Check for lap change or restart
        current_lap = data.get('lap_count', 0)
        current_time = data.get('current_time', 0)
        
        # Reset graphs if:
        # 1. New lap detected (lap count increased)
        # 2. Session restart (lap count reset to 0 from higher value)
        # 3. Current time is very low (< 5 seconds) - indicates restart
        lap_changed = (
            current_lap > self.current_lap_count
            or (current_lap == 0 and self.current_lap_count > 0)
            or (current_time < 5000 and self.last_lap_time > 5000)
        )
        
        if lap_changed:
            # Persist the data for the lap that just finished
            self._store_completed_lap()
            
            # Reset per-lap visuals and containers so each graph shows only one lap
            print(f"Resetting graphs - Lap: {current_lap}, Time: {current_time}")
            self.reset_graphs()
            self._reset_analysis_graphs()
            self._reset_current_lap_data()
            
            # Update dynamic titles to reflect the new lap number (1-based for display)
            display_lap = current_lap if current_lap > 0 else 1
            self._set_graph_title_suffix(f"Lap {display_lap}")
        
        self.current_lap_count = current_lap
        self.last_lap_time = current_time
        
        game_type = "AC" if isinstance(self.current_reader, ACUDPReader) else "ACC"
        self.connection_label.setText(f"🟢 Connected to {game_type}")
        self.connection_label.setStyleSheet("color: green;")
        
        # Update dashboard
        self.speed_label.setText(f"{int(data['speed'])}")
        
        gear = data['gear']
        if gear == 0:
            gear_text = "R"
        elif gear == 1:
            gear_text = "N"
        else:
            gear_text = str(gear - 1) if isinstance(self.current_reader, ACCReader) else str(gear)
        self.gear_label.setText(gear_text)
        
        rpm_percent = int((data['rpm'] / data['max_rpm']) * 100) if data['max_rpm'] > 0 else 0
        self.rpm_bar.setValue(rpm_percent)
        self.rpm_bar.setFormat(f"{int(data['rpm'])} / {int(data['max_rpm'])} RPM")
        
        self.throttle_bar.setValue(int(data['throttle']))
        self.brake_bar.setValue(int(data['brake']))
        
        self.steering_widget.set_angle(data['steer_angle'])
        
        # Update ABS/TC indicators
        if data['abs'] > 0:
            self.abs_label.setText(f"ABS: ON ({data['abs']:.1f})")
            self.abs_label.setStyleSheet("color: orange; font-weight: bold; font-size: 14px;")
        else:
            self.abs_label.setText("ABS: OFF")
            self.abs_label.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        
        if data['tc'] > 0:
            self.tc_label.setText(f"TC: ON ({data['tc']:.1f})")
            self.tc_label.setStyleSheet("color: orange; font-weight: bold; font-size: 14px;")
        else:
            self.tc_label.setText("TC: OFF")
            self.tc_label.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        
        self.car_label.setText(f"Car: {data['car_name']}")
        self.track_label.setText(f"Track: {data['track_name']}")
        
        fuel_percent = (data['fuel'] / data['max_fuel']) * 100 if data['max_fuel'] > 0 else 0
        self.fuel_label.setText(f"Fuel: {data['fuel']:.1f}L ({fuel_percent:.0f}%)")
        
        self.position_label.setText(f"Position: {data['position']}")
        if data['lap_time'] > 0:
            minutes = int(data['lap_time'] // 60)
            seconds = data['lap_time'] % 60
            self.lap_time_label.setText(f"Last Lap: {minutes}:{seconds:06.3f}")
        
        # Update graphs
        self.speed_graph.update_data(data['speed'])
        self.pedals_graph.update_data(data['throttle'], data['brake'])
        self.steering_graph.update_data(math.degrees(data['steer_angle']))
        self.rpm_graph.update_data(data['rpm'])
        self.gear_graph.update_data(gear if isinstance(gear, int) else 0)
        self.aids_graph.update_data(data['abs'], data['tc'])
        
        # Lap Analysis tab: distance from lap progress, track map, sector times, analysis graphs, time delta
        lap_dur_ms = 90000  # ~90s typical Monza lap
        lap_progress = min(1.0, current_time / lap_dur_ms) if lap_dur_ms > 0 else 0
        distance_m = lap_progress * MONZA_LENGTH_M
        self.track_map.set_car_progress(lap_progress)
        ref_lap_s = 101.475  # Simulated reference lap (01:41.475)
        gap_s = 0.035 + 0.02 * math.sin(time.time() * 0.5)  # Simulated gap
        self.sector_panel.update_laps(ref_lap_s, ref_lap_s + gap_s, gap_s)
        self.ana_speed.update_data(distance_m, data['speed'])
        self.ana_throttle.update_data(distance_m, data['throttle'])
        self.ana_brake.update_data(distance_m, data['brake'])
        self.ana_gear.update_data(distance_m, gear if isinstance(gear, int) else 0)
        self.ana_rpm.update_data(distance_m, data['rpm'])
        self.ana_steer.update_data(distance_m, math.degrees(data['steer_angle']))
        n = len(self.current_lap_data.get('time_ms', []))
        dists = [(self.current_lap_data['time_ms'][i] / lap_dur_ms) * MONZA_LENGTH_M for i in range(n)] if n else []
        deltas = [0.1 * math.sin(d / 500) for d in dists] if dists else []
        self.time_delta_graph.update_data(dists, deltas, distance_m)
        
        # Store raw data for current lap/session exports
        self.current_lap_data['time_ms'].append(current_time)
        self.current_lap_data['speed'].append(data['speed'])
        self.current_lap_data['throttle'].append(data['throttle'])
        self.current_lap_data['brake'].append(data['brake'])
        self.current_lap_data['steer_deg'].append(math.degrees(data['steer_angle']))
        self.current_lap_data['rpm'].append(data['rpm'])
        self.current_lap_data['gear'].append(gear if isinstance(gear, int) else 0)
        self.current_lap_data['abs'].append(data['abs'])
        self.current_lap_data['tc'].append(data['tc'])
    
    def reset_graphs(self):
        """Reset all telemetry graphs"""
        self.speed_graph.clear()
        self.pedals_graph.clear()
        self.steering_graph.clear()
        self.rpm_graph.clear()
        self.gear_graph.clear()
        self.aids_graph.clear()
    
    def _reset_analysis_graphs(self):
        """Reset Lap Analysis tab graphs and time delta."""
        self.ana_speed.clear()
        self.ana_throttle.clear()
        self.ana_brake.clear()
        self.ana_gear.clear()
        self.ana_rpm.clear()
        self.ana_steer.clear()
        self.time_delta_graph.clear()
    
    def _get_last_lap_data(self):
        """Return data for the most recently completed lap, or current lap if none completed yet."""
        if self.session_laps:
            return self.session_laps[-1]['data']
        return self.current_lap_data
    
    def _get_session_data(self):
        """Combine all laps (completed + current) into a single data dictionary."""
        combined = {k: [] for k in self.current_lap_data}
        
        for lap in self.session_laps:
            lap_data = lap['data']
            for key in combined:
                combined[key].extend(lap_data.get(key, []))
        
        for key in combined:
            combined[key].extend(self.current_lap_data.get(key, []))
        
        return combined
    
    def _export_graphs(self, data_dict, dialog_title: str, default_filename: str):
        """Create and save a multi-panel figure for the provided data."""
        # Need at least some speed data to bother exporting
        if not data_dict.get('speed'):
            QMessageBox.information(self, "Export Graphs", "No telemetry data available to export yet.")
            return
        
        # Ask user where to save
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            dialog_title,
            default_filename,
            "PNG Image (*.png);;All Files (*)",
        )
        if not file_path:
            return
        
        time_ms = data_dict.get('time_ms', [])
        if time_ms:
            start = time_ms[0]
            x_values = [(t - start) / 1000.0 for t in time_ms]
            x_label = "Time (s)"
        else:
            x_values = list(range(len(data_dict['speed'])))
            x_label = "Samples"
        
        # Build a new figure with the same dark theme
        export_fig = Figure(figsize=(10, 8), facecolor='#2b2b2b')
        axs = export_fig.subplots(3, 2, sharex=True)
        axs = axs.flatten()
        
        def style_ax(ax, title):
            ax.set_facecolor('#1e1e1e')
            ax.set_title(title, color='white', fontsize=10)
            ax.tick_params(colors='white', labelsize=7)
            ax.grid(True, alpha=0.2, color='gray')
        
        # Speed
        style_ax(axs[0], "Speed")
        axs[0].plot(x_values, data_dict['speed'], color='cyan', linewidth=1.0)
        axs[0].set_ylabel("km/h", color='white', fontsize=8)
        
        # Throttle & Brake
        style_ax(axs[1], "Throttle & Brake")
        axs[1].plot(x_values, data_dict['throttle'], color='lime', linewidth=1.0, label='Throttle')
        axs[1].plot(x_values, data_dict['brake'], color='red', linewidth=1.0, label='Brake')
        axs[1].set_ylabel("%", color='white', fontsize=8)
        axs[1].legend(loc='upper right', fontsize=7, facecolor='#2b2b2b', edgecolor='white')
        
        # Steering
        style_ax(axs[2], "Steering Angle")
        axs[2].plot(x_values, data_dict['steer_deg'], color='magenta', linewidth=1.0)
        axs[2].set_ylabel("degrees", color='white', fontsize=8)
        
        # RPM
        style_ax(axs[3], "RPM")
        axs[3].plot(x_values, data_dict['rpm'], color='yellow', linewidth=1.0)
        axs[3].set_ylabel("RPM", color='white', fontsize=8)
        
        # Gear
        style_ax(axs[4], "Gear")
        axs[4].step(x_values, data_dict['gear'], color='white', linewidth=1.0, where='post')
        axs[4].set_ylabel("Gear", color='white', fontsize=8)
        
        # ABS & TC
        style_ax(axs[5], "ABS & TC Activity")
        axs[5].plot(x_values, data_dict['abs'], color='orange', linewidth=1.0, label='ABS')
        axs[5].plot(x_values, data_dict['tc'], color='yellow', linewidth=1.0, label='TC')
        axs[5].set_ylabel("Intensity", color='white', fontsize=8)
        axs[5].legend(loc='upper right', fontsize=7, facecolor='#2b2b2b', edgecolor='white')
        
        for ax in axs[4:]:
            ax.set_xlabel(x_label, color='white', fontsize=8)
        
        export_fig.tight_layout()
        export_fig.savefig(file_path, dpi=150)
        QMessageBox.information(self, "Export Graphs", f"Graphs saved to:\n{file_path}")
    
    def export_last_lap_graphs(self):
        """Export graphs for the latest lap."""
        lap_data = self._get_last_lap_data()
        self._export_graphs(lap_data, "Save Last Lap Graphs", "last_lap.png")
    
    def export_session_graphs(self):
        """Export graphs for the full session (all laps)."""
        session_data = self._get_session_data()
        self._export_graphs(session_data, "Save Full Session Graphs", "session.png")
    
    def reset_display(self):
        """Reset display when disconnected"""
        self._reset_analysis_graphs()
        self.track_map.set_car_progress(0)
        self.speed_label.setText("0")
        self.gear_label.setText("N")
        self.rpm_bar.setValue(0)
        self.throttle_bar.setValue(0)
        self.brake_bar.setValue(0)
        self.steering_widget.set_angle(0)
        self.abs_label.setText("ABS: OFF")
        self.abs_label.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        self.tc_label.setText("TC: OFF")
        self.tc_label.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        self.car_label.setText("Car: --")
        self.track_label.setText("Track: --")
        self.fuel_label.setText("Fuel: --")
        self.position_label.setText("Position: --")
        self.lap_time_label.setText("Last Lap: --")


def main():
    app = QApplication(sys.argv)
    window = TelemetryApp()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()