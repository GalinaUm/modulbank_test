from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Operation, OperationEvent, SubmitIntent

from django.db.models import Max


def create_event(operation, event_type, to_status, message, from_status=None):
    max_id = (OperationEvent.objects
              .filter(operation=operation)
              .aggregate(max_event=Max('event_id'))
              .get('max_event') or 0)
    return OperationEvent.objects.create(
        operation=operation,
        event_id=max_id + 1,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        message=message,
        occurred_at=timezone.now(),
    )


def create_operation(operation_id, amount, currency, description):
    try:
        amount = Decimal(amount)
    except (InvalidOperation, TypeError):
        return None, 400, 'amount must be a decimal string'

    if amount <= 0:
        return None, 400, 'amount must be positive'
    if amount != amount.quantize(Decimal('0.01')):
        return None, 400, 'amount must have at most 2 decimal places'
    if currency != 'RUB':
        return None, 400, 'unsupported currency'

    try:
        with transaction.atomic():
            operation = Operation.objects.create(
                operation_id=operation_id,
                amount=amount,
                currency=currency,
                description=description or '',
            )
            create_event(operation, 'CREATED', Operation.CREATED, 'Operation created')
    except IntegrityError:
        return None, 409, 'operation already exists'

    return operation, 201, None


def operation_to_dict(operation):
    return {
        'operationId': operation.operation_id,
        "amount": str(operation.amount),
        "currency": operation.currency,
        "description": operation.description,
        "status": operation.status,
        "providerPaymentId": operation.provider_payment_id,
    }


def _on_submit_committed(operation):
    from .provider_client import send_payment
    send_payment(operation)


def submit_operation(operation_id):
    try:
        with transaction.atomic():
            operation = Operation.objects.select_for_update().get(operation_id=operation_id)
            if operation.status != Operation.CREATED:
                return operation, 200
            SubmitIntent.objects.get_or_create(operation=operation)
            operation.status = Operation.PROCESSING
            operation.save(update_fields=["status", "updated_at"])
            create_event(
                operation,
                'SUBMITTED',
                Operation.PROCESSING,
                "Submit intent persisted",
                from_status=Operation.CREATED
            )
            transaction.on_commit(lambda: _on_submit_committed(operation))
        return operation, 202
    except Operation.DoesNotExist:
        return None, 404


def save_provider_payment_id(operation, provider_payment_id):
    with transaction.atomic():
        current = Operation.objects.select_for_update().get(operation_id=operation.operation_id)
        if current.provider_payment_id is None:
            current.provider_payment_id = provider_payment_id
            current.save(update_fields=["provider_payment_id", "updated_at"])
    return current


def get_operation(operation_id):
    try:
        operation = Operation.objects.get(operation_id=operation_id)
        return operation, 200, None
    except Operation.DoesNotExist:
        return None, 404, "Operation does not exist"

def operation_events_to_dict(events):
    return [{
        'eventId': e.event_id,
        'type': e.event_type,
        'fromStatus': e.from_status,
        'toStatus': e.to_status,
        'message': e.message,
        'occurredAt': e.occurred_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }  for e in events]