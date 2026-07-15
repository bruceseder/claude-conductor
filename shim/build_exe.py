"""Build a tiny tmux.exe wrapper using Python's zipapp or a C stub.

Since PyInstaller is having issues, this creates a minimal .exe that
just calls python tmux.py with the same arguments.
"""

import os
import sys
import subprocess

SHIM_DIR = os.path.dirname(os.path.abspath(__file__))


def build_with_cython_or_stub():
    """Create tmux.exe by compiling a tiny C wrapper."""
    c_source = os.path.join(SHIM_DIR, "_tmux_stub.c")
    exe_path = os.path.join(SHIM_DIR, "tmux.exe")

    # Write a tiny C program that calls python tmux.py
    with open(c_source, "w") as f:
        f.write(r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

int main(int argc, char *argv[]) {
    char cmd[32768];
    char exe_dir[MAX_PATH];
    char *last_slash;

    // Get directory of this exe
    GetModuleFileNameA(NULL, exe_dir, MAX_PATH);
    last_slash = strrchr(exe_dir, '\\');
    if (last_slash) *last_slash = '\0';

    // Build command: python "<exe_dir>\tmux.py" <args...>
    snprintf(cmd, sizeof(cmd), "python \"%s\\tmux.py\"", exe_dir);

    for (int i = 1; i < argc; i++) {
        strcat(cmd, " ");
        // Quote args that contain spaces
        if (strchr(argv[i], ' ')) {
            strcat(cmd, "\"");
            strcat(cmd, argv[i]);
            strcat(cmd, "\"");
        } else {
            strcat(cmd, argv[i]);
        }
    }

    return system(cmd);
}
""")

    # Try to compile with gcc (from MinGW, MSYS2, or Git Bash)
    for compiler in ["gcc", "cc", "cl"]:
        try:
            result = subprocess.run(
                [compiler, c_source, "-o", exe_path],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"Built {exe_path} with {compiler}")
                os.remove(c_source)
                return True
            print(f"{compiler} failed: {result.stderr}")
        except FileNotFoundError:
            continue

    print("No C compiler found. Trying alternative approach...")
    os.remove(c_source)
    return False


def build_with_pip_exe():
    """Alternative: use Python to create a simple .exe launcher via struct packing."""
    # Actually, the simplest approach: just copy python.exe and rename it,
    # with a __main__.py that imports and runs tmux.py
    # But that's hacky. Let's try one more thing.
    pass


if __name__ == "__main__":
    if build_with_cython_or_stub():
        # Test it
        exe = os.path.join(SHIM_DIR, "tmux.exe")
        r = subprocess.run([exe, "-V"], capture_output=True, text=True)
        print(f"Test: {r.stdout.strip()}")
    else:
        print("Could not build tmux.exe. Install gcc or fix PyInstaller.")
        print("As a workaround, you can add tmux.cmd to PATH and use cmd.exe")
