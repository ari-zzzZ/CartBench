import json
from typing import List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from tau2.data_model.simulation import RunConfig, SimulationRun
from tau2.data_model.tasks import Action, Task
from tau2.metrics.agent_metrics import AgentMetrics, is_successful


class ConsoleDisplay:
    console = Console()

    @classmethod
    def display_run_config(cls, config: RunConfig):
        # Create layout
        layout = Layout()

        # Split layout into sections
        layout.split(Layout(name="header"), Layout(name="body"))

        # Split body into columns
        layout["body"].split_row(
            Layout(name="agent", ratio=1),
            Layout(name="user", ratio=1),
            Layout(name="settings", ratio=1),
        )

        # Create content for each section
        header_content = Panel(
            f"[white]Domain:[/] {config.domain}\n"
            f"[white]Task Set:[/] {config.task_set_name if config.task_set_name else 'Default'}\n"
            f"[white]Task IDs:[/] {', '.join(map(str, config.task_ids)) if config.task_ids else 'All'}\n"
            f"[white]Number of trials:[/] {config.num_trials}\n"
            f"[white]Max steps:[/] {config.max_steps}\n"
            f"[white]Max errors:[/] {config.max_errors}",
            title="[bold blue]Simulation Configuration",
            border_style="blue",
        )

        agent_content = Panel(
            f"[white]Implementation:[/] {config.agent}\n"
            f"[white]Model:[/] {config.llm_agent}\n"
            "[white]LLM Arguments:[/]\n"
            f"{json.dumps(config.llm_args_agent, indent=2)}",
            title="[bold cyan]Agent Configuration",
            border_style="cyan",
        )

        user_content = Panel(
            f"[white]Implementation:[/] {config.user}\n"
            f"[white]Model:[/] {config.llm_user}\n"
            "[white]LLM Arguments:[/]\n"
            f"{json.dumps(config.llm_args_user, indent=2)}",
            title="[bold cyan]User Configuration",
            border_style="cyan",
        )

        settings_content = Panel(
            f"[white]Save To:[/] {config.save_to or 'Not specified'}\n"
            f"[white]Max Concurrency:[/] {config.max_concurrency}",
            title="[bold cyan]Additional Settings",
            border_style="cyan",
        )

        # Assign content to layout sections
        layout["header"].update(header_content)
        layout["agent"].update(agent_content)
        layout["user"].update(user_content)
        layout["body"]["settings"].update(settings_content)

        # Print the layout
        cls.console.print(layout)

    @classmethod
    def display_task(cls, task: Task):
        # Build content string showing only non-None fields
        content_parts = []

        if task.id is not None:
            content_parts.append(f"[white]ID:[/] {task.id}")

        if task.description:
            if task.description.purpose:
                content_parts.append(f"[white]Purpose:[/] {task.description.purpose}")
            if task.description.relevant_policies:
                content_parts.append(
                    f"[white]Relevant Policies:[/] {task.description.relevant_policies}"
                )
            if task.description.notes:
                content_parts.append(f"[white]Notes:[/] {task.description.notes}")

        # User Scenario section
        scenario_parts = []
        # Persona
        if task.user_scenario.persona:
            scenario_parts.append(f"[white]Persona:[/] {task.user_scenario.persona}")

        # User Instruction
        scenario_parts.append(
            f"[white]Task Instructions:[/] {task.user_scenario.instructions}"
        )

        if scenario_parts:
            content_parts.append(
                "[bold cyan]User Scenario:[/]\n" + "\n".join(scenario_parts)
            )

        # Initial State section
        if task.initial_state:
            initial_state_parts = []
            if task.initial_state.initialization_data:
                initial_state_parts.append(
                    f"[white]Initialization Data:[/]\n{task.initial_state.initialization_data.model_dump_json(indent=2)}"
                )
            if task.initial_state.initialization_actions:
                initial_state_parts.append(
                    f"[white]Initialization Actions:[/]\n{json.dumps([a.model_dump() for a in task.initial_state.initialization_actions], indent=2)}"
                )
            if task.initial_state.message_history:
                initial_state_parts.append(
                    f"[white]Message History:[/]\n{json.dumps([m.model_dump() for m in task.initial_state.message_history], indent=2)}"
                )

            if initial_state_parts:
                content_parts.append(
                    "[bold cyan]Initial State:[/]\n" + "\n".join(initial_state_parts)
                )

        # Evaluation Criteria section
        if task.evaluation_criteria:
            eval_parts = []
            if task.evaluation_criteria.actions:
                eval_parts.append(
                    f"[white]Required Actions:[/]\n{json.dumps([a.model_dump() for a in task.evaluation_criteria.actions], indent=2)}"
                )
            if task.evaluation_criteria.env_assertions:
                eval_parts.append(
                    f"[white]Env Assertions:[/]\n{json.dumps([a.model_dump() for a in task.evaluation_criteria.env_assertions], indent=2)}"
                )
            if task.evaluation_criteria.communicate_info:
                eval_parts.append(
                    f"[white]Information to Communicate:[/]\n{json.dumps(task.evaluation_criteria.communicate_info, indent=2)}"
                )
            if task.evaluation_criteria.policy_assertions:
                eval_parts.append(
                    "[white]Applicable Policy Rules:[/]\n"
                    + json.dumps(
                        [
                            assertion.model_dump(mode="json")
                            for assertion in task.evaluation_criteria.policy_assertions
                        ],
                        indent=2,
                    )
                )
            if eval_parts:
                content_parts.append(
                    "[bold cyan]Evaluation Criteria:[/]\n" + "\n".join(eval_parts)
                )
        content = "\n\n".join(content_parts)

        # Create and display panel
        task_panel = Panel(
            content, title="[bold blue]Task Details", border_style="blue", expand=True
        )

        cls.console.print(task_panel)

    @classmethod
    def display_simulation(cls, simulation: SimulationRun, show_details: bool = True):
        """
        Display the simulation content in a formatted way using Rich library.

        Args:
            simulation: The simulation object to display
            show_details: Whether to show detailed information
        """
        # Create main simulation info panel
        sim_info = Text()
        if show_details:
            sim_info.append("Simulation ID: ", style="bold cyan")
            sim_info.append(f"{simulation.id}\n")
        sim_info.append("Task ID: ", style="bold cyan")
        sim_info.append(f"{simulation.task_id}\n")
        sim_info.append("Trial: ", style="bold cyan")
        sim_info.append(f"{simulation.trial}\n")
        if show_details:
            sim_info.append("Start Time: ", style="bold cyan")
            sim_info.append(f"{simulation.start_time}\n")
            sim_info.append("End Time: ", style="bold cyan")
            sim_info.append(f"{simulation.end_time}\n")
        sim_info.append("Duration: ", style="bold cyan")
        sim_info.append(f"{simulation.duration:.2f}s\n")
        sim_info.append("Termination Reason: ", style="bold cyan")
        sim_info.append(f"{simulation.termination_reason}\n")
        if simulation.agent_cost is not None:
            sim_info.append("Agent Cost: ", style="bold cyan")
            sim_info.append(f"${simulation.agent_cost:.4f}\n")
        if simulation.user_cost is not None:
            sim_info.append("User Cost: ", style="bold cyan")
            sim_info.append(f"${simulation.user_cost:.4f}\n")
        if simulation.reward_info:
            marker = "✅" if is_successful(simulation.reward_info.reward) else "❌"
            sim_info.append("Reward: ", style="bold cyan")
            if simulation.reward_info.reward_breakdown:
                breakdown = sorted(
                    [
                        f"{k.value}: {v:.1f}"
                        for k, v in simulation.reward_info.reward_breakdown.items()
                    ]
                )
            else:
                breakdown = []

            sim_info.append(
                f"{marker} {simulation.reward_info.reward:.4f} ({', '.join(breakdown)})\n"
            )

            # Add DB check info if present
            if simulation.reward_info.db_check:
                sim_info.append("\nDB Check:", style="bold magenta")
                sim_info.append(
                    f"{'✅' if simulation.reward_info.db_check.db_match else '❌'} {simulation.reward_info.db_check.db_reward}\n"
                )

            # Add env assertions if present
            if simulation.reward_info.env_assertions:
                sim_info.append("\nEnv Assertions:\n", style="bold magenta")
                for i, assertion in enumerate(simulation.reward_info.env_assertions):
                    sim_info.append(
                        f"- {i}: {assertion.env_assertion.env_type} {assertion.env_assertion.func_name} {'✅' if assertion.met else '❌'} {assertion.reward}\n"
                    )

            # Add action checks if present
            if simulation.reward_info.action_checks:
                sim_info.append("\nAction Checks:\n", style="bold magenta")
                for i, check in enumerate(simulation.reward_info.action_checks):
                    sim_info.append(
                        f"- {i}: {check.action.name} {'✅' if check.action_match else '❌'} {check.action_reward}\n"
                    )

            # Add communication checks if present
            if simulation.reward_info.communicate_checks:
                sim_info.append("\nCommunicate Checks:\n", style="bold magenta")
                for i, check in enumerate(simulation.reward_info.communicate_checks):
                    sim_info.append(
                        f"- {i}: {check.info} {'✅' if check.met else '❌'}\n"
                    )

            if simulation.reward_info.policy_evaluated:
                violations = simulation.reward_info.policy_violations or []
                sim_info.append("\nPolicy Check:\n", style="bold magenta")
                sim_info.append(
                    f"- {'PASS' if not violations else 'FAIL'}; "
                    f"violation events: {len(violations)}\n"
                )
                for violation in violations:
                    disposition = "blocked" if violation.blocked else "observed"
                    sim_info.append(
                        f"- {violation.rule_id} [{violation.severity.value}, "
                        f"{disposition}]: {violation.description}\n"
                    )

            # Add NL assertions if present
            if simulation.reward_info.nl_assertions:
                sim_info.append("\nNL Assertions:\n", style="bold magenta")
                for i, assertion in enumerate(simulation.reward_info.nl_assertions):
                    sim_info.append(
                        f"- {i}: {assertion.nl_assertion} {'✅' if assertion.met else '❌'}\n\t{assertion.justification}\n"
                    )

            # Add additional info if present
            if simulation.reward_info.info:
                sim_info.append("\nAdditional Info:\n", style="bold magenta")
                for key, value in simulation.reward_info.info.items():
                    sim_info.append(f"{key}: {value}\n")

        cls.console.print(
            Panel(sim_info, title="Simulation Overview", border_style="blue")
        )

        # Create messages table
        if simulation.messages:
            table = Table(
                title="Messages",
                show_header=True,
                header_style="bold magenta",
                show_lines=True,  # Add horizontal lines between rows
            )
            table.add_column("Role", style="cyan", no_wrap=True)
            table.add_column("Content", style="green")
            table.add_column("Details", style="yellow")
            table.add_column("Turn", style="yellow", no_wrap=True)

            current_turn = None
            for msg in simulation.messages:
                content = msg.content if msg.content is not None else ""
                details = ""

                # Set different colors based on message type
                if isinstance(msg, AssistantMessage):
                    role_style = "bold blue"
                    content_style = "blue"
                    tool_style = "bright_blue"  # Lighter shade of blue
                elif isinstance(msg, UserMessage):
                    role_style = "bold green"
                    content_style = "green"
                    tool_style = "bright_green"  # Lighter shade of green
                elif isinstance(msg, ToolMessage):
                    # For tool messages, use the color of the requestor's tool style
                    if msg.requestor == "user":
                        role_style = "bold green"
                        content_style = "bright_green"  # Match user's tool style
                    else:  # assistant
                        role_style = "bold blue"
                        content_style = "bright_blue"  # Match assistant's tool style
                else:  # SystemMessage
                    role_style = "bold magenta"
                    content_style = "magenta"

                if isinstance(msg, AssistantMessage) or isinstance(msg, UserMessage):
                    if msg.tool_calls:
                        tool_calls = []
                        for tool in msg.tool_calls:
                            tool_calls.append(
                                f"[{tool_style}]Tool: {tool.name}[/]\n[{tool_style}]Args: {json.dumps(tool.arguments, indent=2)}[/]"
                            )
                        details = "\n".join(tool_calls)
                elif isinstance(msg, ToolMessage):
                    details = f"[{content_style}]Tool ID: {msg.id}. Requestor: {msg.requestor}[/]"
                    if msg.error:
                        details += " [bold red](Error)[/]"

                # Add empty row between turns
                if current_turn is not None and msg.turn_idx != current_turn:
                    table.add_row("", "", "", "")
                current_turn = msg.turn_idx

                table.add_row(
                    f"[{role_style}]{msg.role}[/]",
                    f"[{content_style}]{content}[/]",
                    details,
                    str(msg.turn_idx) if msg.turn_idx is not None else "",
                )
            if show_details:
                cls.console.print(table)

    @classmethod
    def display_agent_metrics(cls, metrics: AgentMetrics):
        # Create content for metrics panel
        content = Text()

        # Add average reward section
        content.append("🏆 Average Reward: ", style="bold cyan")
        content.append(f"{metrics.avg_reward:.4f}\n\n")

        # Add Pass^k metrics section
        content.append("📈 Pass^k Metrics:", style="bold cyan")
        for k, pass_hat_k in metrics.pass_hat_ks.items():
            content.append(f"\nk={k}: ", style="bold white")
            content.append(f"{pass_hat_k:.3f}")

        if metrics.simulation_coverage is not None:
            coverage = metrics.simulation_coverage
            content.append("\n\nSimulation Coverage: ", style="bold cyan")
            content.append(
                f"{coverage.rate:.4f} ({coverage.passed}/{coverage.total})"
            )

        # Add aggregate evaluator dimensions and action recall. ACTION task pass
        # rate is the binary reward component; recall is matched golden calls.
        if metrics.evaluation_metrics:
            content.append("\n\nEvaluation Breakdown:", style="bold cyan")
            metric_labels = {
                "db_pass_rate": "DB Pass Rate",
                "action_task_pass_rate": "ACTION Task Pass Rate",
                "golden_tool_call_recall": "Golden Tool Call Recall",
                "communicate_pass_rate": "COMMUNICATE Pass Rate",
            }
            for metric_name in (
                "db_pass_rate",
                "action_task_pass_rate",
                "golden_tool_call_recall",
                "communicate_pass_rate",
            ):
                metric = metrics.evaluation_metrics.get(metric_name)
                if metric is None:
                    continue
                content.append(f"\n{metric_labels[metric_name]}: ", style="bold white")
                content.append(f"{metric.rate:.4f} ({metric.passed}/{metric.total})")

        if metrics.policy_metrics is not None:
            policy = metrics.policy_metrics
            content.append("\n\nPolicy Evaluation:", style="bold cyan")
            content.append("\nPolicy Violation Rate: ", style="bold white")
            content.append(
                f"{policy.violation_rate:.4f} "
                f"({policy.violating_simulations}/{policy.evaluated_simulations})"
            )
            content.append("\nPolicy Compliance Rate: ", style="bold white")
            content.append(
                f"{policy.compliance_rate:.4f} "
                f"({policy.compliant_simulations}/{policy.evaluated_simulations})"
            )
            content.append("\nPolicy Violation Events: ", style="bold white")
            content.append(str(policy.total_violation_events))
            for rule_id, count in policy.violations_by_rule.items():
                content.append(f"\n  - {rule_id}: {count}")

        # Add average agent cost section
        content.append("\n\n💰 Average Cost per Conversation: ", style="bold cyan")
        content.append(f"${metrics.avg_agent_cost:.4f}")
        if metrics.cost_metrics is not None:
            cost = metrics.cost_metrics
            content.append("\nTotal Agent + User Cost: ", style="bold cyan")
            content.append(f"${cost.total_cost:.4f}")
            content.append("\nCost per Successful Resolution: ", style="bold cyan")
            if cost.cost_per_successful_resolution is None:
                content.append("N/A (0 successful resolutions)")
            else:
                content.append(
                    f"${cost.cost_per_successful_resolution:.4f} "
                    f"({cost.successful_resolutions} successful)"
                )
        content.append("\n\n")

        # Create and display panel
        metrics_panel = Panel(
            content,
            title="[bold blue]Agent Metrics",
            border_style="blue",
            expand=True,
        )

        cls.console.print(metrics_panel)


