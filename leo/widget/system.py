"""CPU, memory and GPU sampling straight from /proc and /sys (no psutil).

NVIDIA cards are read through NVML (ctypes) with an ``nvidia-smi`` fallback.
On laptops with runtime power management the discrete GPU is *never* woken up
just to be measured: if it is suspended we report it as sleeping, and while it
is idle we poll it slowly so it can go back to sleep."""

from __future__ import annotations

import ctypes
import glob
import os
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field

HISTORY = 24
NVIDIA, AMD, INTEL = "0x10de", "0x1002", "0x8086"


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    value = _read(path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


@dataclass
class GpuInfo:
    slot: str
    vendor: str
    name: str
    kind: str = "dgpu"             # dgpu | igpu
    state: str = "active"          # active | suspended | unknown
    util: float | None = None      # %
    mem_used: int | None = None    # bytes
    mem_total: int | None = None
    temp: float | None = None      # °C
    power: float | None = None     # W
    freq: int | None = None        # MHz (Intel: current)
    freq_max: int | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY))


@dataclass
class SystemSample:
    cpu_model: str = ""
    threads: int = 0
    cpu: float = 0.0
    cpu_freq: float | None = None   # GHz
    cpu_temp: float | None = None
    load: tuple = (0.0, 0.0, 0.0)
    mem_used: int = 0
    mem_total: int = 0
    swap_used: int = 0
    swap_total: int = 0
    cpu_history: list = field(default_factory=list)
    gpus: list = field(default_factory=list)


# ── NVML (NVIDIA) ─────────────────────────────────────────────────────────────

class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _MemoryV2(ctypes.Structure):  # "used" excludes driver-reserved memory, like nvidia-smi
    _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong),
                ("reserved", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class Nvml:
    """Minimal NVML binding. ``query`` opens and closes the library every time
    so that an idle laptop GPU is free to enter runtime suspend."""

    def __init__(self):
        self.lib = None
        for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            try:
                self.lib = ctypes.CDLL(name)
                break
            except OSError:
                continue

    def query(self, bus_id: str) -> dict | None:
        lib = self.lib
        if lib is None or lib.nvmlInit_v2() != 0:
            return None
        try:
            handle = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByPciBusId_v2(bus_id.encode(), ctypes.byref(handle)) != 0:
                return None
            out = {}
            name = ctypes.create_string_buffer(96)
            if lib.nvmlDeviceGetName(handle, name, 96) == 0:
                out["name"] = name.value.decode(errors="replace")
            util = _Utilization()
            if lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)) == 0:
                out["util"] = float(util.gpu)
            mem2 = _MemoryV2(version=ctypes.sizeof(_MemoryV2) | (2 << 24))
            mem = _Memory()
            if getattr(lib, "nvmlDeviceGetMemoryInfo_v2", None) and \
                    lib.nvmlDeviceGetMemoryInfo_v2(handle, ctypes.byref(mem2)) == 0:
                out["mem_used"], out["mem_total"] = int(mem2.used), int(mem2.total)
            elif lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) == 0:
                out["mem_used"], out["mem_total"] = int(mem.used), int(mem.total)
            temp = ctypes.c_uint()
            if lib.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(temp)) == 0:
                out["temp"] = float(temp.value)
            power = ctypes.c_uint()
            if lib.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power)) == 0:
                out["power"] = power.value / 1000.0
            return out
        finally:
            lib.nvmlShutdown()


