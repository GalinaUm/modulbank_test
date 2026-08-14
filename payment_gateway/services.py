from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Operation, OperationEvent

from django.db.models import Max


def create_event(operation, event_type, to_status, message, from_status=None):
    max_id = (OperationEvent.objects
              .filter(operation=operation)
              .aggregate(max_event=Max('event_id'))['max_event'] or 0)
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