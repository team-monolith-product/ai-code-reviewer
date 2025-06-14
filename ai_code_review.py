"""
프로젝트: GitHub PR 자동 코드 리뷰

이 프로젝트는 GitHub Pull Request 이벤트가 발생할 때,
1) PR에서 변경된 파일의 Diff를 확인하고,
2) 사전에 정의된 코딩 규칙과 함께
3) ChatGPT(O1) 모델을 이용하여 자동 리뷰를 수행하고,
4) 결과를 GitHub Pull Request 코멘트(Inline Comment)로 게시하는 것을 목표로 한다.
"""

import argparse
import datetime
import os
import subprocess
import tempfile
import json
from typing import Any

from dotenv import load_dotenv
from github import Github, GithubException
from github.PullRequest import PullRequest
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from unidiff import PatchSet

# 환경 변수 로드
load_dotenv()


def main() -> None:
    """
    Main workflow, intended to run inside a GitHub Actions container.
    It relies on environment variables provided by the OS (Actions):
      - GITHUB_TOKEN (required to authenticate GitHub API calls)
      - GITHUB_REPOSITORY (e.g. "owner/repo")
      - PR_NUMBER (the pull request number to be analyzed)
      - OPENAI_API_KEY (key for ChatGPT(O1) / OpenAI API)
      - SYSTEM_PROMPT (prompt to be used for the AI model)

    --force : 리뷰 상태와 관계없이 강제로 리뷰를 수행합니다.
    """

    # 0) Load environment variables
    github_token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPOSITORY")  # "owner/repo"
    pr_number_str = os.getenv("PR_NUMBER")  # e.g. "123"
    # e.g. "Always answer in Korean."
    system_prompt = os.getenv("SYSTEM_PROMPT")

    if not github_token or not repo_name or not pr_number_str or not system_prompt:
        raise EnvironmentError(
            "Missing one or more required environment variables: "
            "GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, SYSTEM_PROMPT."
        )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="리뷰 상태와 관계없이 강제로 리뷰를 수행합니다.",
    )
    args = parser.parse_args()

    pr_number = int(pr_number_str)

    # 1) PyGithub로 PullRequest 가져오기
    g = get_github_client(github_token)  # -> Github
    pr = get_pull_request(g, repo_name, pr_number)  # -> PullRequest

    # 1-1) 리뷰 요청을 받지 않았다면 종료
    if not args.force and not user_requested_for_review(g, pr):
        print("[SKIP] 리뷰가 요청되지 않아 종료합니다.")
        return

    # /github/workspace 경로가 존재하지 않는 경우, 로컬 환경으로 가정
    if not os.path.exists("/github/workspace"):
        git_dir = clone_repo(pr)
    else:
        git_dir = "/github/workspace"

    # 2) PullRequest의 파일별 patch를 모아서 unidiff PatchSet 생성
    patch_set = get_patchset_from_git(git_dir, pr, 30)

    # 3) 코딩 규칙 로드
    rules_text = load_coding_rules(git_dir)

    # 4) ChatGPT(O1) API 호출 → 코드 리뷰 결과 획득
    comments = get_chatgpt_review(
        patch_set=patch_set, rules_text=rules_text, system_prompt=system_prompt, pr=pr
    )

    # 4-1) 코멘트가 없으면 Approve
    if not comments:
        pr.create_review(body="LGTM :)", event="APPROVE")
        print("[SKIP] AI 리뷰 결과 코멘트가 없어 Approve 처리했습니다.")
        return

    # 5) GitHub PR에 코멘트 등록
    post_comments_to_pr(pr, comments)


