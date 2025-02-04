# Image name: landerlini/interlink-pilot:v0
FROM ubuntu:latest

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
            wget \
            curl \
            autoconf \
            automake \
            cryptsetup \
            fuse3 \
            fuse2fs \
            git \
            libfuse-dev \
            libglib2.0-dev \
            libseccomp-dev \
            libtool \
            pkg-config \
            runc \
            squashfs-tools \
            squashfs-tools-ng \
            uidmap \
            wget \
            zlib1g-dev \
            squashfs-tools-ng \
            rclone \
            sshfs \
    && rm -rf /var/lib/apt/lists

# Install crun
RUN wget -O /usr/bin/crun https://github.com/containers/crun/releases/download/1.18.2/crun-1.18.2-linux-amd64

# Install singularity
RUN wget -O pkg.deb https://github.com/sylabs/singularity/releases/download/v4.2.1/singularity-ce_4.2.1-noble_amd64.deb && \
    dpkg -i pkg.deb && \
    rm pkg.deb

# Install juicefs
RUN wget -O /usr/local/bin/juicefs https://pandora.infn.it/public/3423df/dl/juicefs && \
    chmod a+x /usr/local/bin/juicefs
## RUN mkdir /tmp/juicefs && \
##     cd /tmp/juicefs && \
##     wget -O jfs.tar.gz https://github.com/juicedata/juicefs/releases/download/v1.2.2/juicefs-1.2.2-linux-amd64.tar.gz && \
##     tar xfz jfs.tar.gz && \
##     cp juicefs /usr/local/bin && \
##     cd - && \
##     rm -rf /tmp/juicefs


