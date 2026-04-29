ARG BUILD_FROM
FROM $BUILD_FROM

# Install system dependencies
RUN apk add --no-cache \
    samba \
    samba-common-tools \
    python3 \
    py3-pip \
    util-linux \
    blkid \
    e2fsprogs \
    psmisc \
    lsof \
    ntfs-3g \
    exfatprogs \
    bash \
    curl \
    jq \
    avahi \
    avahi-tools \
    dbus \
    rsync

# Install Python packages
RUN pip3 install --break-system-packages flask psutil

# Install wsdd (WS-Discovery for Windows 10+) directly from GitHub
RUN curl -sSL https://raw.githubusercontent.com/christgau/wsdd/master/src/wsdd.py \
    -o /usr/local/bin/wsdd && chmod +x /usr/local/bin/wsdd

# Create required directories
RUN mkdir -p /etc/samba /var/log/samba /var/run/samba /data/shares /data/mounts \
    /var/run/dbus /var/run/avahi-daemon /etc/avahi/services

# Copy avahi service definition (mDNS discovery for Linux & macOS)
COPY smb.service /etc/avahi/services/smb.service

# Avahi config: allow running in container without chroot
# Do not restrict to a single interface so it works regardless of HA network setup
RUN sed -i 's/#enable-dbus=yes/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true

# Copy application files and set permissions
COPY app/ /app/
COPY run.sh /run.sh
RUN chmod +x /run.sh /app/mount_helper.sh

WORKDIR /app
CMD ["/run.sh"]
