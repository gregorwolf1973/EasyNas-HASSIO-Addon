#!/usr/bin/with-contenv bashio

set -e

bashio::log.info "Starting Simple NAS..."

# Read config and EXPORT so child processes (Flask) can read via os.environ
export WORKGROUP
WORKGROUP=$(bashio::config 'workgroup')

bashio::log.info "Workgroup: ${WORKGROUP}"

# Initialize persistent data files if not present
for f in shares users mounts; do
    if [ ! -f "/data/${f}.json" ]; then
        echo '[]' > "/data/${f}.json"
        bashio::log.info "Initialized /data/${f}.json"
    fi
done

# Generate initial smb.conf from current shares
python3 /app/generate_smb_conf.py "$WORKGROUP"

# Ensure Samba runtime directories exist
mkdir -p /var/log/samba /var/run/samba

# Restore mounts saved from previous session
python3 /app/restore_mounts.py

# Start Samba daemons in background
bashio::log.info "Starting Samba daemons (smbd + nmbd)..."
smbd --no-process-group --foreground --log-stdout &
nmbd --no-process-group --foreground --log-stdout &

# Give Samba a moment to initialise before starting the GUI
sleep 2

# Start web GUI (foreground – keeps container alive)
bashio::log.info "Starting Web GUI on port 8099..."
exec python3 /app/app.py
