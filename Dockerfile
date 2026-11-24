ARG GRAINDESK_RUNTIME_IMAGE=python:3.12-slim
FROM ${GRAINDESK_RUNTIME_IMAGE}
USER root
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000 GRAINDESK_DATA=/var/lib/grain_desk
WORKDIR /opt/grain_desk
COPY requirements.txt scripts/check_dependencies.py ./
RUN (python check_dependencies.py || python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r requirements.txt) \
    && python check_dependencies.py && python -m pip check
COPY grain_desk ./grain_desk
COPY image_lab ./image_lab
COPY static ./static
COPY templates ./templates
RUN useradd -U -u 11548 survey && install -d -o survey -g survey /var/lib/grain_desk
USER survey
EXPOSE 8000
CMD ["python", "-m", "grain_desk"]
