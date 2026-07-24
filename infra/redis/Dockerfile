FROM redis:8-alpine

COPY docker-entrypoint.sh /usr/local/bin/physician-redis-entrypoint
RUN chmod 0755 /usr/local/bin/physician-redis-entrypoint

ENTRYPOINT ["/usr/local/bin/physician-redis-entrypoint"]
