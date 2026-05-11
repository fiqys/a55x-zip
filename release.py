#!/usr/bin/env python3
"""

Usage:
    python release.py
    python release.py --draft
"""

import os
import sys
import json
import argparse
import mimetypes
import subprocess
import tempfile
import getpass
from pathlib import Path
from urllib import request, error

API = "https://api.github.com"

CHANGELOG_TEMPLATE = """\
# Changelog
# Lines starting with # are ignored.
# Write your changelog below — markdown is supported.

## Changes
- 

## Notes
- 
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def gh_request(method, url, token, data=None):
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = request.Request(url, data=body, headers=h, method=method)
    try:
        with request.urlopen(req) as resp:
            return json.loads(resp.read())
    except error.HTTPError as e:
        print(f"\n  [!] GitHub API error {e.code}: {e.read().decode()}")
        sys.exit(1)


def upload_asset(upload_url, token, file_path):
    base_url = upload_url.split("{")[0]
    url = f"{base_url}?name={file_path.name}"
    content_type, _ = mimetypes.guess_type(str(file_path))
    content_type = content_type or "application/octet-stream"

    cmd = [
        "curl", "-#",
        "-X", "POST",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2022-11-28",
        "-H", f"Content-Type: {content_type}",
        "--data-binary", f"@{file_path}",
        "-o", "/tmp/upload_response.json",
        url,
    ]

    result = subprocess.call(cmd)
    print()
    if result != 0:
        print(f"\n  [!] Upload failed (curl exit code {result})")
        sys.exit(1)

    try:
        with open("/tmp/upload_response.json") as f:
            raw = f.read()
        data = json.loads(raw)
        if "browser_download_url" not in data:
            print(f"\n  [!] Upload error from GitHub:\n      {raw}")
            sys.exit(1)
        return data
    except Exception as e:
        print(f"\n  [!] Could not parse upload response: {e}")
        sys.exit(1)


def open_editor(initial_text):
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        for candidate in ["nano", "vim", "vi", "notepad"]:
            if subprocess.call(
                ["which", candidate], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ) == 0:
                editor = candidate
                break
        else:
            editor = "notepad"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="changelog_", delete=False
    ) as tmp:
        tmp.write(initial_text)
        tmp_path = tmp.name

    try:
        subprocess.call([editor, tmp_path])
        with open(tmp_path) as f:
            content = f.read()
    finally:
        os.unlink(tmp_path)

    lines = [l for l in content.splitlines() if not l.startswith("#")]
    return "\n".join(lines).strip()


def prompt_file(label, required=True):
    while True:
        hint = "" if required else " (Enter to skip)"
        raw = input(f"  {label}{hint}: ").strip()
        if not raw:
            if not required:
                return None
            print("  [!] Path cannot be empty")
            continue
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            print(f"  [!] File not found: {path}")
            continue
        return path


def parse_zip_name(zip_path):
    stem = zip_path.stem
    parts = stem.split("-")
    if len(parts) >= 3:
        return parts[0], "-".join(parts[1:-1]), parts[-1]
    return stem, "a55x", "unknown"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Upload ROM release to GitHub")
    parser.add_argument("--draft",      action="store_true", help="Create as draft")
    parser.add_argument("--prerelease", action="store_true", help="Mark as pre-release")
    parser.add_argument("--token", "-t", help="GitHub token")
    parser.add_argument("--repo",  "-r", help="owner/repo")
    args = parser.parse_args()

    print()
    print("Release Script")
    print()

    # ── Token + Repo ──────────────────────────────────────────────────────────

    repo = args.repo or os.environ.get("GITHUB_REPO")
    if not repo:
        repo = input("  Secret target repo (account/repo): ").strip()
    if not repo or "/" not in repo:
        print("  [!] Invalid repo format. Must be 'owner/repo'.")
        sys.exit(1)

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        # getpass hides the input while typing/pasting
        token = getpass.getpass("  GitHub PAT : ").strip()
    if not token:
        print("  [!] Token cannot be empty")
        sys.exit(1)
        
    print()

    # ── Google Drive? ─────────────────────────────────────────────────────────

    use_drive = input("  Use Google Drive for ROM zip? [y/N]: ").strip().lower() == "y"
    print()

    # ── ROM zip ───────────────────────────────────────────────────────────────

    zip_path = prompt_file("Path to ROM zip")
    size_mb  = zip_path.stat().st_size / 1024 / 1024
    print(f"  ROM    : {zip_path.name}  ({size_mb:.1f} MB)")

    build_name, device, date_str = parse_zip_name(zip_path)

    # ── Drive link ────────────────────────────────────────────────────────────

    drive_link = None
    if use_drive:
        drive_link = input("  Google Drive link: ").strip()
        if not drive_link:
            print("  [!] Drive link cannot be empty if you chose Drive mode")
            sys.exit(1)
        print(f"  Drive  : {drive_link}")

    # ── vendor_boot.img ───────────────────────────────────────────────────────

    print()
    recovery_path = prompt_file("Path to vendor_boot.img", required=False)
    if recovery_path:
        rec_mb = recovery_path.stat().st_size / 1024 / 1024
        print(f"  Recovery: {recovery_path.name}  ({rec_mb:.1f} MB)")
    else:
        print("  Recovery: skipped")

    # ── Tag ───────────────────────────────────────────────────────────────────

    default_tag = f"{build_name}-{date_str}"
    raw = input(f"\n  Release tag [{default_tag}]: ").strip()
    tag = raw or default_tag

    release_title = f"{build_name.title()} for {device} — {date_str}"

    # ── Changelog ─────────────────────────────────────────────────────────────

    print()
    print("  Opening editor for changelog (save and close when done)...")
    input("  Press Enter to open...")
    notes = open_editor(CHANGELOG_TEMPLATE)
    if not notes:
        notes = f"Build for {device}.\nBuild date: {date_str}"
        print("  (No changelog entered, using default)")

    # Embed Drive link invisibly in notes for the website to detect
    if drive_link:
        notes += f"\n\nDRIVE_LINK: {drive_link}"

    print()
    print("  Changelog preview:")
    print("  " + "\n  ".join(notes.splitlines()[:6]))
    if notes.count("\n") > 6:
        print("  ...")

    # ── Summary ───────────────────────────────────────────────────────────────

    # Files that will actually be uploaded to GitHub
    files_to_upload = []
    if not use_drive:
        files_to_upload.append(zip_path)
    if recovery_path:
        files_to_upload.append(recovery_path)

    print()
    print("  ── Summary ──────────────────────────────────────")
    print(f"  Repo   : {repo}")
    print(f"  Tag    : {tag}")
    print(f"  Title  : {release_title}")
    print(f"  Draft  : {args.draft}")
    if use_drive:
        print(f"  ROM    : Google Drive ({drive_link})")
    for f in files_to_upload:
        mb = f.stat().st_size / 1024 / 1024
        print(f"  Upload : {f.name}  ({mb:.1f} MB)")
    print("  ─────────────────────────────────────────────────")
    if input("\n  Proceed? [Y/n]: ").strip().lower() == "n":
        print("  Aborted."); sys.exit(0)

    # ── Create release ────────────────────────────────────────────────────────

    print("\n  Creating release...")
    release = gh_request("POST", f"{API}/repos/{repo}/releases", token, data={
        "tag_name":    tag,
        "name":        release_title,
        "body":        notes,
        "draft":       args.draft,
        "prerelease":  args.prerelease,
        "make_latest": "true",
    })
    print(f"  Release: {release['html_url']}")

    # ── Upload files ──────────────────────────────────────────────────────────

    for f in files_to_upload:
        print(f"\n  Uploading {f.name}...")
        asset = upload_asset(release["upload_url"], token, f)
        print(f"  Done   : {asset['browser_download_url']}")

    print()
    print(f"  All done: {release['html_url']}")
    if args.draft:
        print("  (Draft — go to GitHub to publish)")
    print()


if __name__ == "__main__":
    main()
