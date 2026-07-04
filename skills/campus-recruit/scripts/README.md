`run_with_env_check.sh` prepares a project-local Python environment with `uv` when available, then runs the next command.

Examples:

```sh
./scripts/run_with_env_check.sh
./scripts/run_with_env_check.sh python3 scripts/query_base.py --as-user --pretty
./scripts/run_with_env_check.sh python3 scripts/query_base.py --as-user --where 招聘类型=暑期实习,实习
```
