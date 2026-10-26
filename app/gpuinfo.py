"""The name of the GPU this host runs on, so measured numbers can say which GPU they were measured on."""
import re
import subprocess

# rocm-smi prints "Card Series: N/A" without access to the render nodes; the PCI device id still names the part.
DEVICE_IDS = {"0x74a1": "AMD Instinct MI300X", "0x74b5": "AMD Instinct MI300X",  # bare metal, virtual function
              "0x74a5": "AMD Instinct MI325X", "0x74b9": "AMD Instinct MI325X"}


def gpu_name():
    """rocm-smi's product name for GPU 0, or "unknown" when rocm-smi is missing or says nothing useful."""
    try:
        out = subprocess.run(["rocm-smi", "--showproductname"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    fields = dict(re.findall(r"GPU\[0\]\s*:\s*([^:\n]+?):\s*(.*)", out))
    series = (fields.get("Card Series") or "").strip()
    if series and series != "N/A":
        return series
    for key in ("Card Model", "Subsystem ID"):
        name = DEVICE_IDS.get((fields.get(key) or "").strip().lower())
        if name:
            return name
    return "unknown"
