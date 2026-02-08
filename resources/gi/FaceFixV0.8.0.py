#!/usr/bin/env python3
import os
import re
import shutil
import sys
import platform
import concurrent.futures

PRODUCT_NAME = "FaceFix"
VERSION = "0.8.0"
AUTHOR = "Sanddino (modified)"

# -----------------------------------------------------------
# Color Support (safe)
# -----------------------------------------------------------
def supports_color():
    if sys.platform.startswith('win'):
        return os.getenv('ANSICON') or 'WT_SESSION' in os.environ or platform.release() >= '10'
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

COLOR_ENABLED = supports_color()
RED = "\033[91m" if COLOR_ENABLED else ""
GREEN = "\033[92m" if COLOR_ENABLED else ""
RESET = "\033[0m" if COLOR_ENABLED else ""

# -----------------------------------------------------------
# Safe Wait for Exit
# -----------------------------------------------------------
def wait_for_exit():
    """Pause safely before exiting without using console-attaching APIs."""
    try:
        input("\nPress ENTER to exit...")
    except Exception:
        import time
        print("\nExiting...")
        time.sleep(2)

# -----------------------------------------------------------
# Section-aware replace engine
# -----------------------------------------------------------
SECTION_HEADER_RE = re.compile(r'^\s*\[(.+?)\]\s*$', re.IGNORECASE)
PS_T_RE = re.compile(r'^(\s*)(ps-t0)\s*=\s*(.+?)(\s*)$', re.IGNORECASE)
RUN_FACE_RE = re.compile(r'^\s*run\s*=\s*(CommandList\w*Face\w*)\s*$', re.IGNORECASE)

def split_into_sections(text):
    """
    Split file text into a list of sections: each is a tuple (header, lines),
    where header is the section name (string) or None for preamble, and lines is a list of the section's lines.
    """
    lines = text.splitlines(True)
    sections = []
    current_header = None
    current_lines = []

    for line in lines:
        m = SECTION_HEADER_RE.match(line)
        if m:
            # flush previous
            if current_lines or current_header is not None:
                sections.append((current_header, current_lines))
            current_header = m.group(1).strip()
            current_lines = [line]  # keep the header line at start of section's lines
        else:
            current_lines.append(line)

    # flush last
    if current_lines or current_header is not None:
        sections.append((current_header, current_lines))
    return sections

def is_face_section_by_header(header):
    """Return True if the section header indicates a face-related section by name."""
    if not header:
        return False
    h = header.lower()
    if 'face' not in h:
        return False
    # allow CommandList*Face* and TextureOverride*Face*
    if h.startswith('commandlist') or h.startswith('textureoverride'):
        return True
    return False

def section_has_run_face(lines):
    """Return True if the given section lines contain a run = CommandList*Face* (case-insensitive)."""
    for line in lines:
        if RUN_FACE_RE.match(line):
            return True
    return False

def process_sections_and_replace(sections):
    """
    For each section determine if it's allowed (face-related) and perform replacements
    only for allowed sections. Return (changed_lines_info, new_text).
    changed_lines_info: list of tuples (section_header, line_number_in_file, old_line, new_line)
    """
    changed = []
    # rebuild whole file with modifications
    out_lines = []
    # keep a running line counter for file-level reporting (1-indexed)
    file_line_no = 0

    for header, lines in sections:
        # Determine if this section is face-related:
        allowed = is_face_section_by_header(header) or section_has_run_face(lines)
        for ln in lines:
            file_line_no += 1
            if allowed:
                m = PS_T_RE.match(ln)
                if m:
                    indent, ps_token, rhs, trailing = m.groups()
                    # Preserve RHS whitespace as-is except strip newline because we'll add it from ln
                    # Construct replacement
                    # Keep the same trailing whitespace (but we keep the newline as before)
                    # Find newline at end if any
                    newline = ''
                    if ln.endswith('\r\n'):
                        newline = '\r\n'
                    elif ln.endswith('\n'):
                        newline = '\n'
                    # Build new line with same indent and trailing spaces before newline
                    new_line = f"{indent}this = {rhs}{trailing}{newline}"
                    out_lines.append(new_line)
                    changed.append((header, file_line_no, ln.rstrip('\r\n'), new_line.rstrip('\r\n')))
                    continue
            # default: append original
            out_lines.append(ln)

    new_text = ''.join(out_lines)
    return changed, new_text

