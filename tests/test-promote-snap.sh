#!/usr/bin/env bash
# Exercise the promotion CLI against a process-level fake Snapcraft store.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
HELPER=${HELPER:-$ROOT/.github/scripts/promote-snap.sh}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export SNAP_TEST_DIR=$WORK
export PATH="$WORK:$PATH"
VERSION=1.12.3
count=0

cat > "$WORK/snapcraft" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
call="$*"
index=$(awk -v call="$call" '$0 == call { n++ } END { print n+1 }' "$SNAP_TEST_DIR/calls")
printf '%s\n' "$call" >> "$SNAP_TEST_DIR/calls"
if [[ $# == 4 && $1 == status && $2 == metanorma && $3 == --arch ]]; then
  [[ $index -le 2 ]] || index=2
  response="$SNAP_TEST_DIR/$4.$index"
elif [[ $# == 4 && $1 == release && $2 == metanorma && $4 == stable ]]; then
  response="$SNAP_TEST_DIR/release.$3"
else
  echo "Unexpected Snapcraft arguments: $*" >&2
  exit 99
fi
[[ ! -f $response ]] || cat "$response"
[[ ! -f $response.err ]] || cat "$response.err" >&2
code=0
[[ ! -f $response.code ]] || read -r code < "$response.code"
exit "$code"
FAKE
chmod +x "$WORK/snapcraft"

status() {
  local arch=$1 revision=$2 stable_version=${3:-1.12.2} stable_revision=${4:-90}
  cat <<TABLE
Track    Arch    Channel    Version    Revision    Progress
latest $arch stable $stable_version $stable_revision -
       $arch candidate $VERSION $revision -
       $arch beta ↑ ↑ -
       $arch edge ↑ ↑ -
TABLE
}
reset() {
  rm -f "$WORK"/amd64.* "$WORK"/arm64.* "$WORK"/release.*
  : > "$WORK/calls"
  status amd64 101 > "$WORK/amd64.1"
  status arm64 202 > "$WORK/arm64.1"
  status amd64 101 "$VERSION" 101 > "$WORK/amd64.2"
  status arm64 202 "$VERSION" 202 > "$WORK/arm64.2"
}
replace() {
  local file=$1 expression=$2
  sed "$expression" "$file" > "$WORK/edited"
  mv "$WORK/edited" "$file"
}
run() {
  : > "$WORK/calls"
  code=0
  bash "$HELPER" "$@" > "$WORK/out" 2> "$WORK/err" || code=$?
}
promote() { run --version "$VERSION" --architectures amd64,arm64 "$@"; }
fail() {
  echo "FAIL: $name: $*" >&2
  cat "$WORK/err" "$WORK/calls" >&2
  exit 1
}
success() { [[ $code == 0 ]] || fail "expected success (exit $code)"; }
failure() {
  [[ $code != 0 ]] || fail 'expected failure'
  [[ -s $WORK/err ]] || fail 'failure needs a diagnostic'
}
no_releases() { ! grep -q '^release ' "$WORK/calls" || fail 'preflight wrote to store'; }
preflight_failure() { promote "$@"; failure; no_releases; }
revisions() {
  local actual
  actual=$(awk '$1 == "release" { print $3 }' "$WORK/calls" | paste -sd, -)
  [[ $actual == "$1" ]] || fail "expected releases $1, got $actual"
}
diagnostic() { grep -Fq "$1" "$WORK/err" || fail "missing diagnostic: $1"; }
pass() { count=$((count + 1)); printf 'ok %d - %s\n' "$count" "$name"; }

name='both architectures resolve before release and verify afterward'
reset; promote; success
cat > "$WORK/expected" <<'CALLS'
status metanorma --arch amd64
status metanorma --arch arm64
release metanorma 101 stable
release metanorma 202 stable
status metanorma --arch amd64
status metanorma --arch arm64
CALLS
cmp -s "$WORK/expected" "$WORK/calls" || fail 'unexpected operation order'
pass

for shape in stale missing; do
  name="$shape second candidate releases nothing"
  reset
  if [[ $shape == stale ]]; then
    replace "$WORK/arm64.1" 's/candidate 1.12.3/candidate 1.12.2/'
  else
    replace "$WORK/arm64.1" '/ candidate /d'
  fi
  preflight_failure; pass
done

name='version matching rejects numeric-equivalent spelling'
reset
replace "$WORK/amd64.1" 's/candidate 1.12.3/candidate 1.00/'
replace "$WORK/arm64.1" 's/candidate 1.12.3/candidate 1.00/'
run --version 1.0 --architectures amd64,arm64
failure; no_releases; pass

name='probe failure rejects parseable stdout and reports store error'
reset; echo 1 > "$WORK/arm64.1.code"; echo 'store unavailable' > "$WORK/arm64.1.err"
preflight_failure; diagnostic 'store unavailable'; pass

name='candidate from another track cannot satisfy latest'
reset; replace "$WORK/arm64.1" 's/^latest /2.0 /'
preflight_failure; pass

name='other tracks do not interfere with latest'
reset; echo '2.0 arm64 candidate 9.9.9 999 -' >> "$WORK/arm64.1"
promote; success; revisions 101,202; pass

for shape in full risk-only; do
  name="status accepts $shape continuation rows"
  reset
  for arch in amd64 arm64; do
    for index in 1 2; do
      if [[ $shape == full ]]; then
        replace "$WORK/$arch.$index" "s/^       $arch /latest $arch /"
      else
        replace "$WORK/$arch.$index" "s/^       $arch /       /"
      fi
    done
  done
  promote; success; revisions 101,202; pass
done

for candidate in '↑ ↑ -' '- - -' "$VERSION 202 10%"; do
  name="non-promotable candidate $candidate releases nothing"
  reset; replace "$WORK/arm64.1" "s/candidate $VERSION 202 -/candidate $candidate/"
  preflight_failure; pass
done

name='unavailable stable permits a valid candidate'
reset
for arch in amd64 arm64; do replace "$WORK/$arch.1" 's/stable 1.12.2 90/stable - -/'; done
promote; success; revisions 101,202; pass

for expiry in '2026-11-01T00:00:00Z' '2026-11-01 00:00:00'; do
  name="expiry columns and branch rows preserve actual channels ($expiry)"
  reset
  for arch in amd64 arm64; do
    for index in 1 2; do
      # shellcheck disable=SC2016 # Literal sed addresses, not shell variables.

      replace "$WORK/$arch.$index" '1s/$/ Expires at/; 2,$s/$/ -/'
      echo "       $arch edge/testing 9.9.9 999 - $expiry" >> "$WORK/$arch.$index"
    done
  done
  promote; success; revisions 101,202; pass
done

name='branch candidate cannot replace actual candidate'
reset
# shellcheck disable=SC2016 # Literal sed addresses, not shell variables.

replace "$WORK/arm64.1" 's/ candidate / candidate\/testing /; 1s/$/ Expires at/; 2,$s/$/ -/'
preflight_failure; pass

for shape in header revision duplicate conflicting wrong-arch missing-field extra-field; do
  name="malformed or ambiguous actual candidate ($shape) releases nothing"
  reset
  case $shape in
    header) echo 'not a status table' > "$WORK/arm64.1" ;;
    revision) replace "$WORK/arm64.1" 's/candidate 1.12.3 202/candidate 1.12.3 invalid/' ;;
    duplicate) echo "latest arm64 candidate $VERSION 202 -" >> "$WORK/arm64.1" ;;
    conflicting) echo "latest arm64 candidate $VERSION 303 -" >> "$WORK/arm64.1" ;;
    wrong-arch) cp "$WORK/amd64.1" "$WORK/arm64.1" ;;
    missing-field) replace "$WORK/arm64.1" 's/candidate 1.12.3 202 -/candidate 1.12.3 202/' ;;
    extra-field) replace "$WORK/arm64.1" 's/candidate 1.12.3 202 -/candidate 1.12.3 202 - unexpected/' ;;
  esac
  preflight_failure; pass
