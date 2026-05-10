# a55x-flasher

Packaging script and flasher structure for Galaxy A55x custom ROM zips.

## Repo structure

```
a55x-zip/
├── pack.py                          ← packaging script
├── flasher/
│   ├── META-INF/
│   │   └── com/google/android/
│   │       ├── update-binary        ← the installer script
│   │       └── updater-script       ← empty, required by recovery
│   ├── tools/
│   │   ├── lpdump                   ← static arm64 binary
│   │   └── awk                      ← static arm64 binary
│   └── images/
│       └── *.img                    ← empty placeholders, replaced at pack time
```

## Setup

1. Clone the repo
2. Finish your android build

## Usage

```bash
python3 pack.py
```

You will be prompted for:
- **AOSP builds out directory** — path to `out/target/product/<device>/` (you can get the path doing `pwd` on your build directory
- **Build name** — e.g. `lineageos`, `aosp`, `pixelos`

The output will be

```
buildname-a55x-YYYYMMDD.zip
```

You can also pass arguments directly:

```bash
python3 pack.py --out ~/android/out/target/product/a55x --name lineageos
```

## Requirements

- Python 3.6+
- No external dependencies (uses stdlib only)
