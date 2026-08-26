from pathlib import Path
from typing import Optional

from tau2.data_model.tasks import Task
from tau2.domains.retail_plus.data_model import RetailPlusDB
from tau2.domains.retail_plus.tools import RetailPlusTools
from tau2.domains.retail_plus.utils import (
    RETAIL_PLUS_DB_PATH,
    RETAIL_PLUS_POLICY_PATH,
    RETAIL_PLUS_TASK_SET_PATH,
)
from tau2.environment.environment import Environment
from tau2.utils import load_file


def get_environment(
    db: Optional[RetailPlusDB] = None,
    solo_mode: bool = False,
) -> Environment:
    if solo_mode:
        raise ValueError("Retail Plus domain does not support solo mode")
    if db is None:
        db = RetailPlusDB.load(RETAIL_PLUS_DB_PATH)
    policy = Path(RETAIL_PLUS_POLICY_PATH).read_text(encoding="utf-8")
    return Environment(
        domain_name="retail_plus",
        policy=policy,
        tools=RetailPlusTools(db),
    )


def get_tasks(task_split_name: Optional[str] = "base") -> list[Task]:
    tasks = [Task.model_validate(task) for task in load_file(RETAIL_PLUS_TASK_SET_PATH)]
    if task_split_name is None:
        return tasks
    task_splits = get_tasks_split()
    if task_split_name not in task_splits:
        raise ValueError(
            f"Invalid task split name: {task_split_name}. "
            f"Valid splits are: {task_splits.keys()}"
        )
    selected = set(task_splits[task_split_name])
    return [task for task in tasks if task.id in selected]


def get_tasks_split() -> dict[str, list[str]]:
    split_file = (
        Path(RETAIL_PLUS_TASK_SET_PATH).parent
        / f"split_{Path(RETAIL_PLUS_TASK_SET_PATH).stem}.json"
    )
    return load_file(split_file)
