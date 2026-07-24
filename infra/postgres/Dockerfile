FROM postgres:18-alpine

COPY postgresql.conf /etc/postgresql/postgresql.conf
COPY psql-app /usr/local/bin/psql-app
RUN chmod 0755 /usr/local/bin/psql-app

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
