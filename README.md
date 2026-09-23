# RedRobin

Прототип анализа изменений организационной структуры и функционала по двум
редакциям документа DOCX.

Сейчас реализован минимальный backend P0: безопасная загрузка двух DOCX,
временные задания, разбор абзацев и таблиц, сегментация пунктов, проверка
доказательств и JSON API. Смысловой AI-анализ и пользовательский интерфейс ещё
не подключены. Полные требования находятся в `docs/TECH_SPEC.md`.

## Требования

- Python 3.12
- ключ OpenAI API и доступная модель для будущей AI-части
- Docker опционально

## Локальный запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

После запуска проверка доступна по адресу `http://localhost:8000/health`, а
OpenAPI — по адресу `http://localhost:8000/docs`.

## Backend API

- `GET /health` — состояние процесса без обращения к AI.
- `GET /ready` — доступность временного каталога и состояние AI-конфигурации.
- `POST /api/jobs` — загрузка `before_file` и `after_file` в формате DOCX.
- `POST /api/jobs/{job_id}/analyze` — запуск обработки документов.
- `GET /api/jobs/{job_id}` — статус и прогресс задания.
- `GET /api/jobs/{job_id}/result` — результат завершённого задания.

Если AI не настроен, анализ разных документов завершается контролируемой ошибкой
`ai_unavailable`. Разобранные документы сохраняются как JSON-артефакты задания,
поэтому после настройки AI их можно обработать повторно без новой загрузки DOCX.
Для двух идентичных файлов формируется пустой отчёт без обращения к модели.

Пошаговая ручная проверка всех эндпоинтов через Postman описана в
[`docs/POSTMAN_TESTING.md`](docs/POSTMAN_TESTING.md).

## Тесты

```bash
pytest
```

## Docker

```bash
docker build -t redrobin .
docker run --rm -p 8000:8000 --env-file .env redrobin
```

## Структура

```text
app/
  api/        HTTP-маршруты
  models/     общие контракты API, домена и AI
  services/   парсинг, сопоставление, проверка и отчёт
  ai/         клиент OpenAI, промпты и схемы
  templates/  серверные HTML-шаблоны
  static/     CSS и JavaScript
tests/
  fixtures/   контрольный набор и тестовые данные
docs/
  TECH_SPEC.md
```

Секреты должны храниться только в локальном `.env`. Этот файл исключён из Git
и Docker-контекста.
