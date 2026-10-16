#!/usr/bin/env python3
"""Promote a complete, version-matched candidate set and verify stable."""

import argparse
from datetime import datetime
import re
import subprocess
import sys


class PromotionError(Exception):
    """A failed precondition, store operation, or verification."""


def comma_list(value):
    items = [item.strip() for item in value.split(",")]
    if not all(items) or len(set(items)) != len(items):
        raise argparse.ArgumentTypeError("expected a nonempty comma list without duplicates")
    return items


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="exact candidate version to promote")
    parser.add_argument("--architectures", required=True, type=comma_list)
    parser.add_argument("--revisions", type=comma_list, help="optional complete candidate revision set")
    args = parser.parse_args(argv)
    if not args.version.strip() or any(char.isspace() for char in args.version):
        parser.error("--version must be a nonempty version without whitespace")
    if any(not re.fullmatch(r"[a-z][a-z0-9]*", arch) for arch in args.architectures):
        parser.error("--architectures must contain architecture names")
    if args.revisions is not None and any(
        not re.fullmatch(r"[1-9][0-9]*", revision) for revision in args.revisions
    ):
        parser.error("--revisions must contain positive decimal revision numbers")
    return args


def snapcraft(*args):
    command = ["snapcraft", *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PromotionError(f"{' '.join(command)} failed: {error}") from error
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip()
        raise PromotionError(
            f"{' '.join(command)} failed (exit {result.returncode}): {details}"
        )
    return result.stdout


def parse_status(output):
    """Read explicit rows, carrying only the table's omitted track/arch cells."""
    lines = [line for line in output.splitlines() if line.strip()]
    header = [
        "Track", "Arch", "Channel", "Version", "Revision", "Progress"
    ]
    if not lines or lines[0].split() not in (header, header + ["Expires", "at"]):
        raise PromotionError("malformed snapcraft status: expected status table header")
    has_expiry = lines[0].split() == header + ["Expires", "at"]

    rows = {}
    track = architecture = None
    channel_pattern = re.compile(r"(?:stable|candidate|beta|edge)(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?")
    for line in lines[1:]:
        cells = line.split()
        if has_expiry:
            expiry = cells.pop()
            if cells and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", cells[-1]):
                expiry = cells.pop() + "T" + expiry
            if expiry != "-":
                try:
                    if "T" not in expiry:
                        raise ValueError("expected timestamp")
                    datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                except ValueError as error:
                    raise PromotionError(f"malformed status expiry: {expiry!r}") from error
        indented = line[0].isspace()
        if len(cells) == 6 and channel_pattern.fullmatch(cells[2]):
            track, architecture, risk, version, revision, progress = cells
        elif len(cells) == 5 and channel_pattern.fullmatch(cells[1]) and indented and track:
            architecture, risk, version, revision, progress = cells
        elif len(cells) == 4 and channel_pattern.fullmatch(cells[0]) and indented and track and architecture:
            risk, version, revision, progress = cells
        else:
            raise PromotionError(f"malformed or ambiguous snapcraft status row: {line!r}")
        key = (track, architecture, risk)
        if key in rows:
            raise PromotionError(f"duplicate snapcraft status row for {'/'.join(key)}")
        if "/" in risk and (
            version in {"-", "↑", "?"}
            or not re.fullmatch(r"[1-9][0-9]*", revision)
            or progress != "-"
        ):
            raise PromotionError(f"malformed branch status row: {line!r}")
        rows[key] = (version, revision, progress)
    return rows


def channel_revision(architecture, risk, version):
    rows = parse_status(snapcraft("status", "metanorma", "--arch", architecture))
    key = ("latest", architecture, risk)
    if key not in rows:
        raise PromotionError(f"missing actual latest/{risk} row for {architecture}")
    actual_version, revision, progress = rows[key]
    if (
        actual_version != version
        or not re.fullmatch(r"[1-9][0-9]*", revision)
        or progress != "-"
    ):
        raise PromotionError(
            f"latest/{risk} for {architecture} must be an actual release of version "
            f"{version!r}; found version={actual_version!r}, revision={revision!r}, "
            f"progress={progress!r}"
        )
    return revision


def promote(args):
    released = []
    verified = []
    release_in_progress = False
    try:
        # Freeze and validate every architecture before making any store change.
        expected = tuple(
            (arch, channel_revision(arch, "candidate", args.version))
            for arch in args.architectures
        )
        revisions = list(dict.fromkeys(revision for _, revision in expected))
        if args.revisions is not None and set(args.revisions) != set(revisions):
            raise PromotionError(
                "manual revisions must match the complete candidate revision set: "
                + ",".join(revisions)
            )
        print(
            "Frozen candidate set: "
            + ", ".join(f"{arch}={revision}" for arch, revision in expected),
            flush=True,
        )
        for revision in revisions:
            print(f"Releasing revision {revision} to stable", flush=True)
            release_in_progress = True
            snapcraft("release", "metanorma", revision, "stable")
            release_in_progress = False
            released.append(revision)

        failures = []
        for architecture, revision in expected:
            try:
                stable_revision = channel_revision(architecture, "stable", args.version)
                if stable_revision != revision:
                    raise PromotionError(
                        f"stable verification for {architecture}: expected revision "
                        f"{revision}, found {stable_revision}"
                    )
                verified.append(architecture)
            except PromotionError as error:
                failures.append(str(error))
        if failures:
            raise PromotionError("; ".join(failures))
    except PromotionError as error:
        print(f"Promotion failed: {error}", file=sys.stderr)
        print(
            "Completed releases: " + (",".join(released) or "none")
            + "; verified stable architectures: " + (",".join(verified) or "none"),
            file=sys.stderr,
        )
        if release_in_progress:
            print(
                "The failed release command may still have changed the store; "
                "inspect status before retrying.",
                file=sys.stderr,
            )
        return 1
    print(f"Verified version {args.version} on stable for {','.join(verified)}", flush=True)
    return 0


def main(argv=None):
    return promote(arguments(argv))


if __name__ == "__main__":
    sys.exit(main())
