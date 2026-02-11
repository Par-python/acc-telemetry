// Simple UDP-based telemetry simulator for test-listener.py
// 
// Usage:
//   1) In one terminal:  node telemetry_simulator.js
//   2) In another:       python3 test-listener.py
//   3) In the app UI, select "AC (UDP)" as the game,
//      with Host = 127.0.0.1 and Port = 9996.
//
// This script implements just enough of the AC UDP protocol used by
// ACUDPReader in test-listener.py to drive the speed/RPM/gear graphs.
// It:
//   - responds to the handshake (op = 0)
//   - starts streaming RT_CAR_INFO packets (packet_id = 2) after subscribe (op = 1)
//
// No external dependencies are required (only Node's standard library).

const dgram = require('dgram');

const PORT = 9996;
const HOST = '127.0.0.1';

const server = dgram.createSocket('udp4');

let client = null;
let sending = false;
let t = 0; // simulation time (seconds)

server.on('error', (err) => {
  console.error('Simulator socket error:', err);
  server.close();
});

server.on('message', (msg, rinfo) => {
  if (msg.length < 12) {
    return;
  }

  const identifier = msg.readInt32LE(0);
  const version = msg.readInt32LE(4);
  const operationId = msg.readInt32LE(8);

  // Handshake (op = 0)
  if (operationId === 0) {
    console.log('Received handshake from', rinfo.address, rinfo.port,
      'id=', identifier, 'ver=', version);

    // ACUDPReader just checks for "some data", so a small dummy reply is fine.
    const resp = Buffer.alloc(4);
    resp.writeInt32LE(1, 0);
    server.send(resp, rinfo.port, rinfo.address);
    return;
  }

  // Subscribe (op = 1)
  if (operationId === 1) {
    console.log('Received subscribe from', rinfo.address, rinfo.port);
    client = { address: rinfo.address, port: rinfo.port };

    if (!sending) {
      startStreaming();
    }
  }
});

// Monza lap duration ~90 sec; use as base for repeating patterns
const LAP_SEC = 90;
const PI = Math.PI;

function startStreaming() {
  sending = true;

  setInterval(() => {
    if (!client) {
      return;
    }

    t += 0.05; // ~20 Hz, matches test-listener update timer
    const lapProgress = (t % LAP_SEC) / LAP_SEC; // 0..1 within lap

    // --- Lap-like patterns (reference: throttle/brake rectangular + spikes, steering oscillations) ---

    // Braking zones: sharp dips in speed, spikes in brake
    const brakeZone1 = lapProgress > 0.02 && lapProgress < 0.08;   // Turn 1
    const brakeZone2 = lapProgress > 0.22 && lapProgress < 0.28;   // Chicane
    const brakeZone3 = lapProgress > 0.45 && lapProgress < 0.52;   // Lesmo
    const brakeZone4 = lapProgress > 0.68 && lapProgress < 0.75;   // Ascari
    const brakeZone5 = lapProgress > 0.88 && lapProgress < 0.95;   // Parabolica
    const inBrakeZone = brakeZone1 || brakeZone2 || brakeZone3 || brakeZone4 || brakeZone5;

    // Brake: spikes (60-100%) in brake zones, 0 otherwise
    let brake = 0;
    if (inBrakeZone) {
      brake = 75 + 25 * Math.max(0, Math.sin(t * 12));  // oscillating spike
    }

    // Throttle: rectangular 0/100, full off in brake zones, full on otherwise
    let throttle = inBrakeZone ? 0 : 100;
    // Slight lift in fast corners (e.g. Lesmo, Parabolica exit)
    if (lapProgress > 0.48 && lapProgress < 0.55) throttle = 85;
    if (lapProgress > 0.92 && lapProgress < 0.98) throttle = 90;

    // Speed: dips in brake zones, ramps on straights
    const straightBoost = !inBrakeZone ? 1 : 0.3;
    const speed = 120 + 130 * Math.sin(t * 0.7) * straightBoost + 50 * Math.cos(lapProgress * 4 * PI);
    const speedClamped = Math.max(80, Math.min(310, speed));

    // RPM: follows speed and gear
    const gear = Math.max(2, Math.min(6, Math.floor(2 + (speedClamped / 55) + 0.3 * Math.sin(t))));
    const rpm = 3500 + (speedClamped / 300) * 4500 + 800 * Math.sin(t * 2);

    // Steering: oscillations for turns, sharp peaks in chicanes
    const steerMain = 120 * Math.sin(lapProgress * 6 * PI);           // main corners
    const steerChicane = 80 * Math.sin(lapProgress * 25 * PI);        // chicanes
    const steerAngleRad = (steerMain + steerChicane) * (PI / 180);    // convert to radians

    // ABS: activates when braking hard (lock-up simulation)
    const absVal = brake > 65 ? Math.min(100, 30 + (brake - 65) * 1.5) : 0;

    // TC: activates on throttle when accelerating (wheel slip)
    const tcVal = (throttle > 80 && brake < 10) ? 15 + 25 * Math.sin(t * 3) * Math.max(0, Math.sin(t * 0.5)) : 0;

    // Packet layout (must match ACUDPReader._parse_car_info):
    const buf = Buffer.alloc(64);
    buf.writeInt32LE(2, 0);              // packet_id = 2
    buf.writeFloatLE(speedClamped, 4);   // speed_kmh
    buf.writeFloatLE(rpm, 28);           // rpm
    buf.writeInt32LE(gear, 32);          // gear
    buf.writeFloatLE(throttle, 36);      // throttle 0-100
    buf.writeFloatLE(brake, 40);         // brake 0-100
    buf.writeFloatLE(steerAngleRad, 44); // steer_angle (radians)
    buf.writeFloatLE(Math.min(100, absVal), 48);  // abs
    buf.writeFloatLE(Math.min(100, tcVal), 52);   // tc

    server.send(buf, client.port, client.address);
  }, 50);
}

server.on('listening', () => {
  const addr = server.address();
  console.log(`Telemetry simulator listening on ${addr.address}:${addr.port}`);
  console.log('Open test-listener.py and select "AC (UDP)" with 127.0.0.1:9996 to see data.');
});

server.bind(PORT, HOST);

