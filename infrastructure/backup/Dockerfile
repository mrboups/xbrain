FROM google/cloud-sdk:slim
# Step 1: base tools (gpg required before adding mongo repo)
RUN apt-get update && apt-get install -y --no-install-recommends \
      postgresql-client \
      gnupg \
      curl \
      cron \
      tar \
      gzip \
      jq \
      tzdata \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Step 2: MongoDB Database Tools (static binaries, version-pinned, OS-agnostic)
# Avoids fragile mongodb apt repo (which depends on Debian codename matching)
RUN curl -fsSL https://fastdl.mongodb.org/tools/db/mongodb-database-tools-debian12-x86_64-100.10.0.deb -o /tmp/mdbtools.deb \
    && dpkg -i /tmp/mdbtools.deb \
    && rm /tmp/mdbtools.deb
WORKDIR /scripts
COPY backup.sh restore.sh ./
COPY crontab /etc/cron.d/xbrain-backup
RUN chmod +x backup.sh restore.sh && chmod 0644 /etc/cron.d/xbrain-backup
ENV TZ=UTC
CMD ["sh", "-c", "touch /var/log/cron.log && cron && tail -F /var/log/cron.log"]
