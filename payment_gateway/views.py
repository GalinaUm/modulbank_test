import json

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .services import (
    create_operation as create_operation_service,
    submit_operation as submit_operation_service,
    get_operation as get_operation_service,
    handle_receipt as handle_receipt_service,
    operation_to_dict,
    operation_events_to_dict,
    get_metrics as get_metrics_service,
)

def health(request):
    return JsonResponse({"status": "ok"})


def metrics(request):
    return JsonResponse(get_metrics_service())


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

    return JsonResponse(operation_to_dict(operation), status=201)


@csrf_exempt
def submit_operation(request, operation_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    operation, status_code = submit_operation_service(operation_id)
    if operation is None:
        return JsonResponse({"error": "operation not found"}, status=404)
    return JsonResponse(operation_to_dict(operation), status=status_code)


def get_operation(request, operation_id):
    operation, status_code, error = get_operation_service(operation_id)
    if error:
        return JsonResponse({"error": error}, status=status_code)
    return JsonResponse(operation_to_dict(operation), status=200)


def get_operation_events(request, operation_id):
    operation, status_code, error = get_operation_service(operation_id)
    if error:
        return JsonResponse({"error": error}, status=status_code)
    events = operation.events.all().order_by("event_id")
    return JsonResponse(operation_events_to_dict(events), safe=False)


@csrf_exempt
def receipt(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid json"}, status=400)

    if not all(payload.get(key) for key in ("operationId", "providerPaymentId", "result")):
        return JsonResponse(
            {"error": "operationId, providerPaymentId and result are required"},
            status=400,
        )

    operation, status_code, error = handle_receipt_service(
        payload["operationId"],
        payload["providerPaymentId"],
        payload["result"],
        payload.get("message"),
    )
    if error:
        return JsonResponse({"error": error}, status=status_code)
    return HttpResponse(status=204)