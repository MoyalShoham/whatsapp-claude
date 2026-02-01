"""Background task scheduler for invoice automation."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    """Task priority levels."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class TaskResult:
    """Result of a task execution."""

    success: bool
    message: str
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    executed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ScheduledTask:
    """Represents a scheduled task."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    task_type: str = ""
    scheduled_at: datetime = field(default_factory=datetime.utcnow)
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    result: Optional[TaskResult] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def can_retry(self) -> bool:
        """Check if task can be retried."""
        return self.retry_count < self.max_retries

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type,
            "scheduled_at": self.scheduled_at.isoformat(),
            "priority": self.priority.name,
            "status": self.status.value,
            "payload": self.payload,
            "result": {
                "success": self.result.success,
                "message": self.result.message,
                "executed_at": self.result.executed_at.isoformat(),
            } if self.result else None,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat(),
        }


class BaseTask(ABC):
    """Base class for all scheduled tasks."""

    name: str = "base_task"
    task_type: str = "base"

    @abstractmethod
    async def execute(self, payload: dict[str, Any]) -> TaskResult:
        """
        Execute the task.

        Args:
            payload: Task-specific data.

        Returns:
            TaskResult with execution outcome.
        """
        ...

    def should_retry(self, result: TaskResult) -> bool:
        """Determine if task should be retried on failure."""
        return not result.success


class ReminderTask(BaseTask):
    """Send payment reminders for upcoming due dates."""

    name = "payment_reminder"
    task_type = "reminder"

    def __init__(
        self,
        send_message: Callable[[str, str], Awaitable[Any]],
        get_invoice: Callable[[str], Optional[dict[str, Any]]],
    ):
        """
        Initialize reminder task.

        Args:
            send_message: Coroutine to send message (phone, message).
            get_invoice: Function to get invoice details.
        """
        self.send_message = send_message
        self.get_invoice = get_invoice

    async def execute(self, payload: dict[str, Any]) -> TaskResult:
        """Send payment reminder."""
        invoice_id = payload.get("invoice_id")
        customer_phone = payload.get("customer_phone")
        days_until_due = payload.get("days_until_due", 0)

        if not invoice_id or not customer_phone:
            return TaskResult(
                success=False,
                message="Missing invoice_id or customer_phone",
                error="INVALID_PAYLOAD",
            )

        # Get invoice details
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return TaskResult(
                success=False,
                message=f"Invoice {invoice_id} not found",
                error="INVOICE_NOT_FOUND",
            )

        # Compose reminder message
        if days_until_due > 0:
            message = (
                f"Reminder: Invoice {invoice_id} is due in {days_until_due} days. "
                f"Amount: ${invoice.get('amount', 'N/A')}. "
                "Please ensure payment is made on time."
            )
        elif days_until_due == 0:
            message = (
                f"Reminder: Invoice {invoice_id} is due today. "
                f"Amount: ${invoice.get('amount', 'N/A')}. "
                "Please make payment as soon as possible."
            )
        else:
            message = (
                f"Notice: Invoice {invoice_id} was due {abs(days_until_due)} days ago. "
                f"Amount: ${invoice.get('amount', 'N/A')}. "
                "Please contact us regarding payment."
            )

        try:
            await self.send_message(customer_phone, message)
            logger.info(f"Sent reminder for invoice {invoice_id} to {customer_phone}")

            return TaskResult(
                success=True,
                message=f"Reminder sent for invoice {invoice_id}",
                data={
                    "invoice_id": invoice_id,
                    "customer_phone": customer_phone,
                    "days_until_due": days_until_due,
                },
            )
        except Exception as e:
            logger.exception(f"Failed to send reminder: {e}")
            return TaskResult(
                success=False,
                message=f"Failed to send reminder: {str(e)}",
                error="SEND_FAILED",
            )


class OverdueCheckTask(BaseTask):
    """Check for overdue invoices and trigger notifications."""

    name = "overdue_check"
    task_type = "maintenance"

    def __init__(
        self,
        list_invoices: Callable[..., list[dict[str, Any]]],
        schedule_reminder: Callable[[str, str, int], None],
    ):
        """
        Initialize overdue check task.

        Args:
            list_invoices: Function to list invoices.
            schedule_reminder: Function to schedule a reminder.
        """
        self.list_invoices = list_invoices
        self.schedule_reminder = schedule_reminder

    async def execute(self, payload: dict[str, Any]) -> TaskResult:
        """Check for overdue invoices."""
        try:
            # Get all pending payment invoices
            invoices = self.list_invoices(state="payment_pending")

            overdue_count = 0
            notified_count = 0

            for invoice in invoices:
                due_date_str = invoice.get("due_date")
                if not due_date_str:
                    continue

                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
                days_overdue = (datetime.utcnow() - due_date).days

                if days_overdue > 0:
                    overdue_count += 1
                    customer_phone = invoice.get("customer_phone")

                    if customer_phone:
                        self.schedule_reminder(
                            invoice["invoice_id"],
                            customer_phone,
                            -days_overdue,  # Negative indicates overdue
                        )
                        notified_count += 1

            return TaskResult(
                success=True,
                message=f"Found {overdue_count} overdue invoices, scheduled {notified_count} notifications",
                data={
                    "overdue_count": overdue_count,
                    "notified_count": notified_count,
                },
            )
        except Exception as e:
            logger.exception(f"Overdue check failed: {e}")
            return TaskResult(
                success=False,
                message=f"Overdue check failed: {str(e)}",
                error="CHECK_FAILED",
            )


class PaymentFollowUpTask(BaseTask):
    """Follow up on pending payments."""

    name = "payment_followup"
    task_type = "followup"

    def __init__(
        self,
        send_message: Callable[[str, str], asyncio.coroutine],
        get_invoice: Callable[[str], Optional[dict[str, Any]]],
        update_metadata: Callable[[str, dict[str, Any]], None],
    ):
        """
        Initialize follow-up task.

        Args:
            send_message: Coroutine to send message.
            get_invoice: Function to get invoice.
            update_metadata: Function to update invoice metadata.
        """
        self.send_message = send_message
        self.get_invoice = get_invoice
        self.update_metadata = update_metadata

    async def execute(self, payload: dict[str, Any]) -> TaskResult:
        """Send payment follow-up."""
        invoice_id = payload.get("invoice_id")
        customer_phone = payload.get("customer_phone")
        followup_number = payload.get("followup_number", 1)

        if not invoice_id or not customer_phone:
            return TaskResult(
                success=False,
                message="Missing required payload fields",
                error="INVALID_PAYLOAD",
            )

        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return TaskResult(
                success=False,
                message=f"Invoice {invoice_id} not found",
                error="INVOICE_NOT_FOUND",
            )

        # Compose follow-up message based on attempt number
        if followup_number == 1:
            message = (
                f"Hi! Just following up on invoice {invoice_id}. "
                "Have you had a chance to review it? "
                "Let us know if you have any questions."
            )
        elif followup_number == 2:
            message = (
                f"Hello, this is a second follow-up for invoice {invoice_id}. "
                "Please let us know your payment timeline or if there are any issues."
            )
        else:
            message = (
                f"Important: Invoice {invoice_id} requires your attention. "
                "Please contact us immediately regarding payment status."
            )

        try:
            await self.send_message(customer_phone, message)

            # Update followup count in metadata
            self.update_metadata(invoice_id, {
                "last_followup": datetime.utcnow().isoformat(),
                "followup_count": followup_number,
            })

            return TaskResult(
                success=True,
                message=f"Follow-up #{followup_number} sent for invoice {invoice_id}",
                data={
                    "invoice_id": invoice_id,
                    "followup_number": followup_number,
                },
            )
        except Exception as e:
            logger.exception(f"Follow-up failed: {e}")
            return TaskResult(
                success=False,
                message=f"Follow-up failed: {str(e)}",
                error="SEND_FAILED",
            )


class InvoiceScheduler:
    """Main scheduler for invoice-related background tasks."""

    def __init__(self):
        """Initialize the scheduler."""
        self._tasks: dict[str, ScheduledTask] = {}
        self._handlers: dict[str, BaseTask] = {}
        self._running = False
        self._task_queue: asyncio.Queue[ScheduledTask] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None

    def register_handler(self, task_type: str, handler: BaseTask) -> None:
        """Register a task handler."""
        self._handlers[task_type] = handler
        logger.info(f"Registered handler for task type: {task_type}")

    def schedule(
        self,
        task_type: str,
        payload: dict[str, Any],
        run_at: Optional[datetime] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        name: Optional[str] = None,
    ) -> ScheduledTask:
        """
        Schedule a new task.

        Args:
            task_type: Type of task to run.
            payload: Task-specific data.
            run_at: When to run (default: now).
            priority: Task priority.
            name: Optional task name.

        Returns:
            The scheduled task.
        """
        task = ScheduledTask(
            name=name or f"{task_type}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            task_type=task_type,
            scheduled_at=run_at or datetime.utcnow(),
            priority=priority,
            payload=payload,
        )

        self._tasks[task.id] = task
        logger.info(f"Scheduled task {task.id} of type {task_type} for {task.scheduled_at}")

        return task

    def schedule_reminder(
        self,
        invoice_id: str,
        customer_phone: str,
        days_until_due: int,
        run_at: Optional[datetime] = None,
    ) -> ScheduledTask:
        """Convenience method to schedule a payment reminder."""
        return self.schedule(
            task_type="reminder",
            payload={
                "invoice_id": invoice_id,
                "customer_phone": customer_phone,
                "days_until_due": days_until_due,
            },
            run_at=run_at,
            priority=TaskPriority.HIGH if days_until_due <= 0 else TaskPriority.NORMAL,
            name=f"reminder_{invoice_id}",
        )

    def schedule_recurring(
        self,
        task_type: str,
        payload: dict[str, Any],
        interval: timedelta,
        name: Optional[str] = None,
    ) -> str:
        """
        Schedule a recurring task.

        Args:
            task_type: Type of task.
            payload: Task data.
            interval: Time between executions.
            name: Task name.

        Returns:
            Recurring task identifier.
        """
        recurring_id = str(uuid4())

        async def reschedule():
            while self._running:
                task = self.schedule(
                    task_type=task_type,
                    payload=payload,
                    name=name or f"recurring_{recurring_id}",
                )
                await asyncio.sleep(interval.total_seconds())

        # Start the recurring scheduler
        asyncio.create_task(reschedule())
        logger.info(f"Scheduled recurring task {recurring_id} every {interval}")

        return recurring_id

    def cancel(self, task_id: str) -> bool:
        """Cancel a scheduled task."""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
            logger.info(f"Cancelled task {task_id}")
            return True
        return False

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def list_pending(self) -> list[ScheduledTask]:
        """List all pending tasks."""
        return [
            task for task in self._tasks.values()
            if task.status == TaskStatus.PENDING
        ]

    async def _execute_task(self, task: ScheduledTask) -> None:
        """Execute a single task."""
        handler = self._handlers.get(task.task_type)
        if not handler:
            logger.error(f"No handler for task type: {task.task_type}")
            task.status = TaskStatus.FAILED
            task.result = TaskResult(
                success=False,
                message=f"No handler for task type: {task.task_type}",
                error="NO_HANDLER",
            )
            return

        task.status = TaskStatus.RUNNING
        logger.info(f"Executing task {task.id} ({task.name})")

        try:
            result = await handler.execute(task.payload)
            task.result = result

            if result.success:
                task.status = TaskStatus.COMPLETED
                logger.info(f"Task {task.id} completed: {result.message}")
            else:
                if handler.should_retry(result) and task.can_retry:
                    task.retry_count += 1
                    task.status = TaskStatus.PENDING
                    task.scheduled_at = datetime.utcnow() + timedelta(minutes=5)
                    logger.warning(
                        f"Task {task.id} failed, retry {task.retry_count}/{task.max_retries}"
                    )
                else:
                    task.status = TaskStatus.FAILED
                    logger.error(f"Task {task.id} failed: {result.message}")

        except Exception as e:
            logger.exception(f"Task {task.id} raised exception: {e}")
            task.result = TaskResult(
                success=False,
                message=str(e),
                error="EXCEPTION",
            )
            task.status = TaskStatus.FAILED

    async def _worker(self) -> None:
        """Background worker that processes tasks."""
        logger.info("Scheduler worker started")

        while self._running:
            # Get pending tasks that are due
            now = datetime.utcnow()
            due_tasks = [
                task for task in self._tasks.values()
                if task.status == TaskStatus.PENDING and task.scheduled_at <= now
            ]

            # Sort by priority (higher first) then by scheduled time
            due_tasks.sort(key=lambda t: (-t.priority.value, t.scheduled_at))

            for task in due_tasks:
                await self._execute_task(task)

            # Sleep briefly before checking again
            await asyncio.sleep(1)

        logger.info("Scheduler worker stopped")

    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    def get_stats(self) -> dict[str, Any]:
        """Get scheduler statistics."""
        status_counts = {}
        for task in self._tasks.values():
            status_counts[task.status.value] = status_counts.get(task.status.value, 0) + 1

        return {
            "total_tasks": len(self._tasks),
            "running": self._running,
            "registered_handlers": list(self._handlers.keys()),
            "by_status": status_counts,
        }


async def setup_invoice_scheduler(
    send_message: Callable[[str, str], asyncio.coroutine],
    get_invoice: Callable[[str], Optional[dict[str, Any]]],
    list_invoices: Callable[..., list[dict[str, Any]]],
    update_metadata: Callable[[str, dict[str, Any]], None],
) -> InvoiceScheduler:
    """
    Create and configure an invoice scheduler with default tasks.

    Args:
        send_message: Coroutine to send WhatsApp message.
        get_invoice: Function to get invoice details.
        list_invoices: Function to list invoices.
        update_metadata: Function to update invoice metadata.

    Returns:
        Configured InvoiceScheduler.
    """
    scheduler = InvoiceScheduler()

    # Register handlers
    scheduler.register_handler(
        "reminder",
        ReminderTask(send_message, get_invoice),
    )

    scheduler.register_handler(
        "followup",
        PaymentFollowUpTask(send_message, get_invoice, update_metadata),
    )

    scheduler.register_handler(
        "maintenance",
        OverdueCheckTask(
            list_invoices,
            lambda inv_id, phone, days: scheduler.schedule_reminder(inv_id, phone, days),
        ),
    )

    # Schedule daily overdue check at midnight
    scheduler.schedule_recurring(
        task_type="maintenance",
        payload={},
        interval=timedelta(hours=24),
        name="daily_overdue_check",
    )

    return scheduler
