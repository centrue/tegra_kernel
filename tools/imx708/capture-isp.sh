#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Capture/override workflow adapted from ov5647-jetson/tools/isp/capture-isp.sh
# Copyright (C) 2026 Artur Andrzejczak <andrzejczak.artur@gmail.com>
# License: public_sources/ov5647-jetson/LICENSE
set -Eeuo pipefail
usage() {
    cat <<'EOF'
Usage: capture-isp.sh OVERRIDE.isp|none|keep NEW_OUTPUT_DIR [options]
  none: temporarily remove override; keep: use current configuration unchanged.
  --size WxH (2304x1296) --sensor N (0) --mode N (1) --fps N (30)
  --frames N (50) --restart (restart/cache-clear even if override unchanged)
  --exposure-ns N --gain N (fixed Argus ranges) --focus N (metadata only)
  --illuminant TEXT --black DN (64) --bayer RGGB --stride BYTES
  --reference-kind display|reflective (label only; default unspecified)
  --argus-property KEY=VALUE (repeat; one shell argument, no eval)
Output: last.i420, command.txt, invocation.txt, journal.log, capture.log,
        service.log, metadata.json, original.isp backup when originally present.
EOF
}
[[ $# -ge 2 ]] || { usage >&2; exit 2; }
invocation=("$0" "$@")
policy=$1; out=$2; shift 2
width=2304; height=1296; sensor=0; mode=1; fps=30; frames=50; restart=0
exposure=''; gain=''; focus=''; illuminant=''; black=64; bayer=RGGB; stride=''
reference_kind=unspecified
props=()
while (($#)); do
    key=$1; shift
    if [[ $key == --restart ]]; then restart=1; continue; fi
    (($#)) || { echo "missing value for $key" >&2; exit 2; }
    value=$1; shift
    case $key in
        --size) [[ $value =~ ^([0-9]+)x([0-9]+)$ ]] || exit 2
                width=${BASH_REMATCH[1]}; height=${BASH_REMATCH[2]} ;;
        --sensor) sensor=$value;; --mode) mode=$value;; --fps) fps=$value;;
        --frames) frames=$value;; --exposure-ns) exposure=$value;; --gain) gain=$value;;
        --focus) focus=$value;; --illuminant) illuminant=$value;; --black) black=$value;;
        --bayer) bayer=$value;; --stride) stride=$value;;
        --reference-kind) reference_kind=$value;;
        --argus-property)
            [[ $value =~ ^[a-zA-Z][a-zA-Z0-9_-]*=.+$ ]] || { echo 'expected KEY=VALUE' >&2; exit 2; }
            case ${value%%=*} in sensor-id|sensor-mode|num-buffers)
                echo 'use the dedicated sensor/mode/frames options' >&2; exit 2;; esac
            props+=("$value");;
        *) echo "unknown option: $key" >&2; exit 2;;
    esac
done
for n in "$width" "$height" "$fps" "$frames"; do
    [[ $n =~ ^[1-9][0-9]*$ ]] || { echo 'dimensions/fps/frames must be positive integers' >&2; exit 2; }
done
[[ $sensor =~ ^[0-9]+$ && $mode =~ ^[0-9]+$ ]] || exit 2
((width % 2 == 0 && height % 2 == 0)) || { echo 'I420 dimensions must be even' >&2; exit 2; }
[[ -z $exposure || $exposure =~ ^[1-9][0-9]*$ ]] || exit 2
[[ -z $gain || $gain =~ ^[0-9]+([.][0-9]+)?$ ]] || exit 2
case $bayer in RGGB|BGGR|GRBG|GBRG) ;; *) echo 'unsupported Bayer' >&2; exit 2;; esac
case $reference_kind in unspecified|display|reflective) ;; *) echo 'unsupported reference kind' >&2; exit 2;; esac
[[ -z $stride || $stride =~ ^[0-9]+$ ]] || exit 2
if [[ $policy != none && $policy != keep ]]; then
    [[ -f $policy && -s $policy ]] || { echo 'override must be a nonempty regular file' >&2; exit 2; }
    policy=$(realpath -- "$policy")
    if grep -q 'DISPLAY EXPERIMENT ONLY' "$policy"; then
        [[ $reference_kind != reflective ]] || { echo 'display override cannot be labelled reflective' >&2; exit 2; }
        reference_kind=display
    fi
