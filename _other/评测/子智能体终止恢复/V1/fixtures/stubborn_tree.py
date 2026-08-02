import signal
import subprocess
import sys
import time


signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen(
    [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"],
)
print(child.pid, flush=True)
while True:
    time.sleep(1)
