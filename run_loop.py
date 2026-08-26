"""
Use this INSTEAD of GitHub Actions if you have a machine that stays on
24/7 (Raspberry Pi, old laptop always plugged in, free-tier cloud VM).
This checks every CHECK_INTERVAL_SECONDS instead of waiting on GitHub's
~5 minute cron granularity - much closer to real-time.

Run with: python run_loop.py
Stop with: Ctrl+C
"""

import time
from monitor import main

CHECK_INTERVAL_SECONDS = 20  # adjust based on how aggressive you want to be

if __name__ == "__main__":
    print(f"Starting continuous monitor, checking every {CHECK_INTERVAL_SECONDS}s...")
    while True:
        try:
            main()
        except Exception as e:
            print(f"Error during check cycle: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)
