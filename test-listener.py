import sys
import socket
import struct
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QProgressBar, QComboBox, QPushButton,
                             QLineEdit, QFormLayout, QGroupBox)
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from abc import ABC, abstractmethod
import threading

# Install these libraries:
# pip install PyQt6 pyaccsharedmemory
# AC uses UDP - no external library needed (implemented below)


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
        
        # Start listener thread
        self.running = False
        self.listener_thread = None
        
    def connect(self):
        """Connect to AC UDP server"""
        try:
            if self.socket:
                self.socket.close()
                
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.settimeout(1.0)
            
            # Send handshake
            # Handshake packet: identifier (4 bytes) + version (4 bytes) + operation ID (4 bytes)
            identifier = 1  # Can be any number
            version = 1
            operation_id = 0  # Handshake
            
            handshake = struct.pack('<iii', identifier, version, operation_id)
            self.socket.sendto(handshake, (self.host, self.port))
            
            # Wait for response
            try:
                data, addr = self.socket.recvfrom(2048)
                if data:
                    self.connected = True
                    self.handshake_sent = True
                    
                    # Subscribe to updates
                    subscribe = struct.pack('<iii', identifier, version, 1)  # 1 = Subscribe
                    self.socket.sendto(subscribe, (self.host, self.port))
                    
                    # Start listening thread
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
                    # Parse packet type
                    packet_id = struct.unpack('<i', data[0:4])[0]
                    
                    if packet_id == 2:  # RT_CAR_INFO packet
                        self.latest_data = self._parse_car_info(data)
                        
            except socket.timeout:
                continue
            except Exception as e:
                print(f"UDP listen error: {e}")
                break
    
    def _parse_car_info(self, data):
        """Parse RT_CAR_INFO UDP packet"""
        try:
            offset = 4  # Skip packet ID
            
            # Read fields (simplified version - AC UDP has many fields)
            speed_kmh = struct.unpack('<f', data[offset:offset+4])[0]
            offset += 8  # Skip speed_mph
            
            rpm = struct.unpack('<f', data[offset+16:offset+20])[0]
            gear = struct.unpack('<i', data[offset+20:offset+24])[0]
            
            # Get more data (positions vary, this is simplified)
            # Full implementation would parse all fields according to AC UDP spec
            
            return {
                'speed': speed_kmh,
                'rpm': rpm,
                'max_rpm': 8000,  # Would come from handshake response
                'gear': gear,
                'throttle': 0,  # Parse from data
                'brake': 0,
                'fuel': 0,
                'max_fuel': 100,
                'lap_time': 0,
                'position': 0,
                'car_name': 'Unknown',
                'track_name': 'Unknown'
            }
        except Exception as e:
            print(f"Parse error: {e}")
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
            print("ACC: Library not available")
            return None
        try:
            sm = self.asm.read_shared_memory()
            if sm is None:
                print("ACC: read_shared_memory() returned None - game not running or not in session")
                return None
            
            # Don't print every frame, too spammy
            # print(f"ACC: Reading data - Speed: {sm.Physics.speed_kmh}, RPM: {sm.Physics.rpm}")
            
            return {
                'speed': sm.Physics.speed_kmh,
                'rpm': sm.Physics.rpm,
                'max_rpm': sm.Static.max_rpm,
                'gear': sm.Physics.gear - 1,  # ACC uses 0=R, 1=N, 2=1st
                'throttle': sm.Physics.gas * 100,
                'brake': sm.Physics.brake * 100,
                'fuel': sm.Physics.fuel,
                'max_fuel': sm.Static.max_fuel,
                'lap_time': sm.Graphics.last_time / 1000,  # Convert ms to seconds
                'position': sm.Graphics.position,
                'car_name': sm.Static.car_model,
                'track_name': sm.Static.track
            }
        except AttributeError as e:
            print(f"ACC: Attribute error - {e}")
            print(f"ACC: Available Physics attrs: {[a for a in dir(sm.Physics) if not a.startswith('_')]}")
            print(f"ACC: Available Static attrs: {[a for a in dir(sm.Static) if not a.startswith('_')]}")
            print(f"ACC: Available Graphics attrs: {[a for a in dir(sm.Graphics) if not a.startswith('_')]}")
            return None
        except Exception as e:
            print(f"ACC: General error - {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def is_connected(self):
        if not self.available:
            return False
        try:
            sm = self.asm.read_shared_memory()
            result = sm is not None
            if result:
                print("ACC: is_connected() = True")
            return result
        except Exception as e:
            print(f"ACC: is_connected() error: {e}")
            return False


class TelemetryApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AC/ACC Telemetry Dashboard")
        self.setGeometry(100, 100, 900, 700)
        
        # Initialize readers
        self.ac_reader = None
        self.acc_reader = ACCReader()
        self.current_reader = None
        self.auto_detect = True
        
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
        
        # Connection settings group
        settings_group = QGroupBox("Connection Settings")
        settings_layout = QFormLayout()
        
        # Game selector
        game_layout = QHBoxLayout()
        self.game_combo = QComboBox()
        self.game_combo.addItems(['Auto-Detect', 'AC (UDP)', 'ACC (Shared Memory)'])
        self.game_combo.currentTextChanged.connect(self.on_game_changed)
        game_layout.addWidget(self.game_combo)
        
        self.connection_label = QLabel("⚫ Disconnected")
        self.connection_label.setStyleSheet("color: red;")
        game_layout.addWidget(self.connection_label)
        settings_layout.addRow("Game:", game_layout)
        
        # AC UDP settings
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
        
        # Car and Track info
        info_layout = QHBoxLayout()
        self.car_label = QLabel("Car: --")
        self.track_label = QLabel("Track: --")
        info_layout.addWidget(self.car_label)
        info_layout.addWidget(self.track_label)
        main_layout.addLayout(info_layout)
        
        # Speed display
        self.speed_label = QLabel("0")
        self.speed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        speed_font = QFont()
        speed_font.setPointSize(72)
        speed_font.setBold(True)
        self.speed_label.setFont(speed_font)
        main_layout.addWidget(self.speed_label)
        
        speed_unit_label = QLabel("km/h")
        speed_unit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(speed_unit_label)
        
        # Gear display
        self.gear_label = QLabel("N")
        self.gear_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gear_font = QFont()
        gear_font.setPointSize(48)
        gear_font.setBold(True)
        self.gear_label.setFont(gear_font)
        main_layout.addWidget(self.gear_label)
        
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
            }
            QProgressBar::chunk {
                background-color: #05B8CC;
            }
        """)
        rpm_layout.addWidget(self.rpm_bar)
        main_layout.addLayout(rpm_layout)
        
        # Throttle and Brake bars
        pedals_layout = QHBoxLayout()
        
        throttle_layout = QVBoxLayout()
        throttle_layout.addWidget(QLabel("Throttle"))
        self.throttle_bar = QProgressBar()
        self.throttle_bar.setOrientation(Qt.Orientation.Vertical)
        self.throttle_bar.setStyleSheet("""
            QProgressBar::chunk {
                background-color: green;
            }
        """)
        throttle_layout.addWidget(self.throttle_bar)
        pedals_layout.addLayout(throttle_layout)
        
        brake_layout = QVBoxLayout()
        brake_layout.addWidget(QLabel("Brake"))
        self.brake_bar = QProgressBar()
        self.brake_bar.setOrientation(Qt.Orientation.Vertical)
        self.brake_bar.setStyleSheet("""
            QProgressBar::chunk {
                background-color: red;
            }
        """)
        brake_layout.addWidget(self.brake_bar)
        pedals_layout.addLayout(brake_layout)
        
        main_layout.addLayout(pedals_layout)
        
        # Fuel and position
        bottom_layout = QHBoxLayout()
        self.fuel_label = QLabel("Fuel: --")
        self.position_label = QLabel("Position: --")
        self.lap_time_label = QLabel("Last Lap: --")
        bottom_layout.addWidget(self.fuel_label)
        bottom_layout.addWidget(self.position_label)
        bottom_layout.addWidget(self.lap_time_label)
        main_layout.addLayout(bottom_layout)
    
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
        else:  # ACC
            self.auto_detect = False
            self.current_reader = self.acc_reader
    
    def detect_game(self):
        """Auto-detect which game is running"""
        # Try ACC first (faster check)
        print("Detecting game...")
        print(f"Checking ACC... available={self.acc_reader.available}")
        if self.acc_reader.is_connected():
            print("ACC detected!")
            return self.acc_reader
        
        # Try AC
        print("Checking AC...")
        if not self.ac_reader:
            self.ac_reader = ACUDPReader(self.udp_host.text(), int(self.udp_port.text()))
        
        if self.ac_reader.is_connected():
            print("AC detected!")
            return self.ac_reader
        
        print("No game detected")
        return None
    
    def update_telemetry(self):
        # Auto-detect or use selected reader
        if self.auto_detect:
            self.current_reader = self.detect_game()
        
        if self.current_reader is None:
            self.connection_label.setText("⚫ Disconnected")
            self.connection_label.setStyleSheet("color: red;")
            self.reset_display()
            return
        
        # Read data
        data = self.current_reader.read()
        if data is None:
            self.connection_label.setText("⚫ Connection Lost")
            self.connection_label.setStyleSheet("color: orange;")
            return
        
        # Update connection status
        game_type = "AC" if isinstance(self.current_reader, ACUDPReader) else "ACC"
        self.connection_label.setText(f"🟢 Connected to {game_type}")
        self.connection_label.setStyleSheet("color: green;")
        
        # Update displays
        self.speed_label.setText(f"{int(data['speed'])}")
        
        # Gear display
        gear = data['gear']

        if gear == 0:
            gear_text = "N"
        elif gear == -1:
            gear_text = "R"
        elif gear == 1:
            gear_text = "1"
        else:
            gear_text = str(gear) if isinstance(self.current_reader, ACCReader) else str(gear)
        self.gear_label.setText(gear_text)
        
        # RPM bar
        rpm_percent = int((data['rpm'] / data['max_rpm']) * 100) if data['max_rpm'] > 0 else 0
        self.rpm_bar.setValue(rpm_percent)
        self.rpm_bar.setFormat(f"{int(data['rpm'])} / {int(data['max_rpm'])} RPM")
        
        # Pedals
        self.throttle_bar.setValue(int(data['throttle']))
        self.brake_bar.setValue(int(data['brake']))
        
        # Info
        self.car_label.setText(f"Car: {data['car_name']}")
        self.track_label.setText(f"Track: {data['track_name']}")
        
        # Fuel
        fuel_percent = (data['fuel'] / data['max_fuel']) * 100 if data['max_fuel'] > 0 else 0
        self.fuel_label.setText(f"Fuel: {data['fuel']:.1f}L ({fuel_percent:.0f}%)")
        
        # Position and lap time
        self.position_label.setText(f"Position: {data['position']}")
        if data['lap_time'] > 0:
            minutes = int(data['lap_time'] // 60)
            seconds = data['lap_time'] % 60
            self.lap_time_label.setText(f"Last Lap: {minutes}:{seconds:06.3f}")
    
    def reset_display(self):
        """Reset display when disconnected"""
        self.speed_label.setText("0")
        self.gear_label.setText("N")
        self.rpm_bar.setValue(0)
        self.throttle_bar.setValue(0)
        self.brake_bar.setValue(0)
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