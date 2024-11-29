"""Tests for the Google Tasks todo component."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.components.google_tasks.todo import (
    GoogleTaskTodoListEntity,
    _convert_api_item,
    _convert_todo_item,
    _extract_date_time,
    _format_due_datetime,
    _is_priority_task,
    _order_tasks,
)
from homeassistant.components.todo import TodoItem, TodoItemStatus
from homeassistant.util import dt as dt_util


def test_convert_api_item():
    """Test conversion of API item to TodoItem."""
    api_item = {
        "id": "123",
        "title": "Buy groceries",
        "status": "needsAction",
        "due": "2024-11-28T10:00:00Z",
        "notes": "Milk, eggs, bread",
    }
    result = _convert_api_item(api_item)
    assert result.summary == "Buy groceries"
    assert result.status == TodoItemStatus.NEEDS_ACTION
    assert result.due == dt_util.parse_datetime("2024-11-28T10:00:00Z")
    assert result.description == "Milk, eggs, bread"


def test_extract_date_time():
    """Test extraction of date and time from text."""
    text = "Meeting on 27 Nov 2024 at 18:00"
    date, time = _extract_date_time(text)
    assert date == datetime(2024, 11, 27, 18, 0)
    assert time == "18:00"


def test_is_priority_task():
    """Test priority task detection."""
    assert _is_priority_task("Urgent meeting", "Important discussion")
    assert not _is_priority_task("Regular task", "Nothing special")


def test_format_due_datetime():
    """Test formatting of due datetime."""
    due = datetime(2024, 11, 27, 18, 0)
    result = _format_due_datetime(due)
    assert result == "2024-11-27T18:00:00Z"


def test_order_tasks():
    """Test ordering of tasks."""
    tasks = [
        {"id": "1", "title": "Regular task", "position": "1"},
        {"id": "2", "title": "Urgent meeting", "position": "2"},
        {"id": "3", "title": "Another task", "position": "3"},
    ]
    ordered = _order_tasks(tasks)
    assert ordered[0]["id"] == "2"
    assert ordered[1]["id"] == "1"
    assert ordered[2]["id"] == "3"


def test_convert_todo_item_no_due_date():
    """Test conversion of TodoItem without due date."""
    item = TodoItem(summary="Task without due date", status=TodoItemStatus.NEEDS_ACTION)
    result = _convert_todo_item(item)
    assert "due" not in result or result["due"] is None


def test_convert_api_item_no_due_date():
    """Test conversion of API item without due date."""
    api_item = {"id": "123", "title": "Task without due date", "status": "needsAction"}
    result = _convert_api_item(api_item)
    assert result.due is None


def test_extract_date_time_invalid_format():
    """Test extraction from invalid date format."""
    text = "Meeting on invalid date"
    date, time = _extract_date_time(text)
    assert date is None
    assert time is None


def test_is_priority_task_case_insensitive():
    """Test priority task detection case insensitivity."""
    assert _is_priority_task("urgent meeting", "important discussion")
    assert _is_priority_task("ASAP task", None)


def test_convert_todo_item_completed_status():
    """Test conversion of completed TodoItem."""
    item = TodoItem(summary="Completed task", status=TodoItemStatus.COMPLETED)
    result = _convert_todo_item(item)
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_async_create_todo_item():
    """Test async creation of a todo item."""
    coordinator = Mock()
    coordinator.api.insert = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    entity = GoogleTaskTodoListEntity(coordinator, "Test List", "config_id", "list_id")

    item = TodoItem(summary="New task", status=TodoItemStatus.NEEDS_ACTION)

    await entity.async_create_todo_item(item)

    coordinator.api.insert.assert_called_once()
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_update_todo_item():
    """Test async updating of a todo item."""
    coordinator = Mock()
    coordinator.api.patch = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.data = [{"id": "123", "title": "Old task"}]

    entity = GoogleTaskTodoListEntity(coordinator, "Test List", "config_id", "list_id")

    item = TodoItem(
        uid="123", summary="Updated task", status=TodoItemStatus.NEEDS_ACTION
    )

    await entity.async_update_todo_item(item)

    coordinator.api.patch.assert_called_once()
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_delete_todo_items():
    """Test async deletion of todo items."""
    coordinator = Mock()
    coordinator.api.delete = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    entity = GoogleTaskTodoListEntity(coordinator, "Test List", "config_id", "list_id")

    await entity.async_delete_todo_items(["123", "456"])

    coordinator.api.delete.assert_called_once_with("list_id", ["123", "456"])
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_move_todo_item():
    """Test async moving of a todo item."""
    coordinator = Mock()
    coordinator.api.move = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    entity = GoogleTaskTodoListEntity(coordinator, "Test List", "config_id", "list_id")

    await entity.async_move_todo_item("123", previous_uid="456")

    coordinator.api.move.assert_called_once_with("list_id", "123", previous="456")
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_integration_create_and_delete_todo_items():
    """Test integration of creating and deleting todo items."""
    coordinator = Mock()
    coordinator.api.insert = AsyncMock(side_effect=[{"id": "task1"}, {"id": "task2"}])
    coordinator.api.delete = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.data = []

    entity = GoogleTaskTodoListEntity(coordinator, "Test List", "config_id", "list_id")

    # Create two new items
    item1 = TodoItem(summary="Task 1", status=TodoItemStatus.NEEDS_ACTION)
    item2 = TodoItem(summary="Task 2", status=TodoItemStatus.NEEDS_ACTION)
    await entity.async_create_todo_item(item1)
    await entity.async_create_todo_item(item2)

    # Delete both items
    await entity.async_delete_todo_items(["task1", "task2"])

    assert coordinator.api.insert.call_count == 2
    coordinator.api.delete.assert_called_once_with("list_id", ["task1", "task2"])
    assert coordinator.async_refresh.call_count == 3

