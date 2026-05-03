FROM google/cloud-sdk:slim
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
    && curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update && apt-get install -y --no-install-recommends mongodb-database-tools mongodb-mongosh \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /scripts
COPY backup.sh restore.sh ./
COPY crontab /etc/cron.d/xbrain-backup
RUN chmod +x backup.sh restore.sh && chmod 0644 /etc/cron.d/xbrain-backup
ENV TZ=UTC
CMD ["sh", "-c", "touch /var/log/cron.log && cron && tail -F /var/log/cron.log"]
