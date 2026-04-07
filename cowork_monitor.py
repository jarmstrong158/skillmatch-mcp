"""
Cowork VM Monitor — Conductor Worker Script
Checks if the Cowork VM (Vmmem process) is running.
If it's down, automatically clicks the Cowork tab in Claude desktop to boot it.
Falls back to email alert if the click fails.
"""
import subprocess
import sys
import os
import time


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COWORK_TAB_REF = os.path.join(SCRIPT_DIR, "cowork_tab.png")


def check_vmmem():
    """Check if Vmmem process is running."""
    # Try tasklist
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Vmmem"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if "Vmmem" in result.stdout and "No tasks" not in result.stdout:
            return True
    except Exception:
        pass

    # Fallback: wslservice.exe
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq wslservice.exe"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if "wslservice" in result.stdout.lower() and "No tasks" not in result.stdout:
            return True
    except Exception:
        pass

    # Fallback: PowerShell
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process Vmmem -ErrorAction SilentlyContinue | Select-Object -First 1 | ForEach-Object { 'RUNNING' }"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if "RUNNING" in result.stdout:
            return True
    except Exception:
        pass

    return False


def click_cowork_tab():
    """Find and click the Cowork tab in the Claude desktop app."""
    try:
        import pyautogui
        import pygetwindow as gw
    except ImportError:
        print("pyautogui/pygetwindow not installed. Cannot auto-click.")
        return False

    # Find Claude window
    windows = gw.getWindowsWithTitle("Claude")
    if not windows:
        print("Claude desktop window not found.")
        return False

    w = windows[0]

    # Bring Claude to foreground
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        time.sleep(1)
    except Exception as e:
        print(f"Could not activate Claude window: {e}")
        # Continue anyway — locateOnScreen works on visible portions

    # Try image-based matching first (most robust)
    if os.path.isfile(COWORK_TAB_REF):
        try:
            location = pyautogui.locateOnScreen(COWORK_TAB_REF, confidence=0.8)
            if location:
                center = pyautogui.center(location)
                print(f"Found Cowork tab at {center}")
                pyautogui.click(center)
                print("Clicked Cowork tab!")
                return True
            else:
                print("Cowork tab reference image not found on screen.")
        except Exception as e:
            print(f"Image matching failed: {e}")

    # Fallback: calculated position (tabs are centered in Claude window)
    # The Cowork tab is the middle of 3 tabs (Chat, Cowork, Code) at the top
    try:
        tab_x = w.left + w.width // 2  # Center of window = roughly Cowork tab
        tab_y = w.top + 18  # Top bar area
        print(f"Using calculated position: ({tab_x}, {tab_y})")
        pyautogui.click(tab_x, tab_y)
        print("Clicked calculated Cowork tab position!")
        return True
    except Exception as e:
        print(f"Calculated click failed: {e}")

    return False


def main():
    output_file = os.environ.get("OUTPUT_FILE", os.path.join(SCRIPT_DIR, "data", "cowork_status.txt"))

    vm_running = check_vmmem()

    if vm_running:
        # VM is running — write empty file (no email triggered)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("")
        print("Cowork VM is running. No alert needed.")
        return

    # VM is DOWN — attempt auto-recovery
    print("Cowork VM is NOT running. Attempting auto-recovery...")

    clicked = click_cowork_tab()

    if clicked:
        # Wait for VM to boot (usually takes ~7-10 seconds)
        print("Waiting 15 seconds for VM to boot...")
        time.sleep(15)

        # Check again
        if check_vmmem():
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("")
            print("SUCCESS: Cowork VM auto-started!")
            return
        else:
            # Give it more time
            print("VM not up yet. Waiting another 15 seconds...")
            time.sleep(15)
            if check_vmmem():
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write("")
                print("SUCCESS: Cowork VM auto-started (took extra time)!")
                return

    # Auto-recovery failed — write alert for email
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("WARNING: Cowork VM is NOT running!\n\n")
        if clicked:
            f.write("Auto-recovery was attempted (clicked Cowork tab) but the VM did not start.\n\n")
        else:
            f.write("Auto-recovery failed — could not find or click the Cowork tab.\n\n")
        f.write("Your scheduled tasks (job-scout, job-ranker) will NOT fire until the VM is started.\n\n")
        f.write("Fix: Open Claude desktop > click the Cowork tab to boot the VM.\n")
    print("ALERT: Auto-recovery failed. Alert written for email.")
    sys.exit(1)


if __name__ == "__main__":
    main()
