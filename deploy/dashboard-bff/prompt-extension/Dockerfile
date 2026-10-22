ARG MEMOS_BASE_IMAGE
FROM ${MEMOS_BASE_IMAGE}
COPY store.py runtime.py api.py /app/src/memos/dashboard_prompts/
COPY patch.py /tmp/dashboard-prompt-patch.py
RUN python /tmp/dashboard-prompt-patch.py /app/src/memos
