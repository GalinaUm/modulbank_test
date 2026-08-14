import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .services import create_operation as create_operation_service


def health(request):
    return JsonResponse({"status": "ok"})

@csrf_exempt
def create_operation(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid json"}, status=400)

    operation_id = payload.get("operationId")
    if not operation_id:
        return JsonResponse({"error": "operationId is required"}, status=400)

    operation, status_code, error = create_operation_service(
        operation_id=str(operation_id),
        amount=payload.get("amount"),
        currency=payload.get("currency"),
        description=payload.get("description")
    )

    if error:
        return JsonResponse({"error": error}, status=status_code)

    return JsonResponse({
        "operationId": operation.operation_id,
        "amount": str(operation.amount),
        "currency": operation.currency,
        "description": operation.description,
        "status": operation.status,
        "providerPaymentId": operation.provider_payment_id,
    }, status=201)
