from datetime import datetime, timedelta
import logging
import re
from typing import Any, cast

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import AsyncConfigEntryAuth
from .const import DOMAIN
from .coordinator import TaskUpdateCoordinator
from .exceptions import GoogleTasksApiError

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=15)

TODO_STATUS_MAP = {
    "needsAction": TodoItemStatus.NEEDS_ACTION,
    "completed": TodoItemStatus.COMPLETED,
}
TODO_STATUS_MAP_INV = {v: k for k, v in TODO_STATUS_MAP.items()}

DATE_PATTERNS = [
    (r"\d{4}/\d{2}/\d{2}", "%Y/%m/%d"),
    (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
    (r"\d{2}/\d{2}/\d{4}", "%m/%d/%Y"),
    (
        r"\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{4}",
        "%d %b %Y",
    ),
    (
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{1,2},?\s\d{4}",
        "%b %d %Y",
    ),
    (
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{1,2}\s\d{4}",
        "%b %d %Y",
    ),
    (
        r"\d{4}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{1,2}",
        "%Y %b %d",
    ),
    (
        r"\d{4}\s\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
        "%Y %d %b",
    ),
]

TIME_PATTERN = r"(\d{1,2}:\d{2}(?:\s?[AP]M)?)"

PRIORITY_KEYWORDS = [
    "ASAP",
    "Urgent",
    "Immediately",
    "Critical",
    "Important",
    "Top Priority",
    "Must Do",
    "High Importance",
]


def _format_due_datetime(due: datetime) -> str:
    return due.isoformat() + "Z"


def _is_priority_task(title: str, description: str | None) -> bool:
    text = f"{title} {description or ''}".lower()
    return any(keyword.lower() in text for keyword in PRIORITY_KEYWORDS)


def _convert_todo_item(item: TodoItem) -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["title"] = item.summary
    result["status"] = TODO_STATUS_MAP_INV.get(item.status, "needsAction")
    if item.due:
        result["due"] = _format_due_datetime(dt_util.as_utc(item.due))
    if item.description:
        result["notes"] = item.description
    return result


def _extract_date_time(text: str) -> tuple[datetime | None, str | None]:
    date_match = None
    extracted_date = None
    for pattern, date_format in DATE_PATTERNS:
        if match := re.search(pattern, text):
            date_match = match.group()
            try:
                extracted_date = datetime.strptime(date_match, date_format)
                break
            except ValueError:
                continue

    time_match = re.search(TIME_PATTERN, text)
    extracted_time = time_match.group() if time_match else None

    if extracted_date and extracted_time:
        try:
            if "AM" in extracted_time or "PM" in extracted_time:
                time_obj = datetime.strptime(extracted_time.strip(), "%I:%M %p")
            else:
                time_obj = datetime.strptime(extracted_time.strip(), "%H:%M")
            extracted_date = extracted_date.replace(
                hour=time_obj.hour, minute=time_obj.minute
            )
        except ValueError:
            pass

    return extracted_date, extracted_time


def _convert_api_item(item: dict[str, Any]) -> TodoItem:
    due: datetime | None = None
    if (due_str := item.get("due")) is not None:
        due = dt_util.parse_datetime(due_str)
    else:
        title = item.get("title", "")
        notes = item.get("notes", "")
        extracted_datetime, _ = _extract_date_time(f"{title} {notes}")
        if extracted_datetime:
            due = extracted_datetime

    return TodoItem(
        summary=item["title"],
        uid=item["id"],
        status=TODO_STATUS_MAP.get(
            item.get("status", ""),
            TodoItemStatus.NEEDS_ACTION,
        ),
        due=due,
        description=item.get("notes"),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    api: AsyncConfigEntryAuth = hass.data[DOMAIN][entry.entry_id]
    task_lists = await api.list_task_lists()
    async_add_entities(
        (
            GoogleTaskTodoListEntity(
                TaskUpdateCoordinator(hass, api, task_list["id"]),
                task_list["title"],
                entry.entry_id,
                task_list["id"],
            )
            for task_list in task_lists
        ),
        True,
    )


class GoogleTaskTodoListEntity(
    CoordinatorEntity[TaskUpdateCoordinator], TodoListEntity
):
    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    )

    def __init__(
        self,
        coordinator: TaskUpdateCoordinator,
        name: str,
        config_entry_id: str,
        task_list_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name.capitalize()
        self._attr_unique_id = f"{config_entry_id}-{task_list_id}"
        self._task_list_id = task_list_id

    @property
    def todo_items(self) -> list[TodoItem] | None:
        if self.coordinator.data is None:
            return None
        return [_convert_api_item(item) for item in _order_tasks(self.coordinator.data)]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self.coordinator.api.insert(
            self._task_list_id,
            task=_convert_todo_item(item),
        )
        await self.coordinator.async_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        uid: str = cast(str, item.uid)
        existing_item = next((i for i in self.coordinator.data if i["id"] == uid), None)
        if existing_item:
            updated_item = _convert_todo_item(item)
            try:
                await self.coordinator.api.patch(
                    self._task_list_id,
                    uid,
                    task=updated_item,
                )
                await self.coordinator.async_refresh()
            except GoogleTasksApiError as err:
                _LOGGER.error("Error updating todo item: %s", err)
                raise

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        await self.coordinator.api.delete(self._task_list_id, uids)
        await self.coordinator.async_refresh()

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        await self.coordinator.api.move(self._task_list_id, uid, previous=previous_uid)
        await self.coordinator.async_refresh()


def _order_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def get_due_date(task):
        due_str = task.get("due")
        if due_str:
            return dt_util.parse_datetime(due_str)
        title = task.get("title", "")
        notes = task.get("notes", "")
        extracted_datetime, _ = _extract_date_time(f"{title} {notes}")
        return extracted_datetime or datetime.max

    def sort_key(task):
        is_completed = task.get("status") == "completed"
        is_priority = _is_priority_task(task["title"], task.get("notes"))
        due_date = get_due_date(task)
        return (is_completed, not is_priority, due_date)

    incomplete_tasks = [task for task in tasks if task.get("status") != "completed"]
    completed_tasks = [task for task in tasks if task.get("status") == "completed"]

    sorted_incomplete = sorted(incomplete_tasks, key=sort_key)
    sorted_completed = sorted(completed_tasks, key=sort_key)

    return sorted_completed + sorted_incomplete

