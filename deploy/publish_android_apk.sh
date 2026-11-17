#!/usr/bin/env bash
# Publish a built NEXORA Android APK as the OTA update every phone will be offered.
#
#   bash deploy/publish_android_apk.sh "[business] New bill screen; [executive] Faster Hisaab; [all] Fingerprint lock"
#
# Points are split on ';'. Tag each with the UI it belongs to — [business], [executive] or [all]
# (app lock, Ask Nexora, updates, watch…). No tag = all UIs. Each UI's Settings → Updates shows only its own
# points plus [all]; a build with nothing for a UI does not pop the update window there.
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
LOCAL_LATEST=$(mktemp)
"$PY" -c '
import json, sys
notes = sys.argv[1]
TAGS = {"[business]": "business", "[b]": "business", "[executive]": "executive", "[e]": "executive",
        "[all]": "all", "[a]": "all", "[global]": "all"}
items = []
for part in notes.split(";"):
    text, ui = part.strip(), "all"
    for tag, val in TAGS.items():
        if text.lower().startswith(tag):
            text, ui = text[len(tag):].strip(), val
            break
    if text:
        items.append({"ui": ui, "text": text})
notes = "; ".join(i["text"] for i in items)
meta = {
  "items": items,
  "version_code": int(sys.argv[2]),
  "version_name": sys.argv[3],
  "notes": notes,
  "sha256": sys.argv[4],
  "size_bytes": int(sys.argv[5]),
  "file": sys.argv[6],
  "published_at": sys.argv[7],
}
json.dump(meta, open(sys.argv[8], "w", encoding="utf-8"), ensure_ascii=False)
' "$NOTES" "$CODE" "$NAME" "$SHA" "$SIZE" "$REMOTE_APK" "$STAMP" "$LOCAL_LATEST"

echo "Publishing NEXORA $NAME (code $CODE, $SIZE bytes, sha256 ${SHA:0:12}…) to $SERVER"

SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes "$SSH_USER@$SERVER")
# Upload in 8 MB pieces with retries: a slow or broken connection only resends the piece that failed,
# and a re-run skips pieces already on the server (same size).
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes "$SSH_USER@$SERVER")
PARTS_LOCAL=$(mktemp -d)
PARTS_REMOTE="/tmp/nxapk_$CODE"
split -b 8m -d -a 3 "$APK" "$PARTS_LOCAL/part_"
"${SSH[@]}" "mkdir -p $PARTS_REMOTE"
for part in "$PARTS_LOCAL"/part_*; do
  name=$(basename "$part"); size=$(wc -c < "$part" | tr -d ' ')
  have=$("${SSH[@]}" "stat -c %s $PARTS_REMOTE/$name 2>/dev/null || echo 0" | tr -d '\r')
  if [ "$have" = "$size" ]; then echo "  $name already there"; continue; fi
  ok=0
  for try in 1 2 3 4 5; do
    if scp -i "$SSH_KEY" -q -o ServerAliveInterval=15 -o ServerAliveCountMax=4 "$part" "$SSH_USER@$SERVER:$PARTS_REMOTE/$name"; then ok=1; break; fi
    echo "  $name failed (try $try) - retrying"; sleep 5
  done
  [ $ok = 1 ] || { echo "Upload failed for $name - run the same command again to resume"; exit 1; }
  echo "  $name sent"
done
rm -rf "$PARTS_LOCAL"
"${SSH[@]}" "cat $PARTS_REMOTE/part_* > /tmp/$REMOTE_APK && rm -rf $PARTS_REMOTE"
scp -i "$SSH_KEY" -q "$LOCAL_LATEST" "$SSH_USER@$SERVER:/tmp/latest.json"
rm -f "$LOCAL_LATEST"

"${SSH[@]}" "set -e
  sudo mkdir -p $REMOTE_DIR
  sudo install -m 644 /tmp/$REMOTE_APK $REMOTE_DIR/$REMOTE_APK && rm -f /tmp/$REMOTE_APK
  echo \"\$(sha256sum $REMOTE_DIR/$REMOTE_APK | cut -d' ' -f1)\" | grep -qx '$SHA' || { echo 'checksum mismatch on server'; exit 1; }
  sudo install -m 644 /tmp/latest.json $REMOTE_DIR/latest.json && rm -f /tmp/latest.json
  # history.json: every build's notes, newest first (Settings -> Updates on the phone)
  sudo python3 - $REMOTE_DIR <<'PYH'
import json, sys, pathlib
d = pathlib.Path(sys.argv[1]); h = d / 'history.json'
latest = json.loads((d / 'latest.json').read_text())
items = json.loads(h.read_text()) if h.is_file() else []
items = [r for r in items if r.get('version_code') != latest['version_code']]
items.insert(0, {k: latest.get(k) for k in ('version_code', 'version_name', 'notes', 'items', 'published_at')})
h.write_text(json.dumps(items[:200], ensure_ascii=False, indent=1))
PYH
  # keep the 3 newest APKs (rollback = point latest.json at an older one)
  ls -1t $REMOTE_DIR/nexora-*.apk | tail -n +4 | xargs -r sudo rm -f
  sudo chown -R nexora:nexora /var/data/app-releases
  echo published: \$(cat $REMOTE_DIR/latest.json | tr -d '\n')
"
