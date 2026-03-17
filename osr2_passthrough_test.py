import threading
import time
import serial

VIRTUAL_PORT = "COM15"   # broker side of com0com pair
REAL_PORT = "COM4"       # actual OSR2
BAUD = 115200

stop_flag = False

def forward(src, dst, label):
    global stop_flag
    while not stop_flag:
        try:
            data = src.read(src.in_waiting or 1)
            if data:
                dst.write(data)
        except Exception as e:
            print(f"{label} error: {e}")
            stop_flag = True
            break

def main():
    global stop_flag
    with serial.Serial(VIRTUAL_PORT, BAUD, timeout=0.02) as virt, \
         serial.Serial(REAL_PORT, BAUD, timeout=0.02) as real:

        print(f"Bridge running: {VIRTUAL_PORT} <-> {REAL_PORT}")
        print("Set MultiFunPlayer to COM14")
        print("Ctrl+C to stop")

        t1 = threading.Thread(target=forward, args=(virt, real, "MFP->OSR2"), daemon=True)
        t2 = threading.Thread(target=forward, args=(real, virt, "OSR2->MFP"), daemon=True)
        t1.start()
        t2.start()

        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            stop_flag = True

if __name__ == "__main__":
    main()