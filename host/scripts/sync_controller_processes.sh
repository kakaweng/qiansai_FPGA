#!/bin/sh
# Sourced by run_sync_board.sh. Never select a process by port or PID file alone.
# SYNC_PROC_ROOT is overridden only by the lifecycle regression harness.
SYNC_PROC_ROOT="${SYNC_PROC_ROOT:-/proc}"

sync_is_controller()
{
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    # Explicitly invoking glibc can leave /proc/exe pointing at the loader and
    # argv may be rewritten. Executable mappings still identify the loaded ELF.
    SYNC_EXE_TARGET="$(readlink "$SYNC_PROC_ROOT/$1/exe" 2>/dev/null)" || SYNC_EXE_TARGET=
    case "$SYNC_EXE_TARGET" in
        "$SYNC_BOARD/rk_sync_control"|"$SYNC_BOARD/rk_sync_control (deleted)"|"$SYNC_BOARD/rk_drive_control"|"$SYNC_BOARD/rk_drive_control (deleted)"|"$SYNC_BOARD/rk_front_control"|"$SYNC_BOARD/rk_front_control (deleted)"|"$SYNC_BOARD/rk_front_p1_control"|"$SYNC_BOARD/rk_front_p1_control (deleted)"|"$SYNC_BOARD/rk_front_pie_control"|"$SYNC_BOARD/rk_front_pie_control (deleted)") return 0 ;;
    esac
    if [ -r "$SYNC_PROC_ROOT/$1/maps" ]; then
        while read -r SYNC_MAP_RANGE SYNC_MAP_PERMS SYNC_MAP_OFFSET SYNC_MAP_DEVICE SYNC_MAP_INODE SYNC_MAP_PATH; do
            case "$SYNC_MAP_PERMS" in
                *x*)
                    case "$SYNC_MAP_PATH" in
                        "$SYNC_BOARD/rk_sync_control"|"$SYNC_BOARD/rk_sync_control (deleted)"|"$SYNC_BOARD/rk_drive_control"|"$SYNC_BOARD/rk_drive_control (deleted)"|"$SYNC_BOARD/rk_front_control"|"$SYNC_BOARD/rk_front_control (deleted)"|"$SYNC_BOARD/rk_front_p1_control"|"$SYNC_BOARD/rk_front_p1_control (deleted)"|"$SYNC_BOARD/rk_front_pie_control"|"$SYNC_BOARD/rk_front_pie_control (deleted)") return 0 ;;
                    esac
                    ;;
            esac
        done < "$SYNC_PROC_ROOT/$1/maps"
    fi
    [ -r "$SYNC_PROC_ROOT/$1/cmdline" ] || return 1
    # The board launches through the explicit glibc loader. Also accept a direct
    # exec, but not a shell/grep/file-transfer command merely mentioning the path.
    { tr '\000' '\n' < "$SYNC_PROC_ROOT/$1/cmdline"; } 2>/dev/null | {
        IFS= read -r SYNC_ARG_FIRST || return 1
        case "$SYNC_ARG_FIRST" in "$SYNC_BOARD/rk_sync_control"|"$SYNC_BOARD/rk_drive_control"|"$SYNC_BOARD/rk_front_control"|"$SYNC_BOARD/rk_front_p1_control"|"$SYNC_BOARD/rk_front_pie_control") return 0 ;; esac
        [ "$SYNC_ARG_FIRST" = "$SYNC_LOADER" ] || return 1
        IFS= read -r SYNC_ARG_SECOND || return 1
        [ "$SYNC_ARG_SECOND" = --library-path ] || return 1
        IFS= read -r SYNC_ARG_THIRD || return 1
        IFS= read -r SYNC_ARG_FOURTH || return 1
        case "$SYNC_ARG_FOURTH" in "$SYNC_BOARD/rk_sync_control"|"$SYNC_BOARD/rk_drive_control"|"$SYNC_BOARD/rk_front_control"|"$SYNC_BOARD/rk_front_p1_control"|"$SYNC_BOARD/rk_front_pie_control") return 0 ;; *) return 1 ;; esac
    }
}

sync_find_controllers()
{
    for SYNC_PROCESS_DIRECTORY in "$SYNC_PROC_ROOT"/[0-9]*; do
        SYNC_PROCESS_ID="${SYNC_PROCESS_DIRECTORY##*/}"
        if sync_is_controller "$SYNC_PROCESS_ID"; then
            printf '%s\n' "$SYNC_PROCESS_ID"
        fi
    done
}

sync_controller_stop()
{
    # Capture port ownership before terminating an identified project process.
    SYNC_OCCUPIED_INODES="$(sync_port_inodes)"
    if [ -n "$SYNC_OCCUPIED_INODES" ]; then sync_port_owners; fi
    SYNC_OLD_PIDS="$(sync_find_controllers)"
    for SYNC_OLD_PID in $SYNC_OLD_PIDS; do
        # Recheck identity immediately before sending the signal (PID reuse).
        if sync_is_controller "$SYNC_OLD_PID"; then
            printf 'Stopping sync controller: pid=%s\n' "$SYNC_OLD_PID"
            if ! kill -TERM "$SYNC_OLD_PID" 2>/dev/null && sync_is_controller "$SYNC_OLD_PID"; then
                printf 'ERROR: could not stop sync controller pid=%s\n' "$SYNC_OLD_PID" >&2
                return 1
            fi
        fi
    done
    SYNC_STOP_WAIT=0
    SYNC_REMAINING="$(sync_find_controllers)"
    while [ -n "$SYNC_REMAINING" ] && [ "$SYNC_STOP_WAIT" -lt 5 ]; do
        sleep 1
        SYNC_STOP_WAIT=$((SYNC_STOP_WAIT + 1))
        SYNC_REMAINING="$(sync_find_controllers)"
    done
    if [ -n "$SYNC_REMAINING" ]; then
        printf 'ERROR: sync controllers still running: %s; refusing duplicate start.\n' "$SYNC_REMAINING" >&2
        return 1
    fi
    # An exited zombie has an empty cmdline and no open sockets; it is stopped.
    rm -f "$SYNC_BOARD/sync_controller.pid"
    printf 'Sync controllers stopped.\n'
}