def smart_replace(file_path, make_backup=True, preview=True, apply_changes=False, process_disabled=False):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if "DISABLED" in content and not process_disabled:
            return False, []

        sections = split_into_sections(content)
        changed_lines, new_text = process_sections_and_replace(sections)

        if not changed_lines:
            return False, []

        if preview:
            print(f"\nPreview changes for '{os.path.relpath(file_path)}':")
            for header, lineno, old, new in changed_lines:
                # Highlight ps-t token in old, and 'this' in new
                highlighted_old = re.sub(r'(ps-t\d+)', lambda m: f"{RED}{m.group(1)}{RESET}", old, flags=re.IGNORECASE)
                highlighted_new = new.replace("this", f"{GREEN}this{RESET}", 1)
                header_display = f" [{header}]" if header else ""
                print(f"  Line {lineno}{header_display}: '{highlighted_old}' → '{highlighted_new}'")

        if apply_changes:
            if make_backup:
                backup_path = f"{os.path.splitext(file_path)[0]}_backup.bak"
                shutil.copyfile(file_path, backup_path)
                print(f"💾 Backup saved as '{os.path.relpath(backup_path)}'")

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_text)

        return True, changed_lines

    except Exception as e:
        print(f"❌ Error processing '{file_path}': {e}")
        return False, []

# -----------------------------------------------------------
# Exclusion system
# -----------------------------------------------------------
def get_exclusion_patterns():
    patterns = ['*_backup.bak']
    print("\nEnter folder names to exclude (ENTER to finish):")
    while True:
        folder_name = input("Folder to exclude: ").strip()
        if not folder_name:
            break
        patterns.append(f"*{folder_name}*")
    return patterns

def is_disabled_file(file_path):
    try:
        basename = os.path.basename(file_path).lower()
        if "disabled" in basename:
            return True

        with open(file_path, 'r', encoding='utf-8') as f:
            return 'DISABLED' in f.read()
    except:
        return False

def should_exclude(path, patterns):
    path_norm = path.replace('\\', '/').lower()
    return any(pattern.strip('*').lower() in path_norm for pattern in patterns)

# -----------------------------------------------------------
# Main Logic
# -----------------------------------------------------------
def main():
    print(f"{PRODUCT_NAME} {VERSION} — by {AUTHOR}\n")

    folder = os.getcwd()
    print(f"Working directory: {folder}")

    process_disabled = input("Process disabled files? (Y/N): ").strip().lower() == 'y'
    scan_subfolders = input("Scan subfolders? (Y/N): ").strip().lower() == 'y'
    make_backup = input("Create backups before modifying? (Y/N): ").strip().lower() == 'y'

    print("\nYour choices:")
    print(f"• Process disabled: {'Yes' if process_disabled else 'No'}")
    print(f"• Scan subfolders: {'Yes' if scan_subfolders else 'No'}")
    print(f"• Create backups: {'Yes' if make_backup else 'No'}")

    if input("\nConfirm? (Y/N): ").strip().lower() != 'y':
        print("Cancelled.")
        wait_for_exit()
        return

    exclusion_patterns = get_exclusion_patterns()

    # Collect files
    ini_files = []
    for dirpath, _, filenames in os.walk(folder):
        if should_exclude(dirpath, exclusion_patterns):
            continue

        for f in filenames:
            if f.lower().endswith(".ini"):
                file_path = os.path.join(dirpath, f)
                if not is_disabled_file(file_path) or process_disabled:
                    ini_files.append(file_path)

        if not scan_subfolders:
            break

    if not ini_files:
        print("No .ini files found.")
        wait_for_exit()
        return

    print(f"\nFound {len(ini_files)} files. Previewing...")

    files_to_modify = []
    previews = {}

    for ini_file in ini_files:
        changed, changes_info = smart_replace(ini_file, make_backup, preview=True, apply_changes=False, process_disabled=process_disabled)
        if changed:
            files_to_modify.append(ini_file)
            previews[ini_file] = changes_info

    if not files_to_modify:
        print("\nNo changes needed.")
        wait_for_exit()
        return

    if input(f"\nApply changes to {len(files_to_modify)} file(s)? (Y/N): ").strip().lower() != 'y':
        print("Cancelled.")
        wait_for_exit()
        return

    max_workers = min(32, (os.cpu_count() or 1) + 4)

    # Apply changes using threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for ini_file in files_to_modify:
            futures.append(
                executor.submit(
                    smart_replace,
                    ini_file,
                    make_backup,
                    False,       # no preview during actual write
                    True,        # apply changes
                    process_disabled
                )
            )
        # Optionally wait for completion
        for f in futures:
            f.result()

    print("\nDone!")
    wait_for_exit()

# -----------------------------------------------------------
if __name__ == "__main__":
    main()
