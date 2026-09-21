"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from nodeseek_bot.config import ConfigError, load_config
from nodeseek_bot.db import DatabaseError
from nodeseek_bot.runtime import build_runtime, run_grant, run_maintenance

logger = logging.getLogger("nodeseek_bot")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nodeseek-bot",
        description="Telegram bot that watches NodeSeek new topics for keywords.",
    )
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument(
        "--maintenance",
        action="store_true",
        help="purge expired items and exit",
    )
    parser.add_argument(
        "--grant",
        type=int,
        metavar="TG_USER_ID",
        help="add a user without an invite code (bootstrap the owner) and exit",
    )
    parser.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        logger.error("配置错误：%s", exc)
        return 2

    try:
        if args.maintenance:
            return run_maintenance(config)

        if args.grant is not None:
            return run_grant(config, args.grant)
    except DatabaseError as exc:
        logger.error("数据库错误：%s", exc)
        return 3

    runtime = build_runtime(config)
    try:
        asyncio.run(runtime.run())
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出")
    finally:
        asyncio.run(runtime.aclose())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
