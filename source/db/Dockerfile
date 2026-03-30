FROM postgres:16-alpine

# Keep init script path explicit in the image so the DB bootstrap is reproducible.
COPY init.sql /docker-entrypoint-initdb.d/init.sql
