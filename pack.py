#!/usr/bin/env python3

import os
import sys
import zipfile
import argparse
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


def get_build_date(out_dir: Path) -> str:
    """Read build date from build.prop or android-info.txt in the out directory."""

    # Try build.prop first
    for prop_path in [
        out_dir / "system" / "build.prop",
        out_dir / "build.prop",
    ]:
        if prop_path.exists():
            with open(prop_path) as f:
                for line in f:
                    if line.startswith("ro.build.date.utc="):
                        import datetime
                        utc = int(line.strip().split("=", 1)[1])
                        return datetime.datetime.utcfromtimestamp(utc).strftime("%Y%m%d")
                    if line.startswith("ro.build.date="):
                        # e.g. "Thu May  9 12:34:56 UTC 2025"
                        import datetime
                        val = line.strip().split("=", 1)[1]
                        try:
                            dt = datetime.datetime.strptime(val, "%a %b %d %H:%M:%S %Z %Y")
                            return dt.strftime("%Y%m%d")
                        except ValueError:
                            pass

    # Try android-info.txt
    info_path = out_dir / "android-info.txt"
    if info_path.exists():
        with open(info_path) as f:
            for line in f:
                if line.startswith("build-id="):
                    # build-id often contains date e.g. AP2A.240905.003
                    # not reliable for a date, skip
                    pass

    # Fall back to system.img modification time
    fallback = out_dir / "system.img"
    if fallback.exists():
        import datetime
        mtime = fallback.stat().st_mtime
        return datetime.datetime.utcfromtimestamp(mtime).strftime("%Y%m%d")

    print("  [!] Could not determine build date, using today's date")
    import datetime
    return datetime.datetime.utcnow().strftime("%Y%m%d")


def validate_out_dir(out_dir: Path) -> bool:
    """Check that the out directory has the required images."""
    missing = []
    for dest, src in PARTITION_MAP.items():
        if not (out_dir / src).exists():
            missing.append(src)
    if missing:
        print(f"\n  [!] Missing images in {out_dir}:")
        for m in missing:
            print(f"       - {m}")
        return False
    return True


def build_zip(out_dir: Path, build_name: str, build_date: str, repo_dir: Path) -> Path:
    """Assemble the flashable zip."""

    zip_name = f"{build_name}-a55x-{build_date}.zip"
    zip_path = repo_dir / zip_name

    flasher_dir = repo_dir / "flasher"

    print(f"\n  Building: {zip_name}")
    print( "  ----------------------------------------")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:

        # META-INF
        for path in (flasher_dir / "META-INF").rglob("*"):
            if path.is_file():
                arcname = path.relative_to(flasher_dir)
                print(f"  + {arcname}")
                zf.write(path, arcname)

        # tools (awk, lpdump — already in repo)
        tools_dir = flasher_dir / "tools"
        for path in tools_dir.iterdir():
            if path.is_file():
                arcname = Path("tools") / path.name
                print(f"  + {arcname}")
                zf.write(path, arcname)

        # images — pulled from AOSP out/
        print()
        for dest, src in PARTITION_MAP.items():
            src_path = out_dir / src
            arcname = f"images/{dest}"
            size_mb = src_path.stat().st_size / (1024 * 1024)
            print(f"  + images/{dest}  ({size_mb:.1f} MB)  <-- {src}")
            zf.write(src_path, arcname)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\n  ----------------------------------------")
    print(f"  Done: {zip_name}  ({size_mb:.1f} MB)")
    print(f"  Path: {zip_path}")

    return zip_path


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
        raw = input("Path to your build output (out/target/product/<device>/) directory: ").strip()
        out_dir = Path(raw).expanduser().resolve()

    if not out_dir.exists():
        print(f"\n  [!] Directory not found: {out_dir}")
        sys.exit(1)

    print(f"\n  Out dir  : {out_dir}")

    # Validate images exist
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
