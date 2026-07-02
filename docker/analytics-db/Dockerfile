# gru-analytics-db — PostgreSQL + a minimal built-in web dashboard.
#
# Extends the stock postgres:16-alpine image with a small stdlib-only Python
# HTTP server that visualizes pipeline_runs / pipeline_run_items directly
# from the local database (via `psql`, already present in the base image —
# no extra DB driver needed). The web UI's lifecycle is tied to this
# container: it starts/stops with postgres, independent of gru-server.
#
# Ports:
#   5432 — postgres (as before)
#   8080 — analytics web dashboard
FROM postgres:16-alpine

RUN apk add --no-cache python3

COPY web_server.py     /usr/local/bin/gru-analytics-web.py
COPY entrypoint.sh     /usr/local/bin/gru-analytics-entrypoint.sh
COPY initdb/           /docker-entrypoint-initdb.d/
RUN chmod +x /usr/local/bin/gru-analytics-entrypoint.sh /usr/local/bin/gru-analytics-web.py

EXPOSE 5432 8080

ENTRYPOINT ["gru-analytics-entrypoint.sh"]
