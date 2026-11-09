"""Desktop entry point for the source distribution."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parent.parent/"run-night-bandit.pyw"),run_name="__main__")
