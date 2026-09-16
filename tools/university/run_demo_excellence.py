"""Launch the existing vNext runtime with its existing restricted demo identities.

This does not seed or reset records, and cannot run outside a loopback database.
"""

import os
import runpy
from pathlib import Path

os.environ["DEMO_STUDENT_ALLOWLIST"] = "SYN-000061"
os.environ["DEMO_STAFF_ALLOWLIST"] = "AU-55ff7e408818"
os.environ["BROWSER_AUTH_REQUIRED"] = "true"
runpy.run_path(str(Path(__file__).with_name("run_runtime.py")), run_name="__main__")
