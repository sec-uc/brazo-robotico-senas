#!/usr/bin/env python3
"""Sonda del brazo por USB-serial, SIN pasar por la plataforma (diagnóstico).

Lista puertos, abre el indicado con el protocolo heredado (9600 8N1, trama
[200][servo][ángulo]) y mueve un servo a un ángulo seguro, luego lo devuelve a
su posición inicial. Útil para confirmar cable/puerto/firmware antes de
arrancar actuator-control en modo serial.

    python scripts/serial_probe.py                       # lista puertos
    python scripts/serial_probe.py /dev/tty.usbserial-1420 --servo 12 --angle 100
"""
import argparse, sys, time
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "services", "actuator-control"))
from app.hardware import SERVOS, clamp, encode_command, BAUD_RATE  # noqa: E402

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("instala pyserial: pip3 install pyserial")

ap = argparse.ArgumentParser()
ap.add_argument("port", nargs="?")
ap.add_argument("--servo", type=int, default=12)
ap.add_argument("--angle", type=int, default=100)
ap.add_argument("--delay-ms", type=int, default=150)
a = ap.parse_args()

if not a.port:
    for p in list_ports.comports():
        print(f"{p.device:32s} {p.description}  [{p.hwid}]")
    sys.exit(0)

spec = SERVOS[a.servo]
safe = clamp(a.servo, a.angle)
print(f"abriendo {a.port} @ {BAUD_RATE}… (el Arduino se reinicia al abrir; espera 2 s)")
with serial.Serial(a.port, BAUD_RATE, timeout=1) as ser:
    ser.dtr = True; ser.rts = True
    time.sleep(2)
    for target in (safe, spec.initial):
        frame = encode_command(a.servo, target)
        ser.write(frame[:1]); time.sleep(a.delay_ms / 1000); ser.write(frame[1:])
        print(f"servo {a.servo} ({spec.name}) → {target}°  trama={list(frame)}")
        time.sleep(0.8)
    print("respuesta del firmware (si la hay):", ser.read(64))
print("ok")
