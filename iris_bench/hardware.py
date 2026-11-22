"""Coarse hardware profile detection without hostnames, usernames or paths."""

from __future__ import annotations

import ctypes
import platform
import subprocess
import sys
from pathlib import Path

from iris_bench.models import HardwareInfo


def _detect_cpu() -> str | None:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                text = str(value).strip()
                if text:
                    return text
        except OSError:
            pass
    elif sys.platform.startswith("linux"):
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
            for line in cpuinfo.splitlines():
                if line.lower().startswith("model name") and ":" in line:
                    text = line.split(":", 1)[1].strip()
                    if text:
                        return text
        except OSError:
            pass
    fallback = platform.processor().strip()
    return fallback or None


def _detect_ram_gb() -> float | None:
    if sys.platform == "win32":
        try:

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            windll = getattr(ctypes, "windll", None)
            if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(float(stat.ullTotalPhys) / (1024**3), 1)
        except OSError:
            pass
    elif sys.platform.startswith("linux"):
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return round(int(parts[1]) / (1024**2), 1)
        except OSError:
            pass
    return None


def _detect_gpu() -> tuple[str | None, float | None]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [part.strip() for part in first_line.split(",")]
            if parts and parts[0]:
                vram: float | None = None
                if len(parts) >= 2:
                    try:
                        vram = round(float(parts[1]) / 1024.0, 1)
                    except ValueError:
                        vram = None
                return parts[0], vram
    except (OSError, subprocess.SubprocessError):
        pass

    if sys.platform == "win32":
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_VideoController | "
                    "Select-Object -First 1 Name,AdapterRAM | ConvertTo-Json -Compress)",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                import json

                raw = json.loads(proc.stdout.strip())
                if isinstance(raw, dict) and raw.get("Name"):
                    name = str(raw["Name"]).strip()
                    adapter_ram = raw.get("AdapterRAM")
                    vram = (
                        round(float(adapter_ram) / (1024**3), 1)
                        if isinstance(adapter_ram, (int, float)) and adapter_ram > 0
                        else None
                    )
                    return name or None, vram
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    return None, None


def detect_hardware() -> HardwareInfo:
    """Collect a coarse, privacy-safe hardware summary."""
    gpu_name, gpu_vram_gb = _detect_gpu()
    return HardwareInfo(
        os=f"{platform.system()} {platform.release()}".strip(),
        arch=platform.machine(),
        python=platform.python_version(),
        cpu=_detect_cpu(),
        ram_gb=_detect_ram_gb(),
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram_gb,
    )
