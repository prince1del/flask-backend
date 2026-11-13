#!/usr/bin/env bash
# Publish a built NEXORA Android APK as the OTA update every phone will be offered.
#
#   bash deploy/publish_android_apk.sh "What changed in this version"
#
# Build first (Android Studio or ./gradlew assembleDebug in the Nexora20 project).
# Env overrides: APK_DIR, NEXORA_SERVER (default 3.7.170.77), SSH_KEY, SSH_USER.
set -euo pipefail

NOTES="${1:-}"
APK_DIR="${APK_DIR:-$HOME/AndroidStudioProjects/Nexora20/app/build/outputs/apk/debug}"
SERVER="${NEXORA_SERVER:-3.7.170.77}"
SSH_USER="${SSH_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/nexora_server}"
REMOTE_DIR=/var/data/app-releases/android

META="$APK_DIR/output-metadata.json"
[ -f "$META" ] || { echo "No build found at $APK_DIR (run assembleDebug first)"; exit 1; }

PY=$(command -v python || command -v python3 || command -v py)
read -r CODE NAME FILE < <("$PY" -c '
import json, sys
e = json.load(open(sys.argv[1], encoding="utf-8"))["elements"][0]
print(e["versionCode"], e["versionName"], e["outputFile"])
' "$META" | tr -d '\r')
APK="$APK_DIR/$FILE"
[ -f "$APK" ] || { echo "APK missing: $APK"; exit 1; }

SHA=$(sha256sum "$APK" | cut -d' ' -f1)
SIZE=$(wc -c < "$APK" | tr -d ' ')
REMOTE_APK="nexora-$CODE.apk"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
NOTES_JSON=$(printf '%s' "$NOTES" | "$PY" -c 'import json,sys; print(json.dumps(sys.stdin.read()))' | tr -d '\r')

echo "Publishing NEXORA $NAME (code $CODE, $SIZE bytes, sha256 ${SHA:0:12}…) to $SERVER"

SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes "$SSH_USER@$SERVER")
scp -i "$SSH_KEY" -q "$APK" "$SSH_USER@$SERVER:/tmp/$REMOTE_APK"

"${SSH[@]}" "set -e
  sudo mkdir -p $REMOTE_DIR
  sudo install -m 644 /tmp/$REMOTE_APK $REMOTE_DIR/$REMOTE_APK && rm -f /tmp/$REMOTE_APK
  echo \"\$(sha256sum $REMOTE_DIR/$REMOTE_APK | cut -d' ' -f1)\" | grep -qx '$SHA' || { echo 'checksum mismatch on server'; exit 1; }
  cat > /tmp/latest.json <<JSON
{\"version_code\": $CODE, \"version_name\": \"$NAME\", \"notes\": $NOTES_JSON,
 \"sha256\": \"$SHA\", \"size_bytes\": $SIZE, \"file\": \"$REMOTE_APK\", \"published_at\": \"$STAMP\"}
JSON
  sudo install -m 644 /tmp/latest.json $REMOTE_DIR/latest.json && rm -f /tmp/latest.json
  # history.json: every build's notes, newest first (Settings -> Updates on the phone)
  sudo python3 - $REMOTE_DIR <<'PYH'
import json, sys, pathlib
d = pathlib.Path(sys.argv[1]); h = d / 'history.json'
latest = json.loads((d / 'latest.json').read_text())
items = json.loads(h.read_text()) if h.is_file() else []
items = [r for r in items if r.get('version_code') != latest['version_code']]
items.insert(0, {k: latest.get(k) for k in ('version_code', 'version_name', 'notes', 'published_at')})
h.write_text(json.dumps(items[:200], ensure_ascii=False, indent=1))
PYH
  # keep the 3 newest APKs (rollback = point latest.json at an older one)
  ls -1t $REMOTE_DIR/nexora-*.apk | tail -n +4 | xargs -r sudo rm -f
  sudo chown -R nexora:nexora /var/data/app-releases
  echo published: \$(cat $REMOTE_DIR/latest.json | tr -d '\n')
"
