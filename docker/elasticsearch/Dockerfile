# Elasticsearch with the analyzers the catalog index needs (backend/app/search/es.py):
# analysis-ukrainian (no built-in Ukrainian analyzer in ES) and analysis-icu (folding and
# the shared analyzer for the other languages).
# TODO(review): the version — the plugins must be of the same version as ES
ARG ES_VERSION=8.17.4
FROM docker.elastic.co/elasticsearch/elasticsearch:${ES_VERSION}
RUN bin/elasticsearch-plugin install --batch analysis-ukrainian analysis-icu
