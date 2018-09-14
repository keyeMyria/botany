import json
import os
import re
import sys

import click
import requests

from botany_core import loader, runner, verifier

from . import utils


@click.group()
def cli():
    """botany"""


@cli.command(short_help="Initialise current directory")
@click.argument("host")
def init(host):
    if host[-1] == "/":
        host = host[:-1]

    setup_url = host + "/api/setup/"
    # TODO If botany-settings.json in current directory, quit
    rsp = requests.get(setup_url)
    data = rsp.json()
    # TODO Check return code

    token_url = host + "/token/"
    print(f"Visit {token_url} and enter your API token")
    api_token = input("> ")

    data["host"] = host
    data["api_token"] = api_token

    with open("botany-settings.json", "w") as f:
        json.dump(data, f, indent=4)


@cli.command(short_help="Submit bot code")
@click.argument("path")
def submit(path):
    settings = utils.load_settings()

    submit_url = settings["host"] + "/api/submit/"
    bot_name = os.path.basename(path)
    with open(path) as f:
        bot_code = f.read()

    try:
        verifier.verify_bot_code(bot_code)
    except verifier.InvalidBotCode as e:
        print("Bot code does not conform to the bot specification:")
        print()
        print(f"  {e}")
        print()
        print("Refer to the Botany user guide for more details")
        sys.exit(1)

    data = {
        "api_token": settings["api_token"],
        "bot_name": bot_name,
        "bot_code": bot_code,
    }
    rsp = requests.post(submit_url, data=data)
    rsp.raise_for_status()


@cli.command(short_help="Play game between bots and/or humans")
@click.argument("path1")
@click.argument("path2")
@click.option("--opcode-limit", type=int, default=None)
def play(path1, path2, opcode_limit):
    game = utils.load_game()

    def get_next_move_human(board):
        available_moves = game.available_moves(board)

        while True:
            col = input("> ")
            try:
                col = int(col)
            except ValueError:
                continue

            if col not in available_moves:
                continue

            return col

    def wrap_bot_fn(fn):
        param_list = runner.get_param_list(fn)

        def wrapped(board, move_list, token, state):
            input()

            all_args = {
                "board": board,
                "move_list": move_list,
                "token": token,
                "state": state,
            }

            args = {
                param: value for param, value in all_args.items() if param in param_list
            }

            return fn(**args)

        return wrapped

    if path1 == "human":
        fn1 = get_next_move_human
    else:
        mod1 = loader.load_module_from_filesystem_path("mod1", path1)
        fn1 = wrap_bot_fn(mod1.get_next_move)

    if path2 == "human":
        fn2 = get_next_move_human
    else:
        mod2 = loader.load_module_from_filesystem_path("mod2", path2)
        fn2 = wrap_bot_fn(mod2.get_next_move)

    result = runner.run_game(game, fn1, fn2, display_board=True)
    if result.traceback:
        print(result.traceback)


@cli.command(short_help="Run tournament between several bots")
@click.argument("path1")
@click.argument("path2")
@click.argument("pathn", nargs=-1)
@click.option("--full-output", is_flag=True)
@click.option("--num-rounds", type=int, default=None)
@click.option("--opcode-limit", type=int, default=None)
def tournament(path1, path2, pathn, full_output, num_rounds, opcode_limit):
    settings = utils.load_settings()
    game = utils.load_game()

    paths = [path1, path2] + list(pathn)
    bots = []

    for path in paths:
        mod = loader.load_module_from_filesystem_path("mod", path)
        bots.append(
            {
                "path": path,
                "fn": mod.get_next_move,
                "num_played": 0,
                "num_wins": 0,
                "num_draws": 0,
                "num_losses": 0,
                "score": 0,
            }
        )

    if num_rounds is None:
        num_rounds = settings["botany_num_rounds"]

    for bot1 in bots:
        for bot2 in bots:
            if bot1 == bot2:
                continue

            if full_output:
                print(f"{bot1['path']} vs {bot2['path']}")

            for ix in range(num_rounds):
                result = runner.run_game(game, bot1["fn"], bot2["fn"])

                bot1["num_played"] += 1
                bot2["num_played"] += 1

                if result.score == 1:
                    bot1["num_wins"] += 1
                    bot1["score"] += 1
                    bot2["num_losses"] += 1
                    bot2["score"] -= 1

                    winning_bot = "bot1"
                    losing_bot = "bot2"

                elif result.score == 0:
                    bot1["num_draws"] += 1
                    bot2["num_draws"] += 1

                    winning_bot = None
                    losing_bot = None

                elif result.score == -1:
                    bot1["num_losses"] += 1
                    bot1["score"] -= 1
                    bot2["num_wins"] += 1
                    bot2["score"] += 1

                    winning_bot = "bot2"
                    losing_bot = "bot1"

                else:
                    assert False

                if winning_bot is None:
                    result_summary = "game drawn"
                else:
                    result_summary = f"{winning_bot} wins"

                if result.result_type == runner.ResultType.INVALID_MOVE:
                    result_extra = f"{losing_bot} made an invalid move"
                elif result.result_type == runner.ResultType.EXCEPTION:
                    result_extra = f"{losing_bot} raised an exception"
                elif result.result_type == runner.ResultType.TIMEOUT:
                    result_extra = f"{losing_bot} exceeded the opcode limit"
                elif result.result_type == runner.ResultType.INVALID_STATE:
                    result_extra = f"{losing_bot} returned an invalid state"
                else:
                    assert result.result_type == runner.ResultType.COMPLETE
                    result_extra = None

                if full_output:
                    items = [
                        str(ix).rjust(len(str(num_rounds))),
                        result_summary.ljust(10),
                        "".join(str(move) for move in result.move_list),
                    ]

                    if result_extra:
                        items.append(result_extra)

                    print("  " + " ".join(items))

            if full_output:
                print()

    if full_output:
        print()

    max_path_width = max(len(bot["path"]) for bot in bots)
    max_col_width = len(str(num_rounds * len(bots) * len(bots)))

    def sortkey(bot):
        return [-bot["score"], bot["num_played"], -bot["num_wins"]]

    bots = sorted(bots, key=sortkey)

    row_items = [
        "Bot".center(max_path_width),
        "P".center(max_col_width),
        "W".center(max_col_width),
        "D".center(max_col_width),
        "L".center(max_col_width),
        "Score",
    ]
    header_row = " | ".join(row_items)
    print(header_row)

    dividing_row = header_row.replace("|", "+")
    dividing_row = re.sub("[^+]", "-", dividing_row)
    print(dividing_row)

    for bot in bots:
        row_items = [
            bot["path"].ljust(max_path_width),
            str(bot["num_played"]).rjust(max_col_width),
            str(bot["num_wins"]).rjust(max_col_width),
            str(bot["num_draws"]).rjust(max_col_width),
            str(bot["num_losses"]).rjust(max_col_width),
            str(bot["score"]).rjust(max_col_width),
        ]

        print(" | ".join(row_items))
