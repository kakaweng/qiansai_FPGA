#!/bin/sh
set -eu

is_rknn_server()
{
    { IFS= read -r process_name < "$1/comm"; } 2>/dev/null || return 1
    case "$process_name" in
        pcie_rknn_probe|pcie_rknn_probe_) return 0 ;;
        *) return 1 ;;
    esac
}

find_servers()
{
    for process_directory in /proc/[0-9]*; do
        if is_rknn_server "$process_directory"; then
            printf '%s\n' "${process_directory##*/}"
        fi
    done
}

server_pids=$(find_servers)
if [ -z "$server_pids" ]; then
    echo 'No matching RKNN server process found.'
else
    for server_pid in $server_pids; do
        if is_rknn_server "/proc/$server_pid"; then
            printf 'Stopping RKNN server: pid=%s\n' "$server_pid"
            if ! kill -TERM "$server_pid"; then
                if is_rknn_server "/proc/$server_pid"; then
                    echo 'ERROR: could not stop RKNN server.' >&2
                    exit 1
                fi
            fi
        fi
    done
fi

attempt=0
remaining_pids=$(find_servers)
while [ -n "$remaining_pids" ] && [ "$attempt" -lt 5 ]; do
    sleep 1
    attempt=$((attempt + 1))
    remaining_pids=$(find_servers)
done
if [ -n "$remaining_pids" ]; then
    printf 'ERROR: RKNN servers still running: %s\n' "$remaining_pids" >&2
    exit 1
fi

if [ ! -r /proc/net/tcp ]; then
    echo 'ERROR: cannot inspect TCP listeners.' >&2
    exit 1
fi
tcp_tables=/proc/net/tcp
if [ -r /proc/net/tcp6 ]; then
    tcp_tables="$tcp_tables /proc/net/tcp6"
fi
for tcp_table in $tcp_tables; do
    while read -r entry_index local_address remote_address tcp_state remaining_fields; do
        case "$local_address:$tcp_state" in
            *:2329:0A)
                echo 'ERROR: board TCP port 9001 still has a listener; inspect netstat -ltnp.' >&2
                exit 1
                ;;
        esac
    done < "$tcp_table"
done

echo 'RKNN servers stopped; board TCP port 9001 is free.'
