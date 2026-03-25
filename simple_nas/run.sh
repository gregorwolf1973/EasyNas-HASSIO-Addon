#!/usr/bin/with-contenv bashio

set -e

bashio::log.info "Starting Simple NAS..."

# Read config and export so Flask child process can read via os.environ
export WORKGROUP
WORKGROUP=$(bashio::config 'workgroup')

bashio::log.info "Workgroup: ${WORKGROUP}"

# Initialize persistent data files
for f in shares users mounts; do
    if [ ! -f "/data/${f}.json" ]; then
        echo '[]' > "/data/${f}.json"
    fi
done

# Generate smb.conf
python3 /app/generate_smb_conf.py "$WORKGROUP"

# Ensure Samba runtime directories
mkdir -p /var/log/samba /var/run/samba

# Restore saved mounts
python3 /app/restore_mounts.py

# Start Samba daemons
# FIX: --log-stdout is invalid in this Samba version → use --debug-stdout
bashio::log.info "Starting Samba daemons (smbd + nmbd)..."
smbd --no-process-group --foreground --debug-stdout &
nmbd --no-process-group --foreground --debug-stdout &

sleep 2

bashio::log.info "Starting Web GUI on port 8099..."
exec python3 /app/app.py
