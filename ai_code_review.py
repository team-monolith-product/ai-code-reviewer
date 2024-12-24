#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
프로젝트: GitHub PR 자동 코드 리뷰 (파일럿 구현)

이 프로젝트는 GitHub Pull Request 이벤트가 발생할 때,
1) PR에서 변경된 파일의 Diff를 확인하고,
2) 사전에 정의된 코딩 규칙과 함께
3) ChatGPT(O1) 모델을 이용하여 자동 리뷰를 수행하고,
4) 결과를 GitHub Pull Request 코멘트(Inline Comment)로 게시하는 것을 목표로 한다.

본 파일은 1차 설계 스켈레톤으로, 주요 라이브러리(PyGithub, unidiff, openai)와
GitHub Actions 환경 변수(GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, OPENAI_API_KEY)를 사용한다.

주요 흐름:
  - (1) GitHub API(PullRequest)에서 Diff 정보(파일별 patch) 획득 → unidiff로 파싱
  - (2) 코딩 규칙 로드
  - (3) ChatGPT(O1) API를 통해 코드 리뷰
  - (4) AI 응답을 분석하여 라인 단위 코멘트 데이터 생성
  - (5) GitHub PR에 In-line Review Comment로 등록

본 1차 설계의 목적:
  - 코드를 어떻게 구성/구현할지 보여주되, 실제 로직은 생략(# TODO)한다.
  - 2차 구현 단계에서 우수한 프로그래머가 이 설계를 토대로 각 함수 내용을 완성할 수 있도록 한다.
"""

import os
from typing import List, Dict, Any
import json

# (1) PyGithub 관련
from github import Github
from github.PullRequest import PullRequest

# (2) unidiff 관련
from unidiff import PatchSet

# (3) openai 관련
import openai
from openai import OpenAI
from openai.types.chat.chat_completion import ChatCompletion


def main() -> None:
    """
    Main workflow, intended to run inside a GitHub Actions container.
    It relies on environment variables provided by the OS (Actions):
      - GITHUB_TOKEN (required to authenticate GitHub API calls)
      - GITHUB_REPOSITORY (e.g. "owner/repo")
      - PR_NUMBER (the pull request number to be analyzed)
      - OPENAI_API_KEY (key for ChatGPT(O1) / OpenAI API)
    """

    # 0) Load environment variables
    github_token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPOSITORY")  # "owner/repo"
    pr_number_str = os.getenv("PR_NUMBER")      # e.g. "123"
    # e.g. "Always answer in Korean."
    system_prompt = os.getenv("SYSTEM_PROMPT")

    if not github_token or not repo_name or not pr_number_str or not system_prompt:
        raise EnvironmentError(
            "Missing one or more required environment variables: "
            "GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, SYSTEM_PROMPT."
        )

    pr_number = int(pr_number_str)

    # 1) PyGithub로 PullRequest 가져오기
    g = get_github_client(github_token)        # -> Github
    pr = get_pull_request(g, repo_name, pr_number)  # -> PullRequest

    # 1-1) 이미 'COMMENTED' 또는 'CHANGES_REQUESTED' 리뷰가 있는지 확인
    if user_already_commented_or_requested_changes(g, pr):
        print("[SKIP] 이미 리뷰가 완료되어 새 리뷰를 남기지 않고 종료합니다.")
        return

    # 2) PullRequest의 파일별 patch를 모아서 unidiff PatchSet 생성
    patch_set = get_diff_patchset(pr)          # -> PatchSet

    # 3) 코딩 규칙 로드
    rules_text = load_coding_rules()           # -> str

    # 4) ChatGPT(O1) API 호출 → 코드 리뷰 결과 획득
    comments = get_chatgpt_review(
        patch_set=patch_set,
        rules_text=rules_text,
        system_prompt=system_prompt
    )                                          # -> OpenAIObject or dict

    # 4-1) 코멘트가 없으면 Approve
    if not comments:
        pr.create_review(body="Approved by AI.", event="APPROVE")
        print("[SKIP] AI 리뷰 결과 코멘트가 없어 Approve 처리했습니다.")
        return

    # 5) GitHub PR에 코멘트 등록
    post_comments_to_pr(pr, comments)


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


def user_already_commented_or_requested_changes(
    g: Github,
    pr: PullRequest
) -> bool:
    """
    현재 유저(current_user_login)가 이미 'APPROVED', 'COMMENTED', 'CHANGES_REQUESTED' 상태의 리뷰를 남겼는지 확인.
    있으면 True, 없으면 False.
    """
    current_user_login = g.get_user().login

    reviews = pr.get_reviews().reversed
    for review in reviews:
        if review.user.login != current_user_login:
            continue

        return review.state in ["APPROVED", "COMMENTED", "CHANGES_REQUESTED"]
    return False


def get_diff_patchset(pr: PullRequest) -> PatchSet:
    """
    From a PullRequest, gather each file's 'patch' text and parse via unidiff.

    Args:
        pr (PullRequest): The pull request object.

    Returns:
        PatchSet: Combined patch set for the entire PR.
    """
    patch_text = ""
    files = pr.get_files()  # Each is PullRequestFile
    for f in files:
        if f.patch:
            # unidiff 파싱을 위해 'diff --git' 헤더가 있어야 하는 경우가 종종 있습니다.
            patch_text += f"diff --git a/{f.filename} b/{f.filename}\n"
            patch_text += f.patch + "\n"
    return PatchSet(patch_text)


def load_coding_rules() -> str:
    """
    Load the repository or org-level coding guidelines from a file or other source.

    Returns:
        str: The entire text of the coding rules.
    """
    rules_path = "/github/workspace/.github/coding-rules.md"
    if os.path.exists(rules_path):
        with open(rules_path, "r", encoding="utf-8") as f:
            return f.read()
    raise FileNotFoundError(
        f"Could not find coding rules file at: {rules_path}")


def get_chatgpt_review(
    patch_set: PatchSet,
    rules_text: str,
    system_prompt: str
) -> List[Dict[str, Any]]:
    """
    Send patch info + coding rules to ChatGPT(O1) (via openai) and return raw response.

    Args:
        patch_set (PatchSet): The unidiff PatchSet representing changed files/lines.
        rules_text (str): The loaded coding guidelines.

    Returns:
        ChatCompletion: The model's response object, typically from openai.ChatCompletion.create().
    """
    client = OpenAI()

    # 1) 프롬프트 생성
    prompt = build_prompt_from_patchset_and_rules(patch_set, rules_text)

    # 2) ChatCompletion 호출
    # 모델 선택은 예시이므로, 필요한 모델로 수정 가능(gpt-3.5-turbo, gpt-4 등)
    response = client.chat.completions.create(
        model="o1",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a code reviewer. Given the patch (diff) and the coding rules, "
                    "review the changes and suggest improvements or highlight issues." + system_prompt
                )
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "AIReviewComments",
                "strict": False,
                "schema": SCHEMA
            }
        }
    )
    return json.loads(response.choices[0].message.content)['comments']


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
                        "description": "해당 코멘트가 달릴 파일의 경로"
                    },
                    "line": {
                        "type": "integer",
                        "description": "파일 내 라인 번호"
                    },
                    "body": {
                        "type": "string",
                        "description": "코멘트 내용"
                    }
                },
                "required": ["path", "line", "body"]
            },
        }
    },
    "required": []
}


def build_prompt_from_patchset_and_rules(patch_set: PatchSet, rules_text: str) -> str:
    """
    단순한 예시 프롬프트 생성기.
    실제로는 Diff가 매우 클 수 있으므로, 토큰 한계에 맞춰 잘라내거나 요약하는 로직이 필요할 수 있습니다.
    """
    patch_summary = []
    for patched_file in patch_set:
        patch_summary.append(f"File: {patched_file.path}")
        for hunk in patched_file:
            for line in hunk:
                if line.is_added:
                    patch_summary.append(
                        f"Line{line.target_line_no}+ : {line.value.strip()}")
                elif line.is_removed:
                    patch_summary.append(
                        f"Line{line.source_line_no}- : {line.value.strip()}")

    patch_text = "\n".join(patch_summary)
    prompt = (
        f"## Coding Rules:\n{rules_text}\n\n"
        f"## Patch Diff:\n{patch_text}\n\n"
        f"Please review the code changes above according to the coding rules."
    )
    return prompt


def post_comments_to_pr(pr: PullRequest, comments: List[Dict[str, Any]]) -> None:
    """
    Post the AI-generated comments to the specified PR using PyGithub's review comment API.

    Args:
        pr (PullRequest): The PyGithub PullRequest object.
        comments (List[Dict[str, Any]]): Each dict:
            {
              "path": str,
              "line": int,
              "body": str
            }

    Returns:
        None
    """
    commit = pr.get_commits().reversed[0]
    for c in comments:
        pr.create_review_comment(
            body=c["body"],
            commit=commit,
            path=c["path"],
            line=c["line"]
        )


if __name__ == "__main__":
    main()
