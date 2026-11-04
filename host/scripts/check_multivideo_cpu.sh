#!/bin/sh

CPU_POLICY_ROOT=/sys/devices/system/cpu/cpufreq
CPU_STATE_DIR=/data/local/tmp/multivideo_cpu_profile.saved
VIDEO_SERVER=/data/local/tmp/pcie_multivideo_server
CPU_UPTIME=/proc/uptime
CPU_PROFILE_VERSION=cpu-governor-v3

read_value()
{
    cat "$1" 2>/dev/null || printf 'unavailable\n'
}

write_governor()
{
    write_target=$1
    write_value=$2
    write_actual=$(cat "$write_target" 2>/dev/null) || write_actual=unreadable
    [ "$write_actual" = "$write_value" ] && return 0
    if ! command -v dd >/dev/null 2>&1
    then
        printf 'CPU_WRITE_FAILED reason=dd_unavailable target=%s\n' "$write_target" >&2
        return 1
    fi
    printf '%s' "$write_value" > "$CPU_STATE_DIR/governor.value" || return 1
    dd if="$CPU_STATE_DIR/governor.value" of="$write_target" bs=4096 count=1 \
        2> "$CPU_STATE_DIR/governor.write.log"
    write_exit=$?
    write_actual=$(cat "$write_target" 2>/dev/null) || write_actual=unreadable
    if [ "$write_actual" = "$write_value" ]
    then
        if [ "$write_exit" -ne 0 ]
        then
            printf 'CPU_WRITE_WARNING target=%s requested=%s readback=%s exit=%s\n' \
                "$write_target" "$write_value" "$write_actual" "$write_exit" >&2
        fi
        return 0
    fi
    printf 'CPU_WRITE_FAILED target=%s requested=%s readback=%s exit=%s\n' \
        "$write_target" "$write_value" "$write_actual" "$write_exit" >&2
    cat "$CPU_STATE_DIR/governor.write.log" >&2
    return 1
}

show_status()
{
    printf 'CPU_PROFILE_VERSION=%s\n' "$CPU_PROFILE_VERSION"
    for policy in "$CPU_POLICY_ROOT"/policy*
    do
        [ -d "$policy" ] || continue
        printf 'CPU_STATUS policy=%s governor=%s cur_khz=%s min_khz=%s max_khz=%s available=%s\n' \
            "${policy##*/}" \
            "$(read_value "$policy/scaling_governor")" \
            "$(read_value "$policy/scaling_cur_freq")" \
            "$(read_value "$policy/scaling_min_freq")" \
            "$(read_value "$policy/scaling_max_freq")" \
            "$(read_value "$policy/scaling_available_governors")"
    done
    for zone in /sys/class/thermal/thermal_zone*
    do
        [ -f "$zone/temp" ] || continue
        printf 'THERMAL zone=%s type=%s temp_millicelsius=%s\n' \
            "${zone##*/}" "$(read_value "$zone/type")" "$(read_value "$zone/temp")"
    done
}

restore_saved()
{
    trap '' PIPE
    [ -d "$CPU_STATE_DIR" ] || return 0
    restore_failed=0
    for saved in "$CPU_STATE_DIR"/policy*
    do
        [ -f "$saved" ] || continue
        restore_governor=$(cat "$saved") || return 1
        restore_target="$CPU_POLICY_ROOT/${saved##*/}/scaling_governor"
        if ! write_governor "$restore_target" "$restore_governor"
        then
            printf 'CPU_RESTORE_FAILED policy=%s saved=%s\n' "${saved##*/}" "$saved" >&2
            restore_failed=1
            continue
        fi
        if [ "$(cat "$restore_target")" != "$restore_governor" ]
        then
            restore_failed=1
            continue
        fi
        printf 'CPU_RESTORE policy=%s governor=%s\n' "${saved##*/}" "$restore_governor"
        rm "$saved" || restore_failed=1
    done
    [ "$restore_failed" -eq 0 ] || return 1
    rm -f "$CPU_STATE_DIR/pid" "$CPU_STATE_DIR/governor.value" \
        "$CPU_STATE_DIR/governor.write.log" || return 1
    rmdir "$CPU_STATE_DIR" || return 1
    printf 'CPU_GOVERNORS_RESTORED\n'
}

finish_profile()
{
    profile_exit=$?
    trap - 0 HUP INT TERM
    restore_saved || profile_exit=1
    exit "$profile_exit"
}

uptime_cs()
{
    read uptime_value uptime_idle < "$CPU_UPTIME" || return 1
    uptime_seconds=${uptime_value%%.*}
    uptime_fraction=${uptime_value#*.}
    uptime_fraction=${uptime_fraction#0}
    printf '%s\n' "$((uptime_seconds * 100 + uptime_fraction))"
}

run_case()
{
    case_name=$1
    show_status
    case_start=$(uptime_cs) || return 1
    "$VIDEO_SERVER" --check-stream --port "$video_port" --count "$frame_count"
    case_exit=$?
    case_end=$(uptime_cs) || return 1
    case_elapsed=$((case_end - case_start))
    if [ "$case_exit" -ne 0 ] || [ "$case_elapsed" -le 0 ]
    then
        printf 'CPU_CASE_FAILED name=%s exit=%s elapsed_cs=%s\n' \
            "$case_name" "$case_exit" "$case_elapsed" >&2
        return 1
    fi
    case_fps=$((frame_count * 10000 / case_elapsed))
    printf 'CPU_CASE name=%s frames=%s elapsed_seconds=%d.%02d fps=%d.%02d\n' \
        "$case_name" "$frame_count" \
        "$((case_elapsed / 100))" "$((case_elapsed % 100))" \
        "$((case_fps / 100))" "$((case_fps % 100))"
    show_status
}

cpu_action=${1:-status}
case "$cpu_action" in
    status)
        show_status
        exit 0
        ;;
    restore)
        if [ -f "$CPU_STATE_DIR/pid" ]
        then
            saved_pid=$(cat "$CPU_STATE_DIR/pid") || exit 1
            if kill -0 "$saved_pid" 2>/dev/null
            then
                printf 'CPU comparison still running: pid=%s\n' "$saved_pid" >&2
                exit 1
            fi
        fi
        restore_saved
        exit $?
        ;;
    compare|hold)
        ;;
    *)
        printf 'Usage: sh %s [status|compare [frames=60] [port=9002]|hold [seconds=180]|restore]\n' "$0" >&2
        exit 2
        ;;
