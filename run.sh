#!/usr/bin/with-contenv bashio

set -e

bashio::log.info "Starting Simple NAS..."

# Read config options
WORKGROUP=$(bashio::config 'workgroup')

# Initialize data files if not exists
if [ ! -f /data/shares.json ]; then
    echo '[]' > /data/shares.json
fi
if [ ! -f /data/users.json ]; then
    echo '[]' > /data/users.json
fi
if [ ! -f /data/mounts.json ]; then
    echo '[]' > /data/mounts.json
fi

# Generate initial smb.conf
python3 /app/generate_smb_conf.py "$WORKGROUP"

# Ensure samba directories exist
mkdir -p /var/log/samba /var/run/samba

# Apply saved mounts
python3 /app/restore_mounts.py

# Start Samba services
bashio::log.info "Starting Samba daemon..."
smbd --no-process-group --foreground --log-stdout &
nmbd --no-process-group --foreground --log-stdout &

# Start web GUI
bashio::log.info "Starting Web GUI on port 8099..."
cd /app && python3 app.py
