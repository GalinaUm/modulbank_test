# Payment gateway (ModuleDev internship entry task)

Сервис проводит платёжную операцию через внешнего провайдера (provider-simulator)
и сохраняет корректное состояние при повторах, конкурентных запросах, потерянных
HTTP-ответах и перезапусках. Финальный статус операции определяется только
callback-квитанцией провайдера.

## Стек

- Python / Django 6.1
- PostgreSQL (постоянное хранилище)
- Docker Compose

## Запуск

```
docker compose up --build
```

Сервис кандидата слушает порт `8080`, провайдер-симулятор — `8081`.
Данные хранятся в volume `postgres-data` и переживают пересоздание контейнеров.

## Сквозной сценарий

1. Создать операцию:

```
curl -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"op-1","amount":"1000.00","currency":"RUB","description":"Оплата заказа"}'
```

2. Отправить операцию на платёж:

```
curl -X POST http://localhost:8080/operations/op-1/submit
```

3. Дождаться, пока симулятор пришлёт callback на `POST /receipts`
(обычно ~1–2 секунды), и проверить состояние и историю:

```
curl http://localhost:8080/operations/op-1
curl http://localhost:8080/operations/op-1/events
```

Ожидание: `status: COMPLETED`, `providerPaymentId` заполнен,
в истории события `CREATED`, `SUBMITTED`, `COMPLETED`.

## Проверка восстановления после перезапуска

```
docker compose restart candidate-service
```

Незавершённые операции `PROCESSING` доотправляются с прежним `Idempotency-Key`,
второй платёж не создаётся.

## Тесты

```
docker compose run --rm candidate-service python manage.py test payment_gateway
```

33 теста: контракт API, валидация, конкурентный submit (ровно один намерение),
идемпотентные и конфликтующие квитанции, восстановление, клиент провайдера
(заголовки, backoff, поведение при сетевых сбоях).

## Структура

- `payment_gateway/models.py` — Operation, OperationEvent, SubmitIntent
- `payment_gateway/services.py` — переходы состояний, события, обработка квитанций
- `payment_gateway/provider_client.py` — HTTP к провайдеру, Idempotency-Key, backoff
- `payment_gateway/apps.py` — досылка PROCESSING-операций при старте