done

for requested in 101 101,303 101,202,303 101,101 abc,202 101,0 ''; do
  name="manual revision set must be complete and match discovery ($requested)"
  reset; preflight_failure --revisions "$requested"; pass
done

name='manual revision order preserves architecture release order'
reset; promote --revisions 202,101; success; revisions 101,202; pass

name='shared revision releases once and verifies both architectures'
reset
for index in 1 2; do replace "$WORK/arm64.$index" 's/ 202 / 101 /g'; done
promote --revisions 101; success; revisions 101
[[ $(tail -n 2 "$WORK/calls") == $'status metanorma --arch amd64\nstatus metanorma --arch arm64' ]] || fail 'both architectures need readback'
pass

for shape in none no-arch no-version empty-version; do
  name="required inputs reject before Snapcraft ($shape)"
  reset
  case $shape in
    none) run ;;
    no-arch) run --version "$VERSION" ;;
    no-version) run --architectures amd64 ;;
    empty-version) run --version '' --architectures amd64 ;;
  esac
  failure; [[ ! -s $WORK/calls ]] || fail 'invalid input contacted store'; pass
done

for revision in 101 202; do
  name="release $revision failure stops subsequent operations"
  reset; echo 1 > "$WORK/release.$revision.code"; echo 'release refused' > "$WORK/release.$revision.err"
  promote; failure; diagnostic 'release refused'
  if [[ $revision == 101 ]]; then
    revisions 101
    (( $(wc -l < "$WORK/calls") == 3 )) || fail 'continued after first failure'
  else
    revisions 101,202; diagnostic 101
    (( $(wc -l < "$WORK/calls") == 4 )) || fail 'continued after partial release'
  fi
  pass
done

for shape in revision version; do
  name="stable readback checks frozen $shape"
  reset
  if [[ $shape == revision ]]; then
    replace "$WORK/arm64.2" 's/stable 1.12.3 202/stable 1.12.3 999/'
  else
    replace "$WORK/arm64.2" 's/stable 1.12.3 202/stable 1.12.2 202/'
  fi
  promote; failure; revisions 101,202; diagnostic 101; diagnostic 202; diagnostic amd64; pass
done

name='candidate change after release preserves frozen revisions'
reset
for arch in amd64 arm64; do
  replace "$WORK/$arch.2" 's/candidate 1.12.3 [0-9]* /candidate 1.12.4 999 /'
done
promote; success; revisions 101,202; pass

name='readback probe failure reports completed releases'
reset; echo 1 > "$WORK/arm64.2.code"; echo 'readback unavailable' > "$WORK/arm64.2.err"
promote; failure; revisions 101,202; diagnostic 'readback unavailable'; diagnostic 101; diagnostic 202; pass

printf 'PASS: %d promotion regressions\n' "$count"
