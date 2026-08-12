"""
Profiles CentralizedDB.bulk_upload_masters() directly (bypassing
Flask/HTTP entirely) to find EXACTLY which function is consuming the
time for the real retailer upload.

Usage:
    python profile_retailer_upload.py "Retailer_Template (2).csv"
"""
import cProfile
import pstats
import sys
import shutil
import tempfile
from pathlib import Path

from centralized_db_system.db import CentralizedDB

if len(sys.argv) < 2:
    print("Usage: python profile_retailer_upload.py <path_to_file>")
    sys.exit(1)

SOURCE_FILE = sys.argv[1]
WORKSPACE_ID = "bombay_dyeing_gt_north"

# Copy the live DB to a throwaway temp file so this profiling run
# never touches your real data.
tmp_dir = tempfile.mkdtemp()
tmp_db_path = str(Path(tmp_dir) / "profile_copy.sqlite3")
shutil.copyfile("centralized_db.sqlite3", tmp_db_path)
print(f"Profiling against a throwaway COPY of the database: {tmp_db_path}")

db = CentralizedDB(tmp_db_path)

profiler = cProfile.Profile()
profiler.enable()

result = db.bulk_upload_masters("retailers", SOURCE_FILE, workspace_id=WORKSPACE_ID)

profiler.disable()

print("\n=== Upload result ===")
print(result)

print("\n=== Top 25 functions by CUMULATIVE time ===")
stats = pstats.Stats(profiler)
stats.sort_stats("cumulative")
stats.print_stats(25)

print("\n=== Top 25 functions by TOTAL (self) time ===")
stats.sort_stats("tottime")
stats.print_stats(25)
