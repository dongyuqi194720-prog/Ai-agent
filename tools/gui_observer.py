import re
import subprocess


def list_windows():
    """Return visible X11 windows with basic application metadata."""
    result = subprocess.run(
        ["wmctrl", "-lxG"],
        capture_output=True,
        text=True,
        check=True,
    )

    windows = []
    for line in result.stdout.splitlines():
        m = re.match(
            r"^(0x[0-9a-f]+)\s+\S+\s+(-?\d+)\s+(-?\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+\S+\s+(.*)$",
            line,
        )
        if not m:
            continue

        window_id, x, y, width, height, wm_class, title = m.groups()

        windows.append({
            "window_id": window_id,
            "pid": _get_pid(window_id),
            "wm_class": wm_class,
            "title": title.strip(),
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
        })

    return windows


def _get_pid(window_id):
    result = subprocess.run(
        ["xprop", "-id", window_id, "_NET_WM_PID"],
        capture_output=True,
        text=True,
    )
    m = re.search(r"=\s*(\d+)", result.stdout)
    return int(m.group(1)) if m else None


def inspect_process(pid):
    """Return basic process metadata for a GUI application's PID."""
    pid = int(pid)

    exe = subprocess.run(
        ["readlink", "-f", f"/proc/{pid}/exe"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    raw_cmdline = open(f"/proc/{pid}/cmdline", "rb").read()
    cmdline = raw_cmdline.replace(b"\0", b" ").decode(errors="replace").strip()

    children = []
    children_path = f"/proc/{pid}/task/{pid}/children"
    try:
        raw_children = open(children_path, "rb").read()
        children = [int(x) for x in raw_children.split()]
    except (FileNotFoundError, PermissionError):
        pass

    return {
        "pid": pid,
        "exe": exe,
        "cmdline": cmdline,
        "children": children,
    }


def find_window(query):
    """Find the first GUI window whose title or WM_CLASS matches query."""
    query = str(query).strip().lower()
    if not query:
        return None

    for window in list_windows():
        if (
            query in window["title"].lower()
            or query in window["wm_class"].lower()
        ):
            return window

    return None


def get_active_window():
    """Return metadata for the currently active X11 window."""
    result = subprocess.run(
        ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
        capture_output=True,
        text=True,
        check=True,
    )
    m = re.search(r"#\s*(0x[0-9a-fA-F]+)", result.stdout)
    if not m:
        return None

    window_id = int(m.group(1), 16)
    return next(
        (window for window in list_windows()
         if int(window["window_id"], 16) == window_id),
        None,
    )

def capture_window(window_id, output_path):
    """Capture an X11 window to an image file."""
    subprocess.run(
        ["import", "-window", str(window_id), str(output_path)],
        check=True,
    )
    return str(output_path)

def observe_window(query, output_path="/tmp/v6_window.png"):
    """Find a window and capture its current visual state."""
    window = find_window(query)
    if not window:
        return None

    capture_window(window["window_id"], output_path)
    result = dict(window)
    result["screenshot"] = output_path
    return result
