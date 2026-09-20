"""Run Retail Plus tasks through the standard Orchestrator with risk middleware."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--agent-llm")
    parser.add_argument("--user-llm")
    parser.add_argument("--agent-llm-args", type=json.loads, default={"temperature": 0})
    parser.add_argument("--user-llm-args", type=json.loads, default={"temperature": 0})
    parser.add_argument("--task-ids", nargs="+")
    parser.add_argument("--task-split-name")
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--output", type=Path, default=Path("data/risk_control/evaluation"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    if args.offline:
        return subprocess.call(
            [sys.executable, "-m", "pytest", "tests/test_risk_control.py", "-q"],
            cwd=root,
        )
    if not args.agent_llm or not args.user_llm:
        parser.error("Provide both models, or use --offline")
    if args.task_ids and args.task_split_name:
        parser.error("Use --task-ids or --task-split-name, not both")

    from tau2.agent.guarded_agent import GuardedAgent
    from tau2.data_model.simulation import Results
    from tau2.domains.retail_plus.environment import get_environment, get_tasks
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.risk_control.controller import RetailRiskController
    from tau2.run import get_info
    from tau2.user.user_simulator import UserSimulator

    all_tasks = {task.id: task for task in get_tasks(None)}
    if args.task_split_name:
        tasks = get_tasks(args.task_split_name)
    elif args.task_ids:
        unknown = set(args.task_ids) - all_tasks.keys()
        if unknown:
            parser.error(f"Unknown task IDs: {sorted(unknown)}")
        tasks = [all_tasks[item] for item in args.task_ids]
    else:
        tasks = [
            all_tasks["rp_abcd_refund_status_normal"],
            all_tasks["rp_abcd_mystery_fee_normal"],
        ]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "guarded.json"
    audit_path = output / "risk-audits.json"
    if result_path.exists() or audit_path.exists():
        parser.error("Output already contains results; choose a new directory")

    info = get_info(
        domain="retail_plus",
        agent="llm_agent",
        user="user_simulator",
        llm_agent=args.agent_llm,
        llm_args_agent=args.agent_llm_args,
        llm_user=args.user_llm,
        llm_args_user=args.user_llm_args,
        num_trials=args.num_trials,
        max_steps=args.max_steps,
        max_errors=10,
        seed=args.seed,
    )
    info.agent_info.implementation = "guarded_agent"
    results = Results(info=info, tasks=tasks, simulations=[])
    audits = []

    for trial in range(args.num_trials):
        for task in tasks:
            seed = args.seed + trial
            environment = get_environment()
            agent = GuardedAgent(
                tools=environment.get_tools(),
                domain_policy=environment.get_policy(),
                llm=args.agent_llm,
                llm_args={**args.agent_llm_args, "seed": seed},
            )
            user = UserSimulator(
                tools=None,
                instructions=str(task.user_scenario),
                llm=args.user_llm,
                llm_args={**args.user_llm_args, "seed": seed},
            )
            controller = RetailRiskController(
                environment=environment,
                task_id=task.id,
                llm=args.agent_llm,
                llm_args={**args.agent_llm_args, "seed": seed},
            )
            orchestrator = Orchestrator(
                domain="retail_plus",
                agent=agent,
                user=user,
                environment=environment,
                task=task,
                max_steps=args.max_steps,
                seed=seed,
                middleware=controller,
            )
            simulation = orchestrator.run()
            simulation.trial = trial
            simulation.reward_info = evaluate_simulation(
                simulation, task, EvaluationType.ALL, False, "retail_plus"
            )
            results.simulations.append(simulation)
            audits.append(
                {
                    "task_id": task.id,
                    "trial": trial,
                    "risk_control": controller.export(),
                }
            )
            result_path.write_text(results.model_dump_json(indent=2), encoding="utf-8")
            audit_path.write_text(
                json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                f"{task.id} trial={trial} reward={simulation.reward_info.reward} "
                f"risk={controller.state.context.risk_level}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