sync_port_inodes()
{
    for SYNC_INODE_TABLE in tcp tcp6 udp udp6; do
        [ -r "$SYNC_PROC_ROOT/net/$SYNC_INODE_TABLE" ] || continue
        while read -r SYNC_INODE_SLOT SYNC_INODE_LOCAL SYNC_INODE_REMOTE SYNC_INODE_STATE SYNC_INODE_QUEUE SYNC_INODE_TIMER SYNC_INODE_RETRANS SYNC_INODE_UID SYNC_INODE_TIMEOUT SYNC_INODE_VALUE SYNC_INODE_REST; do
            case "$SYNC_INODE_LOCAL" in
                *:2332|*:2335)
                    case "$SYNC_INODE_VALUE" in ''|0|*[!0-9]*) ;; *) printf '%s\n' "$SYNC_INODE_VALUE" ;; esac
                    ;;
            esac
        done < "$SYNC_PROC_ROOT/net/$SYNC_INODE_TABLE"
    done
}

sync_port_owners()
{
    SYNC_OWNER_INODES="$(sync_port_inodes)"
    [ -n "$SYNC_OWNER_INODES" ] || return 0
    SYNC_OWNER_FOUND=0
    for SYNC_OWNER_DIRECTORY in "$SYNC_PROC_ROOT"/[0-9]*; do
        for SYNC_OWNER_FD in "$SYNC_OWNER_DIRECTORY"/fd/[0-9]*; do
            SYNC_OWNER_TARGET="$(readlink "$SYNC_OWNER_FD" 2>/dev/null)" || continue
            case "$SYNC_OWNER_TARGET" in
                socket:\[*\])
                    SYNC_OWNER_INODE="${SYNC_OWNER_TARGET#*\[}"
                    SYNC_OWNER_INODE="${SYNC_OWNER_INODE%\]}"
                    for SYNC_EXPECTED_INODE in $SYNC_OWNER_INODES; do
                        [ "$SYNC_OWNER_INODE" = "$SYNC_EXPECTED_INODE" ] || continue
                        SYNC_OWNER_FOUND=1
                        SYNC_OWNER_PID="${SYNC_OWNER_DIRECTORY##*/}"
                        SYNC_OWNER_EXE="$(readlink "$SYNC_OWNER_DIRECTORY/exe" 2>/dev/null)" || SYNC_OWNER_EXE=unavailable
                        SYNC_OWNER_NAME=unavailable
                        if [ -r "$SYNC_OWNER_DIRECTORY/comm" ]; then
                            { IFS= read -r SYNC_OWNER_NAME < "$SYNC_OWNER_DIRECTORY/comm"; } || true
                        fi
                        if sync_is_controller "$SYNC_OWNER_PID"; then SYNC_OWNER_PROJECT=yes; else SYNC_OWNER_PROJECT=no; fi
                        printf 'PORT_OWNER pid=%s fd=%s inode=%s project_controller=%s comm=%s exe=%s\n' \
                            "$SYNC_OWNER_PID" "${SYNC_OWNER_FD##*/}" "$SYNC_OWNER_INODE" "$SYNC_OWNER_PROJECT" "$SYNC_OWNER_NAME" "$SYNC_OWNER_EXE"
                    done
                    ;;
            esac
        done
    done
    if [ "$SYNC_OWNER_FOUND" = 0 ]; then
        printf 'PORT_OWNER unresolved: socket exists but its process fd could not be read.\n'
    fi
}

sync_require_free_ports()
{
    if [ -n "$(sync_port_inodes)" ]; then
        sync_controller_diagnostics
        printf 'ERROR: TCP9010/UDP9013 still held by an unidentified or shared process; existing video service preserved.\n' >&2
        return 1
    fi
}

sync_controller_diagnostics()
{
    printf 'SYNC_CONTROLLER_DIAGNOSTICS: expected TCP 127.0.0.1:9010 and UDP 127.0.0.1:9013\n'
    printf 'Recognized project controller PIDs: %s\n' "$(sync_find_controllers)"
    if [ -r "$SYNC_BOARD/sync_controller.pid" ]; then
        printf 'Saved PID: '; cat "$SYNC_BOARD/sync_controller.pid"
    fi
    for SYNC_SOCKET_TABLE in tcp tcp6 udp udp6; do
        if [ -r "$SYNC_PROC_ROOT/net/$SYNC_SOCKET_TABLE" ]; then
            printf 'Socket table %s (9010=2332, 9013=2335):\n' "$SYNC_SOCKET_TABLE"
            while read -r SYNC_NET_SLOT SYNC_NET_LOCAL SYNC_NET_REMOTE SYNC_NET_STATE SYNC_NET_REST; do
                case "$SYNC_NET_LOCAL" in
                    *:2332|*:2335)
                        printf '%s %s %s %s %s\n' "$SYNC_NET_SLOT" "$SYNC_NET_LOCAL" "$SYNC_NET_REMOTE" "$SYNC_NET_STATE" "$SYNC_NET_REST"
                        ;;
                esac
            done < "$SYNC_PROC_ROOT/net/$SYNC_SOCKET_TABLE"
        fi
    done
    sync_port_owners
    printf 'Other processes using these ports are not terminated automatically.\n'
}
