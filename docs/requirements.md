# Host tools

- Python 3, ARM GCC and binutils: payload compilation, patching and manifest export.
- Ghidra: optional APP1 analysis; database generated locally.
- Python `capstone`: optional disassembly in `fwmem.py`.
- A C compiler: the scale-layout check.
- PyQt6, udisks2 and dosfstools (`udisksctl`, `mkfs.fat`, `fsck.fat`, via `pkexec`): optional
  SD card writer `sdflash.py`.
- QEMU ARM, NumPy, SciPy, numba, soundfile and librosa: optional stretch experiments.

Use a virtual environment for Python packages. The compiler flags are
`-mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16`.
