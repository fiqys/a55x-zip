#!/usr/bin/env python3

import sys
import time
import shutil
import subprocess
import zipfile
import argparse
import datetime
from pathlib import Path

# Partition image map
# Maps destination name inside the zip → source filename in AOSP out/
PARTITION_MAP = {
    "system_a.img":      "system.img",
    "product_a.img":     "product.img",
    "system_ext_a.img":  "system_ext.img",
    "system_dlkm_a.img": "system_dlkm.img",
    "vendor_a.img":      "vendor.img",
    "vendor_dlkm_a.img": "vendor_dlkm.img",
    "odm_a.img":         "odm.img",
    "boot.img":          "boot.img",
    "vendor_boot.img":   "vendor_boot.img",
    "vbmeta.img":        "vbmeta.img",
    "dtbo.img":          "dtbo.img",
}

# Minimum sane size for a partition image (1 MB)
MIN_IMAGE_SIZE = 1 * 1024 * 1024

# Images exempt from the minimum size check (legitimately small)
SIZE_CHECK_EXEMPT = {
    "vbmeta.img",
    "dtbo.img",
    "super_metadata.img",
}


# ── Super metadata ─────────────────────────────────────────────────────────────

SPARSE_MAGIC = b"\x3a\xff\x26\xed"

def is_sparse(img_path: Path) -> bool:
    """Check if an image is in Android sparse format."""
    with open(img_path, "rb") as f:
        return f.read(4) == SPARSE_MAGIC


def extract_super_metadata(out_dir: Path, tmp_dir: Path) -> Path:
    """Extract LP metadata from super.img and return path to the metadata file."""
    super_img = out_dir / "super.img"

    if not super_img.exists():
        print("  [!] super.img not found in out directory")
        sys.exit(1)

    raw_img = super_img

    if is_sparse(super_img):
        print("  super.img is sparse, converting to raw...")
        raw_img = tmp_dir / "super-raw.img"
        simg2img = shutil.which("simg2img")
        if not simg2img:
            print("  [!] simg2img not found — install android-tools or AOSP host tools")
            sys.exit(1)
        subprocess.run(
            [simg2img, str(super_img), str(raw_img)],
            check=True
        )
        print("  Conversion done.")
    else:
        print("  super.img is already raw.")

    meta_path = tmp_dir / "super_metadata.img"
    print("  Extracting LP metadata (first 4MB)...")
    subprocess.run(
        ["dd", f"if={raw_img}", f"of={meta_path}", "bs=1048576", "count=4"],
        check=True, capture_output=True
    )

    # Clean up raw if we created it
    if raw_img != super_img and raw_img.exists():
        raw_img.unlink()

    print(f"  Metadata extracted: {format_size(meta_path.stat().st_size)}")
    return meta_path


# ── Progress bar ───────────────────────────────────────────────────────────────

def format_size(b: int) -> str:
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f} GB"
    return f"{b / 1024 ** 2:.1f} MB"


def progress_bar(label: str, current: int, total: int, width: int = 30) -> str:
    pct = current / total if total else 1
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"\r  + {label}  [{bar}]  {format_size(current)} / {format_size(total)}  {pct*100:.0f}%"


def write_with_progress(zf: zipfile.ZipFile, src_path: Path, arcname: str) -> None:
    """Write a file into the zip while showing a live progress bar."""
    total = src_path.stat().st_size
    label = arcname.ljust(36)
    chunk = 1024 * 1024  # 1 MB chunks

    zi = zipfile.ZipInfo(str(arcname))
    zi.compress_type = zipfile.ZIP_DEFLATED

    written = 0
    with src_path.open("rb") as src, zf.open(zi, "w", force_zip64=True) as dest:
        while True:
            data = src.read(chunk)
            if not data:
                break
            dest.write(data)
            written += len(data)
            sys.stdout.write(progress_bar(label, written, total))
            sys.stdout.flush()

    sys.stdout.write(progress_bar(label, total, total))
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── Build date ─────────────────────────────────────────────────────────────────

def get_build_date(out_dir: Path) -> str:
    """Read build date from build.prop in the out directory."""
    for prop_path in [
        out_dir / "system" / "build.prop",
        out_dir / "build.prop",
    ]:
        if prop_path.exists():
            with open(prop_path) as f:
                for line in f:
                    if line.startswith("ro.build.date.utc="):
                        utc = int(line.strip().split("=", 1)[1])
                        return datetime.datetime.utcfromtimestamp(utc).strftime("%Y%m%d")
                    if line.startswith("ro.build.date="):
                        val = line.strip().split("=", 1)[1]
                        try:
                            dt = datetime.datetime.strptime(val, "%a %b %d %H:%M:%S %Z %Y")
                            return dt.strftime("%Y%m%d")
                        except ValueError:
                            pass

    # Fall back to system.img modification time
    fallback = out_dir / "system.img"
    if fallback.exists():
        mtime = fallback.stat().st_mtime
        return datetime.datetime.utcfromtimestamp(mtime).strftime("%Y%m%d")

    print("  [!] Could not determine build date, using today's date")
    return datetime.datetime.utcnow().strftime("%Y%m%d")


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_out_dir(out_dir: Path) -> bool:
    """Check that the out directory has all required images and they look healthy."""
    missing = []
    too_small = []

    for dest, src in PARTITION_MAP.items():
        src_path = out_dir / src
        if not src_path.exists():
            missing.append(src)
        elif src_path.stat().st_size < MIN_IMAGE_SIZE and src not in SIZE_CHECK_EXEMPT:
            too_small.append((src, src_path.stat().st_size))

    ok = True

    if missing:
        print(f"\n  [!] Missing images in {out_dir}:")
        for m in missing:
            print(f"       - {m}")
        ok = False

    if too_small:
        print(f"\n  [!] Images that look too small (build may have failed):")
        for name, size in too_small:
            print(f"       - {name}  ({size} bytes)")
        ok = False

    return ok


