"""Tests for the scheduler module."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from scheduler.tasks import (
    InvoiceScheduler,
    OverdueCheckTask,
    PaymentFollowUpTask,
    ReminderTask,
    ScheduledTask,
    TaskPriority,
    TaskResult,
    TaskStatus,
)


class TestScheduledTask:
    """Test ScheduledTask dataclass."""

    def test_create_task(self):
        """Test creating a scheduled task."""
        task = ScheduledTask(
            name="test_task",
            task_type="reminder",
            payload={"invoice_id": "INV-001"},
        )

        assert task.name == "test_task"
        assert task.task_type == "reminder"
        assert task.status == TaskStatus.PENDING
        assert task.can_retry is True

    def test_can_retry_after_max(self):
        """Test can_retry returns False after max retries."""
        task = ScheduledTask(
            name="test_task",
            task_type="reminder",
            retry_count=3,
            max_retries=3,
        )

        assert task.can_retry is False

    def test_to_dict(self):
        """Test converting task to dictionary."""
        task = ScheduledTask(
            name="test_task",
            task_type="reminder",
            payload={"invoice_id": "INV-001"},
        )

        data = task.to_dict()

        assert data["name"] == "test_task"
        assert data["task_type"] == "reminder"
        assert data["status"] == "pending"
        assert data["payload"]["invoice_id"] == "INV-001"


class TestReminderTask:
    """Test ReminderTask."""

    @pytest.fixture
    def reminder_task(self):
        """Create a reminder task with mocks."""
        send_message = AsyncMock()
        get_invoice = MagicMock(return_value={
            "invoice_id": "INV-001",
            "amount": "100.00",
            "state": "payment_pending",
        })
        return ReminderTask(send_message, get_invoice)

    @pytest.mark.asyncio
    async def test_execute_upcoming_reminder(self, reminder_task):
        """Test sending reminder for upcoming due date."""
        result = await reminder_task.execute({
            "invoice_id": "INV-001",
            "customer_phone": "+1234567890",
            "days_until_due": 3,
        })

        assert result.success is True
        reminder_task.send_message.assert_called_once()
        call_args = reminder_task.send_message.call_args
        assert "+1234567890" in call_args[0]
        assert "3 days" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_execute_due_today(self, reminder_task):
        """Test reminder for due date today."""
        result = await reminder_task.execute({
            "invoice_id": "INV-001",
            "customer_phone": "+1234567890",
            "days_until_due": 0,
        })

        assert result.success is True
        call_args = reminder_task.send_message.call_args
        assert "due today" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_execute_overdue(self, reminder_task):
        """Test reminder for overdue invoice."""
        result = await reminder_task.execute({
            "invoice_id": "INV-001",
            "customer_phone": "+1234567890",
            "days_until_due": -5,
        })

        assert result.success is True
        call_args = reminder_task.send_message.call_args
        assert "5 days ago" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_execute_missing_payload(self, reminder_task):
        """Test execution with missing payload."""
        result = await reminder_task.execute({})

        assert result.success is False
        assert result.error == "INVALID_PAYLOAD"

    @pytest.mark.asyncio
    async def test_execute_invoice_not_found(self, reminder_task):
        """Test execution when invoice not found."""
        reminder_task.get_invoice.return_value = None

        result = await reminder_task.execute({
            "invoice_id": "INV-MISSING",
            "customer_phone": "+1234567890",
            "days_until_due": 3,
        })

        assert result.success is False
        assert result.error == "INVOICE_NOT_FOUND"


class TestPaymentFollowUpTask:
    """Test PaymentFollowUpTask."""

    @pytest.fixture
    def followup_task(self):
        """Create a follow-up task with mocks."""
        send_message = AsyncMock()
        get_invoice = MagicMock(return_value={
            "invoice_id": "INV-001",
            "amount": "100.00",
        })
        update_metadata = MagicMock()
        return PaymentFollowUpTask(send_message, get_invoice, update_metadata)

    @pytest.mark.asyncio
    async def test_first_followup(self, followup_task):
        """Test first follow-up message."""
        result = await followup_task.execute({
            "invoice_id": "INV-001",
            "customer_phone": "+1234567890",
            "followup_number": 1,
        })

        assert result.success is True
        call_args = followup_task.send_message.call_args
        assert "following up" in call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_second_followup(self, followup_task):
        """Test second follow-up message."""
        result = await followup_task.execute({
            "invoice_id": "INV-001",
            "customer_phone": "+1234567890",
            "followup_number": 2,
        })

        assert result.success is True
        call_args = followup_task.send_message.call_args
        assert "second follow-up" in call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_updates_metadata(self, followup_task):
        """Test that metadata is updated after follow-up."""
        await followup_task.execute({
            "invoice_id": "INV-001",
            "customer_phone": "+1234567890",
            "followup_number": 1,
        })

        followup_task.update_metadata.assert_called_once()
        call_args = followup_task.update_metadata.call_args
        assert call_args[0][0] == "INV-001"
        assert "last_followup" in call_args[0][1]
        assert call_args[0][1]["followup_count"] == 1


class TestInvoiceScheduler:
    """Test InvoiceScheduler."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler instance."""
        return InvoiceScheduler()

    def test_schedule_task(self, scheduler):
        """Test scheduling a task."""
        task = scheduler.schedule(
            task_type="reminder",
            payload={"invoice_id": "INV-001"},
            name="test_reminder",
        )

        assert task.name == "test_reminder"
        assert task.task_type == "reminder"
        assert task.status == TaskStatus.PENDING

    def test_schedule_with_priority(self, scheduler):
        """Test scheduling with priority."""
        task = scheduler.schedule(
            task_type="reminder",
            payload={},
            priority=TaskPriority.CRITICAL,
        )

        assert task.priority == TaskPriority.CRITICAL

    def test_schedule_for_future(self, scheduler):
        """Test scheduling for future time."""
        future_time = datetime.utcnow() + timedelta(hours=1)

        task = scheduler.schedule(
            task_type="reminder",
            payload={},
            run_at=future_time,
        )

        assert task.scheduled_at == future_time

    def test_schedule_reminder_convenience(self, scheduler):
        """Test schedule_reminder convenience method."""
        task = scheduler.schedule_reminder(
            invoice_id="INV-001",
            customer_phone="+1234567890",
            days_until_due=3,
        )

        assert task.task_type == "reminder"
        assert task.payload["invoice_id"] == "INV-001"
        assert task.payload["customer_phone"] == "+1234567890"
        assert task.priority == TaskPriority.NORMAL

    def test_schedule_overdue_reminder_high_priority(self, scheduler):
        """Test that overdue reminders get high priority."""
        task = scheduler.schedule_reminder(
            invoice_id="INV-001",
            customer_phone="+1234567890",
            days_until_due=-1,  # Overdue
        )

        assert task.priority == TaskPriority.HIGH

    def test_cancel_task(self, scheduler):
        """Test cancelling a task."""
        task = scheduler.schedule(
            task_type="reminder",
            payload={},
        )

        result = scheduler.cancel(task.id)

        assert result is True
        assert task.status == TaskStatus.CANCELLED

    def test_cancel_nonexistent_task(self, scheduler):
        """Test cancelling non-existent task."""
        result = scheduler.cancel("nonexistent-id")

        assert result is False

    def test_get_task(self, scheduler):
        """Test getting a task by ID."""
        task = scheduler.schedule(
            task_type="reminder",
            payload={},
        )

        retrieved = scheduler.get_task(task.id)

        assert retrieved is not None
        assert retrieved.id == task.id

    def test_list_pending(self, scheduler):
        """Test listing pending tasks."""
        task1 = scheduler.schedule(task_type="reminder", payload={})
        task2 = scheduler.schedule(task_type="reminder", payload={})
        scheduler.cancel(task2.id)

        pending = scheduler.list_pending()

        assert len(pending) == 1
        assert pending[0].id == task1.id

    def test_register_handler(self, scheduler):
        """Test registering a handler."""
        handler = MagicMock(spec=ReminderTask)

        scheduler.register_handler("test_type", handler)

        assert "test_type" in scheduler._handlers

    def test_get_stats(self, scheduler):
        """Test getting scheduler statistics."""
        scheduler.schedule(task_type="reminder", payload={})
        scheduler.schedule(task_type="reminder", payload={})

        stats = scheduler.get_stats()

        assert stats["total_tasks"] == 2
        assert stats["running"] is False
        assert "pending" in stats["by_status"]

    @pytest.mark.asyncio
    async def test_start_stop(self, scheduler):
        """Test starting and stopping the scheduler."""
        await scheduler.start()
        assert scheduler._running is True

        await scheduler.stop()
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_execute_task(self, scheduler):
        """Test task execution."""
        handler = AsyncMock()
        handler.execute.return_value = TaskResult(
            success=True,
            message="Done",
        )
        handler.should_retry.return_value = True

        scheduler.register_handler("test", handler)

        task = scheduler.schedule(
            task_type="test",
            payload={"key": "value"},
        )

        await scheduler._execute_task(task)

        assert task.status == TaskStatus.COMPLETED
        handler.execute.assert_called_once_with({"key": "value"})


