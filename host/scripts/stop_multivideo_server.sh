#!/bin/sh
set -eu

find_servers()
{
    for process_directory in /proc/[0-9]*; do
        if { IFS= read -r process_name < "$process_directory/comm"; } 2>/dev/null; then
            case "$process_name" in
                pcie_multivideo) printf '%s\n' "${process_directory##*/}" ;;
            esac
        fi
    done
}

server_pids=$(find_servers)
for server_pid in $server_pids; do
    printf 'Stopping multivideo server: pid=%s\n' "$server_pid"
    if ! kill -TERM "$server_pid"; then
        if [ -d "/proc/$server_pid" ]; then
            echo 'ERROR: could not stop multivideo server.' >&2
            exit 1
        fi
    fi
done

attempt=0
remaining_pids=$(find_servers)
while [ -n "$remaining_pids" ] && [ "$attempt" -lt 25 ]; do
    sleep 1
    attempt=$((attempt + 1))
    remaining_pids=$(find_servers)
done
if [ -n "$remaining_pids" ]; then
    printf 'ERROR: multivideo servers still running: %s\n' "$remaining_pids" >&2
    exit 1
fi
echo 'Multivideo servers stopped.'