# ── Zip builder ────────────────────────────────────────────────────────────────

def build_zip(out_dir: Path, build_name: str, build_date: str, repo_dir: Path) -> Path:
    """Assemble the flashable zip."""

    zip_name = f"{build_name}-a55x-{build_date}.zip"
    zip_path = repo_dir / zip_name
    flasher_dir = repo_dir / "flasher"

    # Overwrite prompt
    if zip_path.exists():
        answer = input(f"\n  [!] {zip_name} already exists. Overwrite? [y/N]: ").strip().lower()
        if answer != "y":
            print("  Aborted.")
            sys.exit(0)
        zip_path.unlink()

    print(f"\n  Building: {zip_name}")
    print( "  ----------------------------------------")

    start = time.time()

    # Extract super metadata before opening zip
    tmp_dir = repo_dir / ".pack_tmp"
    tmp_dir.mkdir(exist_ok=True)
    print("\n  Extracting super LP metadata...")
    meta_path = extract_super_metadata(out_dir, tmp_dir)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as zf:

        # META-INF
        for path in sorted((flasher_dir / "META-INF").rglob("*")):
            if path.is_file():
                arcname = path.relative_to(flasher_dir)
                print(f"  + {arcname}")
                zi = zipfile.ZipInfo(str(arcname), date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zi, path.read_bytes())

        # tools
        tools_dir = flasher_dir / "tools"
        for path in sorted(tools_dir.iterdir()):
            if path.is_file() and path.name != ".gitkeep":
                arcname = Path("tools") / path.name
                print(f"  + {arcname}")
                zi = zipfile.ZipInfo(str(arcname), date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zi, path.read_bytes())

        # images — with progress bar
        print()
        # super metadata first
        write_with_progress(zf, meta_path, "images/super_metadata.img")
        # partition images
        for dest, src in PARTITION_MAP.items():
            src_path = out_dir / src
            write_with_progress(zf, src_path, f"images/{dest}")

    elapsed = time.time() - start
    zip_size = zip_path.stat().st_size

    # Clean up temp dir
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # Verify zip integrity
    print("\n  Verifying zip integrity...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad = zf.testzip()
        if bad:
            print(f"  [!] Corrupt file detected in zip: {bad}")
            zip_path.unlink()
            sys.exit(1)
    print("  Zip OK.")

    print(f"\n  ----------------------------------------")
    print(f"  Done      : {zip_name}")
    print(f"  Size      : {format_size(zip_size)}")
    print(f"  Time      : {elapsed:.0f}s")
    print(f"  Path      : {zip_path}")

    return zip_path


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Make a flashable zip"
    )
    parser.add_argument(
        "--out", "-o",
        help="Path to your build output (out/target/product/<device>/) directory"
    )
    parser.add_argument(
        "--name", "-n",
        help="Build name (e.g. lineageos, aosp, pixelos)"
    )
    args = parser.parse_args()

    repo_dir = Path(__file__).parent.resolve()

    print()
    print("Packing Script")
    print()

    # Get out directory
    if args.out:
        out_dir = Path(args.out).resolve()
    else:
        raw = input("Path to your build output (out/target/product/<device>/): ").strip()
        out_dir = Path(raw).expanduser().resolve()

    if not out_dir.exists():
        print(f"\n  [!] Directory not found: {out_dir}")
        sys.exit(1)

    print(f"\n  Out dir   : {out_dir}")

    # Validate images
    if not validate_out_dir(out_dir):
        sys.exit(1)

    # Get build name
    if args.name:
        build_name = args.name.strip().lower().replace(" ", "_")
    else:
        build_name = input("  Build name (e.g. lineageos): ").strip().lower().replace(" ", "_")

    if not build_name:
        print("  [!] Build name cannot be empty")
        sys.exit(1)

    # Get build date
    print("  Reading build date from out directory...")
    build_date = get_build_date(out_dir)
    print(f"  Build date: {build_date}")

    # Confirm tools are present
    tools_dir = repo_dir / "flasher" / "tools"
    for tool in ["awk", "lpdump"]:
        tool_path = tools_dir / tool
        if not tool_path.exists():
            print(f"\n  [!] Missing tool: flasher/tools/{tool}")
            print( "      Place the static arm64 binary there before packing.")
            sys.exit(1)

    # Build the zip
    build_zip(out_dir, build_name, build_date, repo_dir)


if __name__ == "__main__":
    main()
