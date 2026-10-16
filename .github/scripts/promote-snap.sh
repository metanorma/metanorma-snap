#!/bin/bash
# Promote a complete candidate set, then verify the frozen versions and revisions.
set -euo pipefail

usage() {
    echo "Usage: $0 --version VERSION --architectures ARCH[,ARCH] [--revisions REV[,REV]]" >&2
}

fail() {
    echo "Promotion failed: $*" >&2
    echo "Completed releases: ${released:-none}; verified stable architectures: ${verified:-none}" >&2
    if [[ $release_in_progress == true ]]; then
        echo "The failed release command may still have changed the store; inspect status before retrying." >&2
    fi
    exit 1
}

# Validate lists before any store request. Whitespace around list items is allowed.
parse_list() {
    local input=$1 pattern=$2 item seen=,
    list=()
    while :; do
        item=${input%%,*}
        item="${item#"${item%%[![:space:]]*}"}"
        item="${item%"${item##*[![:space:]]}"}"
        [[ $item =~ $pattern && $seen != *",$item,"* ]] || return 1
        list+=("$item")
        seen+="$item,"
        [[ $input == *,* ]] || break
        input=${input#*,}
    done
}

version='' architectures_input='' revisions_input='' manual_revisions=false
released='' verified='' release_in_progress=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --version|--architectures|--revisions)
            [[ $# -ge 2 ]] || { usage; fail "missing value for $1"; }
            case $1 in
                --version) version=$2 ;;
                --architectures) architectures_input=$2 ;;
                --revisions) revisions_input=$2; manual_revisions=true ;;
            esac
            shift 2 ;;
        --help) usage; exit 0 ;;
        *) usage; fail "unknown argument: $1" ;;
    esac
done
[[ -n $version && $version != *[[:space:]]* ]] || fail "--version must be nonempty and contain no whitespace"
parse_list "$architectures_input" '^[a-z][a-z0-9]*$' || fail "--architectures must be a nonempty list of unique architecture names"
architectures=("${list[@]}")
if [[ $manual_revisions == true ]]; then
    parse_list "$revisions_input" '^[1-9][0-9]*$' || fail "--revisions must be a nonempty list of unique positive decimal revisions"
    manual=("${list[@]}")
fi

if command -v timeout >/dev/null 2>&1; then
    timeout_command=timeout
elif command -v gtimeout >/dev/null 2>&1; then
    timeout_command=gtimeout
else
    fail "timeout (or gtimeout) is required to bound Snapcraft requests"
fi
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

snapcraft_command() {
    local output result
    if output=$("$timeout_command" --kill-after=10 120 snapcraft "$@" 2>"$workdir/stderr"); then
        printf '%s\n' "$output"
    else
        result=$?
        echo "snapcraft $* failed (exit $result): $(cat "$workdir/stderr") $output" >&2
        return 1
    fi
}

channel_revision() {
    local architecture=$1 channel=$2 output
    output=$(snapcraft_command status metanorma --arch "$architecture") || return 1
    EXPECTED_VERSION=$version awk -v arch="$architecture" -v risk="$channel" '
        function invalid(message) {
            print "malformed snapcraft status: " message > "/dev/stderr"
            failed = 1
            exit 1
        }
        function channel(value) {
            return value ~ /^(stable|candidate|beta|edge)(\/[A-Za-z0-9][A-Za-z0-9._-]*)?$/
        }
        # Branch expiry is irrelevant to base channels; only its column shape matters.
        function columns(start, fields) {
            fields = start + 3
            if (!expiry) return NF == fields
            if ($start !~ /\//) return NF == fields + 1 && $(fields + 1) == "-"
            return NF == fields + 1 || (NF == fields + 2 && $(fields + 1) ~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/)
        }
        NF == 0 { next }
        !header {
            if ($0 !~ /^[[:space:]]*Track[[:space:]]+Arch[[:space:]]+Channel[[:space:]]+Version[[:space:]]+Revision[[:space:]]+Progress([[:space:]]+Expires[[:space:]]+at)?[[:space:]]*$/)
                invalid("expected status table header")
            expiry = (NF == 8)
            header = 1
            next
        }
        {
            indented = ($0 ~ /^[[:space:]]/)
            if (channel($3) && columns(3)) {
                track = $1; architecture = $2; start = 3
            } else if (indented && track != "" && channel($2) && columns(2)) {
                architecture = $1; start = 2
            } else if (indented && track != "" && architecture != "" && channel($1) && columns(1)) {
                start = 1
            } else invalid("ambiguous row: " $0)
            name = $start
            key = track SUBSEP architecture SUBSEP name
            if (seen[key]++) invalid("duplicate row for " track "/" architecture "/" name)
            if (name ~ /\// && ($(start + 1) ~ /^(-|↑|\?)$/ || $(start + 2) !~ /^[1-9][0-9]*$/ || $(start + 3) != "-"))
                invalid("invalid branch row: " $0)
            if (track == "latest" && architecture == arch && name == risk) {
                found = 1
                if (("v" $(start + 1)) != ("v" ENVIRON["EXPECTED_VERSION"]) || $(start + 2) !~ /^[1-9][0-9]*$/ || $(start + 3) != "-")
                    invalid("latest/" risk " for " arch " must be an actual release of version " ENVIRON["EXPECTED_VERSION"] ": " $0)
                revision = $(start + 2)
            }
        }
        END {
            if (failed) exit 1
            if (!header || !found) {
                print "missing actual latest/" risk " row for " arch > "/dev/stderr"
                exit 1
            }
            print revision
        }
    ' <<< "$output"
}

# Freeze every architecture before writing to the store; a shared revision is released once.
expected=() revisions=() seen=,
for architecture in "${architectures[@]}"; do
    revision=$(channel_revision "$architecture" candidate) || fail "candidate preflight failed for $architecture"
    expected+=("$revision")
    if [[ $seen != *",$revision,"* ]]; then
        revisions+=("$revision")
        seen+="$revision,"
    fi
done
if [[ $manual_revisions == true ]]; then
    [[ ${#manual[@]} -eq ${#revisions[@]} ]] || fail "manual revisions must match the complete candidate revision set: ${revisions[*]}"
    for revision in "${manual[@]}"; do
        [[ $seen == *",$revision,"* ]] || fail "manual revisions must match the complete candidate revision set: ${revisions[*]}"
    done
fi
printf 'Frozen candidate set:'
for ((index = 0; index < ${#architectures[@]}; index++)); do
    printf ' %s=%s' "${architectures[index]}" "${expected[index]}"
done
printf '\n'
for revision in "${revisions[@]}"; do
    echo "Releasing revision $revision to stable"
    release_in_progress=true
    snapcraft_command release metanorma "$revision" stable >/dev/null || fail "release failed for revision $revision"
    release_in_progress=false
    released+="${released:+,}$revision"
done

verification_failed=false
for ((index = 0; index < ${#architectures[@]}; index++)); do
    architecture=${architectures[index]}
    if revision=$(channel_revision "$architecture" stable); then
        if [[ $revision == "${expected[index]}" ]]; then
            verified+="${verified:+,}$architecture"
            continue
        fi
        echo "stable verification for $architecture: expected revision ${expected[index]}, found $revision" >&2
    fi
    verification_failed=true
done
[[ $verification_failed == false ]] || fail "stable verification failed"
echo "Verified version $version on stable for $verified"
