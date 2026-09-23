# Ручная проверка backend через Postman

## 1. Запуск backend

Из корня проекта:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Backend должен открыться на `http://localhost:8000`.

Если виртуальное окружение еще не создано:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Для настоящего AI-анализа заполните `.env`:

```dotenv
OPENAI_API_KEY=ваш_api_ключ
OPENAI_MODEL=доступная_вам_модель
```

Подписка ChatGPT Pro сама по себе не является API-ключом.

## 2. Окружение Postman

Создайте Environment `RedRobin local` и добавьте переменные:

| Variable | Initial value |
|---|---|
| `base_url` | `http://localhost:8000` |
| `job_id` | оставить пустым |

Выберите это окружение в правом верхнем углу Postman.

## 3. Проверка процесса

Создайте запрос:

```http
GET {{base_url}}/health
```

Ожидается статус `200`:

```json
{
  "status": "ok"
}
```

Если запрос не проходит, backend не запущен или порт `8000` занят.

## 4. Проверка готовности

```http
GET {{base_url}}/ready
```

Ожидается статус `200`:

```json
{
  "status": "ready",
  "temp_storage": true,
  "ai_configured": true
}
```

`ai_configured: false` означает, что в `.env` не заданы `OPENAI_API_KEY` или
`OPENAI_MODEL`. Загрузку файлов проверить можно, но анализ разных документов
завершится ошибкой `ai_unavailable`.

## 5. Загрузка двух документов

Создайте запрос:

```http
POST {{base_url}}/api/jobs
```

Вкладка **Body**:

1. Выберите `form-data`.
2. Добавьте поле `before_file`, смените тип с `Text` на `File` и выберите
   `materials/source/Положение_о_внутреннем_аудите_редакция_8_обезличено.docx`.
3. Добавьте поле `after_file`, смените тип с `Text` на `File` и выберите
   `materials/source/Положение_о_внутреннем_аудите_редакция_9_обезличено.docx`.
4. Не задавайте `Content-Type` вручную. Postman сам сформирует корректный
   `multipart/form-data` с boundary.

Ожидается статус `201`. Пример ответа:

```json
{
  "job_id": "7d64222a-68d8-4a37-9ea6-d43d6058cb29",
  "status": "ready",
  "documents": [
    {
      "role": "before",
      "name": "Положение_о_внутреннем_аудите_редакция_8_обезличено.docx",
      "revision_label": "8"
    },
    {
      "role": "after",
      "name": "Положение_о_внутреннем_аудите_редакция_9_обезличено.docx",
      "revision_label": "9"
    }
  ],
  "warnings": []
}
```

Чтобы Postman автоматически сохранил идентификатор, откройте вкладку
**Scripts → Post-response** этого запроса и вставьте:

```javascript
pm.test("Задание создано", function () {
    pm.response.to.have.status(201);
});

const body = pm.response.json();
pm.environment.set("job_id", body.job_id);
```

После отправки проверьте, что переменная `job_id` заполнилась.

## 6. Запуск анализа

Создайте запрос:

```http
POST {{base_url}}/api/jobs/{{job_id}}/analyze
```

Вкладка **Body → raw → JSON**:

```json
{
  "confirm_order": false,
  "force_restart": false
}
```

Ожидается статус `202`, начальный статус задания `extracting`.

Если документы случайно загружены наоборот, backend вернет `409` и код
`order_confirmation_required`. После ручной проверки порядка повторите запрос:

```json
{
  "confirm_order": true,
  "force_restart": false
}
```

## 7. Проверка статуса

```http
GET {{base_url}}/api/jobs/{{job_id}}
```

Повторяйте запрос раз в несколько секунд. Во время работы возможны статусы:

- `extracting` — извлекается текст DOCX;
- `analyzing` — выполняется смысловое сравнение;
- `verifying` — проверяются ссылки и цитаты;
- `completed` — анализ завершен;
- `completed_with_warnings` — завершен с предупреждениями;
- `failed` — анализ завершился ошибкой.

Готовый результат можно запрашивать, когда `progress` равен `1.0` и статус
равен `completed` или `completed_with_warnings`.

При `failed` смотрите объект `error`:

```json
{
  "code": "ai_unavailable",
  "message": "Сервис анализа временно недоступен. Извлеченные пункты сохранены.",
  "retryable": true
}
```

## 8. Получение результата

```http
GET {{base_url}}/api/jobs/{{job_id}}/result
```

Ожидается статус `200`. Основные поля ответа:

- `findings` — найденные изменения и риски;
- `findings[].change_type` — тип изменения;
- `findings[].before_evidence` и `after_evidence` — исходные пункты и цитаты;
- `findings[].confidence` — уверенность модели от 0 до 1;
- `findings[].validation_status` — прошла ли находка проверку доказательств;
- `warnings` — предупреждения;
- `summary` и `conclusion` — итоговое заключение.

Если анализ еще не готов, вернется `409` с кодом `result_not_ready`.

## 9. Повторный запуск

После завершения обычный повторный запуск вернет ошибку `invalid_state`.
Для принудительного повторного анализа отправьте:

```http
POST {{base_url}}/api/jobs/{{job_id}}/analyze
```

```json
{
  "confirm_order": false,
  "force_restart": true
}
```

## 10. Минимальный smoke-чек-лист

| Проверка | Ожидаемый результат |
|---|---|
| `GET /health` | `200`, `status: ok` |
| `GET /ready` | `200`, `temp_storage: true` |
| Загрузка редакций 8 и 9 | `201`, получен `job_id` |
| Результат до запуска | `409`, `result_not_ready` |
| Запуск анализа | `202` |
| Проверка статуса | прогресс доходит до `1.0` |
| Получение отчета | `200`, присутствуют `summary`, `conclusion`, `findings` |
| Несуществующий `job_id` | `404`, `job_not_found` |
| Отсутствует один файл | `400`, `missing_file` |
| Вместо DOCX отправлен TXT | `415`, `unsupported_media_type` |

## Быстрая проверка без AI

Чтобы проверить весь HTTP-сценарий без API-ключа, загрузите один и тот же DOCX
и как `before_file`, и как `after_file`. Backend определит одинаковые документы
по хешу и сформирует пустой отчет без обращения к модели. Это проверяет API и
сборку отчета, но не проверяет AI-сравнение.

Интерактивная схема API также доступна по адресу:

```text
http://localhost:8000/docs
```
