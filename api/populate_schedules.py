#!/usr/bin/env python3
"""
Standalone script to populate the route_schedules table
from existing On-Time Performance CSV files.

Run from inside the api container:
    python populate_schedules.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from Classes.DataProcessor import DataProcessor

if __name__ == '__main__':
    data_dir = os.getenv('DATA_DIR', 'Data')
    print(f"[populate_schedules] Using data dir: {data_dir}")
    processor = DataProcessor(data_dir)
    processor.process_schedules_data()
    print("[populate_schedules] Done.")
