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
    ntfs-3g \
    exfatprogs \
    bash \
    curl \
    jq

# Install Python packages
RUN pip3 install --break-system-packages flask psutil

# Create required directories
RUN mkdir -p /etc/samba /var/log/samba /var/run/samba /data/shares /data/mounts

# Copy application files and set permissions
COPY app/ /app/
COPY run.sh /run.sh
RUN chmod +x /run.sh /app/mount_helper.sh

WORKDIR /app
CMD ["/run.sh"]