def _nvidia_smi(bus_id: str) -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        res = subprocess.run(
            ["nvidia-smi", f"--id={bus_id}", "--format=csv,noheader,nounits",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = [p.strip() for p in res.stdout.strip().split(",")]
    if res.returncode != 0 or len(parts) < 6:
        return None

    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    used, total = num(parts[2]), num(parts[3])
    return {"name": parts[0], "util": num(parts[1]),
            "mem_used": int(used * 2**20) if used is not None else None,
            "mem_total": int(total * 2**20) if total is not None else None,
            "temp": num(parts[4]), "power": num(parts[5])}


# ── helpers ───────────────────────────────────────────────────────────────────

def _cpu_model() -> str:
    text = _read("/proc/cpuinfo") or ""
    m = re.search(r"^model name\s*:\s*(.+)$", text, re.M) or re.search(r"^Model\s*:\s*(.+)$", text, re.M)
    model = m.group(1) if m else os.uname().machine
    model = re.sub(r"\((R|TM|tm|r)\)", "", model)
    model = re.sub(r"\b(\d+(st|nd|rd|th) Gen|CPU|Processor|with Radeon.*|@.*)", "", model)
    model = re.sub(r"\b(Intel|AMD)\s+", "", model)
    return re.sub(r"\s+", " ", model).strip()


def _cpu_temp() -> float | None:
    preferred = {"coretemp": ("Package id 0",), "k10temp": ("Tctl", "Tdie"), "zenpower": ("Tdie",),
                 "cpu_thermal": (), "acpitz": ()}
    sensors = {}
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        sensors.setdefault(_read(f"{hw}/name") or "", hw)
    for chip, labels in preferred.items():
        hw = sensors.get(chip)
        if not hw:
            continue
        for inp in sorted(glob.glob(f"{hw}/temp*_input")):
            label = _read(inp.replace("_input", "_label")) or ""
            if not labels or label in labels:
                value = _read_int(inp)
                if value:
                    return value / 1000.0
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        if _read(f"{zone}/type") == "x86_pkg_temp":
            value = _read_int(f"{zone}/temp")
            if value:
                return value / 1000.0
    return None


def _cpu_freq() -> float | None:
    values = [_read_int(p) for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")]
    values = [v for v in values if v]
    return sum(values) / len(values) / 1e6 if values else None


def _pci_names(slots: list[str]) -> dict[str, str]:
    """Human names from lspci, e.g. 'AD106M [GeForce RTX 4070 Max-Q / Mobile]'."""
    names = {}
    if not slots or not shutil.which("lspci"):
        return names
    try:
        out = subprocess.run(["lspci", "-mm"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return names
    for line in out.splitlines():
        parts = re.findall(r'"([^"]*)"', line)
        slot = line.split(" ", 1)[0]
        if len(parts) >= 3:
            for full in slots:
                if full.endswith(slot):
                    device = parts[2]
                    m = re.search(r"\[(.+)\]", device)
                    names[full] = m.group(1) if m else device
    return names


def _short_gpu_name(name: str) -> str:
    name = re.sub(r"^(NVIDIA|AMD|Intel(\(R\))?|Advanced Micro Devices.*?\])\s*", "", name)
    name = name.replace("GeForce ", "").replace("Laptop GPU", "Laptop").replace("Graphics", "").strip()
    return re.sub(r"\s+", " ", name)


# ── monitor ───────────────────────────────────────────────────────────────────

class SystemMonitor:
    def __init__(self):
        self._prev = None
        self._cpu_hist = deque(maxlen=HISTORY)
        self._model = _cpu_model()
        self._threads = os.cpu_count() or 1
        self._nvml = None
        self._next_nvidia: dict[str, float] = {}
        self.gpus = self._find_gpus()

    def _find_gpus(self) -> list[GpuInfo]:
        found = []
        for dev in sorted(glob.glob("/sys/bus/pci/devices/*")):
            cls = _read(f"{dev}/class") or ""
            vendor = _read(f"{dev}/vendor") or ""
            if not (cls.startswith("0x0300") or cls.startswith("0x0302") or cls.startswith("0x0380")):
                continue
            if vendor not in (NVIDIA, AMD, INTEL):
                continue
            kind = "igpu" if vendor == INTEL and not os.path.exists(f"{dev}/mem_info_vram_total") else "dgpu"
            if vendor == AMD:
                vram = _read_int(f"{dev}/mem_info_vram_total") or 0
                kind = "igpu" if vram and vram < 2**31 else "dgpu"
            found.append(GpuInfo(slot=os.path.basename(dev), vendor=vendor, name="", kind=kind))
        names = _pci_names([g.slot for g in found])
        for g in found:
            fallback = {NVIDIA: "NVIDIA GPU", AMD: "AMD GPU", INTEL: "Intel GPU"}[g.vendor]
            g.name = _short_gpu_name(names.get(g.slot, fallback))
        # discrete GPUs first
        return sorted(found, key=lambda g: g.kind != "dgpu")

    # CPU / memory
    def _cpu_percent(self) -> float:
        line = (_read("/proc/stat") or "cpu 0 0 0 0").splitlines()[0]
        vals = [int(x) for x in line.split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals[:8])
        prev, self._prev = self._prev, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))))

    @staticmethod
    def _memory() -> tuple[int, int, int, int]:
        info = {}
        for line in (_read("/proc/meminfo") or "").splitlines():
            key, _, rest = line.partition(":")
            try:
                info[key] = int(rest.split()[0]) * 1024
            except (IndexError, ValueError):
                continue
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        swap_total = info.get("SwapTotal", 0)
        return total - avail, total, swap_total - info.get("SwapFree", 0), swap_total

    # GPUs
    def _sample_gpu(self, g: GpuInfo, interval: float) -> None:
        dev = f"/sys/bus/pci/devices/{g.slot}"
        status = _read(f"{dev}/power/runtime_status")
        if status == "suspended":
            g.state, g.util = "suspended", None
            g.history.append(0.0)
            return
        g.state = "active"
        if g.vendor == NVIDIA:
            t = time.monotonic()
            if t < self._next_nvidia.get(g.slot, 0):
                g.history.append(g.util or 0.0)  # keep previous values while idle
                return
            if self._nvml is None:
                self._nvml = Nvml()
            data = self._nvml.query(g.slot) or _nvidia_smi(g.slot)
            if data:
                g.name = _short_gpu_name(data.get("name") or g.name)
                for key in ("util", "mem_used", "mem_total", "temp", "power"):
                    setattr(g, key, data.get(key))
            # idle GPU: sample rarely so runtime power management can kick in
            busy = (g.util or 0) > 0
            self._next_nvidia[g.slot] = t + (interval if busy else max(10.0, interval))
        elif g.vendor == AMD:
            g.util = _read_int(f"{dev}/gpu_busy_percent")
            g.mem_used = _read_int(f"{dev}/mem_info_vram_used")
            g.mem_total = _read_int(f"{dev}/mem_info_vram_total")
            hw = (glob.glob(f"{dev}/hwmon/hwmon*") or [None])[0]
            if hw:
                temp = _read_int(f"{hw}/temp1_input")
                power = _read_int(f"{hw}/power1_average") or _read_int(f"{hw}/power1_input")
                g.temp = temp / 1000.0 if temp else None
                g.power = power / 1e6 if power else None
        else:  # Intel: no unprivileged busy counter, show the GPU clock instead
            card = (glob.glob(f"{dev}/drm/card*") or [None])[0]
            if card:
                cur = _read_int(f"{card}/gt_act_freq_mhz") or _read_int(f"{card}/gt_cur_freq_mhz")
                top = _read_int(f"{card}/gt_max_freq_mhz") or _read_int(f"{card}/gt_RP0_freq_mhz")
                if cur is None:  # xe driver
                    xe = (glob.glob(f"{dev}/tile0/gt0/freq0") or [None])[0]
                    if xe:
                        cur, top = _read_int(f"{xe}/act_freq"), _read_int(f"{xe}/max_freq")
                g.freq, g.freq_max = cur, top
                if cur is not None and top:
                    g.util = 100.0 * cur / top
        g.history.append(g.util or 0.0)

    def sample(self, interval: float = 2.0) -> SystemSample:
        cpu = self._cpu_percent()
        self._cpu_hist.append(cpu)
        used, total, swap_used, swap_total = self._memory()
        for g in self.gpus:
            try:
                self._sample_gpu(g, interval)
            except Exception:  # a flaky driver must not break the widget
                g.state = "unknown"
        try:
            load = os.getloadavg()
        except OSError:
            load = (0.0, 0.0, 0.0)
        return SystemSample(
            cpu_model=self._model, threads=self._threads, cpu=cpu, cpu_freq=_cpu_freq(),
            cpu_temp=_cpu_temp(), load=load, mem_used=used, mem_total=total,
            swap_used=swap_used, swap_total=swap_total, cpu_history=list(self._cpu_hist),
            gpus=self.gpus)