def clone_repo(pr: PullRequest):
    """
    GHA 환경에서는 이미 대상 레포지토리가 체크아웃되어 있으므로 필요없으나,
    명령줄 환경에서 요구되는 함수입니다.
    """
    dest_dir = tempfile.mkdtemp(prefix="git_repo_")

    repo = pr.base.repo
    clone_url = repo.clone_url
    pr_number = pr.number

    # 1. 레포지토리 clone
    print(f"Cloning repository {repo.full_name} into {dest_dir}...")
    result = subprocess.run(
        ["git", "clone", clone_url, dest_dir], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone repository: {result.stderr}")

    # 2. PR의 ref(fetch) – PR 번호에 해당하는 ref를 로컬 브랜치로 생성
    fetch_command = ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"]
    print(f"Fetching PR branch with command: {' '.join(fetch_command)}")
    result = subprocess.run(fetch_command, cwd=dest_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch PR branch: {result.stderr}")

    # 3. 생성된 브랜치 체크아웃
    checkout_command = ["git", "checkout", f"pr-{pr_number}"]
    print(f"Checking out branch with command: {' '.join(checkout_command)}")
    result = subprocess.run(
        checkout_command, cwd=dest_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to checkout branch: {result.stderr}")

    print(f"Successfully cloned and checked out PR #{pr_number} branch.")

    return dest_dir


def get_github_client(token: str) -> Github:
    """
    Create and return a PyGithub client using the provided token.

    Args:
        token (str): GitHub token for authentication

    Returns:
        Github: PyGithub client instance
    """
    return Github(token)


def get_pull_request(g: Github, repo_name: str, pr_number: int) -> PullRequest:
    """
    Retrieve a specific PullRequest object using PyGithub.

    Args:
        g (Github): PyGithub client.
        repo_name (str): "owner/repo" string.
        pr_number (int): Pull Request number.

    Returns:
        PullRequest: The PullRequest object from PyGithub.
    """
    repo = g.get_repo(repo_name)
    return repo.get_pull(pr_number)


def user_requested_for_review(g: Github, pr: PullRequest) -> bool:
    """
    현재 유저(봇 계정)가 PR의 리뷰 요청 대상자인지 확인.
    즉, re-request가 들어온 상태인지 확인.

    Returns:
        bool: True면 "현재 유저에게 리뷰가 요청된 상태"
    """
    current_user_login = g.get_user().login
    requested_reviewers, requested_teams = pr.get_review_requests()

    # 개인 계정 요청 목록에 포함되어 있다면
    if any(r.login == current_user_login for r in requested_reviewers):
        return True

    # 팀 단위 요청(teams)에 속해있는지도 확인이 필요할 수 있으나,
    # 일반적으로 봇 계정은 팀으로 구성되지 않는 경우가 많으므로 생략.
    # 필요 시 아래와 같이 팀 단위까지 확인 가능.
    #
    # for team in requested_teams:
    #     # team.members 와 current_user_login 비교 (별도 API 필요)
    #     pass

    return False


def get_patchset_from_git(
    git_dir: str, pr: PullRequest, context_lines: int = 3
) -> PatchSet:
    """
    'git diff --unified={context_lines} {base_ref}' 명령어를 실행해
    unified diff를 얻은 뒤, unidiff 라이브러리로 PatchSet 객체를 만들어 반환한다.

    Args:
        pr (PullRequest): The pull request object.
        context_lines (int): diff 생성 시 포함할 context 줄 수(기본 3줄)

    Returns:
        PatchSet: unidiff로 파싱된 diff 정보를 담은 PatchSet 객체
    """
    # GHA에서는 1001 사용자로 checkout 해주지만
    # Docker 사용자는 root 로 하길 권장합니다.
    # 따라서 safe.directory 설정이 필요합니다.
    # 그렇지 않으면 get diff 에서 not a git repository 에러가 발생합니다.
    result = subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", git_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to run git config. Return code: {result.returncode}\n"
            f"stderr: {result.stderr}"
        )

    result = subprocess.run(
        [
            "git",
            "fetch",
            "origin",
            pr.base.ref,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=git_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to run git fetch. Return code: {result.returncode}\n"
            f"stderr: {result.stderr}"
        )

    result = subprocess.run(
        [
            "git",
            "--no-pager",
            "diff",
            f"--unified={context_lines}",
            f"origin/{pr.base.ref}",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=git_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to run git diff. Return code: {result.returncode}\n"
            f"stderr: {result.stderr}"
        )

    diff_text = result.stdout
    return PatchSet(diff_text)


def load_coding_rules(git_dir: str) -> str:
    """
    Load the repository or org-level coding guidelines from a file or other source.

    Returns:
        str: The entire text of the coding rules.
    """
    rules_path = f"{git_dir}/.github/AGENTS.md"
    if os.path.exists(rules_path):
        with open(rules_path, "r", encoding="utf-8") as f:
            return f.read()
    raise FileNotFoundError(f"Could not find coding rules file at: {rules_path}")


def get_chatgpt_review(
    patch_set: PatchSet, rules_text: str, system_prompt: str, pr: PullRequest
) -> list[dict[str, Any]]:
    """
    Send patch info + coding rules to ChatGPT(O3) (via openai) and return raw response.

    Args:
        patch_set (PatchSet): The unidiff PatchSet representing changed files/lines.
        rules_text (str): The loaded coding guidelines.

    Returns:
        list[dict[str, Any]]: List of comments generated by the AI model.
    """
    llm = ChatOpenAI(
        model="o3",
        # o3 에 대한 특수 정책:
        # temperature does not support 0.7 with this model.
        # Only the default (1) value is supported.
        temperature=1,
        model_kwargs={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "AIReviewComments",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )

    prompt = build_prompt(patch_set, rules_text, pr)
    print(f"Prompt: {prompt}")

    current_time = datetime.datetime.now().strftime("%B %d, %Y")

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    f"Today's date is {current_time}.\n"
                    "You are a code reviewer. Your goal is to raise new issues or "
                    "suggestions for the code changes.\n"
                    "- Review the code changes according to the coding rules.\n"
                    "- Suggest a better data structure, algorithm or strategy.\n"
                    "- Verify the implementation satisfies requirements.\n"
                    "- Find bugs and inconsistencies.\n"
                    "- Do not make duplicated or similar comments.\n"
                    "- Do not reply to the existing comments.\n"
                    "If there are no new issues or suggestions, leave no comments.\n"
                    + system_prompt
                )
            ),
            HumanMessage(content=prompt),
        ]
    )

    return json.loads(response.content)["comments"]


SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "AIReviewComments",
    "type": "object",
    "properties": {
        "comments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "해당 코멘트가 달릴 파일의 경로",
                    },
                    "line": {"type": "integer", "description": "파일 내 라인 번호"},
                    "body": {"type": "string", "description": "코멘트 내용"},
                    "side": {
                        "type": "string",
                        "enum": ["LEFT", "RIGHT"],
                        "description": "코멘트가 달릴 위치 (LEFT: 삭제된 라인, RIGHT: 추가된 라인)",
                    },
                },
                "additionalProperties": False,
                "required": ["path", "line", "body", "side"],
            },
        }
    },
    "additionalProperties": False,
    "required": ["comments"],
}