fi
# A new directory avoids mistaking an old successful capture for a new result.
mkdir -- "$out"
out=$(realpath -- "$out")
settings=${IMX708_SETTINGS_DIR:-/var/nvidia/nvcam/settings}
mkdir -p -- "$settings"
settings=$(realpath -- "$settings")
override=$settings/camera_overrides.isp
exec 9>"$settings/.imx708-isp.lock"
flock -n 9 || { echo 'another ISP capture owns the settings lock' >&2; exit 1; }
changed=0; had=0; load_verified=0; journal_saved=0; override_sha=''; frame_sha=''; original_sha=''
capture_rc=''; restore_rc=0; cursor=''; since=$(date --iso-8601=ns)
# Save invocation before changing positional parameters or any override state.
printf '%q ' "${invocation[@]}" >"$out/invocation.txt"
printf '\n' >>"$out/invocation.txt"
touch "$out/service.log" "$out/capture.log" "$out/journal.log"
log() { printf '%s\n' "$*" | tee -a "$out/service.log" >&2; }
clear_cache() { rm -f -- "$settings"/nvcam_cache_*.bin; }
save_journal() {
    if [[ -n $cursor ]]; then
        journalctl -u nvargus-daemon --after-cursor="$cursor" --no-pager >"$out/journal.log" 2>"$out/journal-error.log"
    else
        journalctl -u nvargus-daemon --since "$since" --no-pager >"$out/journal.log" 2>"$out/journal-error.log"
    fi
}
finish() {
    rc=$?
    trap - EXIT
    trap '' INT TERM HUP
    set +e
    if (( ! journal_saved )); then
        save_journal
        jr=$?
        if (( jr != 0 && rc == 0 )); then rc=$jr; fi
    fi
    if ((changed)); then
        log 'restoring original override and clearing candidate cache'
        # Never follow a candidate symlink. cp -a preserves original mode/uid/gid,
        # timestamps and symlink identity. Keep backup for manual recovery too.
        rm -f -- "$override" || restore_rc=1
        if ((had)); then cp -a -- "$out/original.isp" "$override" || restore_rc=1; fi
        clear_cache || restore_rc=1
        systemctl restart nvargus-daemon >>"$out/service.log" 2>&1 || restore_rc=1
        if ((restore_rc)); then log "RESTORE FAILED; backup retained in $out/original.isp"; rc=1; fi
    fi
    python3 - "$out" "$rc" "$restore_rc" "$changed" "$load_verified" "$override_sha" "$frame_sha" \
        "$policy" "$width" "$height" "$sensor" "$mode" "$fps" "$frames" "$capture_rc" \
        "$exposure" "$gain" "$focus" "$illuminant" "$black" "$bayer" "$stride" "$reference_kind" "$original_sha" <<'PY'
import datetime, json, pathlib, sys
(out, rc, restore, changed, verified, override_sha, frame_sha, policy, w, h,
 sensor, mode, fps, frames, capture_rc, exposure, gain, focus, illuminant,
 black, bayer, stride, reference_kind, original_sha) = sys.argv[1:]
root = pathlib.Path(out)
record = dict(created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              exit_code=int(rc), restore_exit_code=int(restore),
              override_changed=bool(int(changed)), override_load_verified=bool(int(verified)),
              override_policy=policy, override_sha256=override_sha or None,
              original_override_sha256=original_sha or None,
              reference_kind=reference_kind,
              warnings=['DISPLAY EXPERIMENT ONLY; not a general-purpose reflective calibration.'] if reference_kind == 'display' else [],
              frame_sha256=frame_sha or None, size=[int(w), int(h)],
              sensor=int(sensor), mode=int(mode), fps=int(fps), requested_frames=int(frames),
              capture_exit_code=int(capture_rc) if capture_rc else None,
              exposure_ns=exposure or None, gain=gain or None, focus_dac=focus or None,
              focus_note='metadata only; this tool does not move the lens',
              illuminant=illuminant or None, raw_context=dict(bayer=bayer, bit_depth=10,
              black_dn=black, stride_bytes=stride or None), output_format='I420',
              command=(root / 'command.txt').read_text() if (root / 'command.txt').exists() else None,
              logs=['capture.log', 'journal.log', 'service.log'])
backup = root / 'original.isp'
if backup.exists() or backup.is_symlink():
    st = backup.lstat()
    record['original_override_attributes'] = dict(mode=oct(st.st_mode & 0o7777), uid=st.st_uid, gid=st.st_gid)
(root / 'metadata.json').write_text(json.dumps(record, indent=2) + '\n')
PY
    meta_rc=$?
    ((meta_rc == 0)) || rc=1
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
# Capture a journal boundary before any restart, so old successes cannot pass.
journalctl -u nvargus-daemon -n 0 --show-cursor --no-pager >"$out/journal-start.log"
cursor=$(sed -n 's/^-- cursor: //p' "$out/journal-start.log" | tail -n 1)
if [[ -e $override || -L $override ]]; then
    if [[ -f $override ]]; then
        original_sha=$(sha256sum -- "$override"); original_sha=${original_sha%% *}
    fi
    cp -a -- "$override" "$out/original.isp"
    had=1
fi
if [[ $policy == none ]]; then
    if ((had)); then changed=1; rm -f -- "$override"; fi
elif [[ $policy != keep ]]; then
    if [[ ! -f $override ]] || ! cmp -s -- "$policy" "$override"; then
        # Stage before setting changed; failures during staging leave live state intact.
        install -m 0644 -- "$policy" "$out/candidate.isp"
        changed=1
        rm -f -- "$override"
        cp -- "$out/candidate.isp" "$override"
        chmod 0644 "$override"
    fi
fi
if [[ -f $override ]]; then
    override_sha=$(sha256sum -- "$override"); override_sha=${override_sha%% *}
fi
if ((changed || restart)); then
    clear_cache
    log 'restarting nvargus-daemon'
    systemctl restart nvargus-daemon >>"$out/service.log" 2>&1
fi
[[ -z $exposure ]] || props+=("exposuretimerange=$exposure $exposure")
[[ -z $gain ]] || props+=("gainrange=$gain $gain")
mkdir "$out/frames"
cmd=(timeout -k 5 60 gst-launch-1.0 -e nvarguscamerasrc "sensor-id=$sensor"
     "sensor-mode=$mode" "num-buffers=$frames" "${props[@]}" '!'
     "video/x-raw(memory:NVMM),width=$width,height=$height,format=NV12,framerate=$fps/1"
     '!' nvvidconv '!' 'video/x-raw,format=I420' '!'
     multifilesink "location=$out/frames/frame-%03d.i420" max-files=1)
printf '%q ' "${cmd[@]}" >"$out/command.txt"
printf '\n' >>"$out/command.txt"
set +e
"${cmd[@]}" >"$out/capture.log" 2>&1
capture_rc=$?
set -e
save_journal
journal_saved=1
# Parser errors are fatal even if GStreamer emitted a frame or exited zero.
if grep -Eiq 'Bad parameter|parse[r]?[^[:cntrl:]]*(error|fail)|syntax error|invalid[^[:cntrl:]]*(override|isp)|error[^[:cntrl:]]*(parsing|override)' "$out/journal.log"; then
    log 'ISP parser rejected configuration; see journal.log'
    exit 1
fi
((capture_rc == 0)) || { log "capture failed: $capture_rc"; exit "$capture_rc"; }
if [[ -n $override_sha ]]; then
    # Require a positive event naming this full path on the SAME log line.
    # A missing/default override warning cannot be interpreted as successful load.
    if python3 - "$out/journal.log" "$override" <<'PY'
import pathlib, re, sys
lines = pathlib.Path(sys.argv[1]).read_text(errors='replace').splitlines()
positive = re.compile(r'\b(loaded|read|using|applied)\b|Found override file \[', re.I)
path = re.compile(re.escape(sys.argv[2]) + r'''(?=$|[\s"';:,\]])''')
negative = re.compile(r'\b(no|not|fail(?:ed|ure)?|error|cannot|could not|attempt(?:ing)?)\b', re.I)
ok = any(path.search(line) and positive.search(line) and not negative.search(line) for line in lines)
sys.exit(0 if ok else 1)
PY
    then load_verified=1
    elif [[ $policy != keep ]]; then
        log 'override loading was not verified by a positive path-specific journal event'
        exit 1
    else log 'current override has no fresh load evidence; recorded as unverified'
    fi
fi
shopt -s nullglob
files=("$out/frames/"*.i420)
((${#files[@]} == 1)) || { log 'expected exactly one retained I420 frame'; exit 1; }
expected=$((width * height * 3 / 2))
[[ $(stat -c %s -- "${files[0]}") == "$expected" ]] || { log 'truncated or padded I420 frame'; exit 1; }
mv -- "${files[0]}" "$out/last.i420"
rmdir "$out/frames"
frame_sha=$(sha256sum -- "$out/last.i420"); frame_sha=${frame_sha%% *}
log "captured $out/last.i420"
