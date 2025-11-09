import sys
import socket
import struct
from collections import deque
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QProgressBar, QComboBox, QPushButton,
                             QLineEdit, QFormLayout, QGroupBox, QTabWidget)
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QColor, QPen
from abc import ABC, abstractmethod
import threading
import math

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
            
            return {
                'speed': speed_kmh,
                'rpm': rpm,
                'max_rpm': 8000,
                'gear': gear,
                'throttle': 0,
                'brake': 0,
                'steer_angle': 0,
                'abs': 0,
                'tc': 0,
                'fuel': 0,
                'max_fuel': 100,
                'lap_time': 0,
                'position': 0,
                'car_name': 'Unknown',
                'track_name': 'Unknown',
                'lap_count': 0,
                'current_time': 0
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
    """Custom matplotlib graph widget"""
    
    def __init__(self, title, ylabel, max_points=500, ylim=(0, 100)):
        self.fig = Figure(figsize=(8, 2), facecolor='#2b2b2b')
        super().__init__(self.fig)
        
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        self.ax.set_title(title, color='white', fontsize=10)
        self.ax.set_ylabel(ylabel, color='white', fontsize=8)
        self.ax.tick_params(colors='white', labelsize=7)
        self.ax.grid(True, alpha=0.2, color='gray')
        self.ax.set_ylim(ylim)
        
        self.max_points = max_points
        self.data = deque(maxlen=max_points)
        self.line, = self.ax.plot([], [], 'cyan', linewidth=1.5)
        
        self.fig.tight_layout()
    
    def update_data(self, value):
        """Add new data point and redraw"""
        self.data.append(value)
        
        self.line.set_data(range(len(self.data)), list(self.data))
        self.ax.set_xlim(0, self.max_points)
        
        self.draw()
    
    def clear(self):
        """Clear all data"""
        self.data.clear()
        self.line.set_data([], [])
        self.draw()


class MultiLineGraph(FigureCanvas):
    """Graph widget for multiple lines (throttle/brake or ABS/TC)"""
    
    def __init__(self, title, ylabel, max_points=500, line1_label='Line 1', line2_label='Line 2',
                 line1_color='lime', line2_color='red'):
        self.fig = Figure(figsize=(8, 2), facecolor='#2b2b2b')
        super().__init__(self.fig)
        
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        self.ax.set_title(title, color='white', fontsize=10)
        self.ax.set_ylabel(ylabel, color='white', fontsize=8)
        self.ax.tick_params(colors='white', labelsize=7)
        self.ax.grid(True, alpha=0.2, color='gray')
        self.ax.set_ylim(0, 100)
        
        self.max_points = max_points
        self.line1_data = deque(maxlen=max_points)
        self.line2_data = deque(maxlen=max_points)
        
        self.line1, = self.ax.plot([], [], line1_color, linewidth=1.5, label=line1_label)
        self.line2, = self.ax.plot([], [], line2_color, linewidth=1.5, label=line2_label)
        self.ax.legend(loc='upper right', fontsize=7, facecolor='#2b2b2b', edgecolor='white')
        
        self.fig.tight_layout()
    
    def update_data(self, value1, value2):
        """Add new data points"""
        self.line1_data.append(value1)
        self.line2_data.append(value2)
        
        x = range(len(self.line1_data))
        self.line1.set_data(x, list(self.line1_data))
        self.line2.set_data(x, list(self.line2_data))
        self.ax.set_xlim(0, self.max_points)
        
        self.draw()
    
    def clear(self):
        """Clear all data"""
        self.line1_data.clear()
        self.line2_data.clear()
        self.line1.set_data([], [])
        self.line2.set_data([], [])
        self.draw()


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


class TelemetryApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AC/ACC Telemetry Dashboard with Graphs")
        self.setGeometry(100, 100, 1400, 900)
        
        # Initialize readers
        self.ac_reader = None
        self.acc_reader = ACCReader()
        self.current_reader = None
        self.auto_detect = True
        
        # Track lap changes for graph reset
        self.last_lap_time = 0
        self.current_lap_count = 0
        
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
        
        # Speed graph
        self.speed_graph = TelemetryGraph("Speed", "km/h", ylim=(0, 300))
        graphs_layout.addWidget(self.speed_graph)
        
        # Throttle/Brake graph
        self.pedals_graph = MultiLineGraph("Throttle & Brake", "%", 
                                          line1_label='Throttle', line2_label='Brake',
                                          line1_color='lime', line2_color='red')
        graphs_layout.addWidget(self.pedals_graph)
        
        # Steering graph
        self.steering_graph = TelemetryGraph("Steering Angle", "degrees", ylim=(-540, 540))
        graphs_layout.addWidget(self.steering_graph)
        
        # RPM graph
        self.rpm_graph = TelemetryGraph("RPM", "RPM", ylim=(0, 10000))
        graphs_layout.addWidget(self.rpm_graph)
        
        # Gear graph
        self.gear_graph = TelemetryGraph("Gear", "Gear", ylim=(-1, 8))
        graphs_layout.addWidget(self.gear_graph)
        
        # ABS/TC graph
        self.aids_graph = MultiLineGraph("ABS & TC Activity", "Intensity",
                                        line1_label='ABS', line2_label='TC',
                                        line1_color='orange', line2_color='yellow')
        graphs_layout.addWidget(self.aids_graph)
        
        tabs.addTab(graphs_tab, "Telemetry Graphs")
        
        main_layout.addWidget(tabs)
    
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
        if (current_lap > self.current_lap_count or 
            (current_lap == 0 and self.current_lap_count > 0) or
            (current_time < 5000 and self.last_lap_time > 5000)):
            
            print(f"Resetting graphs - Lap: {current_lap}, Time: {current_time}")
            self.reset_graphs()
        
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
    
    def reset_graphs(self):
        """Reset all telemetry graphs"""
        self.speed_graph.clear()
        self.pedals_graph.clear()
        self.steering_graph.clear()
        self.rpm_graph.clear()
        self.gear_graph.clear()
        self.aids_graph.clear()
    
    def reset_display(self):
        """Reset display when disconnected"""
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