def build_prompt(
    patch_set: PatchSet,
    rules_text: str,
    pr: PullRequest,
    max_diff_bytes: int = 10 * 1024,  # 기본 10KB 제한, 필요에 따라 조정 가능
) -> str:
    patch_summary = []
    for patched_file in patch_set:
        patch_summary.append(f"File: {patched_file.path}")
        file_diff_lines = []
        # 각 파일의 모든 hunk의 라인 정보를 모아서 하나의 문자열로 생성
        for hunk in patched_file:
            for line in hunk:
                if line.is_added:
                    file_diff_lines.append(
                        f"L{line.target_line_no}+ : {line.value.rstrip()}"
                    )
                elif line.is_removed:
                    file_diff_lines.append(
                        f"L{line.source_line_no}- : {line.value.rstrip()}"
                    )
                else:
                    file_diff_lines.append(
                        f"L{line.source_line_no} : {line.value.rstrip()}"
                    )
        file_diff_text = "\n".join(file_diff_lines)
        # utf-8 인코딩 바이트 수 기준으로 크기 체크
        if len(file_diff_text.encode("utf-8")) > max_diff_bytes:
            print(f"[WARN] Diff too large for {patched_file.path}")
            patch_summary.append("Diff: [Too Long]")
        else:
            patch_summary.append(file_diff_text)
    patch_text = "\n".join(patch_summary)

    comments_summary = []
    id_to_threads = {}
    for comment in pr.get_review_comments():
        if comment.in_reply_to_id:
            id_to_threads[comment.in_reply_to_id].append(comment)
        else:
            id_to_threads[comment.id] = [comment]

    for _, threads in id_to_threads.items():
        thread_summary = []
        for thread in threads:
            name_or_login = thread.user.name or thread.user.login
            thread_summary.append(f"From: {name_or_login}\n" f"{thread.body}\n")
        comments_summary.append(
            f"Thread At {threads[0].path}:L{threads[0].position}\n"
            + "--------------\n".join(thread_summary)
        )

    comment_text = "==============\n".join(comments_summary)

    prompt = (
        "<coding-rules>\n"
        f"{rules_text}\n"
        "</coding-rules>\n\n"
        "<pr-title>\n"
        f"{pr.title}\n"
        "</pr-title>\n\n"
        "<pr-body>\n"
        f"{pr.body}\n"
        "</pr-body>\n\n"
        f"<patch-diff>\n"
        "_L13+ : This line was added in the PR._\n"
        "_L13- : This line was removed in the PR._\n"
        "_L13 : This line was unchanged in the PR._\n"
        f"{patch_text}\n"
        "</patch-diff>\n\n"
        f"<existing-comments>\n"
        f"{comment_text}\n"
        "</existing-comments>\n\n"
        "Please raise new issues or suggestions according to the coding rules."
    )
    return prompt


def post_comments_to_pr(pr: PullRequest, comments: list[dict[str, Any]]) -> None:
    """
    Post the AI-generated comments to the specified PR using PyGithub's review comment API.
    Args:
        pr (PullRequest): The PyGithub PullRequest object.
        comments (list[dict[str, Any]]): Each dict:
            {
              "path": str,
              "line": int,
              "body": str,
              "side": str
            }
    Returns:
        None
    """
    commit = pr.get_commits().reversed[0]
    for c in comments:
        try:
            pr.create_review_comment(
                body=c["body"],
                commit=commit,
                path=c["path"],
                line=c["line"],
                side=c["side"],
            )
        except GithubException as e:
            if not any(
                error["message"]
                == "pull_request_review_thread.line must be part of the diff"
                for error in e.data["errors"]
            ):
                raise
            pr.create_review_comment(
                body=f"_AI failed to specify correct line number._\n{c['body']}",
                commit=commit,
                path=c["path"],
                side=c["side"],
                subject_type="file",
            )


if __name__ == "__main__":
    main()
