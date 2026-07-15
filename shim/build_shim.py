"""Build script to compile tmux.py into tmux.exe via PyInstaller.

Usage:
    python shim/build_shim.py

Produces: shim/dist/tmux.exe
"""

import subprocess
import sys
import os

SHIM_DIR = os.path.dirname(os.path.abspath(__file__))
TMUX_SCRIPT = os.path.join(SHIM_DIR, "tmux.py")


def main():
    # Ensure PyInstaller is available
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "tmux",
        "--distpath", os.path.join(SHIM_DIR, "dist"),
        "--workpath", os.path.join(SHIM_DIR, "build"),
        "--specpath", SHIM_DIR,
        "--hidden-import", "win32file",
        "--hidden-import", "win32pipe",
        "--hidden-import", "pywintypes",
        TMUX_SCRIPT,
    ]

    print(f"Building tmux.exe...")
    print(f"  Command: {' '.join(cmd)}")
    subprocess.check_call(cmd)
    print(f"\nDone! tmux.exe is at: {os.path.join(SHIM_DIR, 'dist', 'tmux.exe')}")
    print(f"Add {os.path.join(SHIM_DIR, 'dist')} to the front of your PATH.")


if __name__ == "__main__":
    main()