class TestOverdueCheckTask:
    """Test OverdueCheckTask."""

    @pytest.fixture
    def overdue_task(self):
        """Create an overdue check task with mocks."""
        list_invoices = MagicMock()
        schedule_reminder = MagicMock()
        return OverdueCheckTask(list_invoices, schedule_reminder)

    @pytest.mark.asyncio
    async def test_finds_overdue_invoices(self, overdue_task):
        """Test finding overdue invoices."""
        yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()
        overdue_task.list_invoices.return_value = [
            {
                "invoice_id": "INV-001",
                "due_date": yesterday,
                "customer_phone": "+1234567890",
            },
        ]

        result = await overdue_task.execute({})

        assert result.success is True
        assert result.data["overdue_count"] == 1
        overdue_task.schedule_reminder.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_future_due_dates(self, overdue_task):
        """Test that future due dates are ignored."""
        tomorrow = (datetime.utcnow() + timedelta(days=1)).isoformat()
        overdue_task.list_invoices.return_value = [
            {
                "invoice_id": "INV-001",
                "due_date": tomorrow,
                "customer_phone": "+1234567890",
            },
        ]

        result = await overdue_task.execute({})

        assert result.success is True
        assert result.data["overdue_count"] == 0
        overdue_task.schedule_reminder.assert_not_called()
