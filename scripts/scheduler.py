import os
import platform
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

server = os.getenv("LOAN_SERVER", "").lower()

if not server:
    if platform.system().lower().startswith("win"):
        server = "windows"
    else:
        server = "ubuntu"

print(f"Running scheduler for : {server}")

if server == "ubuntu":
    subprocess.run(
        ["bash", str(BASE_DIR / "ubuntu_auto_debit.sh")],
        check=True,
    )

elif server == "windows":
    subprocess.run(
        [str(BASE_DIR / "windows_auto_debit.bat")],
        shell=True,
        check=True,
    )

else:
    raise Exception(f"Unknown server type : {server}")