esac

if [ "$cpu_action" = hold ]
then
    hold_seconds=${2:-180}
    case "$hold_seconds" in
        ''|*[!0-9]*)
            printf 'seconds must be a positive integer\n' >&2
            exit 2
            ;;
    esac
    if [ "$#" -gt 2 ] || [ "${#hold_seconds}" -gt 3 ] || \
        [ "${hold_seconds#0}" != "$hold_seconds" ]
    then
        printf 'Use hold seconds=1..600 without leading zeros\n' >&2
        exit 2
    fi
    if [ "$hold_seconds" -lt 1 ] || [ "$hold_seconds" -gt 600 ]
    then
        printf 'Use hold seconds=1..600\n' >&2
        exit 2
    fi
    command -v sleep >/dev/null 2>&1 || exit 1
else
    frame_count=${2:-60}
    video_port=${3:-9002}
    case "$frame_count" in
        ''|*[!0-9]*)
            printf 'frames must be a positive integer\n' >&2
            exit 2
            ;;
    esac
    case "$video_port" in
        ''|*[!0-9]*)
            printf 'port must be a positive integer\n' >&2
            exit 2
            ;;
    esac
    if [ "${#frame_count}" -gt 4 ] || [ "${#video_port}" -gt 5 ] || \
        [ "${frame_count#0}" != "$frame_count" ] || [ "${video_port#0}" != "$video_port" ]
    then
        printf 'Use frames=1..1000 and port=1..65535 without leading zeros\n' >&2
        exit 2
    fi
    if [ "$frame_count" -lt 1 ] || [ "$frame_count" -gt 1000 ] || \
        [ "$video_port" -lt 1 ] || [ "$video_port" -gt 65535 ]
    then
        printf 'Use frames=1..1000 and port=1..65535\n' >&2
        exit 2
    fi
    [ -x "$VIDEO_SERVER" ] || exit 1
fi
umask 077
if ! mkdir "$CPU_STATE_DIR"
then
    printf 'Saved CPU state exists; inspect the previous run and use restore if it has stopped\n' >&2
    exit 1
fi
trap finish_profile 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 141' PIPE
printf '%s\n' "$$" > "$CPU_STATE_DIR/pid" || exit 1
policy_count=0
needs_change=0
for policy in "$CPU_POLICY_ROOT"/policy*
do
    [ -d "$policy" ] || continue
    original_governor=$(cat "$policy/scaling_governor") || exit 1
    printf '%s\n' "$original_governor" > "$CPU_STATE_DIR/${policy##*/}" || exit 1
    available_governors=$(cat "$policy/scaling_available_governors") || exit 1
    case " $available_governors " in
        *" performance "*) ;;
        *)
            printf 'performance governor unavailable: %s\n' "$policy" >&2
            exit 2
            ;;
    esac
    policy_count=$((policy_count + 1))
    [ "$original_governor" = performance ] || needs_change=1
done
[ "$policy_count" -gt 0 ] || exit 1
if [ "$needs_change" -eq 0 ] && [ "$cpu_action" = compare ]
then
    show_status
    printf 'CPU_COMPARE_SKIPPED reason=already_performance\n'
    exit 0
fi

if [ "$cpu_action" = compare ]
then
    profile_label=CPU_COMPARE
    printf 'CPU_COMPARE_BEGIN frames=%s port=%s workload=RK_loopback version=%s\n' \
        "$frame_count" "$video_port" "$CPU_PROFILE_VERSION"
    run_case original || exit 1
else
    profile_label=CPU_HOLD
    printf 'CPU_HOLD_BEGIN seconds=%s version=%s\n' "$hold_seconds" "$CPU_PROFILE_VERSION"
    show_status
fi
for saved in "$CPU_STATE_DIR"/policy*
do
    [ -f "$saved" ] || continue
    target="$CPU_POLICY_ROOT/${saved##*/}/scaling_governor"
    write_governor "$target" performance || exit 1
    [ "$(cat "$target")" = performance ] || exit 1
done
if [ "$cpu_action" = compare ]
then
    run_case performance || exit 1
else
    show_status
    printf 'CPU_HOLD_READY seconds=%s pid=%s governor=performance\n' "$hold_seconds" "$$"
    sleep "$hold_seconds" || exit 1
    show_status
fi
for saved in "$CPU_STATE_DIR"/policy*
do
    [ -f "$saved" ] || continue
    target="$CPU_POLICY_ROOT/${saved##*/}/scaling_governor"
    if [ "$(cat "$target")" != performance ]
    then
        printf '%s_INVALID reason=governor_changed policy=%s\n' "$profile_label" "${saved##*/}" >&2
        exit 1
    fi
done
printf '%s_COMPLETE\n' "$profile_label"