class MarkdownDisplay:
    @classmethod
    def display_actions(cls, actions: List[Action]) -> str:
        """Display actions in markdown format."""
        return f"```json\n{json.dumps([action.model_dump() for action in actions], indent=2)}\n```"

    @classmethod
    def display_messages(cls, messages: list[Message]) -> str:
        """Display messages in markdown format."""
        return "\n\n".join(cls.display_message(msg) for msg in messages)

    @classmethod
    def display_simulation(cls, sim: SimulationRun) -> str:
        """Display simulation in markdown format."""
        # Otherwise handle SimulationRun object
        output = []

        # Add basic simulation info
        output.append(f"**Task ID**: {sim.task_id}")
        output.append(f"**Trial**: {sim.trial}")
        output.append(f"**Duration**: {sim.duration:.2f}s")
        output.append(f"**Termination**: {sim.termination_reason}")
        if sim.agent_cost is not None:
            output.append(f"**Agent Cost**: ${sim.agent_cost:.4f}")
        if sim.user_cost is not None:
            output.append(f"**User Cost**: ${sim.user_cost:.4f}")

        # Add reward info if present
        if sim.reward_info:
            breakdown = sorted(
                [
                    f"{k.value}: {v:.1f}"
                    for k, v in sim.reward_info.reward_breakdown.items()
                ]
            )
            output.append(
                f"**Reward**: {sim.reward_info.reward:.4f} ({', '.join(breakdown)})\n"
            )
            output.append(f"**Reward**: {sim.reward_info.reward:.4f}")

            # Add DB check info if present
            if sim.reward_info.db_check:
                output.append("\n**DB Check**")
                output.append(
                    f"- Status: {'✅' if sim.reward_info.db_check.db_match else '❌'} {sim.reward_info.db_check.db_reward}"
                )

            # Add env assertions if present
            if sim.reward_info.env_assertions:
                output.append("\n**Env Assertions**")
                for i, assertion in enumerate(sim.reward_info.env_assertions):
                    output.append(
                        f"- {i}: {assertion.env_assertion.env_type} {assertion.env_assertion.func_name} {'✅' if assertion.met else '❌'} {assertion.reward}"
                    )

            # Add action checks if present
            if sim.reward_info.action_checks:
                output.append("\n**Action Checks**")
                for i, check in enumerate(sim.reward_info.action_checks):
                    output.append(
                        f"- {i}: {check.action.name} {'✅' if check.action_match else '❌'} {check.action_reward}"
                    )

            # Add communication checks if present
            if sim.reward_info.communicate_checks:
                output.append("\n**Communicate Checks**")
                for i, check in enumerate(sim.reward_info.communicate_checks):
                    output.append(
                        f"- {i}: {check.info} {'✅' if check.met else '❌'} {check.justification}"
                    )

            if sim.reward_info.policy_evaluated:
                violations = sim.reward_info.policy_violations or []
                output.append("\n**Policy Check**")
                output.append(
                    f"- {'PASS' if not violations else 'FAIL'}; "
                    f"violation events: {len(violations)}"
                )
                for violation in violations:
                    disposition = "blocked" if violation.blocked else "observed"
                    output.append(
                        f"- {violation.rule_id} [{violation.severity.value}, "
                        f"{disposition}]: {violation.description}"
                    )

            # Add NL assertions if present
            if sim.reward_info.nl_assertions:
                output.append("\n**NL Assertions**")
                for i, assertion in enumerate(sim.reward_info.nl_assertions):
                    output.append(
                        f"- {i}: {assertion.nl_assertion} {'✅' if assertion.met else '❌'} {assertion.justification}"
                    )

            # Add additional info if present
            if sim.reward_info.info:
                output.append("\n**Additional Info**")
                for key, value in sim.reward_info.info.items():
                    output.append(f"- {key}: {value}")

        # Add messages using the display_message method
        if sim.messages:
            output.append("\n**Messages**:")
            output.extend(cls.display_message(msg) for msg in sim.messages)

        return "\n\n".join(output)

    @classmethod
    def display_result(
        cls,
        task: Task,
        sim: SimulationRun,
        reward: Optional[float] = None,
        show_task_id: bool = False,
    ) -> str:
        """Display a single result with all its components in markdown format."""
        output = [
            f"## Task {task.id}" if show_task_id else "## Task",
            "\n### User Instruction",
            task.user_scenario.instructions,
            "\n### Ground Truth Actions",
            cls.display_actions(task.evaluation_criteria.actions),
        ]

        if task.evaluation_criteria.communicate_info:
            output.extend(
                [
                    "\n### Communicate Info",
                    "```\n" + str(task.evaluation_criteria.communicate_info) + "\n```",
                ]
            )

        if reward is not None:
            output.extend(["\n### Reward", f"**{reward:.3f}**"])

        output.extend(["\n### Simulation", cls.display_simulation(sim)])

        return "\n".join(output)

    @classmethod
    def display_message(cls, msg: Message) -> str:
        """Display a single message in markdown format."""
        # Common message components
        parts = []

        # Add turn index if present
        turn_prefix = f"[TURN {msg.turn_idx}] " if msg.turn_idx is not None else ""

        # Format based on message type
        if isinstance(msg, AssistantMessage) or isinstance(msg, UserMessage):
            parts.append(f"{turn_prefix}**{msg.role}**:")
            if msg.content:
                parts.append(msg.content)
            if msg.tool_calls:
                tool_calls = []
                for tool in msg.tool_calls:
                    tool_calls.append(
                        f"**Tool Call**: {tool.name}\n```json\n{json.dumps(tool.arguments, indent=2)}\n```"
                    )
                parts.extend(tool_calls)

        elif isinstance(msg, ToolMessage):
            status = " (Error)" if msg.error else ""
            parts.append(f"{turn_prefix}**tool{status}**:")
            parts.append(f"Reponse to: {msg.requestor}")
            if msg.content:
                parts.append(f"```\n{msg.content}\n```")

        elif isinstance(msg, SystemMessage):
            parts.append(f"{turn_prefix}**system**:")
            if msg.content:
                parts.append(msg.content)

        return "\n".join(parts)
