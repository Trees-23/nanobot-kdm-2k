import signal
import sys
import time


cancelled = False


def stop(_signum, _frame):
    global cancelled
    cancelled = True


signal.signal(signal.SIGTERM, stop)
deadline = time.monotonic() + 120
while time.monotonic() < deadline and not cancelled:
    time.sleep(0.05)
sys.exit(0 if cancelled else 2)
