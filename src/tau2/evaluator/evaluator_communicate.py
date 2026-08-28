import re
from datetime import datetime

from tau2.data_model.message import AssistantMessage, Message
from tau2.data_model.simulation import CommunicateCheck, RewardInfo
from tau2.data_model.tasks import RewardType, Task
from tau2.evaluator.evaluator_base import EvaluatorBase


class CommunicateEvaluator(EvaluatorBase):
    """
    Evaluates whether or not the agent communicated the required information.
    """

    @classmethod
    def calculate_reward(
        cls,
        task: Task,
        full_trajectory: list[Message],
    ) -> RewardInfo:
        """
        Calculate the reward based on whether the agent communicated the required information.
        """
        if task.evaluation_criteria is None:
            return RewardInfo(
                reward=1.0,
                info={"notes": "No evaluation criteria"},
                reward_breakdown={RewardType.COMMUNICATE: 1.0},
            )
        communicate_info = task.evaluation_criteria.communicate_info
        if not communicate_info:
            return RewardInfo(
                reward=1.0,
                info={"note": "No communicate_info to evaluate"},
                reward_breakdown={RewardType.COMMUNICATE: 1.0},
            )

        communicate_info_checks = cls.evaluate_communicate_info(
            full_trajectory, communicate_info
        )

        # Calculate reward: 1 if all expectations are met, 0 otherwise
        all_expectations_met = all(result.met for result in communicate_info_checks)
        reward = 1.0 if all_expectations_met else 0.0

        return RewardInfo(
            reward=reward,
            communicate_checks=communicate_info_checks,
            reward_breakdown={RewardType.COMMUNICATE: reward},
        )

    @classmethod
    def evaluate_communicate_info(
        cls,
        full_trajectory: list[Message],
        communicate_info: list[str],
    ) -> list[CommunicateCheck]:
        """
        Evaluate whether the agent communicates the information correctly.
        """
        if len(communicate_info) == 0:
            return []

        outputs = []
        for info_str in communicate_info:
            found = False
            for message in full_trajectory:
                if not isinstance(message, AssistantMessage):
                    continue
                if not message.has_text_content():
                    continue
                if cls._contains_required_info(message.content, info_str):
                    found = True
                    break
            if found:
                met = True
                justification = f"Information '{info_str}' communicated in the message:\n '{message.content}'"
            else:
                met = False
                justification = f"Information '{info_str}' not communicated."
            outputs.append(
                CommunicateCheck(
                    info=info_str,
                    met=met,
                    justification=justification,
                )
            )
        return outputs

    @staticmethod
    def _contains_required_info(content: str, info_str: str) -> bool:
        """Match literal facts, plus equivalent English renderings of ISO dates."""
        normalized_content = content.lower().replace(",", "")
        if info_str.lower() in normalized_content:
            return True

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", info_str):
            return False

        expected_date = datetime.strptime(info_str, "%Y-%m-%d").date()
        date_pattern = re.compile(
            r"\b("
            + "|".join(
                [
                    "january",
                    "february",
                    "march",
                    "april",
                    "may",
                    "june",
                    "july",
                    "august",
                    "september",
                    "october",
                    "november",
                    "december",
                    "jan",
                    "feb",
                    "mar",
                    "apr",
                    "jun",
                    "jul",
                    "aug",
                    "sep",
                    "sept",
                    "oct",
                    "nov",
                    "dec",
                ]
            )
            + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b",
            re.IGNORECASE,
        )
        for month, day, year in date_pattern.findall(content):
            month_token = month[:3].title()
            try:
                rendered_date = datetime.strptime(
                    f"{month_token} {day} {year}", "%b %d %Y"
                ).date()
            except ValueError:
                continue
            if rendered_date == expected_date:
                return True
        return False
