"""Finding an NVIDIA graphics card and choosing the software that can use it.

Standard library only: the launcher's package check imports this before any of
LensLedger's own packages are guaranteed to be installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def nvidia_gpu_name() -> str:
    """The name of an NVIDIA graphics card in this computer, or "" when there
    is none (or its driver tool is missing). Asked once per run."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        done = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return lines[0] if done.returncode == 0 and lines else ""


CUDA_WHEEL_BASE = "https://download.pytorch.org/whl"
GPU_SWITCH_MARKER = "gpu-torch-pending"


def cuda_driver_version() -> tuple[int, int] | None:
    """The newest CUDA version the installed NVIDIA driver supports, from the
    header of ``nvidia-smi``, or None when it cannot be read."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        done = subprocess.run(
            [exe], capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_cuda_version(done.stdout or "")


def parse_cuda_version(header: str) -> tuple[int, int] | None:
    """The CUDA version in an ``nvidia-smi`` header. Older drivers print
    "CUDA Version: 12.6", newer ones "CUDA UMD Version: 13.5"."""
    found = re.search(r"CUDA\s+(?:\w+\s+)?Version:\s*(\d+)\.(\d+)", header)
    return (int(found.group(1)), int(found.group(2))) if found else None


def wheel_tag_for_driver(version: tuple[int, int] | None) -> str:
    """The PyTorch download folder that suits a driver's CUDA version: the
    newest build the driver can run, or "" when it is too old for any."""
    if version is None:
        return ""
    if version >= (13, 0):
        return "cu130"
    if version >= (12, 6):
        return "cu126"
    return ""


@lru_cache(maxsize=1)
def cuda_wheel_tag() -> str:
    """The PyTorch build to use for this computer's NVIDIA card. Only Windows
    needs asking: PyPI's Windows PyTorch has no card support, while Linux's
    already has it and macOS has none to give."""
    if sys.platform != "win32" or not nvidia_gpu_name():
        return ""
    return wheel_tag_for_driver(cuda_driver_version())


def installed_torch_version() -> str:
    """The version string of the installed PyTorch ("2.13.0+cpu"), without
    importing it; "" when it is not installed."""
    try:
        from importlib.metadata import version
        return version("torch")
    except Exception:
        return ""


def torch_lacks_gpu_support(installed: str, tag: str) -> bool:
    """True when a card PyTorch could use exists but the installed build is one
    without card support."""
    return bool(tag and installed and "+cu" not in installed)


def gpu_switch_available() -> bool:
    return torch_lacks_gpu_support(installed_torch_version(), cuda_wheel_tag())


def distribution_version(name: str) -> str:
    """The installed version of a pip package, or "" -- read from its metadata so
    the package itself is never imported (importing it would hold its files open)."""
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return ""


def onnx_gpu_requirement(packages: list[str], with_cuda_libraries: bool) -> str:
    """The onnxruntime line of a requirements section, turned into its
    graphics-card package ("onnxruntime>=1.28,<2" becomes
    "onnxruntime-gpu>=1.28,<2"). Without PyTorch's CUDA libraries to share,
    the CUDA and cuDNN libraries are requested with it."""
    line = next((p for p in packages if p.lower().startswith("onnxruntime")), "onnxruntime")
    rest = re.sub(r"^onnxruntime(-gpu)?", "", line, flags=re.IGNORECASE)
    return f"onnxruntime-gpu{'[cuda,cudnn]' if with_cuda_libraries else ''}{rest}"


def cuda_torch_pip_args(requirement: str, tag: str) -> list[str]:
    """pip arguments that install the NVIDIA build of PyTorch. The build lives
    on PyTorch's own download site, which carries what it needs, so PyPI is
    not consulted for it (that is what keeps pip from picking the card-less
    PyPI build over it)."""
    # torchvision is installed with it: open_clip needs it, and each torchvision
    # release works with exactly one PyTorch release.
    return ["install", "--upgrade", requirement, "torchvision", "--index-url", f"{CUDA_WHEEL_BASE}/{tag}"]


def run_pip(args: list[str], timeout: int, stream: bool = False) -> tuple[bool, str]:
    """Run ``python -m pip`` with ``args``; return (succeeded, last lines of output)."""
    command = [sys.executable, "-m", "pip", *args]
    try:
        if stream:
            done = subprocess.run(command, timeout=timeout)
            return done.returncode == 0, ""
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    tail = "\n".join(((done.stderr or "") + (done.stdout or "")).strip().splitlines()[-8:])
    return done.returncode == 0, tail
