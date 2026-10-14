"""Inspect the Microsoft Graph fields used by the Entra login flow.

The script reads a delegated Graph access token from GRAPH_ACCESS_TOKEN or
stdin. It never prints the token. Use it only with a disposable development
session and remove the environment variable after running it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
PROFILE_FIELDS = (
    "id,displayName,givenName,surname,mail,userPrincipalName,"
    "jobTitle,department,employeeId"
)


def graph_get(path: str, token: str) -> tuple[int, object]:
    request = Request(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            body: object = json.load(error)
        except (json.JSONDecodeError, ValueError):
            body = error.read().decode("utf-8", errors="replace")
        return error.code, body
    except URLError as error:
        raise RuntimeError(f"Could not reach Microsoft Graph: {error.reason}") from error


def read_token(use_stdin: bool) -> str:
    token = sys.stdin.read().strip() if use_stdin else os.environ.get("GRAPH_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Set GRAPH_ACCESS_TOKEN or provide the token through standard input."
        )
    return token


def print_result(title: str, status: int, body: object) -> None:
    print(f"\n{title} (HTTP {status})")
    print(json.dumps(body, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the Graph profile and optional group memberships."
    )
    parser.add_argument(
        "--groups",
        action="store_true",
        help="Also request /me/transitiveMemberOf/microsoft.graph.group.",
    )
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read the access token from stdin instead of GRAPH_ACCESS_TOKEN.",
    )
    args = parser.parse_args()

    try:
        token = read_token(args.token_stdin)
        profile_query = urlencode({"$select": PROFILE_FIELDS})
        status, profile = graph_get(f"/me?{profile_query}", token)
        print_result("Profile fields used by GreenITESO", status, profile)
        if args.groups:
            groups_query = urlencode({"$select": "id,displayName", "$top": "999"})
            status, groups = graph_get(
                f"/me/transitiveMemberOf/microsoft.graph.group?{groups_query}",
                token,
            )
            print_result("Microsoft Entra groups", status, groups)
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
