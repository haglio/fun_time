import re
import socket
import threading
import time

import serial

from pathlib import Path

STATE_FILE = Path(r"C:\path\to\suite-root\projects\osr2_reader\robot_mode.txt")

VIRTUAL_PORT = "COM15"   # broker side of com0com pair
REAL_PORT = "COM4"       # actual OSR2
BAUD = 115200

UDP_HOST = "127.0.0.1"
UDP_PORT = 50555

RE_BPM = re.compile(r"\bbpm\s+(\d+),\s+beats\s+(\d+)", re.IGNORECASE)
RE_STROKE = re.compile(r"StrokeName:\s*([^,]+),\s*PatternDuration:\s*([0-9.]+)", re.IGNORECASE)

stop_flag = False
auto_active = False
lock = threading.Lock()


def udp_send(sock, msg: str):
    sock.sendto(msg.encode("utf-8"), (UDP_HOST, UDP_PORT))


def set_auto(sock, value: bool):
    global auto_active
    with lock:
        changed = (auto_active != value)
        auto_active = value

    try:
        STATE_FILE.write_text("1" if value else "0", encoding="utf-8")
    except Exception:
        pass

    udp_send(sock, f"AUTO {1 if value else 0}")
    if value:
        udp_send(sock, "SHOW")
    else:
        udp_send(sock, "HIDE")

    if changed:
        print(f"AUTO {'ON' if value else 'OFF'}")


def get_auto():
    with lock:
        return auto_active


def handle_line(sock, line: str):
    low = line.lower()

    if "freemode is on" in low or "freemode tcode task started" in low:
        set_auto(sock, True)

    if "freemode is off" in low or "freemode tcode task is stopped" in low:
        set_auto(sock, False)

    m = RE_STROKE.search(line)
    if m:
        udp_send(sock, f"STROKE {m.group(1).strip()}")
        udp_send(sock, f"PATTERN {m.group(2)}")
        udp_send(sock, "SYNC")

    m = RE_BPM.search(line)
    if m:
        udp_send(sock, f"BPM {m.group(1)}")
        udp_send(sock, f"BEATS {m.group(2)}")

    if "continue strokename:" in low or "start transition" in low:
        udp_send(sock, "SYNC")


def forward_real_to_virtual(real, virt, udp_sock):
    global stop_flag
    buf = bytearray()

    while not stop_flag:
        try:
            data = real.read(real.in_waiting or 1)
            if not data:
                continue

            virt.write(data)

            buf.extend(data)
            while b"\n" in buf:
                raw_line, _, rest = buf.partition(b"\n")
                buf[:] = rest
                line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace").strip()
                if line:
                    handle_line(udp_sock, line)

        except Exception as e:
            print(f"REAL->VIRT error: {e}")
            stop_flag = True
            break


def forward_virtual_to_real(virt, real):
    global stop_flag
    while not stop_flag:
        try:
            data = virt.read(virt.in_waiting or 1)
            if not data:
                continue

            if not get_auto():
                real.write(data)
            # else: swallow MFP writes while OSR2 auto mode is active

        except Exception as e:
            print(f"VIRT->REAL error: {e}")
            stop_flag = True
            break


def main():
    global stop_flag

    STATE_FILE.write_text("0", encoding="utf-8")

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    with serial.Serial(VIRTUAL_PORT, BAUD, timeout=0.02) as virt, \
         serial.Serial(REAL_PORT, BAUD, timeout=0.02) as real:

        print(f"Broker running: {VIRTUAL_PORT} <-> {REAL_PORT}")
        print("MFP should use COM14")
        print(f"Robot Hand UDP: {UDP_HOST}:{UDP_PORT}")
        print("Ctrl+C to stop")

        t1 = threading.Thread(target=forward_real_to_virtual, args=(real, virt, udp_sock), daemon=True)
        t2 = threading.Thread(target=forward_virtual_to_real, args=(virt, real), daemon=True)
        t1.start()
        t2.start()

        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            stop_flag = True
        finally:
            udp_sock.close()


if __name__ == "__main__":
    main()