from django.db import models


class Operation(models.Model):
    """Модель операции"""

    CREATED = "created"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"

    CHOICES = [
        (CREATED, "создано"),
        (PROCESSING, "в процессе"),
        (COMPLETED, "исполнено"),
        (REJECTED, "отклонено"),
    ]

    operation_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=255, default='RUB')
    description = models.TextField(
        max_length=1000,
        null=True,
        blank=True,
        verbose_name="Описание",
        help_text="Укажите описание",
        default="",
    )
    status = models.CharField(max_length=255, choices=CHOICES, default=CREATED)
    provider_payment_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Payment operation'
        verbose_name_plural = 'Payment operations'
