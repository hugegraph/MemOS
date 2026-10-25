ARG MEMOS_BASE_IMAGE
FROM ${MEMOS_BASE_IMAGE}
COPY soft_delete.py api.py models.py /app/src/memos/dashboard_memory/
COPY patch.py /tmp/dashboard-memory-patch.py
RUN python /tmp/dashboard-memory-patch.py
