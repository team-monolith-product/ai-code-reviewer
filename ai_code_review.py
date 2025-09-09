"""
프로젝트: GitHub PR 자동 코드 리뷰

이 프로젝트는 GitHub Pull Request 이벤트가 발생할 때,
1) PR에서 변경된 파일의 Diff를 확인하고,
2) 사전에 정의된 코딩 규칙과 함께
3) ChatGPT(O1) 모델을 이용하여 자동 리뷰를 수행하고,
4) 결과를 GitHub Pull Request 코멘트(Inline Comment)로 게시하는 것을 목표로 한다.
"""

import argparse
import os
import subprocess
import tempfile

from dotenv import load_dotenv
from github import Github
from github.PullRequest import PullRequest
import review_openai

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

    review_openai.review(pr, git_dir, system_prompt)



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
        ["git", "clone", clone_url, dest_dir], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone repository: {result.stderr}")

    # 2. PR의 ref(fetch) – PR 번호에 해당하는 ref를 로컬 브랜치로 생성
    fetch_command = ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"]
    print(f"Fetching PR branch with command: {' '.join(fetch_command)}")
    result = subprocess.run(
        fetch_command, cwd=dest_dir, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch PR branch: {result.stderr}")

    # 3. 생성된 브랜치 체크아웃
    checkout_command = ["git", "checkout", f"pr-{pr_number}"]
    print(f"Checking out branch with command: {' '.join(checkout_command)}")
    result = subprocess.run(
        checkout_command, cwd=dest_dir, capture_output=True, text=True, check=False
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
    requested_reviewers, _ = pr.get_review_requests()

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














if __name__ == "__main__":
    main()
