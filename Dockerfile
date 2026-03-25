ARG BUILD_FROM
FROM $BUILD_FROM

# Install dependencies
RUN apk add --no-cache \
    samba \
    samba-common-tools \
    python3 \
    py3-pip \
    py3-flask \
    util-linux \
    blkid \
    e2fsprogs \
    ntfs-3g \
    exfat-utils \
    bash \
    curl \
    jq

# Install Python packages
RUN pip3 install --break-system-packages flask psutil

# Create necessary directories
RUN mkdir -p /etc/samba /var/log/samba /var/run/samba /data/shares /data/mounts

# Copy app files
COPY app/ /app/
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Default smb.conf will be generated at runtime
WORKDIR /app

CMD ["/run.sh"]
