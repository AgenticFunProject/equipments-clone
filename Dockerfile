FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV HOST=0.0.0.0
ENV PORT=3000

EXPOSE 3000

CMD ["uvicorn", "equipments_clone.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "3000"]
